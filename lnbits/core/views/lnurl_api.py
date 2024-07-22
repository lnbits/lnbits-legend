import json
from http import HTTPStatus

from fastapi import (
    APIRouter,
    Depends,
)
from fastapi.exceptions import HTTPException
from lnurl import (
    LnurlPayActionResponse,
    LnurlSuccessResponse,
    execute_login,
    execute_pay_request,
)
from lnurl import handle as lnurl_handle
from lnurl.core import execute_withdraw
from lnurl.exceptions import InvalidLnurl

from lnbits.core.models import CreateLnurlAuth, CreateLnurlPay, CreateLnurlWithdraw
from lnbits.core.services import create_invoice, pay_invoice
from lnbits.decorators import WalletTypeInfo, require_admin_key
from lnbits.settings import settings
from lnbits.utils.exchange_rates import fiat_amount_as_satoshis

lnurl_router = APIRouter(tags=["LNURL"])


@lnurl_router.get("/api/v1/lnurlscan/{code}")
@lnurl_router.get("/lnurl/api/v1/scan/{code}")
async def api_lnurl_scan(code: str) -> dict:
    try:
        handle = await lnurl_handle(code, user_agent=settings.user_agent)
        return handle.dict()
    except InvalidLnurl as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Invalid LNURL",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Error processing LNURL",
        ) from exc


@lnurl_router.post("/lnurl/api/v1/auth")
async def api_lnurl_auth(
    data: CreateLnurlAuth, key_type: WalletTypeInfo = Depends(require_admin_key)
):
    try:
        res = await execute_login(data.auth_response, key_type.wallet.adminkey)
        assert isinstance(
            res, LnurlSuccessResponse
        ), "unexpected response from execute_login"
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Failed to auth, try new LNURL",
        ) from exc


@lnurl_router.post("/lnurl/api/v1/withdraw")
async def api_lnurl_withdraw(
    data: CreateLnurlWithdraw, key_type: WalletTypeInfo = Depends(require_admin_key)
):
    try:
        payment_hash, payment_request = await create_invoice(
            wallet_id=key_type.wallet.id,
            amount=data.amount / 1000,
            memo=data.memo or "",
            extra={"tag": "lnurl-withdraw"},
        )
        res = await execute_withdraw(data.withdraw_response, payment_request)
        assert isinstance(
            res, LnurlSuccessResponse
        ), "unexpected response from execute_withdraw"
        return {"payment_hash": payment_hash}
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Failed to withdraw: {exc}",
        ) from exc


@lnurl_router.post("/api/v1/payments/lnurl")
@lnurl_router.post("/lnurl/api/v1/pay")
async def api_lnurl_pay(
    data: CreateLnurlPay, key_type: WalletTypeInfo = Depends(require_admin_key)
):
    amount_msat = data.amount
    if data.unit and data.unit != "sat":
        amount_msat = await fiat_amount_as_satoshis(data.amount, data.unit)
        # no msat precision, why?
        amount_msat = int(amount_msat // 1000) * 1000

    description = None
    metadata = json.loads(data.pay_response.metadata)
    for x in metadata:
        if x[0] == "text/plain":
            description = x[1]

    if not description:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="description required on LNURL pay_response.",
        )

    # pay
    # params.update(
    #     description_hash=hashlib.sha256(
    #         data["metadata"].encode()
    #     ).hexdigest()
    # )
    # metadata = json.loads(data["metadata"])
    # for [k, v] in metadata:
    #     if k == "text/plain":
    #         params.update(description=v)
    #     if k in ("image/jpeg;base64", "image/png;base64"):
    #         data_uri = f"data:{k},{v}"
    #         params.update(image=data_uri)
    #     if k in ("text/email", "text/identifier"):
    #         params.update(targetUser=v)

    try:
        res = await execute_pay_request(data.pay_response, str(amount_msat))
        assert isinstance(
            res, LnurlPayActionResponse
        ), "unexpected response from execute_pay_request"
    except Exception as exc:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"Failed to fetch invoice: {exc}",
        ) from exc

    extra: dict = {}
    if res.success_action:
        extra["success_action"] = res.success_action.json()
    if data.comment:
        extra["comment"] = data.comment
    if data.unit and data.unit != "sat":
        extra["fiat_currency"] = data.unit
        extra["fiat_amount"] = data.amount / 1000

    payment_hash = await pay_invoice(
        wallet_id=key_type.wallet.id,
        payment_request=res.pr,
        description=description,
        extra=extra,
    )
    return {
        "success_action": res.success_action,
        "payment_hash": payment_hash,
        # maintain backwards compatibility with API clients:
        "checking_id": payment_hash,
    }
