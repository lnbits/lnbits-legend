import asyncio
import json
from collections.abc import Callable
from http import HTTPStatus
from io import BytesIO
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from lnurl import LnurlErrorResponse
from lnurl import decode as decode_lnurl
from loguru import logger

from lnbits.db import Connection
from lnbits.decorators import (
    WalletTypeInfo,
    require_admin_key,
)
from lnbits.exceptions import InvoiceError, PaymentError
from lnbits.helpers import url_for
from lnbits.settings import settings

from .payments import create_invoice


class LnurlErrorResponseHandler(APIRoute):
    """
    Custom APIRoute class to handle LNURL errors.
    LNURL errors always return with status 200 and
    a JSON response with `status="ERROR"` and a `reason` key.
    Helps to catch HTTPException and return a valid lnurl error response
    Example:
    withdraw_lnurl_router = APIRouter(prefix="/api/v1/lnurl")
    withdraw_lnurl_router.route_class = LnurlErrorResponseHandler
    """

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def lnurl_route_handler(request: Request) -> Response:
            try:
                response = await original_route_handler(request)
                return response
            except (InvoiceError, PaymentError) as exc:
                logger.debug(f"Wallet Error: {exc}")
                response = JSONResponse(
                    status_code=HTTPStatus.OK,
                    content={"status": "ERROR", "reason": f"{exc.message}"},
                )
                return response
            except HTTPException as exc:
                logger.debug(f"HTTPException: {exc}")
                response = JSONResponse(
                    status_code=HTTPStatus.OK,
                    content={"status": "ERROR", "reason": f"{exc.detail}"},
                )
                return response
            except Exception as exc:
                logger.error("Unknown Error:", exc)
                response = JSONResponse(
                    status_code=HTTPStatus.OK,
                    content={
                        "status": "ERROR",
                        "reason": f"UNKNOWN ERROR: {exc!s}",
                    },
                )
                return response

        return lnurl_route_handler


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
