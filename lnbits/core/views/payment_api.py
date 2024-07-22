import asyncio
import uuid
from http import HTTPStatus
from typing import List, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse
from lnurl import decode as lnurl_decode
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from lnbits import bolt11
from lnbits.core.db import db
from lnbits.core.models import (
    CreateInvoice,
    DecodePayment,
    KeyType,
    Payment,
    PaymentFilters,
    PaymentHistoryPoint,
    Wallet,
)
from lnbits.db import Filters, Page
from lnbits.decorators import (
    WalletTypeInfo,
    get_key_type,
    parse_filters,
    require_invoice_key,
)
from lnbits.helpers import generate_filter_params_openapi
from lnbits.settings import settings

from ..crud import (
    DateTrunc,
    get_payments,
    get_payments_history,
    get_payments_paginated,
    get_standalone_payment,
    get_wallet_for_key,
    update_pending_payments,
)
from ..services import (
    check_transaction_status,
    create_invoice,
    fee_reserve_total,
    pay_invoice,
)
from ..tasks import api_invoice_listeners

payment_router = APIRouter(prefix="/api/v1/payments", tags=["Payments"])


@payment_router.get(
    "",
    name="Payment List",
    summary="get list of payments",
    response_description="list of payments",
    response_model=List[Payment],
    openapi_extra=generate_filter_params_openapi(PaymentFilters),
)
async def api_payments(
    wallet: WalletTypeInfo = Depends(get_key_type),
    filters: Filters = Depends(parse_filters(PaymentFilters)),
):
    await update_pending_payments(wallet.wallet.id)
    return await get_payments(
        wallet_id=wallet.wallet.id,
        pending=True,
        complete=True,
        filters=filters,
    )


@payment_router.get(
    "/history",
    name="Get payments history",
    response_model=List[PaymentHistoryPoint],
    openapi_extra=generate_filter_params_openapi(PaymentFilters),
)
async def api_payments_history(
    wallet: WalletTypeInfo = Depends(get_key_type),
    group: DateTrunc = Query("day"),
    filters: Filters[PaymentFilters] = Depends(parse_filters(PaymentFilters)),
):
    await update_pending_payments(wallet.wallet.id)
    return await get_payments_history(wallet.wallet.id, group, filters)


@payment_router.get(
    "/paginated",
    name="Payment List",
    summary="get paginated list of payments",
    response_description="list of payments",
    response_model=Page[Payment],
    openapi_extra=generate_filter_params_openapi(PaymentFilters),
)
async def api_payments_paginated(
    wallet: WalletTypeInfo = Depends(get_key_type),
    filters: Filters = Depends(parse_filters(PaymentFilters)),
):
    await update_pending_payments(wallet.wallet.id)
    page = await get_payments_paginated(
        wallet_id=wallet.wallet.id,
        pending=True,
        complete=True,
        filters=filters,
    )
    return page


async def api_payments_create_invoice(data: CreateInvoice, wallet: Wallet):
    description_hash = b""
    unhashed_description = b""
    memo = data.memo or settings.lnbits_site_title
    if data.description_hash or data.unhashed_description:
        if data.description_hash:
            try:
                description_hash = bytes.fromhex(data.description_hash)
            except ValueError as exc:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail="'description_hash' must be a valid hex string",
                ) from exc
        if data.unhashed_description:
            try:
                unhashed_description = bytes.fromhex(data.unhashed_description)
            except ValueError as exc:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST,
                    detail="'unhashed_description' must be a valid hex string",
                ) from exc
        # do not save memo if description_hash or unhashed_description is set
        memo = ""

    async with db.connect() as conn:
        payment_hash, payment_request = await create_invoice(
            wallet_id=wallet.id,
            amount=data.amount,
            memo=memo,
            currency=data.unit,
            description_hash=description_hash,
            unhashed_description=unhashed_description,
            expiry=data.expiry,
            extra=data.extra,
            webhook=data.webhook,
            internal=data.internal,
            conn=conn,
        )
        # NOTE: we get the checking_id with a seperate query because create_invoice
        # does not return it and it would be a big hustle to change its return type
        # (used across extensions)
        payment_db = await get_standalone_payment(payment_hash, conn=conn)
        assert payment_db is not None, "payment not found"
        checking_id = payment_db.checking_id

    return {
        "payment_hash": payment_hash,
        "payment_request": payment_request,
        # maintain backwards compatibility with API clients:
        "checking_id": checking_id,
    }


@payment_router.post(
    "",
    summary="Create or pay an invoice",
    description="""
        This endpoint can be used both to generate and pay a BOLT11 invoice.
        To generate a new invoice for receiving funds into the authorized account,
        specify at least the first four fields in the POST body: `out: false`,
        `amount`, `unit`, and `memo`. To pay an arbitrary invoice from the funds
        already in the authorized account, specify `out: true` and use the `bolt11`
        field to supply the BOLT11 invoice to be paid.
    """,
    status_code=HTTPStatus.CREATED,
    responses={
        400: {"description": "Invalid BOLT11 string or missing fields."},
        401: {"description": "Invoice (or Admin) key required."},
        520: {"description": "Payment or Invoice error."},
    },
)
async def api_payments_create(
    wallet: WalletTypeInfo = Depends(require_invoice_key),
    invoice_data: CreateInvoice = Body(...),
):
    if invoice_data.out is True and wallet.key_type == KeyType.admin:
        if not invoice_data.bolt11:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="BOLT11 string is invalid or not given",
            )

        payment_hash = await pay_invoice(
            wallet_id=wallet.wallet.id,
            payment_request=invoice_data.bolt11,
            extra=invoice_data.extra,
        )
        return {
            "payment_hash": payment_hash,
            # maintain backwards compatibility with API clients:
            "checking_id": payment_hash,
        }

    elif not invoice_data.out:
        # invoice key
        return await api_payments_create_invoice(invoice_data, wallet.wallet)
    else:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Invoice (or Admin) key required.",
        )


@payment_router.get("/fee-reserve")
async def api_payments_fee_reserve(invoice: str = Query("invoice")) -> JSONResponse:
    invoice_obj = bolt11.decode(invoice)
    if invoice_obj.amount_msat:
        response = {
            "fee_reserve": fee_reserve_total(invoice_obj.amount_msat),
        }
        return JSONResponse(response)
    else:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Invoice has no amount.",
        )


async def subscribe_wallet_invoices(request: Request, wallet: Wallet):
    """
    Subscribe to new invoices for a wallet. Can be wrapped in EventSourceResponse.
    Listenes incoming payments for a wallet and yields jsons with payment details.
    """
    this_wallet_id = wallet.id

    payment_queue: asyncio.Queue[Payment] = asyncio.Queue(0)

    uid = f"{this_wallet_id}_{str(uuid.uuid4())[:8]}"
    logger.debug(f"adding sse listener for wallet: {uid}")
    api_invoice_listeners[uid] = payment_queue

    try:
        while settings.lnbits_running:
            if await request.is_disconnected():
                await request.close()
                break
            payment: Payment = await payment_queue.get()
            if payment.wallet_id == this_wallet_id:
                logger.debug("sse listener: payment received", payment)
                yield {"data": payment.json(), "event": "payment-received"}
    except asyncio.CancelledError:
        logger.debug(f"removing listener for wallet {uid}")
    except Exception as exc:
        logger.error(f"Error in sse: {exc}")
    finally:
        api_invoice_listeners.pop(uid)


@payment_router.get("/sse")
async def api_payments_sse(
    request: Request, wallet: WalletTypeInfo = Depends(get_key_type)
):
    return EventSourceResponse(
        subscribe_wallet_invoices(request, wallet.wallet),
        ping=20,
        media_type="text/event-stream",
    )


# TODO: refactor this route into a public and admin one
@payment_router.get("/{payment_hash}")
async def api_payment(payment_hash, x_api_key: Optional[str] = Header(None)):
    # We use X_Api_Key here because we want this call to work with and without keys
    # If a valid key is given, we also return the field "details", otherwise not
    wallet = await get_wallet_for_key(x_api_key) if isinstance(x_api_key, str) else None

    payment = await get_standalone_payment(
        payment_hash, wallet_id=wallet.id if wallet else None
    )
    if payment is None:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    await check_transaction_status(payment.wallet_id, payment_hash)
    payment = await get_standalone_payment(
        payment_hash, wallet_id=wallet.id if wallet else None
    )
    if not payment:
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Payment does not exist."
        )
    elif not payment.pending:
        if wallet and wallet.id == payment.wallet_id:
            return {"paid": True, "preimage": payment.preimage, "details": payment}
        return {"paid": True, "preimage": payment.preimage}

    try:
        status = await payment.check_status()
    except Exception:
        if wallet and wallet.id == payment.wallet_id:
            return {"paid": False, "details": payment}
        return {"paid": False}

    if wallet and wallet.id == payment.wallet_id:
        return {
            "paid": not payment.pending,
            "status": f"{status!s}",
            "preimage": payment.preimage,
            "details": payment,
        }
    return {"paid": not payment.pending, "preimage": payment.preimage}


@payment_router.post("/decode", status_code=HTTPStatus.OK)
async def api_payments_decode(data: DecodePayment) -> JSONResponse:
    payment_str = data.data
    try:
        if payment_str[:5] == "LNURL":
            url = str(lnurl_decode(payment_str))
            return JSONResponse({"domain": url})
        else:
            invoice = bolt11.decode(payment_str)
            return JSONResponse(invoice.data)
    except Exception as exc:
        return JSONResponse(
            {"message": f"Failed to decode: {exc!s}"},
            status_code=HTTPStatus.BAD_REQUEST,
        )
