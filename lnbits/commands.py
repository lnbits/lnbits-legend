import asyncio
import importlib
import time
from functools import wraps
from pathlib import Path
from typing import Optional

import click
import httpx
from fastapi.exceptions import HTTPException
from loguru import logger
from packaging import version

from lnbits.core import db as core_db
from lnbits.core.crud import (
    delete_accounts_no_wallets,
    delete_unused_wallets,
    delete_wallet_by_id,
    delete_wallet_payment,
    get_db_versions,
    get_installed_extension,
    get_installed_extensions,
    get_payment,
    get_payments,
    remove_deleted_wallets,
    update_payment,
)
from lnbits.core.extensions.models import (
    CreateExtension,
    ExtensionRelease,
    InstallableExtension,
)
from lnbits.core.helpers import is_valid_url, migrate_databases
from lnbits.core.models import Payment, PaymentState
from lnbits.core.services import check_admin_settings
from lnbits.core.views.extension_api import (
    api_install_extension,
    api_uninstall_extension,
)
from lnbits.settings import settings
from lnbits.wallets.base import Wallet


def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group()
def lnbits_cli():
    """
    Python CLI for LNbits
    """


@lnbits_cli.group()
def db():
    """
    Database related commands
    """


@lnbits_cli.group()
def extensions():
    """
    Extensions related commands
    """


def get_super_user() -> Optional[str]:
    """Get the superuser"""
    superuser_file = Path(settings.lnbits_data_folder, ".super_user")
    if not superuser_file.exists() or not superuser_file.is_file():
        raise ValueError(
            "Superuser id not found. Please check that the file "
            + f"'{superuser_file.absolute()}' exists and has read permissions."
        )
    with open(superuser_file) as file:
        return file.readline()


@lnbits_cli.command("superuser")
def superuser():
    """Prints the superuser"""
    try:
        click.echo(get_super_user())
    except ValueError as e:
        click.echo(str(e))


@lnbits_cli.command("superuser-url")
def superuser_url():
    """Prints the superuser"""
    try:
        click.echo(
            f"http://{settings.host}:{settings.port}/wallet?usr={get_super_user()}"
        )
    except ValueError as e:
        click.echo(str(e))


@lnbits_cli.command("delete-settings")
@coro
async def delete_settings():
    """Deletes the settings"""

    async with core_db.connect() as conn:
        await conn.execute("DELETE from settings")


@db.command("migrate")
def database_migrate():
    """Migrate databases"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(migrate_databases())


@db.command("versions")
@coro
async def db_versions():
    """Show current database versions"""
    async with core_db.connect() as conn:
        click.echo(await get_db_versions(conn))


@db.command("cleanup-wallets")
@click.argument("days", type=int, required=False)
@coro
async def database_cleanup_wallets(days: Optional[int] = None):
    """Delete all wallets that never had any transaction"""
    async with core_db.connect() as conn:
        delta = days or settings.cleanup_wallets_days
        delta = delta * 24 * 60 * 60
        await delete_unused_wallets(delta, conn)


@db.command("cleanup-deleted-wallets")
@coro
async def database_cleanup_deleted_wallets():
    """Delete all wallets that has been marked deleted"""
    async with core_db.connect() as conn:
        await remove_deleted_wallets(conn)


@db.command("delete-wallet")
@click.option("-w", "--wallet", required=True, help="ID of wallet to be deleted.")
@coro
async def database_delete_wallet(wallet: str):
    """Mark wallet as deleted"""
    async with core_db.connect() as conn:
        count = await delete_wallet_by_id(wallet_id=wallet, conn=conn)
        click.echo(f"Marked as deleted '{count}' rows.")


@db.command("delete-wallet-payment")
@click.option("-w", "--wallet", required=True, help="ID of wallet to be deleted.")
@click.option("-c", "--checking-id", required=True, help="Payment checking Id.")
@coro
async def database_delete_wallet_payment(wallet: str, checking_id: str):
    """Delete wallet payment"""
    async with core_db.connect() as conn:
        await delete_wallet_payment(
            wallet_id=wallet, checking_id=checking_id, conn=conn
        )


@db.command("mark-payment-pending")
@click.option("-c", "--checking-id", required=True, help="Payment checking Id.")
@coro
async def database_revert_payment(checking_id: str):
    """Mark payment as pending"""
    async with core_db.connect() as conn:
        payment = await get_payment(checking_id=checking_id, conn=conn)
        payment.status = PaymentState.PENDING
        await update_payment(payment, conn)
        click.echo(f"Payment '{checking_id}' marked as pending.")


@db.command("cleanup-accounts")
@click.argument("days", type=int, required=False)
@coro
async def database_cleanup_accounts(days: Optional[int] = None):
    """Delete all accounts that have no wallets"""
    async with core_db.connect() as conn:
        delta = days or settings.cleanup_wallets_days
        delta = delta * 24 * 60 * 60
        await delete_accounts_no_wallets(delta, conn)


@db.command("check-payments")
@click.option("-d", "--days", help="Maximum age of payments in days.")
@click.option("-l", "--limit", help="Maximum number of payments to be checked.")
@click.option("-w", "--wallet", help="Only check for this wallet.")
@click.option("-v", "--verbose", is_flag=True, help="Detailed log.")
@coro
async def check_invalid_payments(
    days: Optional[int] = None,
    limit: Optional[int] = None,
    wallet: Optional[str] = None,
    verbose: Optional[bool] = False,
):
    """Check payments that are settled in the DB but pending on the Funding Source"""
    await check_admin_settings()
    settled_db_payments = []

    if verbose:
        click.echo(f"Get Payments: days={days}, limit={limit}, wallet={wallet}")
    async with core_db.connect() as conn:
        delta = int(days) if days else 3  # default to 3 days
        limit = int(limit) if limit else 1000
        since = int(time.time()) - delta * 24 * 60 * 60

        settled_db_payments = await get_payments(
            complete=True,
            incoming=True,
            exclude_uncheckable=True,
            since=since,
            limit=limit,
            wallet_id=wallet,
            conn=conn,
        )

    click.echo("Settled Payments: " + str(len(settled_db_payments)))

    wallets_module = importlib.import_module("lnbits.wallets")
    wallet_class = getattr(wallets_module, settings.lnbits_backend_wallet_class)

    funding_source: Wallet = wallet_class()

    click.echo("Funding source: " + str(funding_source))

    # payments that are settled in the DB, but not at the Funding source level
    invalid_payments: list[Payment] = []
    invalid_wallets = {}
    for db_payment in settled_db_payments:
        if verbose:
            click.echo(
                f"Checking Payment: '{db_payment.checking_id}' for wallet"
                + f" '{db_payment.wallet_id}'."
            )
        payment_status = await funding_source.get_invoice_status(db_payment.checking_id)

        if payment_status.pending:
            invalid_payments.append(db_payment)
            if db_payment.wallet_id not in invalid_wallets:
                invalid_wallets[f"{db_payment.wallet_id}"] = [0, 0]
            invalid_wallets[f"{db_payment.wallet_id}"][0] += 1
            invalid_wallets[f"{db_payment.wallet_id}"][1] += db_payment.amount

            click.echo(
                "Invalid Payment:  '"
                + " ".join(
                    [
                        db_payment.checking_id,
                        db_payment.wallet_id,
                        str(db_payment.amount / 1000).ljust(10),
                        db_payment.memo or "",
                    ]
                )
                + "'"
            )

    click.echo("Invalid Payments: " + str(len(invalid_payments)))
    click.echo("\nInvalid Wallets: " + str(len(invalid_wallets)))
    for w in invalid_wallets:
        data = invalid_wallets[f"{w}"]
        click.echo(" ".join([w, str(data[0]), str(data[1] / 1000).ljust(10)]))


@extensions.command("list")
@coro
async def extensions_list():
    """Show currently installed extensions"""
    click.echo("Installed extensions:")

    from lnbits.app import build_all_installed_extensions_list

    for ext in await build_all_installed_extensions_list():
        assert (
            ext.meta and ext.meta.installed_release
        ), f"Extension {ext.id} has no installed_release"
        click.echo(f"  - {ext.id} ({ext.meta.installed_release.version})")


@extensions.command("update")
@click.argument("extension", required=False)
@click.option("-a", "--all-extensions", is_flag=True, help="Update all extensions.")
@click.option(
    "-i", "--repo-index", help="Select the index of the repository to be used."
)
@click.option(
    "-s",
    "--source-repo",
    help="""
        Provide the repository URL to be used for updating.
        The URL must be one present in `LNBITS_EXTENSIONS_MANIFESTS`
        or configured via the Admin UI. This option is required only
        if an extension is present in more than one repository.
    """,
)
@click.option(
    "-u",
    "--url",
    help="Use this option to update a running server. Eg: 'http://localhost:5000'.",
)
@click.option(
    "-d",
    "--admin-user",
    help="Admin user ID (must have permissions to install extensions).",
)
@coro
async def extensions_update(
    extension: Optional[str] = None,
    all_extensions: Optional[bool] = False,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
    admin_user: Optional[str] = None,
):
    """
    Update extension to the latest version.
    If an extension is not present it will be instaled.
    """
    if not extension and not all_extensions:
        click.echo("Extension ID is required.")
        click.echo("Or specify the '--all-extensions' flag to update all extensions")
        return
    if extension and all_extensions:
        click.echo("Only one of extension ID or the '--all' flag must be specified")
        return
    if url and not is_valid_url(url):
        click.echo(f"Invalid '--url' option value: {url}")
        return

    if not await _can_run_operation(url):
        return

    if extension:
        await update_extension(extension, repo_index, source_repo, url, admin_user)
        return

    click.echo("Updating all extensions...")
    installed_extensions = await get_installed_extensions()
    updated_extensions = []
    for e in installed_extensions:
        try:
            click.echo(f"""{"="*50} {e.id} {"="*(50-len(e.id))} """)
            success, msg = await update_extension(
                e.id, repo_index, source_repo, url, admin_user
            )
            if version:
                updated_extensions.append(
                    {"id": e.id, "success": success, "message": msg}
                )
        except Exception as ex:
            click.echo(f"Failed to install extension '{e.id}': {ex}")

    if len(updated_extensions) == 0:
        click.echo("No extension was updated.")
        return

    for u in sorted(updated_extensions, key=lambda d: str(d["id"])):
        status = "updated to  " if u["success"] else "not updated "
        click.echo(
            f"""'{u["id"]}' {" "*(20-len(str(u["id"])))}"""
            + f""" - {status}: '{u["message"]}'"""
        )


@extensions.command("install")
@click.argument("extension")
@click.option(
    "-i", "--repo-index", help="Select the index of the repository to be used."
)
@click.option(
    "-s",
    "--source-repo",
    help="""
        Provide the repository URL to be used for updating.
        The URL must be one present in `LNBITS_EXTENSIONS_MANIFESTS`
        or configured via the Admin UI. This option is required only
        if an extension is present in more than one repository.
    """,
)
@click.option(
    "-u",
    "--url",
    help="Use this option to update a running server. Eg: 'http://localhost:5000'.",
)
@click.option(
    "-d",
    "--admin-user",
    help="Admin user ID (must have permissions to install extensions).",
)
@coro
async def extensions_install(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
    admin_user: Optional[str] = None,
):
    """Install a extension"""
    click.echo(f"Installing {extension}... {repo_index}")
    if url and not is_valid_url(url):
        click.echo(f"Invalid '--url' option value: {url}")
        return

    if not await _can_run_operation(url):
        return
    await install_extension(extension, repo_index, source_repo, url, admin_user)


@extensions.command("uninstall")
@click.argument("extension")
@click.option(
    "-u",
    "--url",
    help="Use this option to update a running server. Eg: 'http://localhost:5000'.",
)
@click.option(
    "-d",
    "--admin-user",
    help="Admin user ID (must have permissions to install extensions).",
)
@coro
async def extensions_uninstall(
    extension: str, url: Optional[str] = None, admin_user: Optional[str] = None
):
    """Uninstall a extension"""
    click.echo(f"Uninstalling '{extension}'...")

    if url and not is_valid_url(url):
        click.echo(f"Invalid '--url' option value: {url}")
        return

    if not await _can_run_operation(url):
        return
    try:
        await _call_uninstall_extension(extension, url, admin_user)
        click.echo(f"Extension '{extension}' uninstalled.")
    except HTTPException as ex:
        click.echo(f"Failed to uninstall '{extension}' Error: '{ex.detail}'.")
        return False, ex.detail
    except Exception as ex:
        click.echo(f"Failed to uninstall '{extension}': {ex!s}.")
        return False, str(ex)


def main():
    """main function"""
    lnbits_cli()


if __name__ == "__main__":
    main()


async def install_extension(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
    admin_user: Optional[str] = None,
) -> tuple[bool, str]:
    try:
        release = await _select_release(extension, repo_index, source_repo)
        if not release:
            return False, "No release selected"

        data = CreateExtension(
            ext_id=extension,
            archive=release.archive,
            source_repo=release.source_repo,
            version=release.version,
        )
        await _call_install_extension(data, url, admin_user)
        click.echo(f"Extension '{extension}' ({release.version}) installed.")
        return True, release.version
    except HTTPException as ex:
        click.echo(f"Failed to install '{extension}' Error: '{ex.detail}'.")
        return False, ex.detail
    except Exception as ex:
        click.echo(f"Failed to install '{extension}': {ex!s}.")
        return False, str(ex)


async def update_extension(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
    admin_user: Optional[str] = None,
) -> tuple[bool, str]:
    try:
        click.echo(f"Updating '{extension}' extension.")
        installed_ext = await get_installed_extension(extension)
        if not installed_ext:
            click.echo(
                f"Extension '{extension}' is not installed. Preparing to install..."
            )
            return await install_extension(extension, repo_index, source_repo, url)

        click.echo(f"Current '{extension}' version: {installed_ext.installed_version}.")

        assert (
            installed_ext.meta and installed_ext.meta.installed_release
        ), "Cannot find previously installed release. Please uninstall first."

        release = await _select_release(extension, repo_index, source_repo)
        if not release:
            return False, "No release selected."
        if (
            release.version == installed_ext.installed_version
            and release.source_repo == installed_ext.meta.installed_release.source_repo
        ):
            click.echo(f"Extension '{extension}' already up to date.")
            return False, "Already up to date"

        click.echo(f"Updating '{extension}' extension to version: {release.version }")

        data = CreateExtension(
            ext_id=extension,
            archive=release.archive,
            source_repo=release.source_repo,
            version=release.version,
        )

        await _call_install_extension(data, url, admin_user)
        click.echo(f"Extension '{extension}' updated.")
        return True, release.version
    except HTTPException as ex:
        click.echo(f"Failed to update '{extension}' Error: '{ex.detail}.")
        return False, ex.detail
    except Exception as ex:
        click.echo(f"Failed to update '{extension}': {ex!s}.")
        return False, str(ex)


async def _select_release(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
) -> Optional[ExtensionRelease]:
    all_releases = await InstallableExtension.get_extension_releases(extension)
    if len(all_releases) == 0:
        click.echo(f"No repository found for extension '{extension}'.")
        return None

    latest_repo_releases = _get_latest_release_per_repo(all_releases)

    if source_repo:
        if source_repo not in latest_repo_releases:
            click.echo(f"Repository not found: '{source_repo}'")
            return None
        return latest_repo_releases[source_repo]

    if len(latest_repo_releases) == 1:
        return latest_repo_releases[next(iter(latest_repo_releases.keys()))]

    repos = list(latest_repo_releases.keys())
    repos.sort()
    if not repo_index:
        click.echo("Multiple repos found. Please select one using:")
        click.echo("   --repo-index   option for index of the repo, or")
        click.echo("   --source-repo  option for the manifest URL")
        click.echo("")
        click.echo("Repositories: ")

        for index, repo in enumerate(repos):
            release = latest_repo_releases[repo]
            click.echo(f"  [{index}] {repo} --> {release.version}")
        click.echo("")
        return None

    if not repo_index.isnumeric() or not 0 <= int(repo_index) < len(repos):
        click.echo(f"--repo-index must be between '0' and '{len(repos) - 1}'")
        return None

    return latest_repo_releases[repos[int(repo_index)]]


def _get_latest_release_per_repo(all_releases):
    latest_repo_releases = {}
    for release in all_releases:
        try:
            if not release.is_version_compatible:
                continue
            # do not remove, parsing also validates
            release_version = version.parse(release.version)
            if release.source_repo not in latest_repo_releases:
                latest_repo_releases[release.source_repo] = release
                continue
            if release_version > version.parse(
                latest_repo_releases[release.source_repo].version
            ):
                latest_repo_releases[release.source_repo] = release
        except version.InvalidVersion as ex:
            logger.warning(f"Invalid version {release.name}: {ex}")
    return latest_repo_releases


async def _call_install_extension(
    data: CreateExtension, url: Optional[str], user_id: Optional[str] = None
):
    if url:
        user_id = user_id or get_super_user()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url}/api/v1/extension?usr={user_id}", json=data.dict(), timeout=40
            )
            resp.raise_for_status()
    else:
        await api_install_extension(data)


async def _call_uninstall_extension(
    extension: str, url: Optional[str], user_id: Optional[str] = None
):
    if url:
        user_id = user_id or get_super_user()
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{url}/api/v1/extension/{extension}?usr={user_id}", timeout=40
            )
            resp.raise_for_status()
    else:
        await api_uninstall_extension(extension)


async def _can_run_operation(url) -> bool:
    await check_admin_settings()
    if await _is_lnbits_started(url):
        if not url:
            click.echo("LNbits server is started. Please either:")
            click.echo(
                f"  - use the '--url' option. Eg: --url=http://{settings.host}:{settings.port}"
            )
            click.echo(
                f"  - stop the server running at 'http://{settings.host}:{settings.port}'"
            )

            return False
    elif url:
        click.echo(
            "The option '--url' has been provided,"
            f" but no server found running at '{url}'"
        )
        return False

    return True


async def _is_lnbits_started(url: Optional[str]):
    try:
        url = url or f"http://{settings.host}:{settings.port}/api/v1/health"
        async with httpx.AsyncClient() as client:
            await client.get(url)
            return True
    except Exception:
        return False
