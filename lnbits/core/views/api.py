from http import HTTPStatus
from io import BytesIO
from typing import List

import pyqrcode
from fastapi import (
    APIRouter,
    Depends,
)
from fastapi.exceptions import HTTPException
from starlette.responses import StreamingResponse

from lnbits.core.models import (
    BaseWallet,
    ConversionData,
    CreateWallet,
    User,
    Wallet,
)
from lnbits.decorators import (
    check_user_exists,
)
from lnbits.settings import settings
from lnbits.utils.exchange_rates import (
    allowed_currencies,
    fiat_amount_as_satoshis,
    satoshis_amount_as_fiat,
)

from ..services import create_user_account

# backwards compatibility for extension
# TODO: remove api_payment and pay_invoice imports from extensions
from .payment_api import api_payment, pay_invoice  # noqa: F401

api_router = APIRouter(tags=["Core"])


@api_router.get("/api/v1/health", status_code=HTTPStatus.OK)
async def health():
    return


@api_router.get(
    "/api/v1/wallets",
    name="Wallets",
    description="Get basic info for all of user's wallets.",
)
async def api_wallets(user: User = Depends(check_user_exists)) -> List[BaseWallet]:
    return [BaseWallet(**w.dict()) for w in user.wallets]


@api_router.post("/api/v1/account", response_model=Wallet)
async def api_create_account(data: CreateWallet) -> Wallet:
    if not settings.new_accounts_allowed:
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail="Account creation is disabled.",
        )
    account = await create_user_account(wallet_name=data.name)
    return account.wallets[0]


@api_router.get("/api/v1/currencies")
async def api_list_currencies_available() -> List[str]:
    return allowed_currencies()


@api_router.post("/api/v1/conversion")
async def api_fiat_as_sats(data: ConversionData):
    output = {}
    if data.from_ == "sat":
        output["BTC"] = data.amount / 100000000
        output["sats"] = int(data.amount)
        for currency in data.to.split(","):
            output[currency.strip().upper()] = await satoshis_amount_as_fiat(
                data.amount, currency.strip()
            )
        return output
    else:
        output[data.from_.upper()] = data.amount
        output["sats"] = await fiat_amount_as_satoshis(data.amount, data.from_)
        output["BTC"] = output["sats"] / 100000000
        return output


@api_router.get("/api/v1/qrcode/{data}", response_class=StreamingResponse)
async def img(data):
    qr = pyqrcode.create(data)
    stream = BytesIO()
    qr.svg(stream, scale=3)
    stream.seek(0)

    async def _generator(stream: BytesIO):
        yield stream.getvalue()

    return StreamingResponse(
        _generator(stream),
        headers={
            "Content-Type": "image/svg+xml",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
