import asyncio
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import click
import httpx
from fastapi.exceptions import HTTPException
from loguru import logger
from packaging import version

from lnbits.core.models import User
from lnbits.core.services import check_admin_settings
from lnbits.core.views.api import api_install_extension, api_uninstall_extension
from lnbits.settings import settings

from .core import db as core_db
from .core import migrations as core_migrations
from .core.crud import (
    get_dbversions,
    get_inactive_extensions,
    get_installed_extension,
    get_installed_extensions,
)
from .core.helpers import migrate_extension_database, run_migration
from .db import COCKROACH, POSTGRES, SQLITE
from .extension_manager import (
    CreateExtension,
    ExtensionRelease,
    InstallableExtension,
    get_valid_extensions,
)


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


def get_super_user() -> str:
    """Get the superuser"""
    with open(Path(settings.lnbits_data_folder) / ".super_user", "r") as file:
        return file.readline()


@lnbits_cli.command("superuser")
def superuser():
    """Prints the superuser"""
    click.echo(get_super_user())


@lnbits_cli.command("superuser-url")
def superuser_url():
    """Prints the superuser"""
    click.echo(f"http://{settings.host}:{settings.port}/wallet?usr={get_super_user()}")


@lnbits_cli.command("delete-settings")
def delete_settings():
    """Deletes the settings"""

    async def wrap():
        async with core_db.connect() as conn:
            await conn.execute("DELETE from settings")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(wrap())


@db.command("migrate")
def database_migrate():
    """Migrate databases"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(migrate_databases())


async def db_migrate():
    asyncio.create_task(migrate_databases())


async def migrate_databases():
    """Creates the necessary databases if they don't exist already; or migrates them."""

    async with core_db.connect() as conn:
        exists = False
        if conn.type == SQLITE:
            exists = await conn.fetchone(
                "SELECT * FROM sqlite_master WHERE type='table' AND name='dbversions'"
            )
        elif conn.type in {POSTGRES, COCKROACH}:
            exists = await conn.fetchone(
                "SELECT * FROM information_schema.tables WHERE table_schema = 'public'"
                " AND table_name = 'dbversions'"
            )

        if not exists:
            await core_migrations.m000_create_migrations_table(conn)

        current_versions = await get_dbversions(conn)
        core_version = current_versions.get("core", 0)
        await run_migration(conn, core_migrations, core_version)

    for ext in get_valid_extensions():
        current_version = current_versions.get(ext.code, 0)
        try:
            await migrate_extension_database(ext, current_version)
        except Exception as e:
            logger.exception(f"Error migrating extension {ext.code}: {e}")

    logger.info("✔️ All migrations done.")


@db.command("versions")
def database_versions():
    """Show current database versions"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db_versions())


async def db_versions():
    """Show current database versions"""
    async with core_db.connect() as conn:
        return await get_dbversions(conn)


async def load_disabled_extension_list() -> None:
    """Update list of extensions that have been explicitly disabled"""
    inactive_extensions = await get_inactive_extensions()
    settings.lnbits_deactivated_extensions += inactive_extensions


@extensions.command("list")
def extensions_list():
    """Show currently installed extensions"""
    click.echo("Installed extensions:")

    async def wrap():
        from lnbits.app import build_all_installed_extensions_list

        for ext in await build_all_installed_extensions_list():
            assert ext.installed_release, f"Extension {ext.id} has no installed_release"
            click.echo(f"  - {ext.id} ({ext.installed_release.version})")

    _run_async(wrap)


@extensions.command("upgrade")
@click.argument("extension", required=False)
@click.option("-a", "--all", is_flag=True, help="Upgrade all extensions.")
@click.option(
    "-i", "--repo-index", help="Select the index of the repository to be used."
)
@click.option(
    "-s", "--source-repo", help="Provide the repository URL to be used for upgrading."
)
@click.option(
    "-u",
    "--url",
    help="Use this option to update a running server. Eg: 'http://localhost:5000'.",
)
def extensions_upgrade(
    extension: Optional[str] = None,
    all: Optional[bool] = False,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
):
    """Upgrade extensions"""
    if not extension and not all:
        click.echo("Extension ID is required.")
        click.echo("Or specify the '--all' flag to upgrade all extensions")
        return
    if extension and all:
        click.echo("Only one of extension ID or the '--all' flag must be specified")
        return
    if url and not _is_url(url):
        click.echo(f"Invalid '--url' option value: {url}")
        return

    async def wrap():
        await check_admin_settings()
        if not await _can_run_operation(url):
            return

        if extension:
            await upgrade_extension(extension, repo_index, source_repo, url)
            return

        click.echo("Upgrading all extensions...")
        installed_extensions = await get_installed_extensions()
        upgraded_extensions = []
        for e in installed_extensions:
            try:
                click.echo(f"""{"="*50} {e.id} {"="*(50-len(e.id))} """)
                success, msg = await upgrade_extension(
                    e.id, repo_index, source_repo, url
                )
                if version:
                    upgraded_extensions.append(
                        {"id": e.id, "success": success, "message": msg}
                    )
            except Exception as ex:
                click.echo(f"Failed to install extension '{e.id}': {ex}")

        if len(upgraded_extensions) == 0:
            click.echo("No extension was upgraded.")
        else:
            for u in sorted(upgraded_extensions, key=lambda d: d["id"]):
                status = "upgraded to  " if u["success"] else "not upgraded "
                click.echo(
                    f"""'{u["id"]}' {" "*(20-len(u["id"]))}"""
                    + f""" - {status}: '{u["message"]}'"""
                )

    _run_async(wrap)
    return


@extensions.command("install")
@click.argument("extension")
@click.option("--repo-index", help="Select the index of the repository to be used.")
@click.option(
    "--source-repo", help="Provide the repository URL to be used for installing."
)
@click.option(
    "-u",
    "--url",
    help="Use this option to update a running server. Eg: 'http://localhost:5000'.",
)
def extensions_install(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
):
    """Install a extension"""
    click.echo(f"Installing {extension}... {repo_index}")
    if url and not _is_url(url):
        click.echo(f"Invalid '--url' option value: {url}")
        return

    async def wrap():
        if not await _can_run_operation(url):
            return
        await install_extension(extension, repo_index, source_repo, url)

    _run_async(wrap)


@extensions.command("uninstall")
@click.argument("extension")
@click.option(
    "-u",
    "--url",
    help="Use this option to update a running server. Eg: 'http://localhost:5000'.",
)
def extensions_uninstall(extension: str, url: Optional[str] = None):
    """Uninstall a extension"""
    click.echo(f"Uninstalling '{extension}'...")

    if url and not _is_url(url):
        click.echo(f"Invalid '--url' option value: {url}")
        return

    async def wrap():
        if not await _can_run_operation(url):
            return
        try:
            user = User(id=get_super_user(), super_user=True)
            await _call_uninstall_extension(extension, user, url)
            click.echo(f"Extension '{extension}' uninstalled.")
        except HTTPException as ex:
            click.echo(f"Failed to uninstall '{extension}' Error: '{ex.detail}'.")
            return False, ex.detail
        except Exception as ex:
            click.echo(f"Failed to uninstall '{extension}': {str(ex)}.")
            return False, str(ex)

    _run_async(wrap)


def main():
    """main function"""
    lnbits_cli()


if __name__ == "__main__":
    main()


def _run_async(fn) -> Any:
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(fn())


async def install_extension(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
) -> Tuple[bool, str]:
    try:
        release = await _select_release(extension, repo_index, source_repo)
        if not release:
            return False, "No release selected"

        data = CreateExtension(
            ext_id=extension, archive=release.archive, source_repo=release.source_repo
        )
        user = User(id=get_super_user(), super_user=True)
        await _call_install_extension(data, user, url)
        click.echo(f"Extension '{extension}' ({release.version}) installed.")
        return True, release.version
    except HTTPException as ex:
        click.echo(f"Failed to install '{extension}' Error: '{ex.detail}'.")
        return False, ex.detail
    except Exception as ex:
        click.echo(f"Failed to install '{extension}': {str(ex)}.")
        return False, str(ex)


async def upgrade_extension(
    extension: str,
    repo_index: Optional[str] = None,
    source_repo: Optional[str] = None,
    url: Optional[str] = None,
) -> Tuple[bool, str]:
    try:
        click.echo(f"Upgrading '{extension}' extension.")
        installed_ext = await get_installed_extension(extension)
        if not installed_ext:
            click.echo(
                f"Extension '{extension}' is not installed. Preparing to install..."
            )
            return await install_extension(extension, repo_index, source_repo, url)

        click.echo(f"Current '{extension}' version: {installed_ext.installed_version}.")

        assert (
            installed_ext.installed_release
        ), "Cannot find previously installed release. Please uninstall first."

        release = await _select_release(extension, repo_index, source_repo)
        if not release:
            return False, "No release selected."
        if (
            release.version == installed_ext.installed_version
            and release.source_repo == installed_ext.installed_release.source_repo
        ):
            click.echo(f"Extension '{extension}' already up to date.")
            return False, "Already up to date"

        click.echo(f"Upgrading '{extension}' extension to version: {release.version }")

        data = CreateExtension(
            ext_id=extension, archive=release.archive, source_repo=release.source_repo
        )
        user = User(id=get_super_user(), super_user=True)

        await _call_install_extension(data, user, url)
        click.echo(f"Extension '{extension}' upgraded.")
        return True, release.version
    except HTTPException as ex:
        click.echo(f"Failed to upgrade '{extension}' Error: '{ex.detail}'.")
        return False, ex.detail
    except Exception as ex:
        click.echo(f"Failed to upgrade '{extension}': {str(ex)}.")
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
        return latest_repo_releases[list(latest_repo_releases.keys())[0]]

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
    data: CreateExtension, user: User, url: Optional[str]
):
    if url:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url}/api/v1/extension?usr={user.id}", json=data.dict(), timeout=40
            )
            resp.raise_for_status()
    else:
        await api_install_extension(data, user)


async def _call_uninstall_extension(extension: str, user: User, url: Optional[str]):
    if url:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{url}/api/v1/extension/{extension}?usr={user.id}", timeout=40
            )
            resp.raise_for_status()
    else:
        await api_uninstall_extension(extension, user)


async def _can_run_operation(url) -> bool:
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
            + f" but no server found runnint at '{url}'"
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


def _is_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False
