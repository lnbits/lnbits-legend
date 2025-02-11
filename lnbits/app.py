import asyncio
import glob
import importlib
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from lnbits.core.crud import (
    get_db_version,
    get_installed_extensions,
    update_installed_extension_state,
)
from lnbits.core.crud.extensions import create_installed_extension
from lnbits.core.helpers import migrate_extension_database
from lnbits.core.models.notifications import NotificationType
from lnbits.core.services.extensions import deactivate_extension, get_valid_extensions
from lnbits.core.services.notifications import enqueue_notification
from lnbits.core.tasks import (
    audit_queue,
    collect_exchange_rates_data,
    purge_audit_data,
    run_by_the_minute_tasks,
    wait_for_audit_data,
    wait_for_paid_invoices,
    wait_notification_messages,
)
from lnbits.exceptions import register_exception_handlers
from lnbits.helpers import version_parse
from lnbits.settings import settings
from lnbits.tasks import (
    cancel_all_tasks,
    create_permanent_task,
    register_invoice_listener,
)
from lnbits.utils.cache import cache
from lnbits.utils.logger import (
    configure_logger,
    initialize_server_websocket_logger,
    log_server_info,
)
from lnbits.wallets import get_funding_source, set_funding_source

from .commands import migrate_databases
from .core import init_core_routers
from .core.db import core_app_extra
from .core.models.extensions import Extension, ExtensionMeta, InstallableExtension
from .core.services import check_admin_settings, check_webpush_settings
from .middleware import (
    AuditMiddleware,
    ExtensionsRedirectMiddleware,
    InstalledExtensionMiddleware,
    add_first_install_middleware,
    add_ip_block_middleware,
    add_ratelimit_middleware,
)
from .requestvars import g
from .tasks import (
    check_pending_payments,
    internal_invoice_listener,
    invoice_listener,
)


async def startup(app: FastAPI):
    settings.lnbits_running = True

    # wait till migration is done
    await migrate_databases()

    # setup admin settings
    await check_admin_settings()
    await check_webpush_settings()

    # check extensions after restart
    if not settings.lnbits_extensions_deactivate_all:
        await check_and_register_extensions(app)

    log_server_info()

    # initialize WALLET
    try:
        set_funding_source()
    except Exception as e:
        logger.error(f"Error initializing {settings.lnbits_backend_wallet_class}: {e}")
        set_void_wallet_class()

    # initialize funding source
    await check_funding_source()

    # register core routes
    init_core_routers(app)

    # initialize tasks
    register_async_tasks()

    enqueue_notification(
        NotificationType.server_start_stop,
        {
            "message": "LNbits server started.",
            "up_time": settings.lnbits_server_up_time,
        },
    )


async def shutdown():
    logger.warning("LNbits shutting down...")
    enqueue_notification(
        NotificationType.server_start_stop,
        {
            "message": "LNbits server shutting down...",
            "up_time": settings.lnbits_server_up_time,
        },
    )
    settings.lnbits_running = False

    # shutdown event
    cancel_all_tasks()

    # wait a bit to allow them to finish, so that cleanup can run without problems
    await asyncio.sleep(0.1)
    funding_source = get_funding_source()
    await funding_source.cleanup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup(app)
    yield
    await shutdown()


def create_app() -> FastAPI:
    configure_logger()
    app = FastAPI(
        title=settings.lnbits_title,
        description=(
            "API for LNbits, the free and open source bitcoin wallet and "
            "accounts system with plugins."
        ),
        version=settings.version,
        lifespan=lifespan,
        license_info={
            "name": "MIT License",
            "url": "https://raw.githubusercontent.com/lnbits/lnbits/main/LICENSE",
        },
    )

    # Allow registering new extensions routes without direct access to the `app` object
    core_app_extra.register_new_ext_routes = register_new_ext_routes(app)
    core_app_extra.register_new_ratelimiter = register_new_ratelimiter(app)

    # register static files
    static_path = Path("lnbits", "static")
    static = StaticFiles(directory=static_path)
    app.mount("/static", static, name="static")

    g().base_url = f"http://{settings.host}:{settings.port}"

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.add_middleware(AuditMiddleware, audit_queue=audit_queue)

    # required for SSO login
    app.add_middleware(SessionMiddleware, secret_key=settings.auth_secret_key)

    # order of these two middlewares is important
    app.add_middleware(InstalledExtensionMiddleware)
    app.add_middleware(ExtensionsRedirectMiddleware)

    register_custom_extensions_path()

    add_first_install_middleware(app)

    # adds security middleware
    add_ip_block_middleware(app)
    add_ratelimit_middleware(app)

    register_exception_handlers(app)

    return app


async def check_funding_source() -> None:
    funding_source = get_funding_source()

    max_retries = settings.funding_source_max_retries
    retry_counter = 0

    while settings.lnbits_running:
        try:
            logger.info(f"Connecting to backend {funding_source.__class__.__name__}...")
            error_message, balance = await funding_source.status()
            if not error_message:
                retry_counter = 0
                logger.success(
                    f"✔️ Backend {funding_source.__class__.__name__} connected "
                    f"and with a balance of {balance} msat."
                )
                break
            logger.error(
                f"The backend for {funding_source.__class__.__name__} isn't "
                f"working properly: '{error_message}'",
                RuntimeWarning,
            )
        except Exception as e:
            logger.error(
                f"Error connecting to {funding_source.__class__.__name__}: {e}"
            )

        if retry_counter >= max_retries:
            set_void_wallet_class()
            funding_source = get_funding_source()
            break

        retry_counter += 1
        sleep_time = min(0.25 * (2**retry_counter), 60)
        logger.warning(
            f"Retrying connection to backend in {sleep_time} seconds... "
            f"({retry_counter}/{max_retries})"
        )
        await asyncio.sleep(sleep_time)


def set_void_wallet_class():
    logger.warning(
        "Fallback to VoidWallet, because the backend for "
        f"{settings.lnbits_backend_wallet_class} isn't working properly"
    )
    set_funding_source("VoidWallet")


async def check_installed_extensions(app: FastAPI):
    """
    Check extensions that have been installed, but for some reason no longer present in
    the 'lnbits/extensions' directory. One reason might be a docker-container that was
    re-created. The 'data' directory (where the '.zip' files live) is expected to
    persist state. Zips that are missing will be re-downloaded.
    """

    installed_extensions = await build_all_installed_extensions_list(False)

    for ext in installed_extensions:
        try:
            installed = await check_installed_extension_files(ext)
            if not installed:
                await restore_installed_extension(app, ext)
                logger.info(
                    "✔️ Successfully re-installed extension: "
                    f"{ext.id} ({ext.installed_version})"
                )
        except Exception as e:
            logger.warning(e)
            await deactivate_extension(ext.id)
            logger.warning(
                f"Failed to re-install extension: {ext.id} ({ext.installed_version})"
            )

    logger.info(f"Installed Extensions ({len(installed_extensions)}):")
    for ext in installed_extensions:
        logger.info(f"{ext.id} ({ext.installed_version})")


async def build_all_installed_extensions_list(
    include_deactivated: Optional[bool] = True,
) -> list[InstallableExtension]:
    """
    Returns a list of all the installed extensions plus the extensions that
    MUST be installed by default (see LNBITS_EXTENSIONS_DEFAULT_INSTALL).
    """
    installed_extensions = await get_installed_extensions()
    settings.lnbits_installed_extensions_ids = {e.id for e in installed_extensions}

    for ext_dir in Path(settings.lnbits_extensions_path, "extensions").iterdir():
        try:
            if not ext_dir.is_dir():
                continue
            ext_id = ext_dir.name
            if ext_id in settings.lnbits_installed_extensions_ids:
                continue
            ext_info = InstallableExtension.from_ext_dir(ext_id)
            if not ext_info:
                continue

            installed_extensions.append(ext_info)
            await create_installed_extension(ext_info)
            current_version = await get_db_version(ext_id)
            await migrate_extension_database(ext_info, current_version)

        except Exception as e:
            logger.warning(e)

    for ext_id in settings.lnbits_extensions_default_install:
        if ext_id in settings.lnbits_installed_extensions_ids:
            continue

        ext_releases = await InstallableExtension.get_extension_releases(ext_id)
        ext_releases = sorted(
            ext_releases, key=lambda r: version_parse(r.version), reverse=True
        )

        release = next((e for e in ext_releases if e.is_version_compatible), None)

        if release:
            ext_meta = ExtensionMeta(installed_release=release)
            ext_info = InstallableExtension(
                id=ext_id,
                name=ext_id,
                version=release.version,
                icon=release.icon,
                meta=ext_meta,
            )
            installed_extensions.append(ext_info)

    if include_deactivated:
        return installed_extensions

    if settings.lnbits_extensions_deactivate_all:
        return []

    return [
        e
        for e in installed_extensions
        if e.id not in settings.lnbits_deactivated_extensions
    ]


async def check_installed_extension_files(ext: InstallableExtension) -> bool:
    if ext.has_installed_version:
        return True

    zip_files = glob.glob(os.path.join(settings.lnbits_data_folder, "zips", "*.zip"))

    if f"./{ext.zip_path!s}" not in zip_files:
        await ext.download_archive()
    ext.extract_archive()

    return False


async def restore_installed_extension(app: FastAPI, ext: InstallableExtension):
    await update_installed_extension_state(ext_id=ext.id, active=True)

    extension = Extension.from_installable_ext(ext)
    register_ext_routes(app, extension)

    current_version = await get_db_version(ext.id)
    await migrate_extension_database(ext, current_version)

    # mount routes for the new version
    core_app_extra.register_new_ext_routes(extension)


def register_custom_extensions_path():
    upgrades_dir = settings.lnbits_extensions_upgrade_path
    shutil.rmtree(upgrades_dir, True)
    Path(upgrades_dir).mkdir(parents=True, exist_ok=True)
    sys.path.append(str(upgrades_dir))

    if settings.has_default_extension_path:
        return
    default_ext_path = os.path.join("lnbits", "extensions")
    if os.path.isdir(default_ext_path) and len(os.listdir(default_ext_path)) != 0:
        logger.warning(
            "You are using a custom extensions path, "
            + "but the default extensions directory is not empty. "
            + f"Please clean-up the '{default_ext_path}' directory."
        )
        logger.warning(
            f"You can move the existing '{default_ext_path}' directory to: "
            + f" '{settings.lnbits_extensions_path}/extensions'"
        )

    extensions_dir = Path(settings.lnbits_extensions_path, "extensions")
    Path(extensions_dir).mkdir(parents=True, exist_ok=True)
    sys.path.append(str(extensions_dir))


def register_new_ext_routes(app: FastAPI) -> Callable:
    # Returns a function that registers new routes for an extension.
    # The returned function encapsulates (creates a closure around)
    # the `app` object but does expose it.
    def register_new_ext_routes_fn(ext: Extension):
        register_ext_routes(app, ext)

    return register_new_ext_routes_fn


def register_new_ratelimiter(app: FastAPI) -> Callable:
    def register_new_ratelimiter_fn():
        limiter = Limiter(
            key_func=get_remote_address,
            default_limits=[
                f"{settings.lnbits_rate_limit_no}/{settings.lnbits_rate_limit_unit}"
            ],
        )
        app.state.limiter = limiter

    return register_new_ratelimiter_fn


def register_ext_tasks(ext: Extension) -> None:
    """Register extension async tasks."""
    ext_module = importlib.import_module(ext.module_name)

    if hasattr(ext_module, f"{ext.code}_start"):
        ext_start_func = getattr(ext_module, f"{ext.code}_start")
        ext_start_func()


def register_ext_routes(app: FastAPI, ext: Extension) -> None:
    """Register FastAPI routes for extension."""
    ext_module = importlib.import_module(ext.module_name)

    ext_route = getattr(ext_module, f"{ext.code}_ext")

    if hasattr(ext_module, f"{ext.code}_static_files"):
        ext_statics = getattr(ext_module, f"{ext.code}_static_files")
        for s in ext_statics:
            static_dir = Path(
                settings.lnbits_extensions_path, "extensions", *s["path"].split("/")
            )
            app.mount(s["path"], StaticFiles(directory=static_dir), s["name"])

    ext_redirects = (
        getattr(ext_module, f"{ext.code}_redirect_paths")
        if hasattr(ext_module, f"{ext.code}_redirect_paths")
        else []
    )

    settings.activate_extension_paths(ext.code, ext.upgrade_hash, ext_redirects)

    logger.trace(f"Adding route for extension {ext_module}.")
    prefix = f"/upgrades/{ext.upgrade_hash}" if ext.upgrade_hash != "" else ""
    app.include_router(router=ext_route, prefix=prefix)


async def check_and_register_extensions(app: FastAPI):
    await check_installed_extensions(app)
    for ext in await get_valid_extensions(False):
        try:
            register_ext_routes(app, ext)
            register_ext_tasks(ext)
        except Exception as exc:
            logger.error(f"Could not load extension `{ext.code}`: {exc!s}")


def register_async_tasks():

    create_permanent_task(wait_for_audit_data)
    create_permanent_task(wait_notification_messages)

    create_permanent_task(check_pending_payments)
    create_permanent_task(invoice_listener)
    create_permanent_task(internal_invoice_listener)
    create_permanent_task(cache.invalidate_forever)

    # core invoice listener
    invoice_queue: asyncio.Queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "core")
    create_permanent_task(lambda: wait_for_paid_invoices(invoice_queue))

    create_permanent_task(run_by_the_minute_tasks)
    create_permanent_task(purge_audit_data)
    create_permanent_task(collect_exchange_rates_data)

    # server logs for websocket
    if settings.lnbits_admin_ui:
        server_log_task = initialize_server_websocket_logger()
        create_permanent_task(server_log_task)
