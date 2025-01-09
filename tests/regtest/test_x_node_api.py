import asyncio
import random
from http import HTTPStatus

import bolt11
import pytest
from pydantic import parse_obj_as

from lnbits.nodes.base import ChannelPoint, ChannelState, NodeChannel

from ..helpers import (
    funding_source,
    get_random_invoice_data,
)
from .helpers import (
    get_unconnected_node_uri,
    mine_blocks,
)

pytestmark = pytest.mark.skipif(
    funding_source.__node_cls__ is None,
    reason="Cant test if node implementation isnt available",
)


@pytest.fixture()
async def node_client(client, from_super_user, settings):
    settings.lnbits_node_ui = True
    settings.lnbits_public_node_ui = False
    settings.lnbits_node_ui_transactions = True
    params = client.params
    client.params = {"usr": from_super_user.id}
    yield client
    client.params = params
    settings.lnbits_node_ui = False


@pytest.fixture()
async def public_node_client(node_client, settings):
    settings.lnbits_public_node_ui = True
    yield node_client
    settings.lnbits_public_node_ui = False


@pytest.mark.anyio
async def test_node_info_not_found(client, from_super_user, settings):
    settings.lnbits_node_ui = False
    response = await client.get("/node/api/v1/info", params={"usr": from_super_user.id})
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.anyio
async def test_public_node_info_not_found(node_client):
    response = await node_client.get("/node/public/api/v1/info")
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.anyio
async def test_public_node_info(public_node_client):
    response = await public_node_client.get("/node/public/api/v1/info")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_node_info(node_client):
    response = await node_client.get("/node/api/v1/info")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_node_invoices(inkey_headers_from, node_client):
    data = await get_random_invoice_data()
    response = await node_client.post(
        "/api/v1/payments", json=data, headers=inkey_headers_from
    )
    invoice = response.json()

    response = await node_client.get("/node/api/v1/invoices", params={"limit": 1})
    assert response.status_code == 200
    invoices = response.json()["data"]
    assert len(invoices) == 1
    assert invoices[0]["payment_hash"] == invoice["payment_hash"]


@pytest.mark.anyio
async def test_node_payments(node_client, real_invoice, adminkey_headers_from):
    response = await node_client.post(
        "/api/v1/payments", json=real_invoice, headers=adminkey_headers_from
    )
    assert response.status_code < 300

    response = await node_client.get("/node/api/v1/payments", params={"limit": 1})
    assert response.status_code == 200
    payments = response.json()["data"]
    assert len(payments) == 1
    assert (
        payments[0]["payment_hash"]
        == bolt11.decode(real_invoice["bolt11"]).payment_hash
    )


@pytest.mark.anyio
async def test_get_channel(node_client):
    # lndrest is slow / async with channel commands
    await asyncio.sleep(3)
    response = await node_client.get("/node/api/v1/channels")
    assert response.status_code == 200
    channels = parse_obj_as(list[NodeChannel], response.json())
    ch = random.choice(
        [channel for channel in channels if channel.state == ChannelState.ACTIVE]
    )
    assert ch, "No active channel found"
    assert ch.point, "No channel point found"

    response = await node_client.get(f"/node/api/v1/channels/{ch.id}")
    assert response.status_code == 200

    channel = parse_obj_as(NodeChannel, response.json())
    assert channel.id == ch.id


@pytest.mark.anyio
async def test_set_channel_fees(node_client):
    # lndrest is slow / async with channel commands
    await asyncio.sleep(3)
    response = await node_client.get("/node/api/v1/channels")
    assert response.status_code == 200
    channels = parse_obj_as(list[NodeChannel], response.json())

    ch = random.choice(
        [channel for channel in channels if channel.state == ChannelState.ACTIVE]
    )
    assert ch, "No active channel found"
    assert ch.point, "No channel point found"

    response = await node_client.put(
        f"/node/api/v1/channels/{ch.id}", json={"fee_base_msat": 42, "fee_ppm": 69}
    )
    assert response.status_code == 200

    response = await node_client.get(f"/node/api/v1/channels/{ch.id}")
    assert response.status_code == 200
    channel = parse_obj_as(NodeChannel, response.json())
    assert channel.fee_ppm == 69
    assert channel.fee_base_msat == 42


@pytest.mark.anyio
async def test_channel_management(node_client):
    async def get_channels():
        # lndrest is slow / async with channel commands
        await asyncio.sleep(3)
        response = await node_client.get("/node/api/v1/channels")
        assert response.status_code == 200
        return parse_obj_as(list[NodeChannel], response.json())

    data = await get_channels()
    close = random.choice(
        [channel for channel in data if channel.state == ChannelState.ACTIVE]
    )
    assert close, "No active channel found"
    assert close.point, "No channel point found"

    response = await node_client.delete(
        "/node/api/v1/channels",
        params={"short_id": close.short_id, **close.point.dict()},
    )
    assert response.status_code == 200

    data = await get_channels()
    assert any(
        channel.point == close.point and channel.state == ChannelState.CLOSED
        for channel in data
    )

    response = await node_client.post(
        "/node/api/v1/channels",
        json={
            "peer_id": close.peer_id,
            "funding_amount": 100000,
        },
    )
    assert response.status_code == 200
    created = ChannelPoint(**response.json())

    data = await get_channels()
    assert any(
        channel.point == created and channel.state == ChannelState.PENDING
        for channel in data
    )

    # mine some blocks so that the newly created channel eventually
    # gets confirmed to avoid a situation where no channels are
    # left for testing
    mine_blocks(5)

    await asyncio.sleep(1)


@pytest.mark.anyio
async def test_peer_management(node_client):
    connect_uri = get_unconnected_node_uri()
    peer_id = connect_uri.split("@")[0]
    response = await node_client.post("/node/api/v1/peers", json={"uri": connect_uri})
    assert response.status_code == 200

    response = await node_client.get("/node/api/v1/peers")
    assert response.status_code == 200
    assert any(peer["id"] == peer_id for peer in response.json())

    response = await node_client.delete(f"/node/api/v1/peers/{peer_id}")
    assert response.status_code == 200
    # lndrest is slow to remove the peer
    await asyncio.sleep(0.3)

    response = await node_client.get("/node/api/v1/peers")
    assert response.status_code == 200
    assert not any(peer["id"] == peer_id for peer in response.json())

    response = await node_client.delete(f"/node/api/v1/peers/{peer_id}")
    assert response.status_code == 400


@pytest.mark.anyio
async def test_connect_invalid_uri(node_client):
    response = await node_client.post("/node/api/v1/peers", json={"uri": "invalid"})
    assert response.status_code == 400
