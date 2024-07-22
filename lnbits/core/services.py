import datetime
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TypedDict
from uuid import UUID, uuid4

from bolt11 import decode as bolt11_decode
from cryptography.hazmat.primitives import serialization
from fastapi import WebSocket
from loguru import logger
from passlib.context import CryptContext
from py_vapid import Vapid
from py_vapid.utils import b64urlencode

from lnbits.core.db import db
from lnbits.db import Connection
from lnbits.decorators import (
    check_user_extension_access,
)
from lnbits.settings import (
    EditableSettings,
    SuperSettings,
    readonly_variables,
    send_admin_user_to_saas,
    settings,
)
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis, satoshis_amount_as_fiat
from lnbits.wallets import fake_wallet, get_funding_source, set_funding_source
from lnbits.wallets.base import (
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
)

from .crud import (
    check_internal,
    check_internal_pending,
    create_account,
    create_admin_settings,
    create_payment,
    create_wallet,
    delete_wallet_payment,
    get_account,
    get_account_by_email,
    get_account_by_username,
    get_payments,
    get_standalone_payment,
    get_super_settings,
    get_total_balance,
    get_wallet,
    get_wallet_payment,
    update_admin_settings,
    update_payment_details,
    update_payment_status,
    update_super_user,
    update_user_extension,
)
from .helpers import to_valid_user_id
from .models import BalanceDelta, Payment, User, UserConfig, Wallet


class PaymentError(Exception):
    def __init__(self, message: str, status: str = "pending"):
        self.message = message
        self.status = status


class InvoiceError(Exception):
    def __init__(self, message: str, status: str = "pending"):
        self.message = message
        self.status = status


async def calculate_fiat_amounts(
    amount: float,
    wallet_id: str,
    currency: Optional[str] = None,
    extra: Optional[Dict] = None,
    conn: Optional[Connection] = None,
) -> Tuple[int, Optional[Dict]]:
    wallet = await get_wallet(wallet_id, conn=conn)
    assert wallet, "invalid wallet_id"
    wallet_currency = wallet.currency or settings.lnbits_default_accounting_currency

    if currency and currency != "sat":
        amount_sat = await fiat_amount_as_satoshis(amount, currency)
        extra = extra or {}
        if currency != wallet_currency:
            extra["fiat_currency"] = currency
            extra["fiat_amount"] = round(amount, ndigits=3)
            extra["fiat_rate"] = amount_sat / amount
    else:
        amount_sat = int(amount)

    if wallet_currency:
        if wallet_currency == currency:
            fiat_amount = amount
        else:
            fiat_amount = await satoshis_amount_as_fiat(amount_sat, wallet_currency)
        extra = extra or {}
        extra["wallet_fiat_currency"] = wallet_currency
        extra["wallet_fiat_amount"] = round(fiat_amount, ndigits=3)
        extra["wallet_fiat_rate"] = amount_sat / fiat_amount

    logger.debug(
        f"Calculated fiat amounts {wallet.id=} {amount=} {currency=}: {extra=}"
    )

    return amount_sat, extra


async def create_invoice(
    *,
    wallet_id: str,
    amount: float,
    currency: Optional[str] = "sat",
    memo: str,
    description_hash: Optional[bytes] = None,
    unhashed_description: Optional[bytes] = None,
    expiry: Optional[int] = None,
    extra: Optional[Dict] = None,
    webhook: Optional[str] = None,
    internal: Optional[bool] = False,
    conn: Optional[Connection] = None,
) -> Tuple[str, str]:
    if not amount > 0:
        raise InvoiceError("Amountless invoices not supported.", status="failed")

    user_wallet = await get_wallet(wallet_id, conn=conn)
    if not user_wallet:
        raise InvoiceError(f"Could not fetch wallet '{wallet_id}'.", status="failed")

    invoice_memo = None if description_hash else memo

    # use the fake wallet if the invoice is for internal use only
    funding_source = fake_wallet if internal else get_funding_source()

    amount_sat, extra = await calculate_fiat_amounts(
        amount, wallet_id, currency=currency, extra=extra, conn=conn
    )

    if settings.is_wallet_max_balance_exceeded(
        user_wallet.balance_msat / 1000 + amount_sat
    ):
        raise InvoiceError(
            f"Wallet balance cannot exceed "
            f"{settings.lnbits_wallet_limit_max_balance} sats.",
            status="failed",
        )

    (
        ok,
        checking_id,
        payment_request,
        error_message,
    ) = await funding_source.create_invoice(
        amount=amount_sat,
        memo=invoice_memo,
        description_hash=description_hash,
        unhashed_description=unhashed_description,
        expiry=expiry or settings.lightning_invoice_expiry,
    )
    if not ok or not payment_request or not checking_id:
        raise InvoiceError(
            error_message or "unexpected backend error.", status="pending"
        )

    invoice = bolt11_decode(payment_request)

    amount_msat = 1000 * amount_sat
    await create_payment(
        wallet_id=wallet_id,
        checking_id=checking_id,
        payment_request=payment_request,
        payment_hash=invoice.payment_hash,
        amount=amount_msat,
        expiry=invoice.expiry_date,
        memo=memo,
        extra=extra,
        webhook=webhook,
        conn=conn,
    )

    return invoice.payment_hash, payment_request


async def pay_invoice(
    *,
    wallet_id: str,
    payment_request: str,
    max_sat: Optional[int] = None,
    extra: Optional[Dict] = None,
    description: str = "",
    conn: Optional[Connection] = None,
) -> str:
    """
    Pay a Lightning invoice.
    First, we create a temporary payment in the database with fees set to the reserve
    fee. We then check whether the balance of the payer would go negative.
    We then attempt to pay the invoice through the backend. If the payment is
    successful, we update the payment in the database with the payment details.
    If the payment is unsuccessful, we delete the temporary payment.
    If the payment is still in flight, we hope that some other process
    will regularly check for the payment.
    """
    try:
        invoice = bolt11_decode(payment_request)
    except Exception as exc:
        raise PaymentError("Bolt11 decoding failed.", status="failed") from exc

    if not invoice.amount_msat or not invoice.amount_msat > 0:
        raise PaymentError("Amountless invoices not supported.", status="failed")
    if max_sat and invoice.amount_msat > max_sat * 1000:
        raise PaymentError("Amount in invoice is too high.", status="failed")

    await check_wallet_limits(wallet_id, conn, invoice.amount_msat)

    async with db.reuse_conn(conn) if conn else db.connect() as conn:
        temp_id = invoice.payment_hash
        internal_id = f"internal_{invoice.payment_hash}"

        _, extra = await calculate_fiat_amounts(
            invoice.amount_msat / 1000, wallet_id, extra=extra, conn=conn
        )

        # put all parameters that don't change here
        class PaymentKwargs(TypedDict):
            wallet_id: str
            payment_request: str
            payment_hash: str
            amount: int
            memo: str
            expiry: Optional[datetime.datetime]
            extra: Optional[Dict]

        payment_kwargs: PaymentKwargs = PaymentKwargs(
            wallet_id=wallet_id,
            payment_request=payment_request,
            payment_hash=invoice.payment_hash,
            amount=-invoice.amount_msat,
            expiry=invoice.expiry_date,
            memo=description or invoice.description or "",
            extra=extra,
        )

        # we check if an internal invoice exists that has already been paid
        # (not pending anymore)
        if not await check_internal_pending(invoice.payment_hash, conn=conn):
            raise PaymentError("Internal invoice already paid.", status="failed")

        # check_internal() returns the checking_id of the invoice we're waiting for
        # (pending only)
        internal_checking_id = await check_internal(invoice.payment_hash, conn=conn)
        if internal_checking_id:
            fee_reserve_total_msat = fee_reserve_total(
                invoice.amount_msat, internal=True
            )
            # perform additional checks on the internal payment
            # the payment hash is not enough to make sure that this is the same invoice
            internal_invoice = await get_standalone_payment(
                internal_checking_id, incoming=True, conn=conn
            )
            assert internal_invoice is not None
            if (
                internal_invoice.amount != invoice.amount_msat
                or internal_invoice.bolt11 != payment_request.lower()
            ):
                raise PaymentError("Invalid invoice.", status="failed")

            logger.debug(f"creating temporary internal payment with id {internal_id}")
            # create a new payment from this wallet
            new_payment = await create_payment(
                checking_id=internal_id,
                fee=0 + abs(fee_reserve_total_msat),
                pending=False,
                conn=conn,
                **payment_kwargs,
            )
        else:
            fee_reserve_total_msat = fee_reserve_total(
                invoice.amount_msat, internal=False
            )
            logger.debug(f"creating temporary payment with id {temp_id}")
            # create a temporary payment here so we can check if
            # the balance is enough in the next step
            try:
                new_payment = await create_payment(
                    checking_id=temp_id,
                    fee=-abs(fee_reserve_total_msat),
                    conn=conn,
                    **payment_kwargs,
                )
            except Exception as exc:
                logger.error(f"could not create temporary payment: {exc}")
                # happens if the same wallet tries to pay an invoice twice
                raise PaymentError("Could not make payment.", status="failed") from exc

        # do the balance check
        wallet = await get_wallet(wallet_id, conn=conn)
        assert wallet, "Wallet for balancecheck could not be fetched"
        _check_wallet_balance(wallet, fee_reserve_total_msat, internal_checking_id)

    if extra and "tag" in extra:
        # check if the payment is made for an extension that the user disabled
        status = await check_user_extension_access(wallet.user, extra["tag"])
        if not status.success:
            raise PaymentError(status.message)

    if internal_checking_id:
        service_fee_msat = service_fee(invoice.amount_msat, internal=True)
        logger.debug(f"marking temporary payment as not pending {internal_checking_id}")
        # mark the invoice from the other side as not pending anymore
        # so the other side only has access to his new money when we are sure
        # the payer has enough to deduct from
        async with db.connect() as conn:
            await update_payment_status(
                checking_id=internal_checking_id, pending=False, conn=conn
            )
        await send_payment_notification(wallet, new_payment)

        # notify receiver asynchronously
        from lnbits.tasks import internal_invoice_queue

        logger.debug(f"enqueuing internal invoice {internal_checking_id}")
        await internal_invoice_queue.put(internal_checking_id)
    else:
        fee_reserve_msat = fee_reserve(invoice.amount_msat, internal=False)
        service_fee_msat = service_fee(invoice.amount_msat, internal=False)
        logger.debug(f"backend: sending payment {temp_id}")
        # actually pay the external invoice
        funding_source = get_funding_source()
        payment: PaymentResponse = await funding_source.pay_invoice(
            payment_request, fee_reserve_msat
        )

        if payment.checking_id and payment.checking_id != temp_id:
            logger.warning(
                f"backend sent unexpected checking_id (expected: {temp_id} got:"
                f" {payment.checking_id})"
            )

        logger.debug(f"backend: pay_invoice finished {temp_id}")
        logger.debug(f"backend: pay_invoice response {payment}")
        if payment.checking_id and payment.ok is not False:
            # payment.ok can be True (paid) or None (pending)!
            logger.debug(f"updating payment {temp_id}")
            async with db.connect() as conn:
                await update_payment_details(
                    checking_id=temp_id,
                    pending=payment.ok is not True,
                    fee=-(
                        abs(payment.fee_msat if payment.fee_msat else 0)
                        + abs(service_fee_msat)
                    ),
                    preimage=payment.preimage,
                    new_checking_id=payment.checking_id,
                    conn=conn,
                )
                wallet = await get_wallet(wallet_id, conn=conn)
                updated = await get_wallet_payment(
                    wallet_id, payment.checking_id, conn=conn
                )
                if wallet and updated:
                    await send_payment_notification(wallet, updated)
                logger.debug(f"payment successful {payment.checking_id}")
        elif payment.checking_id is None and payment.ok is False:
            # payment failed
            logger.warning("backend sent payment failure")
            async with db.connect() as conn:
                logger.debug(f"deleting temporary payment {temp_id}")
                await delete_wallet_payment(temp_id, wallet_id, conn=conn)
            raise PaymentError(
                f"Payment failed: {payment.error_message}"
                or "Payment failed, but backend didn't give us an error message.",
                status="failed",
            )
        else:
            logger.warning(
                "didn't receive checking_id from backend, payment may be stuck in"
                f" database: {temp_id}"
            )

    # credit service fee wallet
    if settings.lnbits_service_fee_wallet and service_fee_msat:
        new_payment = await create_payment(
            wallet_id=settings.lnbits_service_fee_wallet,
            fee=0,
            amount=abs(service_fee_msat),
            memo="Service fee",
            checking_id="service_fee" + temp_id,
            payment_request=payment_request,
            payment_hash=invoice.payment_hash,
            pending=False,
        )
    return invoice.payment_hash


def _check_wallet_balance(
    wallet: Wallet,
    fee_reserve_total_msat: int,
    internal_checking_id: Optional[str] = None,
):
    if wallet.balance_msat < 0:
        logger.debug("balance is too low, deleting temporary payment")
        if not internal_checking_id and wallet.balance_msat > -fee_reserve_total_msat:
            raise PaymentError(
                f"You must reserve at least ({round(fee_reserve_total_msat/1000)}"
                "  sat) to cover potential routing fees.",
                status="failed",
            )
        raise PaymentError("Insufficient balance.", status="failed")


async def check_wallet_limits(wallet_id, conn, amount_msat):
    await check_time_limit_between_transactions(conn, wallet_id)
    await check_wallet_daily_withdraw_limit(conn, wallet_id, amount_msat)


async def check_time_limit_between_transactions(conn, wallet_id):
    limit = settings.lnbits_wallet_limit_secs_between_trans
    if not limit or limit <= 0:
        return

    payments = await get_payments(
        since=int(time.time()) - limit,
        wallet_id=wallet_id,
        limit=1,
        conn=conn,
    )

    if len(payments) == 0:
        return

    raise PaymentError(
        status="failed",
        message=f"The time limit of {limit} seconds between payments has been reached.",
    )


async def check_wallet_daily_withdraw_limit(conn, wallet_id, amount_msat):
    limit = settings.lnbits_wallet_limit_daily_max_withdraw
    if not limit or limit <= 0:
        return

    payments = await get_payments(
        since=int(time.time()) - 60 * 60 * 24,
        outgoing=True,
        wallet_id=wallet_id,
        limit=1,
        conn=conn,
    )
    if len(payments) == 0:
        return

    total = 0
    for pay in payments:
        total += pay.amount
    total = total - amount_msat
    if limit * 1000 + total < 0:
        raise ValueError(
            "Daily withdrawal limit of "
            + str(settings.lnbits_wallet_limit_daily_max_withdraw)
            + " sats reached."
        )


async def check_transaction_status(
    wallet_id: str, payment_hash: str, conn: Optional[Connection] = None
) -> PaymentStatus:
    payment: Optional[Payment] = await get_wallet_payment(
        wallet_id, payment_hash, conn=conn
    )
    if not payment:
        return PaymentPendingStatus()
    if not payment.pending:
        # note: before, we still checked the status of the payment again
        return PaymentSuccessStatus(fee_msat=payment.fee)

    status: PaymentStatus = await payment.check_status()
    return status


# WARN: this same value must be used for balance check and passed to
# funding_source.pay_invoice(), it may cause a vulnerability if the values differ
def fee_reserve(amount_msat: int, internal: bool = False) -> int:
    if internal:
        return 0
    reserve_min = settings.lnbits_reserve_fee_min
    reserve_percent = settings.lnbits_reserve_fee_percent
    return max(int(reserve_min), int(amount_msat * reserve_percent / 100.0))


def service_fee(amount_msat: int, internal: bool = False) -> int:
    service_fee_percent = settings.lnbits_service_fee
    fee_max = settings.lnbits_service_fee_max * 1000
    if settings.lnbits_service_fee_wallet:
        if internal and settings.lnbits_service_fee_ignore_internal:
            return 0
        fee_percentage = int(amount_msat / 100 * service_fee_percent)
        if fee_max > 0 and fee_percentage > fee_max:
            return fee_max
        else:
            return fee_percentage
    else:
        return 0


def fee_reserve_total(amount_msat: int, internal: bool = False) -> int:
    return fee_reserve(amount_msat, internal) + service_fee(amount_msat, internal)


async def send_payment_notification(wallet: Wallet, payment: Payment):
    await websocket_updater(
        wallet.inkey,
        json.dumps(
            {
                "wallet_balance": wallet.balance,
                "payment": payment.dict(),
            }
        ),
    )

    await websocket_updater(
        payment.payment_hash, json.dumps({"pending": payment.pending})
    )


async def update_wallet_balance(wallet_id: str, amount: int):
    payment_hash, _ = await create_invoice(
        wallet_id=wallet_id,
        amount=amount,
        memo="Admin top up",
        internal=True,
    )
    async with db.connect() as conn:
        checking_id = await check_internal(payment_hash, conn=conn)
        assert checking_id, "newly created checking_id cannot be retrieved"
        await update_payment_status(checking_id=checking_id, pending=False, conn=conn)
        # notify receiver asynchronously
        from lnbits.tasks import internal_invoice_queue

        await internal_invoice_queue.put(checking_id)


async def check_admin_settings():
    if settings.super_user:
        settings.super_user = to_valid_user_id(settings.super_user).hex

    if settings.lnbits_admin_ui:
        settings_db = await get_super_settings()
        if not settings_db:
            # create new settings if table is empty
            logger.warning("Settings DB empty. Inserting default settings.")
            settings_db = await init_admin_settings(settings.super_user)
            logger.warning("Initialized settings from environment variables.")

        if settings.super_user and settings.super_user != settings_db.super_user:
            # .env super_user overwrites DB super_user
            settings_db = await update_super_user(settings.super_user)

        update_cached_settings(settings_db.dict())

        # saving superuser to {data_dir}/.super_user file
        with open(Path(settings.lnbits_data_folder) / ".super_user", "w") as file:
            file.write(settings.super_user)

        # callback for saas
        if (
            settings.lnbits_saas_callback
            and settings.lnbits_saas_secret
            and settings.lnbits_saas_instance_id
        ):
            send_admin_user_to_saas()

        account = await get_account(settings.super_user)
        if account and account.config and account.config.provider == "env":
            settings.first_install = True

        logger.success(
            "✔️ Admin UI is enabled. run `poetry run lnbits-cli superuser` "
            "to get the superuser."
        )


async def check_webpush_settings():
    if not settings.lnbits_webpush_privkey:
        vapid = Vapid()
        vapid.generate_keys()
        privkey = vapid.private_pem()
        assert vapid.public_key, "VAPID public key does not exist"
        pubkey = b64urlencode(
            vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )
        push_settings = {
            "lnbits_webpush_privkey": privkey.decode(),
            "lnbits_webpush_pubkey": pubkey,
        }
        update_cached_settings(push_settings)
        await update_admin_settings(EditableSettings(**push_settings))

    logger.info("Initialized webpush settings with generated VAPID key pair.")
    logger.info(f"Pubkey: {settings.lnbits_webpush_pubkey}")


def update_cached_settings(sets_dict: dict):
    for key, value in sets_dict.items():
        if key in readonly_variables:
            continue
        if key not in settings.dict().keys():
            continue
        try:
            setattr(settings, key, value)
        except Exception:
            logger.warning(f"Failed overriding setting: {key}, value: {value}")
    if "super_user" in sets_dict:
        settings.super_user = sets_dict["super_user"]


async def init_admin_settings(super_user: Optional[str] = None) -> SuperSettings:
    account = None
    if super_user:
        account = await get_account(super_user)
    if not account:
        account = await create_account(
            user_id=super_user, user_config=UserConfig(provider="env")
        )
    if not account.wallets or len(account.wallets) == 0:
        await create_wallet(user_id=account.id)

    editable_settings = EditableSettings.from_dict(settings.dict())

    return await create_admin_settings(account.id, editable_settings.dict())


async def create_user_account(
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    wallet_name: Optional[str] = None,
    user_config: Optional[UserConfig] = None,
) -> User:
    if not settings.new_accounts_allowed:
        raise ValueError("Account creation is disabled.")
    if username and await get_account_by_username(username):
        raise ValueError("Username already exists.")

    if email and await get_account_by_email(email):
        raise ValueError("Email already exists.")

    if user_id:
        user_uuid4 = UUID(hex=user_id, version=4)
        assert user_uuid4.hex == user_id, "User ID is not valid UUID4 hex string"
    else:
        user_id = uuid4().hex

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password = pwd_context.hash(password) if password else None

    account = await create_account(user_id, username, email, password, user_config)
    wallet = await create_wallet(user_id=account.id, wallet_name=wallet_name)
    account.wallets = [wallet]

    for ext_id in settings.lnbits_user_default_extensions:
        await update_user_extension(user_id=account.id, extension=ext_id, active=True)

    return account


class WebsocketConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket, item_id: str):
        logger.debug(f"Websocket connected to {item_id}")
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_data(self, message: str, item_id: str):
        for connection in self.active_connections:
            if connection.path_params["item_id"] == item_id:
                await connection.send_text(message)


websocket_manager = WebsocketConnectionManager()


async def websocket_updater(item_id, data):
    return await websocket_manager.send_data(f"{data}", item_id)


async def switch_to_voidwallet() -> None:
    funding_source = get_funding_source()
    if funding_source.__class__.__name__ == "VoidWallet":
        return
    set_funding_source("VoidWallet")
    settings.lnbits_backend_wallet_class = "VoidWallet"


async def get_balance_delta() -> BalanceDelta:
    funding_source = get_funding_source()
    status = await funding_source.status()
    lnbits_balance = await get_total_balance()
    return BalanceDelta(
        lnbits_balance_msats=lnbits_balance,
        node_balance_msats=status.balance_msat,
    )
