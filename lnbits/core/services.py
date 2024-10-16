import asyncio
import json
import time
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
from bolt11 import decode as bolt11_decode
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, WebSocket
from loguru import logger
from py_vapid import Vapid
from py_vapid.utils import b64urlencode

from lnbits.core.db import db
from lnbits.core.extensions.models import UserExtension
from lnbits.db import Connection
from lnbits.decorators import (
    WalletTypeInfo,
    check_user_extension_access,
    require_admin_key,
)
from lnbits.exceptions import InvoiceError, PaymentError
from lnbits.helpers import url_for
from lnbits.lnurl import LnurlErrorResponse
from lnbits.lnurl import decode as decode_lnurl
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
    create_account,
    create_admin_settings,
    create_payment,
    create_wallet,
    get_account,
    get_account_by_email,
    get_account_by_pubkey,
    get_account_by_username,
    get_payments,
    get_standalone_payment,
    get_super_settings,
    get_total_balance,
    get_user,
    get_wallet,
    get_wallet_payment,
    is_internal_status_success,
    update_admin_settings,
    update_payment,
    update_super_user,
    update_user_extension,
)
from .helpers import to_valid_user_id
from .models import (
    Account,
    BalanceDelta,
    CreatePayment,
    Payment,
    PaymentFiatAmounts,
    PaymentState,
    User,
    UserExtra,
    Wallet,
)


async def calculate_fiat_amounts(
    amount: float,
    wallet: Wallet,
    currency: Optional[str] = None,
) -> tuple[int, PaymentFiatAmounts]:
    wallet_currency = wallet.currency or settings.lnbits_default_accounting_currency
    fiat_amounts = PaymentFiatAmounts()
    if currency and currency != "sat":
        amount_sat = await fiat_amount_as_satoshis(amount, currency)
        if currency != wallet_currency:
            fiat_amounts.fiat_currency = currency
            fiat_amounts.fiat_amount = round(amount, ndigits=3)
            fiat_amounts.fiat_rate = amount_sat / amount
    else:
        amount_sat = int(amount)

    if wallet_currency:
        if wallet_currency == currency:
            fiat_amount = amount
        else:
            fiat_amount = await satoshis_amount_as_fiat(amount_sat, wallet_currency)
        fiat_amounts.wallet_fiat_currency = wallet_currency
        fiat_amounts.wallet_fiat_amount = round(fiat_amount, ndigits=3)
        fiat_amounts.wallet_fiat_rate = amount_sat / fiat_amount

    logger.debug(
        f"Calculated fiat amounts {wallet.id=} {amount=} {currency=}: {fiat_amounts=}"
    )

    return amount_sat, fiat_amounts


async def create_invoice(
    *,
    wallet_id: str,
    amount: float,
    currency: Optional[str] = "sat",
    memo: str,
    description_hash: Optional[bytes] = None,
    unhashed_description: Optional[bytes] = None,
    expiry: Optional[int] = None,
    extra: Optional[dict] = None,
    webhook: Optional[str] = None,
    internal: Optional[bool] = False,
    conn: Optional[Connection] = None,
) -> Payment:
    if not amount > 0:
        raise InvoiceError("Amountless invoices not supported.", status="failed")

    user_wallet = await get_wallet(wallet_id, conn=conn)
    if not user_wallet:
        raise InvoiceError(f"Could not fetch wallet '{wallet_id}'.", status="failed")

    invoice_memo = None if description_hash else memo

    # use the fake wallet if the invoice is for internal use only
    funding_source = fake_wallet if internal else get_funding_source()

    amount_sat, fiat_amounts = await calculate_fiat_amounts(
        amount, user_wallet, currency
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

    create_payment_model = CreatePayment(
        wallet_id=wallet_id,
        bolt11=payment_request,
        payment_hash=invoice.payment_hash,
        amount_msat=amount_sat * 1000,
        expiry=invoice.expiry_date,
        memo=memo,
        extra=extra,
        fiat_amounts=fiat_amounts,
        webhook=webhook,
    )

    payment = await create_payment(
        checking_id=checking_id,
        data=create_payment_model,
        conn=conn,
    )

    return payment


async def _pay_internal_invoice(
    wallet: Wallet,
    create_payment_model: CreatePayment,
    conn: Optional[Connection] = None,
) -> Optional[Payment]:
    """
    Pay an internal payment.
    returns None if the payment is not internal.
    """
    # check_internal() returns the payment of the invoice we're waiting for
    # (pending only)
    internal_payment = await check_internal(
        create_payment_model.payment_hash, conn=conn
    )
    if not internal_payment:
        return None

    # perform additional checks on the internal payment
    # the payment hash is not enough to make sure that this is the same invoice
    internal_invoice = await get_standalone_payment(
        internal_payment.checking_id, incoming=True, conn=conn
    )
    if not internal_invoice:
        raise PaymentError("Internal payment not found.", status="failed")

    if (
        internal_invoice.amount != abs(create_payment_model.amount_msat)
        or internal_invoice.bolt11 != create_payment_model.bolt11.lower()
    ):
        raise PaymentError(
            "Invalid invoice. Bolt11 or amount is not correct", status="failed"
        )

    fee_reserve_total_msat = fee_reserve_total(
        abs(create_payment_model.amount_msat), internal=True
    )
    create_payment_model.fee = abs(fee_reserve_total_msat)

    if (
        wallet.balance_msat
        < abs(create_payment_model.amount_msat) + fee_reserve_total_msat
    ):
        raise PaymentError("Insufficient balance.", status="failed")

    internal_id = f"internal_{create_payment_model.payment_hash}"
    logger.debug(f"creating temporary internal payment with id {internal_id}")
    payment = await create_payment(
        checking_id=internal_id,
        data=create_payment_model,
        status=PaymentState.SUCCESS,
        conn=conn,
    )

    # mark the invoice from the other side as not pending anymore
    # so the other side only has access to his new money when we are sure
    # the payer has enough to deduct from
    internal_payment.status = PaymentState.SUCCESS
    await update_payment(internal_payment, conn=conn)

    await send_payment_notification(wallet, payment)

    # notify receiver asynchronously
    from lnbits.tasks import internal_invoice_queue

    logger.debug(f"enqueuing internal invoice {internal_payment.checking_id}")
    await internal_invoice_queue.put(internal_payment.checking_id)

    return payment


async def _verify_external_payment(
    payment: Payment, conn: Optional[Connection] = None
) -> Payment:
    # fail on pending payments
    if payment.pending:
        raise PaymentError("Payment is still pending.", status="pending")
    if payment.success:
        raise PaymentError("Payment already paid.", status="success")
    if payment.failed:
        status = await payment.check_status()
        if status.success:
            # payment was successful on the fundingsource
            payment.status = PaymentState.SUCCESS
            await update_payment(payment, conn=conn)
            raise PaymentError(
                "Failed payment was already paid on the fundingsource.",
                status="success",
            )
        if status.failed:
            raise PaymentError(
                "Payment is failed node, retrying is not possible.", status="failed"
            )
        # status.pending fall through and try again
    return payment


async def _pay_external_invoice(
    wallet: Wallet,
    create_payment_model: CreatePayment,
    conn: Optional[Connection] = None,
) -> Payment:
    fee_reserve_total_msat = fee_reserve_total(
        create_payment_model.amount_msat, internal=False
    )
    create_payment_model.fee = -abs(fee_reserve_total_msat)

    checking_id = create_payment_model.payment_hash

    # check if there is already a payment with the same checking_id
    old_payment = await get_standalone_payment(checking_id, conn=conn)
    if old_payment:
        return await _verify_external_payment(old_payment, conn)

    if (
        wallet.balance_msat
        < abs(create_payment_model.amount_msat) + fee_reserve_total_msat
    ):
        raise PaymentError(
            f"You must reserve at least ({round(fee_reserve_total_msat/1000)}"
            "  sat) to cover potential routing fees.",
            status="failed",
        )

    payment = await create_payment(
        checking_id=checking_id,
        data=create_payment_model,
        conn=conn,
    )

    fee_reserve_msat = fee_reserve(create_payment_model.amount_msat, internal=False)
    service_fee_msat = service_fee(create_payment_model.amount_msat, internal=False)

    funding_source = get_funding_source()

    logger.debug(f"fundingsource: sending payment {checking_id}")
    payment_response: PaymentResponse = await funding_source.pay_invoice(
        create_payment_model.bolt11, fee_reserve_msat
    )
    logger.debug(f"backend: pay_invoice finished {checking_id}, {payment_response}")
    if payment_response.checking_id and payment_response.checking_id != checking_id:
        logger.warning(
            f"backend sent unexpected checking_id (expected: {checking_id} got:"
            f" {payment_response.checking_id})"
        )
    if payment_response.checking_id and payment_response.ok is not False:
        # payment.ok can be True (paid) or None (pending)!
        logger.debug(f"updating payment {checking_id}")
        # TODO: why? we log a warning on an unexpected checking_id
        # should that be handled?
        # new checking id
        payment.checking_id = payment_response.checking_id
        payment.status = (
            PaymentState.SUCCESS
            if payment_response.ok is True
            else PaymentState.PENDING
        )
        payment.fee = -(abs(payment_response.fee_msat or 0) + abs(service_fee_msat))
        payment.preimage = payment_response.preimage
        await update_payment(payment, conn=conn)
        await send_payment_notification(wallet, payment)
        logger.success(f"payment successful {payment_response.checking_id}")
    elif payment_response.checking_id is None and payment_response.ok is False:
        # payment failed
        logger.debug(f"payment failed {checking_id}, {payment_response.error_message}")
        payment.status = PaymentState.FAILED
        await update_payment(payment, conn=conn)
        raise PaymentError(
            f"Payment failed: {payment_response.error_message}"
            or "Payment failed, but backend didn't give us an error message.",
            status="failed",
        )
    else:
        logger.warning(
            "didn't receive checking_id from backend, payment may be stuck in"
            f" database: {checking_id}"
        )
    return payment


async def pay_invoice(
    *,
    wallet_id: str,
    payment_request: str,
    max_sat: Optional[int] = None,
    extra: Optional[dict] = None,
    description: str = "",
    tag: str = "",
    conn: Optional[Connection] = None,
) -> Payment:
    try:
        invoice = bolt11_decode(payment_request)
    except Exception as exc:
        raise PaymentError("Bolt11 decoding failed.", status="failed") from exc

    if not invoice.amount_msat or not invoice.amount_msat > 0:
        raise PaymentError("Amountless invoices not supported.", status="failed")
    if max_sat and invoice.amount_msat > max_sat * 1000:
        raise PaymentError("Amount in invoice is too high.", status="failed")

    async with db.reuse_conn(conn) if conn else db.connect() as conn:

        wallet = await get_wallet(wallet_id, conn=conn)
        if not wallet:
            raise PaymentError(
                f"Could not fetch wallet '{wallet_id}'.", status="failed"
            )

        # check if the payment is made for an extension that the user disabled
        if tag:
            status = await check_user_extension_access(wallet.user, tag)
            if not status.success:
                raise PaymentError(status.message)

        await check_wallet_limits(wallet_id, invoice.amount_msat, conn)

        if await is_internal_status_success(invoice.payment_hash, conn):
            raise PaymentError("Internal invoice already paid.", status="failed")

        _, fiat_amounts = await calculate_fiat_amounts(
            invoice.amount_msat / 1000, wallet
        )

        create_payment_model = CreatePayment(
            wallet_id=wallet_id,
            bolt11=payment_request,
            payment_hash=invoice.payment_hash,
            amount_msat=invoice.amount_msat * -1,
            expiry=invoice.expiry_date,
            memo=description or invoice.description or "",
            extra=extra,
            fiat_amounts=fiat_amounts,
        )

        payment = await _pay_internal_invoice(wallet, create_payment_model, conn)
        if not payment:
            payment = await _pay_external_invoice(wallet, create_payment_model, conn)

        await _credit_service_fee_wallet(payment)

        return payment


async def _credit_service_fee_wallet(payment: Payment):
    service_fee_msat = service_fee(payment.amount, internal=payment.is_internal)
    if settings.lnbits_service_fee_wallet and service_fee_msat:
        create_payment_model = CreatePayment(
            wallet_id=settings.lnbits_service_fee_wallet,
            bolt11=payment.bolt11,
            payment_hash=payment.payment_hash,
            amount_msat=abs(service_fee_msat),
            memo="Service fee",
        )
        await create_payment(
            checking_id=f"service_fee_{payment.payment_hash}",
            data=create_payment_model,
            status=PaymentState.SUCCESS,
        )


async def check_wallet_limits(
    wallet_id: str, amount_msat: int, conn: Optional[Connection] = None
):
    await check_time_limit_between_transactions(wallet_id, conn)
    await check_wallet_daily_withdraw_limit(wallet_id, amount_msat, conn)


async def check_time_limit_between_transactions(
    wallet_id: str, conn: Optional[Connection] = None
):
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


async def check_wallet_daily_withdraw_limit(
    wallet_id: str, amount_msat: int, conn: Optional[Connection] = None
):
    limit = settings.lnbits_wallet_limit_daily_max_withdraw
    if not limit:
        return
    if limit < 0:
        raise ValueError("It is not allowed to spend funds from this server.")

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


async def redeem_lnurl_withdraw(
    wallet_id: str,
    lnurl_request: str,
    memo: Optional[str] = None,
    extra: Optional[dict] = None,
    wait_seconds: int = 0,
    conn: Optional[Connection] = None,
) -> None:
    if not lnurl_request:
        return None

    res = {}

    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(headers=headers) as client:
        lnurl = decode_lnurl(lnurl_request)
        r = await client.get(str(lnurl))
        res = r.json()

    try:
        _, payment_request = await create_invoice(
            wallet_id=wallet_id,
            amount=int(res["maxWithdrawable"] / 1000),
            memo=memo or res["defaultDescription"] or "",
            extra=extra,
            conn=conn,
        )
    except Exception:
        logger.warning(
            f"failed to create invoice on redeem_lnurl_withdraw "
            f"from {lnurl}. params: {res}"
        )
        return None

    if wait_seconds:
        await asyncio.sleep(wait_seconds)

    params = {"k1": res["k1"], "pr": payment_request}

    try:
        params["balanceNotify"] = url_for(
            f"/withdraw/notify/{urlparse(lnurl_request).netloc}",
            external=True,
            wal=wallet_id,
        )
    except Exception:
        pass

    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(headers=headers) as client:
        try:
            await client.get(res["callback"], params=params)
        except Exception:
            pass


async def perform_lnurlauth(
    callback: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Optional[LnurlErrorResponse]:
    cb = urlparse(callback)

    k1 = bytes.fromhex(parse_qs(cb.query)["k1"][0])

    key = wallet.wallet.lnurlauth_key(cb.netloc)

    def int_to_bytes_suitable_der(x: int) -> bytes:
        """for strict DER we need to encode the integer with some quirks"""
        b = x.to_bytes((x.bit_length() + 7) // 8, "big")

        if len(b) == 0:
            # ensure there's at least one byte when the int is zero
            return bytes([0])

        if b[0] & 0x80 != 0:
            # ensure it doesn't start with a 0x80 and so it isn't
            # interpreted as a negative number
            return bytes([0]) + b

        return b

    def encode_strict_der(r: int, s: int, order: int):
        # if s > order/2 verification will fail sometimes
        # so we must fix it here see:
        # https://github.com/indutny/elliptic/blob/e71b2d9359c5fe9437fbf46f1f05096de447de57/lib/elliptic/ec/index.js#L146-L147
        if s > order // 2:
            s = order - s

        # now we do the strict DER encoding copied from
        # https://github.com/KiriKiri/bip66 (without any checks)
        r_temp = int_to_bytes_suitable_der(r)
        s_temp = int_to_bytes_suitable_der(s)

        r_len = len(r_temp)
        s_len = len(s_temp)
        sign_len = 6 + r_len + s_len

        signature = BytesIO()
        signature.write(0x30.to_bytes(1, "big", signed=False))
        signature.write((sign_len - 2).to_bytes(1, "big", signed=False))
        signature.write(0x02.to_bytes(1, "big", signed=False))
        signature.write(r_len.to_bytes(1, "big", signed=False))
        signature.write(r_temp)
        signature.write(0x02.to_bytes(1, "big", signed=False))
        signature.write(s_len.to_bytes(1, "big", signed=False))
        signature.write(s_temp)

        return signature.getvalue()

    sig = key.sign_digest_deterministic(k1, sigencode=encode_strict_der)

    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(headers=headers) as client:
        assert key.verifying_key, "LNURLauth verifying_key does not exist"
        r = await client.get(
            callback,
            params={
                "k1": k1.hex(),
                "key": key.verifying_key.to_string("compressed").hex(),
                "sig": sig.hex(),
            },
        )
        try:
            resp = json.loads(r.text)
            if resp["status"] == "OK":
                return None

            return LnurlErrorResponse(reason=resp["reason"])
        except (KeyError, json.decoder.JSONDecodeError):
            return LnurlErrorResponse(
                reason=r.text[:200] + "..." if len(r.text) > 200 else r.text
            )


async def check_transaction_status(
    wallet_id: str, payment_hash: str, conn: Optional[Connection] = None
) -> PaymentStatus:
    payment: Optional[Payment] = await get_wallet_payment(
        wallet_id, payment_hash, conn=conn
    )
    if not payment:
        return PaymentPendingStatus()

    if payment.status == PaymentState.SUCCESS.value:
        return PaymentSuccessStatus(fee_msat=payment.fee)

    return await payment.check_status()


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
    await websocket_manager.send_data(payment.json(), wallet.inkey)
    # json.dumps(
    #     {
    #         "wallet_balance": wallet.balance,
    #         "payment": payment,
    #     }
    # ),
    await websocket_manager.send_data(
        json.dumps({"pending": payment.pending}), payment.payment_hash
    )


async def update_wallet_balance(wallet_id: str, amount: int):
    async with db.connect() as conn:
        payment = await create_invoice(
            wallet_id=wallet_id,
            amount=amount,
            memo="Admin top up",
            internal=True,
            conn=conn,
        )
        payment.status = PaymentState.SUCCESS
        await update_payment(payment, conn=conn)
        # notify receiver asynchronously
        from lnbits.tasks import internal_invoice_queue

        await internal_invoice_queue.put(payment.checking_id)


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
        if account and account.extra and account.extra.provider == "env":
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
        account_id = super_user or uuid4().hex
        account = Account(
            id=account_id,
            extra=UserExtra(provider="env"),
        )
        await create_account(account)
        await create_wallet(user_id=account.id)

    editable_settings = EditableSettings.from_dict(settings.dict())
    return await create_admin_settings(account.id, editable_settings.dict())


async def create_user_account(
    account: Optional[Account] = None, wallet_name: Optional[str] = None
) -> User:
    if not settings.new_accounts_allowed:
        raise ValueError("Account creation is disabled.")
    if account:
        if account.username and await get_account_by_username(account.username):
            raise ValueError("Username already exists.")

        if account.email and await get_account_by_email(account.email):
            raise ValueError("Email already exists.")

        if account.pubkey and await get_account_by_pubkey(account.pubkey):
            raise ValueError("Pubkey already exists.")

        if account.id:
            user_uuid4 = UUID(hex=account.id, version=4)
            assert user_uuid4.hex == account.id, "User ID is not valid UUID4 hex string"
        else:
            account.id = uuid4().hex

    account = await create_account(account)
    await create_wallet(
        user_id=account.id,
        wallet_name=wallet_name or settings.lnbits_default_wallet_name,
    )

    for ext_id in settings.lnbits_user_default_extensions:
        user_ext = UserExtension(user=account.id, extension=ext_id, active=True)
        await update_user_extension(user_ext)

    user = await get_user(account)
    assert user, "Cannot find user for account."

    return user


class WebsocketConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

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


async def websocket_updater(item_id: str, data: str):
    return await websocket_manager.send_data(data, item_id)


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


async def update_pending_payments(wallet_id: str):
    pending_payments = await get_payments(
        wallet_id=wallet_id,
        pending=True,
        exclude_uncheckable=True,
    )
    for payment in pending_payments:
        status = await payment.check_status()
        if status.failed:
            payment.status = PaymentState.FAILED
            await update_payment(payment)
        elif status.success:
            payment.status = PaymentState.SUCCESS
            await update_payment(payment)
