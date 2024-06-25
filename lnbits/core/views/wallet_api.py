from typing import Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
)

from lnbits.core.models import (
    CreateWallet,
    KeyType,
    Wallet,
    WalletConfig,
)
from lnbits.core.tasks import reverse_funding_wallets_ids
from lnbits.decorators import (
    WalletTypeInfo,
    get_key_type,
    require_admin_key,
)

from ..crud import (
    create_wallet,
    delete_wallet,
    update_wallet,
)

wallet_router = APIRouter(prefix="/api/v1/wallet", tags=["Wallet"])


@wallet_router.get("")
async def api_wallet(wallet: WalletTypeInfo = Depends(get_key_type)):
    if wallet.key_type == KeyType.admin:
        return {
            "id": wallet.wallet.id,
            "name": wallet.wallet.name,
            "balance": wallet.wallet.balance_msat,
        }
    else:
        return {"name": wallet.wallet.name, "balance": wallet.wallet.balance_msat}


@wallet_router.put("/{new_name}")
async def api_update_wallet_name(
    new_name: str, wallet: WalletTypeInfo = Depends(require_admin_key)
):
    await update_wallet(wallet.wallet.id, new_name)
    return {
        "id": wallet.wallet.id,
        "name": wallet.wallet.name,
        "balance": wallet.wallet.balance_msat,
    }


@wallet_router.patch("", response_model=Wallet)
async def api_update_wallet(
    name: Optional[str] = Body(None),
    currency: Optional[str] = Body(None),
    config: Optional[WalletConfig] = Body(None),
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    wallet = await update_wallet(wallet.wallet.id, name, currency, config=config)
    await reverse_funding_wallets_ids.put(wallet)
    return wallet


@wallet_router.delete("")
async def api_delete_wallet(
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> None:
    await delete_wallet(
        user_id=wallet.wallet.user,
        wallet_id=wallet.wallet.id,
    )


@wallet_router.post("", response_model=Wallet)
async def api_create_wallet(
    data: CreateWallet,
    wallet: WalletTypeInfo = Depends(require_admin_key),
) -> Wallet:
    return await create_wallet(user_id=wallet.wallet.user, wallet_name=data.name)
