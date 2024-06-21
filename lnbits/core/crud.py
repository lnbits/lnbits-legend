import datetime
import json
from time import time
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

import shortuuid
from passlib.context import CryptContext

from lnbits.core.db import db
from lnbits.db import DB_TYPE, SQLITE, Connection, Database, Filters, Page
from lnbits.extension_manager import (
    InstallableExtension,
    PayToEnableInfo,
    UserExtension,
    UserExtensionInfo,
)
from lnbits.settings import (
    AdminSettings,
    EditableSettings,
    SuperSettings,
    WebPushSettings,
    settings,
)

from .models import (
    Account,
    AccountFilters,
    CreateUser,
    Payment,
    PaymentFilters,
    PaymentHistoryPoint,
    TinyURL,
    UpdateUserPassword,
    User,
    UserConfig,
    Wallet,
    WalletConfig,
    WebPushSubscription,
)

# accounts
# --------


async def create_user(
    data: CreateUser, user_config: Optional[UserConfig] = None
) -> User:
    if not settings.new_accounts_allowed:
        raise ValueError("Account creation is disabled.")
    if await get_account_by_username(data.username):
        raise ValueError("Username already exists.")

    if data.email and await get_account_by_email(data.email):
        raise ValueError("Email already exists.")

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    user_id = uuid4().hex
    tsph = db.timestamp_placeholder
    now = int(time())
    await db.execute(
        f"""
            INSERT INTO accounts
            (id, email, username, pass, extra, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, {tsph}, {tsph})
        """,
        (
            user_id,
            data.email,
            data.username,
            pwd_context.hash(data.password),
            json.dumps(dict(user_config)) if user_config else "{}",
            now,
            now,
        ),
    )
    new_account = await get_account(user_id=user_id)
    assert new_account, "Newly created account couldn't be retrieved"
    return new_account


async def create_account(
    conn: Optional[Connection] = None,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    user_config: Optional[UserConfig] = None,
) -> User:
    if user_id:
        user_uuid4 = UUID(hex=user_id, version=4)
        assert user_uuid4.hex == user_id, "User ID is not valid UUID4 hex string"
    else:
        user_id = uuid4().hex

    extra = json.dumps(dict(user_config)) if user_config else "{}"
    now = int(time())
    await (conn or db).execute(
        f"""
        INSERT INTO accounts (id, email, extra, created_at, updated_at)
        VALUES (?, ?, ?, {db.timestamp_placeholder}, {db.timestamp_placeholder})
        """,
        (user_id, email, extra, now, now),
    )

    new_account = await get_account(user_id=user_id, conn=conn)
    assert new_account, "Newly created account couldn't be retrieved"

    return new_account


async def update_account(
    user_id: str,
    username: Optional[str] = None,
    email: Optional[str] = None,
    user_config: Optional[UserConfig] = None,
) -> Optional[User]:
    user = await get_account(user_id)
    assert user, "User not found"

    if email:
        assert not user.email or email == user.email, "Cannot change email."
        account = await get_account_by_email(email)
        assert not account or account.id == user_id, "Email already in use."

    if username:
        assert not user.username or username == user.username, "Cannot change username."
        account = await get_account_by_username(username)
        assert not account or account.id == user_id, "Username already in exists."

    username = user.username or username
    email = user.email or email
    extra = user_config or user.config

    now = int(time())
    await db.execute(
        f"""
            UPDATE accounts SET (username, email, extra, updated_at) =
            (?, ?, ?, {db.timestamp_placeholder})
            WHERE id = ?
        """,
        (
            username,
            email,
            json.dumps(dict(extra)) if extra else "{}",
            now,
            user_id,
        ),
    )

    user = await get_user(user_id)
    assert user, "Updated account couldn't be retrieved"
    return user


async def delete_account(user_id: str, conn: Optional[Connection] = None) -> None:
    await (conn or db).execute(
        "DELETE from accounts WHERE id = ?",
        (user_id,),
    )


async def get_accounts(
    filters: Optional[Filters[AccountFilters]] = None,
    conn: Optional[Connection] = None,
) -> Page[Account]:
    return await (conn or db).fetch_page(
        """
        SELECT
            accounts.id,
            accounts.username,
            accounts.email,
            SUM(COALESCE((
                SELECT balance FROM balances WHERE wallet = wallets.id
            ), 0)) as balance_msat,
            SUM((
                SELECT COUNT(*) FROM apipayments WHERE wallet = wallets.id
            )) as transaction_count,
            (
                SELECT COUNT(*) FROM wallets WHERE wallets.user = accounts.id
            ) as wallet_count,
            MAX((
                SELECT time FROM apipayments
                WHERE wallet = wallets.id ORDER BY time DESC LIMIT 1
            )) as last_payment
            FROM accounts LEFT JOIN wallets ON accounts.id = wallets.user
        """,
        [],
        [],
        filters=filters,
        model=Account,
        group_by=["accounts.id"],
    )


async def get_account(
    user_id: str, conn: Optional[Connection] = None
) -> Optional[User]:
    row = await (conn or db).fetchone(
        """
           SELECT id, email, username, created_at, updated_at, extra
           FROM accounts WHERE id = ?
        """,
        (user_id,),
    )

    user = User(**row) if row else None
    if user and row["extra"]:
        user.config = UserConfig(**json.loads(row["extra"]))
    return user


async def delete_accounts_no_wallets(
    time_delta: int,
    conn: Optional[Connection] = None,
) -> None:
    delta = int(time()) - time_delta
    await (conn or db).execute(
        f"""
        DELETE FROM accounts
        WHERE NOT EXISTS (
            SELECT wallets.id FROM wallets WHERE wallets.user = accounts.id
        ) AND (
            (updated_at is null AND created_at < {db.timestamp_placeholder})
            OR updated_at < {db.timestamp_placeholder}
        )
        """,
        (
            delta,
            delta,
        ),
    )


async def get_user_password(user_id: str) -> Optional[str]:
    row = await db.fetchone(
        "SELECT pass FROM accounts WHERE id = ?",
        (user_id,),
    )
    if not row:
        return None

    return row[0]


async def verify_user_password(user_id: str, password: str) -> bool:
    existing_password = await get_user_password(user_id)
    if not existing_password:
        return False

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return pwd_context.verify(password, existing_password)


# todo: , conn: Optional[Connection] = None ??
async def update_user_password(data: UpdateUserPassword) -> Optional[User]:
    assert data.password == data.password_repeat, "Passwords do not match."

    # old accounts do not have a pasword
    if await get_user_password(data.user_id):
        assert data.password_old, "Missing old password"
        old_pwd_ok = await verify_user_password(data.user_id, data.password_old)
        assert old_pwd_ok, "Invalid credentials."

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    now = int(time())
    await db.execute(
        f"""
        UPDATE accounts SET pass = ?, updated_at = {db.timestamp_placeholder}
        WHERE id = ?
        """,
        (
            pwd_context.hash(data.password),
            now,
            data.user_id,
        ),
    )

    user = await get_user(data.user_id)
    assert user, "Updated account couldn't be retrieved"
    return user


async def get_account_by_username(
    username: str, conn: Optional[Connection] = None
) -> Optional[User]:
    row = await (conn or db).fetchone(
        """
        SELECT id, username, email, created_at, updated_at
        FROM accounts WHERE username = ?
        """,
        (username,),
    )

    return User(**row) if row else None


async def get_account_by_email(
    email: str, conn: Optional[Connection] = None
) -> Optional[User]:
    row = await (conn or db).fetchone(
        """
        SELECT id, username, email, created_at, updated_at
        FROM accounts WHERE email = ?
        """,
        (email,),
    )

    return User(**row) if row else None


async def get_account_by_username_or_email(
    username_or_email: str, conn: Optional[Connection] = None
) -> Optional[User]:
    user = await get_account_by_username(username_or_email, conn)
    if not user:
        user = await get_account_by_email(username_or_email, conn)
    return user


async def get_user(user_id: str, conn: Optional[Connection] = None) -> Optional[User]:
    user = await (conn or db).fetchone(
        """
        SELECT id, email, username, pass, extra, created_at, updated_at
        FROM accounts WHERE id = ?
        """,
        (user_id,),
    )

    if user:
        extensions = await get_user_active_extensions_ids(user_id, conn)
        wallets = await (conn or db).fetchall(
            """
            SELECT *, COALESCE((
                SELECT balance FROM balances WHERE wallet = wallets.id
            ), 0) AS balance_msat
            FROM wallets
            WHERE "user" = ? and wallets.deleted = false
            """,
            (user_id,),
        )
    else:
        return None

    return User(
        id=user["id"],
        email=user["email"],
        username=user["username"],
        extensions=[
            e for e in extensions if User.is_extension_for_user(e[0], user["id"])
        ],
        wallets=[Wallet.from_row(w) for w in wallets],
        admin=user["id"] == settings.super_user
        or user["id"] in settings.lnbits_admin_users,
        super_user=user["id"] == settings.super_user,
        has_password=True if user["pass"] else False,
        config=UserConfig(**json.loads(user["extra"])) if user["extra"] else None,
    )


# extensions
# -------


async def add_installed_extension(
    ext: InstallableExtension,
    conn: Optional[Connection] = None,
) -> None:
    meta = {
        "installed_release": (
            dict(ext.installed_release) if ext.installed_release else None
        ),
        "pay_to_enable": (dict(ext.pay_to_enable) if ext.pay_to_enable else None),
        "dependencies": ext.dependencies,
        "payments": [dict(p) for p in ext.payments] if ext.payments else None,
    }

    version = ext.installed_release.version if ext.installed_release else ""

    await (conn or db).execute(
        """
        INSERT INTO installed_extensions
        (id, version, name, active, short_description, icon, stars, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET
        (version, name, active, short_description, icon, stars, meta) =
        (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ext.id,
            version,
            ext.name,
            ext.active,
            ext.short_description,
            ext.icon,
            ext.stars,
            json.dumps(meta),
            version,
            ext.name,
            ext.active,
            ext.short_description,
            ext.icon,
            ext.stars,
            json.dumps(meta),
        ),
    )


async def update_installed_extension_state(
    *, ext_id: str, active: bool, conn: Optional[Connection] = None
) -> None:
    await (conn or db).execute(
        """
        UPDATE installed_extensions SET active = ? WHERE id = ?
        """,
        (active, ext_id),
    )


async def update_extension_pay_to_enable(
    ext_id: str, payment_info: PayToEnableInfo, conn: Optional[Connection] = None
) -> None:
    ext = await get_installed_extension(ext_id, conn)
    if not ext:
        return
    ext.pay_to_enable = payment_info

    await add_installed_extension(ext, conn)


async def delete_installed_extension(
    *, ext_id: str, conn: Optional[Connection] = None
) -> None:
    await (conn or db).execute(
        """
        DELETE from installed_extensions  WHERE id = ?
        """,
        (ext_id,),
    )


async def drop_extension_db(*, ext_id: str, conn: Optional[Connection] = None) -> None:
    db_version = await (conn or db).fetchone(
        "SELECT * FROM dbversions WHERE db = ?", (ext_id,)
    )
    # Check that 'ext_id' is a valid extension id and not a malicious string
    assert db_version, f"Extension '{ext_id}' db version cannot be found"

    is_file_based_db = await Database.clean_ext_db_files(ext_id)
    if is_file_based_db:
        return

    # String formatting is required, params are not accepted for 'DROP SCHEMA'.
    # The `ext_id` value is verified above.
    await (conn or db).execute(
        f"DROP SCHEMA IF EXISTS {ext_id} CASCADE",
        (),
    )


async def get_installed_extension(
    ext_id: str, conn: Optional[Connection] = None
) -> Optional[InstallableExtension]:
    row = await (conn or db).fetchone(
        "SELECT * FROM installed_extensions WHERE id = ?",
        (ext_id,),
    )

    return InstallableExtension.from_row(row) if row else None


async def get_installed_extensions(
    active: Optional[bool] = None,
    conn: Optional[Connection] = None,
) -> List["InstallableExtension"]:
    rows = await (conn or db).fetchall(
        "SELECT * FROM installed_extensions",
        (),
    )
    all_extensions = [InstallableExtension.from_row(row) for row in rows]
    if active is None:
        return all_extensions

    return [e for e in all_extensions if e.active == active]


async def get_user_extension(
    user_id: str, extension: str, conn: Optional[Connection] = None
) -> Optional[UserExtension]:
    row = await (conn or db).fetchone(
        """
            SELECT extension, active, extra as _extra FROM extensions
            WHERE "user" = ? AND extension = ?
        """,
        (user_id, extension),
    )
    return UserExtension.from_row(row) if row else None


async def get_user_extensions(
    user_id: str, conn: Optional[Connection] = None
) -> List[UserExtension]:
    rows = await (conn or db).fetchall(
        """
            SELECT extension, active, extra as _extra FROM extensions
            WHERE "user" = ?
        """,
        (user_id,),
    )
    return [UserExtension.from_row(row) for row in rows]


async def update_user_extension(
    *, user_id: str, extension: str, active: bool, conn: Optional[Connection] = None
) -> None:
    await (conn or db).execute(
        """
        INSERT INTO extensions ("user", extension, active) VALUES (?, ?, ?)
        ON CONFLICT ("user", extension) DO UPDATE SET active = ?
        """,
        (user_id, extension, active, active),
    )


async def get_user_active_extensions_ids(
    user_id: str, conn: Optional[Connection] = None
) -> List[str]:
    rows = await (conn or db).fetchall(
        """SELECT extension FROM extensions WHERE "user" = ? AND active""",
        (user_id,),
    )
    return [e[0] for e in rows]


async def update_user_extension_extra(
    user_id: str,
    extension: str,
    extra: UserExtensionInfo,
    conn: Optional[Connection] = None,
) -> None:
    extra_json = json.dumps(dict(extra))
    await (conn or db).execute(
        """
        INSERT INTO extensions ("user", extension, extra) VALUES (?, ?, ?)
        ON CONFLICT ("user", extension) DO UPDATE SET extra = ?
        """,
        (user_id, extension, extra_json, extra_json),
    )


# wallets
# -------


async def create_wallet(
    *,
    user_id: str,
    wallet_name: Optional[str] = None,
    conn: Optional[Connection] = None,
) -> Wallet:
    wallet_id = uuid4().hex
    now = int(time())
    await (conn or db).execute(
        f"""
        INSERT INTO wallets (id, name, "user", adminkey, inkey, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, {db.timestamp_placeholder}, {db.timestamp_placeholder})
        """,
        (
            wallet_id,
            wallet_name or settings.lnbits_default_wallet_name,
            user_id,
            uuid4().hex,
            uuid4().hex,
            now,
            now,
        ),
    )

    new_wallet = await get_wallet(wallet_id=wallet_id, conn=conn)
    assert new_wallet, "Newly created wallet couldn't be retrieved"

    return new_wallet


async def update_wallet(
    wallet_id: str,
    name: Optional[str] = None,
    currency: Optional[str] = None,
    config: Optional[WalletConfig] = None,
    conn: Optional[Connection] = None,
) -> Optional[Wallet]:
    set_clause = []
    values: list = []
    set_clause.append(f"updated_at = {db.timestamp_placeholder}")
    now = int(time())
    values.append(now)
    if name:
        set_clause.append("name = ?")
        values.append(name)
    if currency is not None:
        set_clause.append("currency = ?")
        values.append(currency)
    if config is not None:
        set_clause.append("extra = ?")
        values.append(json.dumps(dict(config)))
        set_clause.append("reverse_funding_enabled = ?")
        values.append(config.reverse_funding_access in ["inkey", "adminkey"])
    values.append(wallet_id)
    await (conn or db).execute(
        f"""
        UPDATE wallets SET {', '.join(set_clause)} WHERE id = ?
        """,
        tuple(values),
    )
    wallet = await get_wallet(wallet_id=wallet_id, conn=conn)
    assert wallet, "updated created wallet couldn't be retrieved"
    return wallet


async def delete_wallet(
    *,
    user_id: str,
    wallet_id: str,
    deleted: bool = True,
    conn: Optional[Connection] = None,
) -> None:
    now = int(time())
    await (conn or db).execute(
        f"""
        UPDATE wallets
        SET deleted = ?, updated_at = {db.timestamp_placeholder}
        WHERE id = ? AND "user" = ?
        """,
        (deleted, now, wallet_id, user_id),
    )


async def force_delete_wallet(
    wallet_id: str, conn: Optional[Connection] = None
) -> None:
    await (conn or db).execute(
        "DELETE FROM wallets WHERE id = ?",
        (wallet_id,),
    )


async def delete_wallet_by_id(
    *, wallet_id: str, conn: Optional[Connection] = None
) -> Optional[int]:
    now = int(time())
    result = await (conn or db).execute(
        f"""
        UPDATE wallets
        SET deleted = true, updated_at = {db.timestamp_placeholder}
        WHERE id = ?
        """,
        (now, wallet_id),
    )
    return result.rowcount


async def remove_deleted_wallets(conn: Optional[Connection] = None) -> None:
    await (conn or db).execute("DELETE FROM wallets WHERE deleted = true")


async def delete_unused_wallets(
    time_delta: int,
    conn: Optional[Connection] = None,
) -> None:
    delta = int(time()) - time_delta
    await (conn or db).execute(
        f"""
        DELETE FROM wallets
        WHERE (
            SELECT COUNT(*) FROM apipayments WHERE wallet = wallets.id
        ) = 0 AND (
            (updated_at is null AND created_at < {db.timestamp_placeholder})
            OR updated_at < {db.timestamp_placeholder}
        )
        """,
        (
            delta,
            delta,
        ),
    )


async def get_wallet(
    wallet_id: str, conn: Optional[Connection] = None
) -> Optional[Wallet]:
    row = await (conn or db).fetchone(
        """
        SELECT *, COALESCE((SELECT balance FROM balances WHERE wallet = wallets.id), 0)
        AS balance_msat FROM wallets WHERE id = ?
        """,
        (wallet_id,),
    )

    return Wallet.from_row(row) if row else None


async def get_wallets(user_id: str, conn: Optional[Connection] = None) -> List[Wallet]:
    rows = await (conn or db).fetchall(
        """
        SELECT *, COALESCE((SELECT balance FROM balances WHERE wallet = wallets.id), 0)
        AS balance_msat FROM wallets WHERE "user" = ?
        """,
        (user_id,),
    )

    return [Wallet.from_row(row) for row in rows]


async def get_wallet_for_key(
    key: str,
    conn: Optional[Connection] = None,
) -> Optional[Wallet]:
    row = await (conn or db).fetchone(
        """
        SELECT *, COALESCE((SELECT balance FROM balances WHERE wallet = wallets.id), 0)
        AS balance_msat FROM wallets
        WHERE (adminkey = ? OR inkey = ?) AND deleted = false
        """,
        (key, key),
    )

    if not row:
        return None

    return Wallet(**row)


async def get_total_balance(conn: Optional[Connection] = None):
    row = await (conn or db).fetchone("SELECT SUM(balance) FROM balances")
    return 0 if row[0] is None else row[0]


# wallet payments
# ---------------


async def get_standalone_payment(
    checking_id_or_hash: str,
    conn: Optional[Connection] = None,
    incoming: Optional[bool] = False,
    wallet_id: Optional[str] = None,
) -> Optional[Payment]:
    clause: str = "checking_id = ? OR hash = ?"
    values = [checking_id_or_hash, checking_id_or_hash]
    if incoming:
        clause = f"({clause}) AND amount > 0"

    if wallet_id:
        clause = f"({clause}) AND wallet = ?"
        values.append(wallet_id)

    row = await (conn or db).fetchone(
        f"""
        SELECT *
        FROM apipayments
        WHERE {clause}
        ORDER BY amount
        LIMIT 1
        """,
        tuple(values),
    )

    return Payment.from_row(row) if row else None


async def get_wallet_payment(
    wallet_id: str, payment_hash: str, conn: Optional[Connection] = None
) -> Optional[Payment]:
    row = await (conn or db).fetchone(
        """
        SELECT *
        FROM apipayments
        WHERE wallet = ? AND hash = ?
        """,
        (wallet_id, payment_hash),
    )

    return Payment.from_row(row) if row else None


async def get_latest_payments_by_extension(ext_name: str, ext_id: str, limit: int = 5):
    rows = await db.fetchall(
        f"""
        SELECT * FROM apipayments
        WHERE pending = false
        AND extra LIKE ?
        AND extra LIKE ?
        ORDER BY time DESC LIMIT {limit}
        """,
        (
            f"%{ext_name}%",
            f"%{ext_id}%",
        ),
    )

    return rows


async def get_payments_paginated(
    *,
    wallet_id: Optional[str] = None,
    complete: bool = False,
    pending: bool = False,
    outgoing: bool = False,
    incoming: bool = False,
    since: Optional[int] = None,
    exclude_uncheckable: bool = False,
    filters: Optional[Filters[PaymentFilters]] = None,
    conn: Optional[Connection] = None,
) -> Page[Payment]:
    """
    Filters payments to be returned by complete | pending | outgoing | incoming.
    """

    values: List[Any] = []
    clause: List[str] = []

    if since is not None:
        clause.append(f"time > {db.timestamp_placeholder}")
        values.append(since)

    if wallet_id:
        clause.append("wallet = ?")
        values.append(wallet_id)

    if complete and pending:
        pass
    elif complete:
        clause.append("((amount > 0 AND pending = false) OR amount < 0)")
    elif pending:
        clause.append("pending = true")
    else:
        pass

    if outgoing and incoming:
        pass
    elif outgoing:
        clause.append("amount < 0")
    elif incoming:
        clause.append("amount > 0")
    else:
        pass

    if exclude_uncheckable:  # checkable means it has a checking_id that isn't internal
        clause.append("checking_id NOT LIKE 'temp_%'")
        clause.append("checking_id NOT LIKE 'internal_%'")

    return await (conn or db).fetch_page(
        "SELECT * FROM apipayments",
        clause,
        values,
        filters=filters,
        model=Payment,
    )


async def get_payments(
    *,
    wallet_id: Optional[str] = None,
    complete: bool = False,
    pending: bool = False,
    outgoing: bool = False,
    incoming: bool = False,
    since: Optional[int] = None,
    exclude_uncheckable: bool = False,
    filters: Optional[Filters[PaymentFilters]] = None,
    conn: Optional[Connection] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> list[Payment]:
    """
    Filters payments to be returned by complete | pending | outgoing | incoming.
    """

    filters = filters or Filters()

    filters.sortby = filters.sortby or "time"
    filters.direction = filters.direction or "desc"
    filters.limit = limit or filters.limit
    filters.offset = offset or filters.offset

    page = await get_payments_paginated(
        wallet_id=wallet_id,
        complete=complete,
        pending=pending,
        outgoing=outgoing,
        incoming=incoming,
        since=since,
        exclude_uncheckable=exclude_uncheckable,
        filters=filters,
        conn=conn,
    )

    return page.data


async def delete_expired_invoices(
    conn: Optional[Connection] = None,
) -> None:
    # first we delete all invoices older than one month
    await (conn or db).execute(
        f"""
        DELETE FROM apipayments
        WHERE pending = true AND amount > 0
          AND time < {db.timestamp_now} - {db.interval_seconds(2592000)}
        """
    )
    # then we delete all invoices whose expiry date is in the past
    await (conn or db).execute(
        f"""
        DELETE FROM apipayments
        WHERE pending = true AND amount > 0
          AND expiry < {db.timestamp_now}
        """
    )


# payments
# --------


async def create_payment(
    *,
    wallet_id: str,
    checking_id: str,
    payment_request: str,
    payment_hash: str,
    amount: int,
    memo: str,
    fee: int = 0,
    preimage: Optional[str] = None,
    expiry: Optional[datetime.datetime] = None,
    pending: bool = True,
    extra: Optional[Dict] = None,
    webhook: Optional[str] = None,
    conn: Optional[Connection] = None,
) -> Payment:
    # we don't allow the creation of the same invoice twice
    # note: this can be removed if the db uniquess constarints are set appropriately
    previous_payment = await get_standalone_payment(checking_id, conn=conn)
    assert previous_payment is None, "Payment already exists"

    await (conn or db).execute(
        """
        INSERT INTO apipayments
          (wallet, checking_id, bolt11, hash, preimage,
           amount, pending, memo, fee, extra, webhook, expiry)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            wallet_id,
            checking_id,
            payment_request,
            payment_hash,
            preimage,
            amount,
            pending,
            memo,
            fee,
            (
                json.dumps(extra)
                if extra and extra != {} and isinstance(extra, dict)
                else None
            ),
            webhook,
            db.datetime_to_timestamp(expiry) if expiry else None,
        ),
    )

    new_payment = await get_wallet_payment(wallet_id, payment_hash, conn=conn)
    assert new_payment, "Newly created payment couldn't be retrieved"

    return new_payment


async def update_payment_status(
    checking_id: str, pending: bool, conn: Optional[Connection] = None
) -> None:
    await (conn or db).execute(
        "UPDATE apipayments SET pending = ? WHERE checking_id = ?",
        (pending, checking_id),
    )


async def update_payment_details(
    checking_id: str,
    pending: Optional[bool] = None,
    fee: Optional[int] = None,
    preimage: Optional[str] = None,
    new_checking_id: Optional[str] = None,
    conn: Optional[Connection] = None,
) -> None:
    set_clause: List[str] = []
    set_variables: List[Any] = []

    if new_checking_id is not None:
        set_clause.append("checking_id = ?")
        set_variables.append(new_checking_id)
    if pending is not None:
        set_clause.append("pending = ?")
        set_variables.append(pending)
    if fee is not None:
        set_clause.append("fee = ?")
        set_variables.append(fee)
    if preimage is not None:
        set_clause.append("preimage = ?")
        set_variables.append(preimage)

    set_variables.append(checking_id)

    await (conn or db).execute(
        f"UPDATE apipayments SET {', '.join(set_clause)} WHERE checking_id = ?",
        tuple(set_variables),
    )
    return


async def update_payment_extra(
    payment_hash: str,
    extra: dict,
    outgoing: bool = False,
    conn: Optional[Connection] = None,
) -> None:
    """
    Only update the `extra` field for the payment.
    Old values in the `extra` JSON object will be kept
    unless the new `extra` overwrites them.
    """

    amount_clause = "AND amount < 0" if outgoing else "AND amount > 0"

    row = await (conn or db).fetchone(
        f"SELECT hash, extra from apipayments WHERE hash = ? {amount_clause}",
        (payment_hash,),
    )
    if not row:
        return
    db_extra = json.loads(row["extra"] if row["extra"] else "{}")
    db_extra.update(extra)

    await (conn or db).execute(
        f"UPDATE apipayments SET extra = ? WHERE hash = ? {amount_clause} ",
        (json.dumps(db_extra), payment_hash),
    )


async def update_pending_payments(wallet_id: str):
    pending_payments = await get_payments(
        wallet_id=wallet_id,
        pending=True,
        exclude_uncheckable=True,
    )
    for payment in pending_payments:
        await payment.check_status()


DateTrunc = Literal["hour", "day", "month"]
sqlite_formats = {
    "hour": "%Y-%m-%d %H:00:00",
    "day": "%Y-%m-%d 00:00:00",
    "month": "%Y-%m-01 00:00:00",
}


async def get_payments_history(
    wallet_id: Optional[str] = None,
    group: DateTrunc = "day",
    filters: Optional[Filters] = None,
) -> List[PaymentHistoryPoint]:
    if not filters:
        filters = Filters()
    where = ["(pending = False OR amount < 0)"]
    values = []
    if wallet_id:
        where.append("wallet = ?")
        values.append(wallet_id)

    if DB_TYPE == SQLITE and group in sqlite_formats:
        date_trunc = f"strftime('{sqlite_formats[group]}', time, 'unixepoch')"
    elif group in ("day", "hour", "month"):
        date_trunc = f"date_trunc('{group}', time)"
    else:
        raise ValueError(f"Invalid group value: {group}")

    transactions = await db.fetchall(
        f"""
        SELECT {date_trunc} date,
               SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) income,
               SUM(CASE WHEN amount < 0 THEN abs(amount) + abs(fee) ELSE 0 END) spending
        FROM apipayments
        {filters.where(where)}
        GROUP BY date
        ORDER BY date DESC
        """,
        filters.values(values),
    )
    if wallet_id:
        wallet = await get_wallet(wallet_id)
        if wallet:
            balance = wallet.balance_msat
        else:
            raise ValueError("Unknown wallet")
    else:
        balance = await get_total_balance()

    # since we dont know the balance at the starting point,
    # we take the current balance and walk backwards
    results: list[PaymentHistoryPoint] = []
    for row in transactions:
        results.insert(
            0,
            PaymentHistoryPoint(
                balance=balance, date=row[0], income=row[1], spending=row[2]
            ),
        )
        balance -= row.income - row.spending
    return results


async def delete_wallet_payment(
    checking_id: str, wallet_id: str, conn: Optional[Connection] = None
) -> None:
    await (conn or db).execute(
        "DELETE FROM apipayments WHERE checking_id = ? AND wallet = ?",
        (checking_id, wallet_id),
    )


async def check_internal(
    payment_hash: str, conn: Optional[Connection] = None
) -> Optional[str]:
    """
    Returns the checking_id of the internal payment if it exists,
    otherwise None
    """
    row = await (conn or db).fetchone(
        """
        SELECT checking_id FROM apipayments
        WHERE hash = ? AND pending AND amount > 0
        """,
        (payment_hash,),
    )
    if not row:
        return None
    else:
        return row["checking_id"]


async def check_internal_pending(
    payment_hash: str, conn: Optional[Connection] = None
) -> bool:
    """
    Returns False if the internal payment is not pending anymore
    (and thus paid), otherwise True
    """
    row = await (conn or db).fetchone(
        """
        SELECT pending FROM apipayments
        WHERE hash = ? AND amount > 0
        """,
        (payment_hash,),
    )
    if not row:
        return True
    else:
        return row["pending"]


async def mark_webhook_sent(payment_hash: str, status: int) -> None:
    await db.execute(
        """
        UPDATE apipayments SET webhook_status = ?
        WHERE hash = ?
        """,
        (status, payment_hash),
    )


# admin
# --------


async def get_super_settings() -> Optional[SuperSettings]:
    row = await db.fetchone("SELECT * FROM settings")
    if not row:
        return None
    editable_settings = json.loads(row["editable_settings"])
    return SuperSettings(**{"super_user": row["super_user"], **editable_settings})


async def get_admin_settings(is_super_user: bool = False) -> Optional[AdminSettings]:
    sets = await get_super_settings()
    if not sets:
        return None
    row_dict = dict(sets)
    row_dict.pop("super_user")
    row_dict.pop("auth_all_methods")

    admin_settings = AdminSettings(
        is_super_user=is_super_user,
        lnbits_allowed_funding_sources=settings.lnbits_allowed_funding_sources,
        **row_dict,
    )
    return admin_settings


async def delete_admin_settings() -> None:
    await db.execute("DELETE FROM settings")


async def update_admin_settings(data: EditableSettings) -> None:
    row = await db.fetchone("SELECT editable_settings FROM settings")
    editable_settings = json.loads(row["editable_settings"]) if row else {}
    editable_settings.update(data.dict(exclude_unset=True))
    await db.execute(
        "UPDATE settings SET editable_settings = ?", (json.dumps(editable_settings),)
    )


async def update_super_user(super_user: str) -> SuperSettings:
    await db.execute("UPDATE settings SET super_user = ?", (super_user,))
    settings = await get_super_settings()
    assert settings, "updated super_user settings could not be retrieved"
    return settings


async def create_admin_settings(super_user: str, new_settings: dict):
    sql = "INSERT INTO settings (super_user, editable_settings) VALUES (?, ?)"
    await db.execute(sql, (super_user, json.dumps(new_settings)))
    settings = await get_super_settings()
    assert settings, "created admin settings could not be retrieved"
    return settings


# db versions
# --------------
async def get_dbversions(conn: Optional[Connection] = None):
    rows = await (conn or db).fetchall("SELECT * FROM dbversions")
    return {row["db"]: row["version"] for row in rows}


async def update_migration_version(conn, db_name, version):
    await (conn or db).execute(
        """
        INSERT INTO dbversions (db, version) VALUES (?, ?)
        ON CONFLICT (db) DO UPDATE SET version = ?
        """,
        (db_name, version, version),
    )


async def delete_dbversion(*, ext_id: str, conn: Optional[Connection] = None) -> None:
    await (conn or db).execute(
        """
        DELETE FROM dbversions WHERE db = ?
        """,
        (ext_id,),
    )


# tinyurl
# -------


async def create_tinyurl(domain: str, endless: bool, wallet: str):
    tinyurl_id = shortuuid.uuid()[:8]
    await db.execute(
        "INSERT INTO tiny_url (id, url, endless, wallet) VALUES (?, ?, ?, ?)",
        (
            tinyurl_id,
            domain,
            endless,
            wallet,
        ),
    )
    return await get_tinyurl(tinyurl_id)


async def get_tinyurl(tinyurl_id: str) -> Optional[TinyURL]:
    row = await db.fetchone(
        "SELECT * FROM tiny_url WHERE id = ?",
        (tinyurl_id,),
    )
    return TinyURL.from_row(row) if row else None


async def get_tinyurl_by_url(url: str) -> List[TinyURL]:
    rows = await db.fetchall(
        "SELECT * FROM tiny_url WHERE url = ?",
        (url,),
    )
    return [TinyURL.from_row(row) for row in rows]


async def delete_tinyurl(tinyurl_id: str):
    await db.execute(
        "DELETE FROM tiny_url WHERE id = ?",
        (tinyurl_id,),
    )


# push_notification
# -----------------


async def get_webpush_settings() -> Optional[WebPushSettings]:
    row = await db.fetchone("SELECT * FROM webpush_settings")
    if not row:
        return None
    vapid_keypair = json.loads(row["vapid_keypair"])
    return WebPushSettings(**vapid_keypair)


async def create_webpush_settings(webpush_settings: dict):
    await db.execute(
        "INSERT INTO webpush_settings (vapid_keypair) VALUES (?)",
        (json.dumps(webpush_settings),),
    )
    return await get_webpush_settings()


async def get_webpush_subscription(
    endpoint: str, user: str
) -> Optional[WebPushSubscription]:
    row = await db.fetchone(
        """SELECT * FROM webpush_subscriptions WHERE endpoint = ? AND "user" = ?""",
        (
            endpoint,
            user,
        ),
    )
    return WebPushSubscription(**dict(row)) if row else None


async def get_webpush_subscriptions_for_user(
    user: str,
) -> List[WebPushSubscription]:
    rows = await db.fetchall(
        """SELECT * FROM webpush_subscriptions WHERE "user" = ?""",
        (user,),
    )
    return [WebPushSubscription(**dict(row)) for row in rows]


async def create_webpush_subscription(
    endpoint: str, user: str, data: str, host: str
) -> WebPushSubscription:
    await db.execute(
        """
        INSERT INTO webpush_subscriptions (endpoint, "user", data, host)
        VALUES (?, ?, ?, ?)
        """,
        (
            endpoint,
            user,
            data,
            host,
        ),
    )
    subscription = await get_webpush_subscription(endpoint, user)
    assert subscription, "Newly created webpush subscription couldn't be retrieved"
    return subscription


async def delete_webpush_subscription(endpoint: str, user: str) -> int:
    resp = await db.execute(
        """DELETE FROM webpush_subscriptions WHERE endpoint = ? AND "user" = ?""",
        (
            endpoint,
            user,
        ),
    )
    return resp.rowcount


async def delete_webpush_subscriptions(endpoint: str) -> int:
    resp = await db.execute(
        "DELETE FROM webpush_subscriptions WHERE endpoint = ?", (endpoint,)
    )
    return resp.rowcount
