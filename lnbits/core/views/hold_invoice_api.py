import binascii
from http import HTTPStatus

from bolt11 import decode as bolt11_decode
from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
)

from lnbits.core.db import db
from lnbits.core.models import (
    CancelInvoice,
    CreateHoldInvoice,
    SettleInvoice,
)
from lnbits.core.services import (
    cancel_hold_invoice,
    create_hold_invoice,
    settle_hold_invoice,
    subscribe_hold_invoice,
)
from lnbits.decorators import (
    WalletTypeInfo,
    require_invoice_key,
    require_admin_key,
)
from lnbits.exceptions import InvoiceError
from lnbits.settings import settings
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis

hold_invoice_router = APIRouter(prefix="/api/v1/hold_invoice", tags=["Hold Invoice"])


@hold_invoice_router.post(
    "/create",
    summary="Create a hold invoice",
    description="""
        This endpoint can be used to generate a BOLT11 hold invoice.
        Specify at least the first four fields in the POST body: `out: false`,
        `amount`, `unit`, and `memo`.
    """,
    status_code=HTTPStatus.CREATED,
    responses={
        400: {"description": "Missing fields."},
        520: {"description": "Payment or Invoice error."},
    },
)
async def api_hold_invoice_create(
    wallet: WalletTypeInfo = Depends(require_invoice_key),
    data: CreateHoldInvoice = Body(...),
):

    if data.description_hash:
        description_hash = binascii.unhexlify(data.description_hash)
        memo = ""
    else:
        description_hash = b""
        memo = data.memo or settings.lnbits_site_title
    if data.unit == "sat":
        amount = int(data.amount)
    else:
        price_in_sats = await fiat_amount_as_satoshis(data.amount, data.unit)
        amount = price_in_sats
    if not data.hash:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Hash is required for hold invoices",
        )
    rhash = binascii.unhexlify(data.hash)

    async with db.connect() as conn:
        try:
            _, payment_request = await create_hold_invoice(
                wallet_id=wallet.wallet.id,
                amount=amount,
                rhash=rhash,
                memo=memo,
                description_hash=description_hash,
                extra=data.extra,
                webhook=data.webhook,
                conn=conn,
            )
        except InvoiceError as e:
            raise HTTPException(status_code=520, detail=str(e))
        except Exception as exc:
            raise exc

    invoice = bolt11_decode(payment_request)

    await subscribe_hold_invoice_internal(
        payment_hash=data.hash,
    )

    return {
        "payment_hash": invoice.payment_hash,
        "payment_request": payment_request,
    }


@hold_invoice_router.post(
    "/settle",
    summary="Settle a hold invoice",
    description="""
        This endpoint can be used to settle a hold invoice.
    """,
    status_code=HTTPStatus.OK,
    responses={
        400: {"description": "Invalid preimage."},
        520: {"description": "Invoice error."},
    },
)
async def api_hold_invoice_settle(
    wallet: WalletTypeInfo = Depends(require_admin_key),
    data: SettleInvoice = Body(...),
):

    # Validate preimage length (32 bytes = 64 hex characters)
    if len(data.preimage) != 64:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Invalid preimage length. Must be 32 bytes (64 hex characters)",
        )

    try:
        settle_result = await settle_hold_invoice(
            preimage=binascii.unhexlify(data.preimage),
        )
    except InvoiceError as e:
        raise HTTPException(status_code=520, detail=str(e))
    except Exception as exc:
        raise exc

    return {
        "settle_result": str(settle_result),
    }


@hold_invoice_router.post(
    "/cancel",
    summary="Cancel a hold invoice",
    description="""
        This endpoint can be used to cancel a hold invoice.
    """,
    status_code=HTTPStatus.OK,
    responses={
        400: {"description": "Invalid payment hash."},
        520: {"description": "Invoice error."},
    },
)
async def api_hold_invoice_cancel(
    wallet: WalletTypeInfo = Depends(require_admin_key),
    data: CancelInvoice = Body(...),
):

    # Validate payment_hash length (32 bytes = 64 hex characters)
    if len(data.payment_hash) != 64:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Invalid payment hash length. Must be 32 bytes (64 hex characters)",
        )

    try:
        cancel_result = await cancel_hold_invoice(
            wallet_id=wallet.wallet.id,
            payment_hash=binascii.unhexlify(data.payment_hash),
        )
    except InvoiceError as e:
        raise HTTPException(status_code=520, detail=str(e))
    except Exception as exc:
        raise exc

    return {
        "cancel_result": str(cancel_result),
    }


# Subscribe to a hold invoice is not exposed to the public API but only for
# internal use. It fetches the payment from the database and then reports to
# webhook which was supplied in the create_hold_invoice call.
async def subscribe_hold_invoice_internal(
    payment_hash: str,
):
    try:
        subscribe_result = await subscribe_hold_invoice(
            payment_hash=payment_hash,
        )
    except Exception as exc:
        raise exc

    return {
        "subscribe_result": str(subscribe_result),
    }
