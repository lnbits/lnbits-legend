import json
import time
from typing import Optional

from bolt11 import Bolt11, MilliSatoshi, Tags
from bolt11 import decode as bolt11_decode
from bolt11 import encode as bolt11_encode
from loguru import logger

from lnbits.core.db import db
from lnbits.core.models.notifications import NotificationType
from lnbits.core.services.notifications import enqueue_notification
from lnbits.db import Connection
from lnbits.decorators import check_user_extension_access
from lnbits.exceptions import InvoiceError, PaymentError
from lnbits.settings import settings
from lnbits.utils.crypto import fake_privkey, random_secret_and_hash
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis, satoshis_amount_as_fiat
from lnbits.wallets import fake_wallet, get_funding_source
from lnbits.wallets.base import (
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
)

from ..crud import (
    check_internal,
    create_payment,
    get_payments,
    get_standalone_payment,
    get_wallet,
    get_wallet_payment,
    is_internal_status_success,
    update_payment,
)
from ..models import (
    CreatePayment,
    Payment,
    PaymentState,
    Wallet,
)
from .websockets import websocket_manager


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
    if settings.lnbits_only_allow_incoming_payments:
        raise PaymentError("Only incoming payments allowed.", status="failed")
    invoice = _validate_payment_request(payment_request, max_sat)
    assert invoice.amount_msat

    async with db.reuse_conn(conn) if conn else db.connect() as conn:
        amount_msat = invoice.amount_msat
        wallet = await _check_wallet_for_payment(wallet_id, tag, amount_msat, conn)

        if await is_internal_status_success(invoice.payment_hash, conn):
            raise PaymentError("Internal invoice already paid.", status="failed")

        _, extra = await calculate_fiat_amounts(amount_msat / 1000, wallet, extra=extra)

        create_payment_model = CreatePayment(
            wallet_id=wallet_id,
            bolt11=payment_request,
            payment_hash=invoice.payment_hash,
            amount_msat=-amount_msat,
            expiry=invoice.expiry_date,
            memo=description or invoice.description or "",
            extra=extra,
        )

        payment = await _pay_invoice(wallet, create_payment_model, conn)
        await _credit_service_fee_wallet(payment, conn)

        return payment


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

    amount_sat, extra = await calculate_fiat_amounts(
        amount, user_wallet, currency, extra
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
        fees_msats,
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
        webhook=webhook,
        fee=abs(fees_msats or 0),
    )

    payment = await create_payment(
        checking_id=checking_id,
        data=create_payment_model,
        conn=conn,
    )

    return payment


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


def fee_reserve_total(amount_msat: int, internal: bool = False) -> int:
    return fee_reserve(amount_msat, internal) + service_fee(amount_msat, internal)


def fee_reserve(amount_msat: int, internal: bool = False) -> int:
    return settings.fee_reserve(amount_msat, internal)


def service_fee(amount_msat: int, internal: bool = False) -> int:
    amount_msat = abs(amount_msat)
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


async def update_wallet_balance(
    wallet: Wallet,
    amount: int,
    conn: Optional[Connection] = None,
):
    if amount == 0:
        raise ValueError("Amount cannot be 0.")

    # negative balance change
    if amount < 0:
        if wallet.balance + amount < 0:
            raise ValueError("Balance change failed, can not go into negative balance.")
        async with db.reuse_conn(conn) if conn else db.connect() as conn:
            payment_secret, payment_hash = random_secret_and_hash()
            invoice = Bolt11(
                currency="bc",
                amount_msat=MilliSatoshi(abs(amount) * 1000),
                date=int(time.time()),
                tags=Tags.from_dict(
                    {
                        "payment_hash": payment_hash,
                        "payment_secret": payment_secret,
                        "description": "Admin debit",
                    }
                ),
            )
            privkey = fake_privkey(settings.fake_wallet_secret)
            bolt11 = bolt11_encode(invoice, privkey)
            await create_payment(
                checking_id=f"internal_{payment_hash}",
                data=CreatePayment(
                    wallet_id=wallet.id,
                    bolt11=bolt11,
                    payment_hash=payment_hash,
                    amount_msat=amount * 1000,
                    memo="Admin debit",
                ),
                status=PaymentState.SUCCESS,
                conn=conn,
            )
        return None

    # positive balance change
    if (
        settings.lnbits_wallet_limit_max_balance > 0
        and wallet.balance + amount > settings.lnbits_wallet_limit_max_balance
    ):
        raise ValueError("Balance change failed, amount exceeds maximum balance.")
    async with db.reuse_conn(conn) if conn else db.connect() as conn:
        payment = await create_invoice(
            wallet_id=wallet.id,
            amount=amount,
            memo="Admin credit",
            internal=True,
            conn=conn,
        )
        payment.status = PaymentState.SUCCESS
        await update_payment(payment, conn=conn)
        # notify receiver asynchronously
        from lnbits.tasks import internal_invoice_queue

        await internal_invoice_queue.put(payment.checking_id)


async def send_payment_notification(wallet: Wallet, payment: Payment):
    try:
        await send_ws_payment_notification(wallet, payment)
    except Exception as e:
        logger.error("Error sending websocket payment notification", e)

    try:
        send_chat_payment_notification(wallet, payment)
    except Exception as e:
        logger.error("Error sending chat payment notification", e)


async def send_ws_payment_notification(wallet: Wallet, payment: Payment):
    # TODO: websocket message should be a clean payment model
    # await websocket_manager.send_data(payment.json(), wallet.inkey)
    # TODO: figure out why we send the balance with the payment here.
    # cleaner would be to have a separate message for the balance
    # and send it with the id of the wallet so wallets can subscribe to it
    payment_notification = json.dumps(
        {
            "wallet_balance": wallet.balance,
            # use pydantic json serialization to get the correct datetime format
            "payment": json.loads(payment.json()),
        },
    )
    await websocket_manager.send_data(payment_notification, wallet.inkey)
    await websocket_manager.send_data(payment_notification, wallet.adminkey)

    await websocket_manager.send_data(
        json.dumps({"pending": payment.pending}), payment.payment_hash
    )


def send_chat_payment_notification(wallet: Wallet, payment: Payment):
    amount_sats = abs(payment.sat)
    values: dict = {
        "wallet_id": wallet.id,
        "wallet_name": wallet.name,
        "amount_sats": amount_sats,
        "fiat_value_fmt": "",
    }
    if payment.extra.get("wallet_fiat_currency", None):
        amount_fiat = payment.extra.get("wallet_fiat_amount", None)
        currency = payment.extra.get("wallet_fiat_currency", None)
        values["fiat_value_fmt"] = f"`{amount_fiat}`*{currency}* / "

    if payment.is_out:
        if amount_sats >= settings.lnbits_notification_outgoing_payment_amount_sats:
            enqueue_notification(NotificationType.outgoing_payment, values)
    else:
        if amount_sats >= settings.lnbits_notification_incoming_payment_amount_sats:
            enqueue_notification(NotificationType.incoming_payment, values)


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


async def calculate_fiat_amounts(
    amount: float,
    wallet: Wallet,
    currency: Optional[str] = None,
    extra: Optional[dict] = None,
) -> tuple[int, dict]:
    wallet_currency = wallet.currency or settings.lnbits_default_accounting_currency
    fiat_amounts: dict = extra or {}
    if currency and currency != "sat":
        amount_sat = await fiat_amount_as_satoshis(amount, currency)
        if currency != wallet_currency:
            fiat_amounts["fiat_currency"] = currency
            fiat_amounts["fiat_amount"] = round(amount, ndigits=3)
            fiat_amounts["fiat_rate"] = amount_sat / amount
            fiat_amounts["btc_rate"] = (amount / amount_sat) * 100_000_000
    else:
        amount_sat = int(amount)

    if wallet_currency:
        if wallet_currency == currency:
            fiat_amount = amount
        else:
            fiat_amount = await satoshis_amount_as_fiat(amount_sat, wallet_currency)
        fiat_amounts["wallet_fiat_currency"] = wallet_currency
        fiat_amounts["wallet_fiat_amount"] = round(fiat_amount, ndigits=3)
        fiat_amounts["wallet_fiat_rate"] = amount_sat / fiat_amount
        fiat_amounts["wallet_btc_rate"] = (fiat_amount / amount_sat) * 100_000_000

    logger.debug(
        f"Calculated fiat amounts {wallet.id=} {amount=} {currency=}: {fiat_amounts=}"
    )

    return amount_sat, fiat_amounts


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


async def _pay_invoice(wallet, create_payment_model, conn):
    payment = await _pay_internal_invoice(wallet, create_payment_model, conn)
    if not payment:
        payment = await _pay_external_invoice(wallet, create_payment_model, conn)
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

    amount_msat = create_payment_model.amount_msat
    if (
        internal_invoice.amount != abs(amount_msat)
        or internal_invoice.bolt11 != create_payment_model.bolt11.lower()
    ):
        raise PaymentError("Invalid invoice. Bolt11 changed.", status="failed")

    fee_reserve_total_msat = fee_reserve_total(abs(amount_msat), internal=True)
    create_payment_model.fee = abs(fee_reserve_total_msat)

    if wallet.balance_msat < abs(amount_msat) + fee_reserve_total_msat:
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
    logger.success(f"internal payment successful {internal_payment.checking_id}")

    await send_payment_notification(wallet, payment)

    # notify receiver asynchronously
    from lnbits.tasks import internal_invoice_queue

    logger.debug(f"enqueuing internal invoice {internal_payment.checking_id}")
    await internal_invoice_queue.put(internal_payment.checking_id)

    return payment


async def _pay_external_invoice(
    wallet: Wallet,
    create_payment_model: CreatePayment,
    conn: Optional[Connection] = None,
) -> Payment:
    checking_id = create_payment_model.payment_hash
    amount_msat = create_payment_model.amount_msat

    fee_reserve_total_msat = fee_reserve_total(amount_msat, internal=False)

    if wallet.balance_msat < abs(amount_msat) + fee_reserve_total_msat:
        raise PaymentError(
            f"You must reserve at least ({round(fee_reserve_total_msat/1000)}"
            "  sat) to cover potential routing fees.",
            status="failed",
        )
    # check if there is already a payment with the same checking_id
    old_payment = await get_standalone_payment(checking_id, conn=conn)
    if old_payment:
        return await _verify_external_payment(old_payment, conn)

    create_payment_model.fee = -abs(fee_reserve_total_msat)
    payment = await create_payment(
        checking_id=checking_id,
        data=create_payment_model,
        conn=conn,
    )

    fee_reserve_msat = fee_reserve(amount_msat, internal=False)
    service_fee_msat = service_fee(amount_msat, internal=False)

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
        payment.status = (
            PaymentState.SUCCESS
            if payment_response.ok is True
            else PaymentState.PENDING
        )
        payment.fee = -(abs(payment_response.fee_msat or 0) + abs(service_fee_msat))
        payment.preimage = payment_response.preimage
        await update_payment(payment, payment_response.checking_id, conn=conn)
        payment.checking_id = payment_response.checking_id
        if payment.success:
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


async def _verify_external_payment(
    payment: Payment, conn: Optional[Connection] = None
) -> Payment:
    # fail on pending payments
    if payment.pending:
        raise PaymentError("Payment is still pending.", status="pending")
    if payment.success:
        raise PaymentError("Payment already paid.", status="success")

    # payment failed
    status = await payment.check_status()
    if status.failed:
        raise PaymentError(
            "Payment is failed node, retrying is not possible.", status="failed"
        )

    if status.success:
        # payment was successful on the fundingsource
        payment.status = PaymentState.SUCCESS
        await update_payment(payment, conn=conn)
        raise PaymentError(
            "Failed payment was already paid on the fundingsource.",
            status="success",
        )

    # status.pending fall through and try again
    return payment


async def _check_wallet_for_payment(
    wallet_id: str,
    tag: str,
    amount_msat: int,
    conn: Optional[Connection] = None,
):
    wallet = await get_wallet(wallet_id, conn=conn)
    if not wallet:
        raise PaymentError(f"Could not fetch wallet '{wallet_id}'.", status="failed")

    # check if the payment is made for an extension that the user disabled
    status = await check_user_extension_access(wallet.user, tag, conn=conn)
    if not status.success:
        raise PaymentError(status.message)

    await check_wallet_limits(wallet_id, amount_msat, conn)
    return wallet


def _validate_payment_request(
    payment_request: str, max_sat: Optional[int] = None
) -> Bolt11:
    try:
        invoice = bolt11_decode(payment_request)
    except Exception as exc:
        raise PaymentError("Bolt11 decoding failed.", status="failed") from exc

    if not invoice.amount_msat or not invoice.amount_msat > 0:
        raise PaymentError("Amountless invoices not supported.", status="failed")

    if max_sat and invoice.amount_msat > max_sat * 1000:
        raise PaymentError("Amount in invoice is too high.", status="failed")

    return invoice


async def _credit_service_fee_wallet(
    payment: Payment, conn: Optional[Connection] = None
):
    service_fee_msat = service_fee(payment.amount, internal=payment.is_internal)
    if not settings.lnbits_service_fee_wallet or not service_fee_msat:
        return

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
        conn=conn,
    )
