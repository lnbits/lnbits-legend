from http import HTTPStatus
from typing import Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
)

from lnbits.core.models import (
    CreateWallet,
    KeyType,
    Wallet,
)
from lnbits.decorators import (
    WalletTypeInfo,
    require_admin_key,
    require_invoice_key,
)

from ..crud import (
    create_wallet,
    delete_wallet,
    get_wallet,
    update_wallet,
)

wallet_router = APIRouter(prefix="/api/v1/wallet", tags=["Wallet"])


@wallet_router.get("")
async def api_wallet(key_info: WalletTypeInfo = Depends(require_invoice_key)):
    res = {
        "name": key_info.wallet.name,
        "balance": key_info.wallet.balance_msat,
    }
    if key_info.key_type == KeyType.admin:
        res["id"] = key_info.wallet.id
    return res


@wallet_router.put("/{new_name}")
async def api_update_wallet_name(
    new_name: str, key_info: WalletTypeInfo = Depends(require_admin_key)
):
    wallet = await get_wallet(key_info.wallet.id)
    if not wallet:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Wallet not found")
    wallet.name = new_name
    await update_wallet(wallet)
    return {
        "id": wallet.id,
        "name": wallet.name,
        "balance": wallet.balance_msat,
    }


@wallet_router.patch("")
async def api_update_wallet(
    name: Optional[str] = Body(None),
    currency: Optional[str] = Body(None),
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> Wallet:
    wallet = await get_wallet(key_info.wallet.id)
    if not wallet:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Wallet not found")
    wallet.name = name or wallet.name
    wallet.currency = currency if currency is not None else wallet.currency
    await update_wallet(wallet)
    return wallet


@wallet_router.delete("")
async def api_delete_wallet(
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> None:
    await delete_wallet(
        user_id=wallet.wallet.user,
        wallet_id=wallet.wallet.id,
    )


@wallet_router.post("")
async def api_create_wallet(
    data: CreateWallet,
    key_info: WalletTypeInfo = Depends(require_admin_key),
) -> Wallet:
    return await create_wallet(user_id=key_info.wallet.user, wallet_name=data.name)
