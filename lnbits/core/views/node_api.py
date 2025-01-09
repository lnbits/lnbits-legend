from http import HTTPStatus
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from lnbits.decorators import check_admin, check_super_user, parse_filters
from lnbits.nodes import get_node_class
from lnbits.settings import settings

from ...db import Filters, Page
from ...nodes.base import (
    ChannelPoint,
    Node,
    NodeChannel,
    NodeInfoResponse,
    NodeInvoice,
    NodeInvoiceFilters,
    NodePayment,
    NodePaymentsFilters,
    NodePeerInfo,
    PublicNodeInfo,
)
from ...utils.cache import cache


def require_node():
    node_class = get_node_class()
    if not node_class:
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail="Active backend does not implement Node API",
        )
    if not settings.lnbits_node_ui:
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Not enabled",
        )
    return node_class


def check_public():
    if not (settings.lnbits_node_ui and settings.lnbits_public_node_ui):
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            detail="Not enabled",
        )


node_router = APIRouter(
    tags=["Node Managment"],
    prefix="/node/api/v1",
    dependencies=[Depends(check_admin)],
)
super_node_router = APIRouter(
    tags=["Node Managment"],
    prefix="/node/api/v1",
    dependencies=[Depends(check_super_user)],
)
public_node_router = APIRouter(
    tags=["Node Managment"],
    prefix="/node/public/api/v1",
    dependencies=[Depends(check_public)],
)


@node_router.get(
    "/ok",
    description="Check if node api can be enabled",
    status_code=HTTPStatus.OK,
    dependencies=[Depends(require_node)],
)
async def api_get_ok():
    pass


@public_node_router.get("/info", response_model=PublicNodeInfo)
async def api_get_public_info(node: Node = Depends(require_node)) -> PublicNodeInfo:
    return await cache.save_result(node.get_public_info, key="node:public_info")


@node_router.get("/info")
async def api_get_info(
    node: Node = Depends(require_node),
) -> Optional[NodeInfoResponse]:
    return await node.get_info()


@node_router.get("/channels")
async def api_get_channels(
    node: Node = Depends(require_node),
) -> Optional[list[NodeChannel]]:
    return await node.get_channels()


@node_router.get("/channels/{channel_id}")
async def api_get_channel(
    channel_id: str,
    node: Node = Depends(require_node),
) -> Optional[NodeChannel]:
    return await node.get_channel(channel_id)


@super_node_router.post("/channels", response_model=ChannelPoint)
async def api_create_channel(
    node: Node = Depends(require_node),
    peer_id: str = Body(),
    funding_amount: int = Body(),
    push_amount: Optional[int] = Body(None),
    fee_rate: Optional[int] = Body(None),
):
    return await node.open_channel(peer_id, funding_amount, push_amount, fee_rate)


@super_node_router.delete("/channels")
async def api_delete_channel(
    short_id: Optional[str],
    funding_txid: Optional[str],
    output_index: Optional[int],
    force: bool = False,
    node: Node = Depends(require_node),
) -> Optional[list[NodeChannel]]:
    return await node.close_channel(
        short_id,
        (
            ChannelPoint(funding_txid=funding_txid, output_index=output_index)
            if funding_txid is not None and output_index is not None
            else None
        ),
        force,
    )


@super_node_router.put("/channels/{channel_id}")
async def api_set_channel_fees(
    channel_id: str,
    node: Node = Depends(require_node),
    fee_ppm: int = Body(None),
    fee_base_msat: int = Body(None),
):
    await node.set_channel_fee(channel_id, fee_base_msat, fee_ppm)


@node_router.get("/payments")
async def api_get_payments(
    node: Node = Depends(require_node),
    filters: Filters = Depends(parse_filters(NodePaymentsFilters)),
) -> Optional[Page[NodePayment]]:
    if not settings.lnbits_node_ui_transactions:
        raise HTTPException(
            HTTP_503_SERVICE_UNAVAILABLE,
            detail="You can enable node transactions in the Admin UI",
        )
    return await node.get_payments(filters)


@node_router.get("/invoices")
async def api_get_invoices(
    node: Node = Depends(require_node),
    filters: Filters = Depends(parse_filters(NodeInvoiceFilters)),
) -> Optional[Page[NodeInvoice]]:
    if not settings.lnbits_node_ui_transactions:
        raise HTTPException(
            HTTP_503_SERVICE_UNAVAILABLE,
            detail="You can enable node transactions in the Admin UI",
        )
    return await node.get_invoices(filters)


@node_router.get("/peers")
async def api_get_peers(node: Node = Depends(require_node)) -> list[NodePeerInfo]:
    return await node.get_peers()


@super_node_router.post("/peers")
async def api_connect_peer(
    uri: str = Body(embed=True), node: Node = Depends(require_node)
):
    return await node.connect_peer(uri)


@super_node_router.delete("/peers/{peer_id}")
async def api_disconnect_peer(peer_id: str, node: Node = Depends(require_node)):
    return await node.disconnect_peer(peer_id)


class NodeRank(BaseModel):
    capacity: Optional[int]
    channelcount: Optional[int]
    age: Optional[int]
    growth: Optional[int]
    availability: Optional[int]


# Same for public and private api
@node_router.get(
    "/rank",
    description="Retrieve node ranks from https://1ml.com",
    response_model=Optional[NodeRank],
)
@public_node_router.get(
    "/rank",
    description="Retrieve node ranks from https://1ml.com",
    response_model=Optional[NodeRank],
)
async def api_get_1ml_stats(node: Node = Depends(require_node)) -> Optional[NodeRank]:
    node_id = await node.get_id()
    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(headers=headers) as client:
        r = await client.get(url=f"https://1ml.com/node/{node_id}/json", timeout=15)
        try:
            r.raise_for_status()
            return r.json()["noderank"]
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="Node not found on 1ml.com"
            ) from exc
