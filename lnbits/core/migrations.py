import json
from time import time
from typing import Any

from loguru import logger
from sqlalchemy.exc import OperationalError

from lnbits import bolt11
from lnbits.db import Connection


async def m000_create_migrations_table(db: Connection):
    await db.execute(
        """
    CREATE TABLE IF NOT EXISTS dbversions (
        db TEXT PRIMARY KEY,
        version INT NOT NULL
    )
    """
    )


async def m001_initial(db: Connection):
    """
    Initial LNbits tables.
    """
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT,
            pass TEXT
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS extensions (
            "user" TEXT NOT NULL,
            extension TEXT NOT NULL,
            active BOOLEAN DEFAULT false,

            UNIQUE ("user", extension)
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS wallets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            "user" TEXT NOT NULL,
            adminkey TEXT NOT NULL,
            inkey TEXT
        );
    """
    )
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS apipayments (
            payhash TEXT NOT NULL,
            amount {db.big_int} NOT NULL,
            fee INTEGER NOT NULL DEFAULT 0,
            wallet TEXT NOT NULL,
            pending BOOLEAN NOT NULL,
            memo TEXT,
            time TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            UNIQUE (wallet, payhash)
        );
    """
    )

    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT wallet, COALESCE(SUM(s), 0) AS balance FROM (
            SELECT wallet, SUM(amount) AS s  -- incoming
            FROM apipayments
            WHERE amount > 0 AND pending = false  -- don't sum pending
            GROUP BY wallet
            UNION ALL
            SELECT wallet, SUM(amount + fee) AS s  -- outgoing, sum fees
            FROM apipayments
            WHERE amount < 0  -- do sum pending
            GROUP BY wallet
        )x
        GROUP BY wallet;
    """
    )


async def m002_add_fields_to_apipayments(db: Connection):
    """
    Adding fields to apipayments for better accounting,
    and renaming payhash to checking_id since that is what it really is.
    """
    try:
        await db.execute("ALTER TABLE apipayments RENAME COLUMN payhash TO checking_id")
        await db.execute("ALTER TABLE apipayments ADD COLUMN hash TEXT")
        await db.execute("CREATE INDEX by_hash ON apipayments (hash)")
        await db.execute("ALTER TABLE apipayments ADD COLUMN preimage TEXT")
        await db.execute("ALTER TABLE apipayments ADD COLUMN bolt11 TEXT")
        await db.execute("ALTER TABLE apipayments ADD COLUMN extra TEXT")

        result = await db.execute("SELECT * FROM apipayments")
        rows = result.mappings().all()
        for row in rows:
            if not row["memo"] or not row["memo"].startswith("#"):
                continue

            for ext in ["withdraw", "events", "lnticket", "paywall", "tpos"]:
                prefix = f"#{ext} "
                if row["memo"].startswith(prefix):
                    new = row["memo"][len(prefix) :]
                    await db.execute(
                        """
                        UPDATE apipayments SET extra = :extra, memo = :memo1
                        WHERE checking_id = :checking_id AND memo = :memo2
                        """,
                        {
                            "extra": json.dumps({"tag": ext}),
                            "memo1": new,
                            "checking_id": row["checking_id"],
                            "memo2": row["memo"],
                        },
                    )
                    break
    except OperationalError:
        # this is necessary now because it may be the case that this migration will
        # run twice in some environments.
        # catching errors like this won't be necessary in anymore now that we
        # keep track of db versions so no migration ever runs twice.
        pass


async def m003_add_invoice_webhook(db: Connection):
    """
    Special column for webhook endpoints that can be assigned
    to each different invoice.
    """

    await db.execute("ALTER TABLE apipayments ADD COLUMN webhook TEXT")
    await db.execute("ALTER TABLE apipayments ADD COLUMN webhook_status TEXT")


async def m004_ensure_fees_are_always_negative(db: Connection):
    """
    Use abs() so wallet backends don't have to care about the sign of the fees.
    """

    await db.execute("DROP VIEW balances")
    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT wallet, COALESCE(SUM(s), 0) AS balance FROM (
            SELECT wallet, SUM(amount) AS s  -- incoming
            FROM apipayments
            WHERE amount > 0 AND pending = false  -- don't sum pending
            GROUP BY wallet
            UNION ALL
            SELECT wallet, SUM(amount - abs(fee)) AS s  -- outgoing, sum fees
            FROM apipayments
            WHERE amount < 0  -- do sum pending
            GROUP BY wallet
        )x
        GROUP BY wallet;
    """
    )


async def m005_balance_check_balance_notify(db: Connection):
    """
    Keep track of balanceCheck-enabled lnurl-withdrawals to be consumed by an
    LNbits wallet and of balanceNotify URLs supplied by users to empty their wallets.
    """

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_check (
          wallet TEXT NOT NULL REFERENCES wallets (id),
          service TEXT NOT NULL,
          url TEXT NOT NULL,

          UNIQUE(wallet, service)
        );
    """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_notify (
          wallet TEXT NOT NULL REFERENCES wallets (id),
          url TEXT NOT NULL,

          UNIQUE(wallet, url)
        );
    """
    )


async def m006_add_invoice_expiry_to_apipayments(db: Connection):
    """
    Adds invoice expiry column to apipayments.
    """
    try:
        await db.execute("ALTER TABLE apipayments ADD COLUMN expiry TIMESTAMP")
    except OperationalError:
        pass


async def m007_set_invoice_expiries(db: Connection):
    """
    Precomputes invoice expiry for existing pending incoming payments.
    """
    try:
        result = await db.execute(
            f"""
            SELECT bolt11, checking_id
            FROM apipayments
            WHERE pending = true
            AND amount > 0
            AND bolt11 IS NOT NULL
            AND expiry IS NULL
            AND time < {db.timestamp_now}
            """
        )
        rows = result.mappings().all()
        if len(rows):
            logger.info(f"Migration: Checking expiry of {len(rows)} invoices")
        for i, (
            payment_request,
            checking_id,
        ) in enumerate(rows):
            try:
                invoice = bolt11.decode(payment_request)
                if invoice.expiry is None:
                    continue

                expiration_date = invoice.date + invoice.expiry
                logger.info(
                    f"Migration: {i+1}/{len(rows)} setting expiry of invoice"
                    f" {invoice.payment_hash} to {expiration_date}"
                )
                await db.execute(
                    f"""
                    UPDATE apipayments SET expiry = {db.timestamp_placeholder('expiry')}
                    WHERE checking_id = :checking_id AND amount > 0
                    """,
                    {"expiry": expiration_date, "checking_id": checking_id},
                )
            except Exception:
                continue
    except OperationalError:
        # this is necessary now because it may be the case that this migration will
        # run twice in some environments.
        # catching errors like this won't be necessary in anymore now that we
        # keep track of db versions so no migration ever runs twice.
        pass


async def m008_create_admin_settings_table(db: Connection):
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            super_user TEXT,
            editable_settings TEXT NOT NULL DEFAULT '{}'
        );
    """
    )


async def m009_create_tinyurl_table(db: Connection):
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS tiny_url (
          id TEXT PRIMARY KEY,
          url TEXT,
          endless BOOL NOT NULL DEFAULT false,
          wallet TEXT,
          time TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
    """
    )


async def m010_create_installed_extensions_table(db: Connection):
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS installed_extensions (
            id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            short_description TEXT,
            icon TEXT,
            stars INT NOT NULL DEFAULT 0,
            active BOOLEAN DEFAULT false,
            meta TEXT NOT NULL DEFAULT '{}'
        );
    """
    )


async def m011_optimize_balances_view(db: Connection):
    """
    Make the calculation of the balance a single aggregation
    over the payments table instead of 2.
    """
    await db.execute("DROP VIEW balances")
    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT wallet, SUM(amount - abs(fee)) AS balance
        FROM apipayments
        WHERE (pending = false AND amount > 0) OR amount < 0
        GROUP BY wallet
    """
    )


async def m012_add_currency_to_wallet(db: Connection):
    await db.execute(
        """
        ALTER TABLE wallets ADD COLUMN currency TEXT
        """
    )


async def m013_add_deleted_to_wallets(db: Connection):
    """
    Adds deleted column to wallets.
    """
    try:
        await db.execute(
            "ALTER TABLE wallets ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT false"
        )
    except OperationalError:
        pass


async def m014_set_deleted_wallets(db: Connection):
    """
    Sets deleted column to wallets.
    """
    try:
        result = await db.execute(
            """
            SELECT *
            FROM wallets
            WHERE user LIKE 'del:%'
            AND adminkey LIKE 'del:%'
            AND inkey LIKE 'del:%'
            """
        )
        rows = result.mappings().all()

        for row in rows:
            try:
                user = row["user"].split(":")[1]
                adminkey = row["adminkey"].split(":")[1]
                inkey = row["inkey"].split(":")[1]
                await db.execute(
                    """
                    UPDATE wallets SET
                    "user" = :user, adminkey = :adminkey, inkey = :inkey, deleted = true
                    WHERE id = :wallet
                    """,
                    {
                        "user": user,
                        "adminkey": adminkey,
                        "inkey": inkey,
                        "wallet": row.get("id"),
                    },
                )
            except Exception:
                continue
    except OperationalError:
        # this is necessary now because it may be the case that this migration will
        # run twice in some environments.
        # catching errors like this won't be necessary in anymore now that we
        # keep track of db versions so no migration ever runs twice.
        pass


async def m015_create_push_notification_subscriptions_table(db: Connection):
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS webpush_subscriptions (
            endpoint TEXT NOT NULL,
            "user" TEXT NOT NULL,
            data TEXT NOT NULL,
            host TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            PRIMARY KEY (endpoint, "user")
        );
    """
    )


async def m016_add_username_column_to_accounts(db: Connection):
    """
    Adds username column to accounts.
    """
    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN username TEXT")
        await db.execute("ALTER TABLE accounts ADD COLUMN extra TEXT")
    except OperationalError:
        pass


async def m017_add_timestamp_columns_to_accounts_and_wallets(db: Connection):
    """
    Adds created_at and updated_at column to accounts and wallets.
    """
    try:
        await db.execute(
            "ALTER TABLE accounts "
            f"ADD COLUMN created_at TIMESTAMP DEFAULT {db.timestamp_column_default}"
        )
        await db.execute(
            "ALTER TABLE accounts "
            f"ADD COLUMN updated_at TIMESTAMP DEFAULT {db.timestamp_column_default}"
        )
        await db.execute(
            "ALTER TABLE wallets "
            f"ADD COLUMN created_at TIMESTAMP DEFAULT {db.timestamp_column_default}"
        )
        await db.execute(
            "ALTER TABLE wallets "
            f"ADD COLUMN updated_at TIMESTAMP DEFAULT {db.timestamp_column_default}"
        )

        # # set their wallets created_at with the first payment
        # await db.execute(
        #     """
        #     UPDATE wallets SET created_at = (
        #         SELECT time FROM apipayments
        #         WHERE apipayments.wallet = wallets.id
        #         ORDER BY time ASC LIMIT 1
        #     )
        #  """
        # )

        # # then set their accounts created_at with the wallet
        # await db.execute(
        #     """
        #     UPDATE accounts SET created_at = (
        #         SELECT created_at FROM wallets
        #         WHERE wallets.user = accounts.id
        #         ORDER BY created_at ASC LIMIT 1
        #     )
        #  """
        # )

        # set all to now where they are null
        now = int(time())
        await db.execute(
            f"""
            UPDATE wallets SET created_at = {db.timestamp_placeholder('now')}
            WHERE created_at IS NULL
            """,
            {"now": now},
        )
        await db.execute(
            f"""
            UPDATE accounts SET created_at = {db.timestamp_placeholder('now')}
            WHERE created_at IS NULL
            """,
            {"now": now},
        )

    except OperationalError as exc:
        logger.error(f"Migration 17 failed: {exc}")
        pass


async def m018_balances_view_exclude_deleted(db: Connection):
    """
    Make deleted wallets not show up in the balances view.
    """
    await db.execute("DROP VIEW balances")
    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT apipayments.wallet,
               SUM(apipayments.amount - ABS(apipayments.fee)) AS balance
        FROM apipayments
        LEFT JOIN wallets ON apipayments.wallet = wallets.id
        WHERE (wallets.deleted = false OR wallets.deleted is NULL)
              AND ((apipayments.pending = false AND apipayments.amount > 0)
              OR apipayments.amount < 0)
        GROUP BY wallet
    """
    )


async def m019_balances_view_based_on_wallets(db: Connection):
    """
    Make deleted wallets not show up in the balances view.
    Important for querying whole lnbits balances.
    """
    await db.execute("DROP VIEW balances")
    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT apipayments.wallet,
               SUM(apipayments.amount - ABS(apipayments.fee)) AS balance
        FROM wallets
        LEFT JOIN apipayments ON apipayments.wallet = wallets.id
        WHERE (wallets.deleted = false OR wallets.deleted is NULL)
              AND ((apipayments.pending = false AND apipayments.amount > 0)
              OR apipayments.amount < 0)
        GROUP BY apipayments.wallet
    """
    )


async def m020_add_column_column_to_user_extensions(db: Connection):
    """
    Adds extra column to user extensions.
    """
    await db.execute("ALTER TABLE extensions ADD COLUMN extra TEXT")


async def m021_add_success_failed_to_apipayments(db: Connection):
    """
    Adds success and failed columns to apipayments.
    """
    await db.execute("ALTER TABLE apipayments ADD COLUMN status TEXT DEFAULT 'pending'")
    #  set all not pending to success true, failed payments were deleted until now
    await db.execute("UPDATE apipayments SET status = 'success' WHERE NOT pending")

    await db.execute("DROP VIEW balances")
    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT apipayments.wallet,
               SUM(apipayments.amount - ABS(apipayments.fee)) AS balance
        FROM wallets
        LEFT JOIN apipayments ON apipayments.wallet = wallets.id
        WHERE (wallets.deleted = false OR wallets.deleted is NULL)
        AND (
            (apipayments.status = 'success' AND apipayments.amount > 0)
            OR (apipayments.status IN ('success', 'pending') AND apipayments.amount < 0)
        )
        GROUP BY apipayments.wallet
    """
    )


async def m022_add_pubkey_to_accounts(db: Connection):
    """
    Adds pubkey column to accounts.
    """
    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN pubkey TEXT")
    except OperationalError:
        pass


async def m023_add_column_column_to_apipayments(db: Connection):
    """
    renames hash to payment_hash and drops unused index
    """
    await db.execute("DROP INDEX by_hash")
    await db.execute("ALTER TABLE apipayments RENAME COLUMN hash TO payment_hash")
    await db.execute("ALTER TABLE apipayments RENAME COLUMN wallet TO wallet_id")
    await db.execute("ALTER TABLE accounts RENAME COLUMN pass TO password_hash")

    await db.execute("CREATE INDEX by_hash ON apipayments (payment_hash)")


async def m024_drop_pending(db: Connection):
    await db.execute("ALTER TABLE apipayments DROP COLUMN pending")


async def m025_refresh_view(db: Connection):
    await db.execute("DROP VIEW balances")
    await db.execute(
        """
        CREATE VIEW balances AS
        SELECT apipayments.wallet_id,
               SUM(apipayments.amount - ABS(apipayments.fee)) AS balance
        FROM wallets
        LEFT JOIN apipayments ON apipayments.wallet_id = wallets.id
        WHERE (wallets.deleted = false OR wallets.deleted is NULL)
        AND (
            (apipayments.status = 'success' AND apipayments.amount > 0)
            OR (apipayments.status IN ('success', 'pending') AND apipayments.amount < 0)
        )
        GROUP BY apipayments.wallet_id
    """
    )


async def m026_update_payment_table(db: Connection):
    await db.execute("ALTER TABLE apipayments ADD COLUMN tag TEXT")
    await db.execute("ALTER TABLE apipayments ADD COLUMN extension TEXT")
    await db.execute("ALTER TABLE apipayments ADD COLUMN created_at TIMESTAMP")
    await db.execute("ALTER TABLE apipayments ADD COLUMN updated_at TIMESTAMP")


async def m027_update_apipayments_data(db: Connection):
    result = None
    try:
        result = await db.execute("SELECT * FROM apipayments")
    except Exception as exc:
        logger.warning("Could not select, trying again after cache cleared.")
        logger.debug(exc)
        await db.execute("COMMIT")

    result = await db.execute("SELECT * FROM apipayments")

    payments = result.mappings().all()
    for payment in payments:
        tag = None
        created_at = payment.get("time")
        if payment.get("extra"):
            extra = json.loads(payment.get("extra"))
            tag = extra.get("tag")
        tsph = db.timestamp_placeholder("created_at")
        await db.execute(
            f"""
            UPDATE apipayments
            SET tag = :tag, created_at = {tsph}, updated_at = {tsph}
            WHERE checking_id = :checking_id
            """,
            {
                "tag": tag,
                "created_at": created_at,
                "checking_id": payment.get("checking_id"),
            },
        )


async def m028_update_settings(db: Connection):

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            id TEXT PRIMARY KEY,
            value TEXT,
            tag TEXT NOT NULL DEFAULT 'core',

            UNIQUE (id, tag)
        );
    """
    )

    async def _insert_key_value(id_: str, value: Any):
        await db.execute(
            """
            INSERT INTO system_settings (id, value, tag)
            VALUES (:id, :value, :tag)
            """,
            {"id": id_, "value": json.dumps(value), "tag": "core"},
        )

    row: dict = await db.fetchone("SELECT * FROM settings")
    if row:
        await _insert_key_value("super_user", row["super_user"])
        editable_settings = json.loads(row["editable_settings"])

        for key, value in editable_settings.items():
            await _insert_key_value(key, value)

    await db.execute("drop table settings")


async def m029_create_audit_table(db: Connection):
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS audit (
            component TEXT,
            ip_address TEXT,
            user_id TEXT,
            path TEXT,
            request_type TEXT,
            request_method TEXT,
            request_details TEXT,
            response_code TEXT,
            duration REAL NOT NULL,
            delete_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """
    )


async def m030_add_user_api_tokens_column(db: Connection):
    await db.execute(
        """
        ALTER TABLE accounts ADD COLUMN access_control_list TEXT
        """
    )


async def m031_add_color_and_icon_to_wallets(db: Connection):
    """
    Adds icon and color columns to wallets.
    """
    try:
        await db.execute("ALTER TABLE wallets ADD COLUMN icon TEXT DEFAULT 'flash_on'")
        await db.execute("ALTER TABLE wallets ADD COLUMN color TEXT DEFAULT 'primary'")
    except OperationalError:
        pass
