import asyncio
from distutils.log import error
import json
from binascii import unhexlify
from io import BytesIO
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import Depends
from lnurl import LnurlErrorResponse
from lnurl import decode as decode_lnurl  # type: ignore
from loguru import logger

from lnbits import bolt11
from lnbits.db import Connection
from lnbits.decorators import (
    WalletTypeInfo,
    get_key_type,
    require_admin_key,
    require_invoice_key,
)
from lnbits.helpers import url_for, urlsafe_short_hash
from lnbits.requestvars import g
from lnbits.settings import FAKE_WALLET, RESERVE_FEE_MIN, RESERVE_FEE_PERCENT, WALLET
from lnbits.wallets.base import PaymentResponse, PaymentStatus

from . import db
from .crud import (
    check_internal,
    create_payment,
    delete_payment,
    get_standalone_payment,
    get_wallet,
    get_wallet_payment,
    update_payment_details,
    update_payment_status,
)
from .models import Payment

try:
    from typing import TypedDict  # type: ignore
except ImportError:  # pragma: nocover
    from typing_extensions import TypedDict


class PaymentFailure(Exception):
    pass


class InvoiceFailure(Exception):
    pass


async def create_invoice(
    *,
    wallet_id: str,
    amount: int,  # in satoshis
    memo: str,
    description_hash: Optional[bytes] = None,
    unhashed_description: Optional[bytes] = None,
    extra: Optional[Dict] = None,
    webhook: Optional[str] = None,
    internal: Optional[bool] = False,
    conn: Optional[Connection] = None,
) -> Tuple[str, str]:
    invoice_memo = None if description_hash else memo

    # use the fake wallet if the invoice is for internal use only
    wallet = FAKE_WALLET if internal else WALLET

    ok, checking_id, payment_request, error_message = await wallet.create_invoice(
        amount=amount,
        memo=invoice_memo,
        description_hash=description_hash,
        unhashed_description=unhashed_description,
    )
    if not ok:
        raise InvoiceFailure(error_message or "unexpected backend error.")

    invoice = bolt11.decode(payment_request)

    amount_msat = amount * 1000
    await create_payment(
        wallet_id=wallet_id,
        checking_id=checking_id,
        payment_request=payment_request,
        payment_hash=invoice.payment_hash,
        amount=amount_msat,
        memo=memo,
        extra=extra,
        webhook=webhook,
        conn=conn,
    )

    return invoice.payment_hash, payment_request

async def create_hold_invoice(
    *,
    wallet_id: str,
    amount: int,  # in satoshis
    memo: str,
    hash: Optional[bytes] = None,
    description_hash: Optional[bytes] = None,
    extra: Optional[Dict] = None,
    webhook: Optional[str] = None,
    conn: Optional[Connection] = None,
) -> Tuple[str, str]:
    invoice_memo = None if description_hash else memo

    ok, checking_id, payment_request, error_message = await WALLET.create_hold_invoice(
        amount=amount, memo=invoice_memo, hash=hash, description_hash=description_hash
    )
    if not ok:
        raise InvoiceFailure(error_message or "Unexpected backend error.")

    invoice = bolt11.decode(payment_request)

    amount_msat = amount * 1000
    await create_payment(
        wallet_id=wallet_id,
        checking_id=checking_id,
        payment_request=payment_request,
        payment_hash=invoice.payment_hash,
        amount=amount_msat,
        memo=memo,
        extra=extra,
        webhook=webhook,
        conn=conn,
    )

    return invoice.payment_hash, payment_request

async def settle_hold_invoice(
    *,
    wallet_id: str,
    preimage: bytes,
    conn: Optional[Connection] = None,
) -> bool:
    ok = await WALLET.settle_hold_invoice(
        preimage=preimage
    )
    if not ok:
        raise InvoiceFailure("Unexpected backend error.")

    return True

async def cancel_hold_invoice(
    *,
    wallet_id: str,
    payment_hash: bytes,
    conn: Optional[Connection] = None,
) -> bool:
    ok = await WALLET.cancel_hold_invoice(
        payment_hash=payment_hash
    )
    if not ok:
        raise InvoiceFailure("Unexpected backend error.")

    return True

async def subscribe_hold_invoice(
    *,
    wallet_id: str,
    payment_hash: bytes,
    conn: Optional[Connection] = None,
) -> bool:
    payment = await get_standalone_payment(payment_hash, incoming=True)
    asyncio.create_task(WALLET.hold_invoices_stream(
        payment_hash=payment_hash, webhook=payment.webhook
    ))

    return True

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
    First, we create a temporary payment in the database with fees set to the reserve fee.
    We then check whether the balance of the payer would go negative.
    We then attempt to pay the invoice through the backend.
    If the payment is successful, we update the payment in the database with the payment details.
    If the payment is unsuccessful, we delete the temporary payment.
    If the payment is still in flight, we hope that some other process will regularly check for the payment.
    """
    invoice = bolt11.decode(payment_request)
    fee_reserve_msat = fee_reserve(invoice.amount_msat)
    async with (db.reuse_conn(conn) if conn else db.connect()) as conn:
        temp_id = invoice.payment_hash
        internal_id = f"internal_{invoice.payment_hash}"

        if invoice.amount_msat == 0:
            raise ValueError("Amountless invoices not supported.")
        if max_sat and invoice.amount_msat > max_sat * 1000:
            raise ValueError("Amount in invoice is too high.")

        # put all parameters that don't change here
        class PaymentKwargs(TypedDict):
            wallet_id: str
            payment_request: str
            payment_hash: str
            amount: int
            memo: str
            extra: Optional[Dict]

        payment_kwargs: PaymentKwargs = PaymentKwargs(
            wallet_id=wallet_id,
            payment_request=payment_request,
            payment_hash=invoice.payment_hash,
            amount=-invoice.amount_msat,
            memo=description or invoice.description or "",
            extra=extra,
        )

        # check_internal() returns the checking_id of the invoice we're waiting for
        internal_checking_id = await check_internal(invoice.payment_hash, conn=conn)
        if internal_checking_id:
            logger.debug(f"creating temporary internal payment with id {internal_id}")
            # create a new payment from this wallet
            await create_payment(
                checking_id=internal_id,
                fee=0,
                pending=False,
                conn=conn,
                **payment_kwargs,
            )
        else:
            logger.debug(f"creating temporary payment with id {temp_id}")
            # create a temporary payment here so we can check if
            # the balance is enough in the next step
            await create_payment(
                checking_id=temp_id,
                fee=-fee_reserve_msat,
                conn=conn,
                **payment_kwargs,
            )

        # do the balance check
        wallet = await get_wallet(wallet_id, conn=conn)
        assert wallet
        if wallet.balance_msat < 0:
            logger.debug("balance is too low, deleting temporary payment")
            if not internal_checking_id and wallet.balance_msat > -fee_reserve_msat:
                raise PaymentFailure(
                    f"You must reserve at least ({round(fee_reserve_msat/1000)} sat) to cover potential routing fees."
                )
            raise PermissionError("Insufficient balance.")

    if internal_checking_id:
        logger.debug(f"marking temporary payment as not pending {internal_checking_id}")
        # mark the invoice from the other side as not pending anymore
        # so the other side only has access to his new money when we are sure
        # the payer has enough to deduct from
        async with db.connect() as conn:
            await update_payment_status(
                checking_id=internal_checking_id, pending=False, conn=conn
            )

        # notify receiver asynchronously

        from lnbits.tasks import internal_invoice_queue

        await internal_invoice_queue.put(internal_checking_id)
    else:
        logger.debug(f"backend: sending payment {temp_id}")
        # actually pay the external invoice
        payment: PaymentResponse = await WALLET.pay_invoice(
            payment_request, fee_reserve_msat
        )

        if payment.checking_id and payment.checking_id != temp_id:
            logger.warning(
                f"backend sent unexpected checking_id (expected: {temp_id} got: {payment.checking_id})"
            )

        logger.debug(f"backend: pay_invoice finished {temp_id}")
        if payment.checking_id and payment.ok != False:
            # payment.ok can be True (paid) or None (pending)!
            logger.debug(f"updating payment {temp_id}")
            async with db.connect() as conn:
                await update_payment_details(
                    checking_id=temp_id,
                    pending=payment.ok != True,
                    fee=payment.fee_msat,
                    preimage=payment.preimage,
                    new_checking_id=payment.checking_id,
                    conn=conn,
                )
                logger.debug(f"payment successful {payment.checking_id}")
        elif payment.checking_id is None and payment.ok == False:
            # payment failed
            logger.warning(f"backend sent payment failure")
            async with db.connect() as conn:
                logger.debug(f"deleting temporary payment {temp_id}")
                await delete_payment(temp_id, conn=conn)
            raise PaymentFailure(
                f"payment failed: {payment.error_message}"
                or "payment failed, but backend didn't give us an error message"
            )
        else:
            logger.warning(
                f"didn't receive checking_id from backend, payment may be stuck in database: {temp_id}"
            )

    return invoice.payment_hash


async def redeem_lnurl_withdraw(
    wallet_id: str,
    lnurl_request: str,
    memo: Optional[str] = None,
    extra: Optional[Dict] = None,
    wait_seconds: int = 0,
    conn: Optional[Connection] = None,
) -> None:
    if not lnurl_request:
        return None

    res = {}

    async with httpx.AsyncClient() as client:
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
    except:
        logger.warning(
            f"failed to create invoice on redeem_lnurl_withdraw from {lnurl}. params: {res}"
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

    async with httpx.AsyncClient() as client:
        try:
            await client.get(res["callback"], params=params)
        except Exception:
            pass


async def perform_lnurlauth(
    callback: str,
    wallet: WalletTypeInfo = Depends(require_admin_key),
    conn: Optional[Connection] = None,
) -> Optional[LnurlErrorResponse]:
    cb = urlparse(callback)

    k1 = unhexlify(parse_qs(cb.query)["k1"][0])

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

    def encode_strict_der(r_int, s_int, order):
        # if s > order/2 verification will fail sometimes
        # so we must fix it here (see https://github.com/indutny/elliptic/blob/e71b2d9359c5fe9437fbf46f1f05096de447de57/lib/elliptic/ec/index.js#L146-L147)
        if s_int > order // 2:
            s_int = order - s_int

        # now we do the strict DER encoding copied from
        # https://github.com/KiriKiri/bip66 (without any checks)
        r = int_to_bytes_suitable_der(r_int)
        s = int_to_bytes_suitable_der(s_int)

        r_len = len(r)
        s_len = len(s)
        sign_len = 6 + r_len + s_len

        signature = BytesIO()
        signature.write(0x30.to_bytes(1, "big", signed=False))
        signature.write((sign_len - 2).to_bytes(1, "big", signed=False))
        signature.write(0x02.to_bytes(1, "big", signed=False))
        signature.write(r_len.to_bytes(1, "big", signed=False))
        signature.write(r)
        signature.write(0x02.to_bytes(1, "big", signed=False))
        signature.write(s_len.to_bytes(1, "big", signed=False))
        signature.write(s)

        return signature.getvalue()

    sig = key.sign_digest_deterministic(k1, sigencode=encode_strict_der)

    async with httpx.AsyncClient() as client:
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
        return PaymentStatus(None)
    if not payment.pending:
        # note: before, we still checked the status of the payment again
        return PaymentStatus(True)

    status: PaymentStatus = await payment.check_status()
    return status


# WARN: this same value must be used for balance check and passed to WALLET.pay_invoice(), it may cause a vulnerability if the values differ
def fee_reserve(amount_msat: int) -> int:
    return max(int(RESERVE_FEE_MIN), int(amount_msat * RESERVE_FEE_PERCENT / 100.0))
