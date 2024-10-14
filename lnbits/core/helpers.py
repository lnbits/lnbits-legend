import importlib
import re
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

from loguru import logger

from lnbits.core import migrations as core_migrations
from lnbits.core.crud import (
    get_db_versions,
    get_installed_extensions,
    update_migration_version,
)
from lnbits.core.db import db as core_db
from lnbits.core.extensions.models import InstallableExtension
from lnbits.core.models import DbVersion
from lnbits.db import COCKROACH, POSTGRES, SQLITE, Connection
from lnbits.settings import settings


async def migrate_extension_database(
    ext: InstallableExtension, current_version: Optional[DbVersion] = None
):

    try:
        ext_migrations = importlib.import_module(f"{ext.module_name}.migrations")
        ext_db = importlib.import_module(ext.module_name).db
    except ImportError as exc:
        logger.error(exc)
        raise ImportError(f"Cannot import module for extension '{ext.id}'.") from exc

    async with ext_db.connect() as ext_conn:
        await run_migration(ext_conn, ext_migrations, ext.id, current_version)


async def run_migration(
    db: Connection,
    migrations_module: Any,
    db_name: str,
    current_version: Optional[DbVersion] = None,
):
    matcher = re.compile(r"^m(\d\d\d)_")
    for key, migrate in migrations_module.__dict__.items():
        match = matcher.match(key)
        if match:
            version = int(match.group(1))
            if not current_version or version > current_version.version:
                logger.debug(f"running migration {db_name}.{version}")
                print(f"running migration {db_name}.{version}")
                await migrate(db)

                if db.schema is None:
                    await update_migration_version(db, db_name, version)
                else:
                    async with core_db.connect() as conn:
                        await update_migration_version(conn, db_name, version)


def to_valid_user_id(user_id: str) -> UUID:
    if len(user_id) < 32:
        raise ValueError("User ID must have at least 128 bits")
    try:
        int(user_id, 16)
    except Exception as exc:
        raise ValueError("Invalid hex string for User ID.") from exc

    return UUID(hex=user_id[:32], version=4)


async def load_disabled_extension_list() -> None:
    """Update list of extensions that have been explicitly disabled"""
    inactive_extensions = await get_installed_extensions(active=False)
    settings.lnbits_deactivated_extensions.update([e.id for e in inactive_extensions])


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

        current_versions = await get_db_versions(conn)
        core_version = next(
            (v for v in current_versions if v.db == "core"),
            DbVersion(db="core", version=0),
        )
        await run_migration(conn, core_migrations, "core", core_version)

    # here is the first place we can be sure that the
    # `installed_extensions` table has been created
    await load_disabled_extension_list()

    for ext in await get_installed_extensions():
        current_version = next(
            (v for v in current_versions if v.db == ext.id),
            DbVersion(db=ext.id, version=0),
        )
        if current_version is None:
            logger.warning(
                f"Extension {ext.id} has no migration version. This should not happen."
            )
            continue
        try:
            await migrate_extension_database(ext, current_version)
        except Exception as e:
            logger.exception(f"Error migrating extension {ext.id}: {e}")

    logger.info("✔️ All migrations done.")


def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False
