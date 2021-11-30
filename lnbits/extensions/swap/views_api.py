from http import HTTPStatus
from urllib.parse import urlparse

from fastapi import Request
from fastapi.params import Depends, Query
from starlette.exceptions import HTTPException

from lnbits.core.crud import get_user
from lnbits.core.services import check_invoice_status, create_invoice
from lnbits.decorators import WalletTypeInfo, get_key_type, require_admin_key
from lnbits.extensions.lnaddress.models import CreateAddress, CreateDomain
from lnbits.extensions.swap.models import CreateSwapOut

from . import swap_ext
from .crud import create_swapout, get_swapouts, unique_recurrent_swapout, update_swapout
from .etleneum import create_queue_pay


# SWAP OUT
@swap_ext.get("/api/v1/out")
async def api_swap_outs(
    g: WalletTypeInfo = Depends(get_key_type),
    all_wallets: bool = Query(False),
):
    wallet_ids = [g.wallet.id]

    if all_wallets:
        wallet_ids = (await get_user(g.wallet.user)).wallet_ids

    return [swap.dict() for swap in await get_swapouts(wallet_ids)]

@swap_ext.post("/api/v1/out")
async def api_swapout_create_or_update(
    data: CreateSwapOut,
    wallet: WalletTypeInfo = Depends(require_admin_key)
):
    ## CHECK IF THERE'S ALREADY A RECURRENT SWAP
    if data.recurrent:
        is_unique = await unique_recurrent_swapout(data.wallet)
        if is_unique > 0:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Recurrent Swap Out already exists!"
            )    
            
    swap_out = await create_swapout(data)
    return swap_out.dict()
    