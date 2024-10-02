import pytest

from lnbits.core.services import (
    fee_reserve,
    fee_reserve_total,
    service_fee,
)
from lnbits.settings import Settings


@pytest.mark.asyncio
async def test_fee_reserve_internal():
    fee = fee_reserve(10_000, internal=True)
    assert fee == 0


@pytest.mark.asyncio
async def test_fee_reserve_min(settings: Settings):
    settings.lnbits_reserve_fee_percent = 2
    settings.lnbits_reserve_fee_min = 500
    fee = fee_reserve(10000)
    assert fee == 500


@pytest.mark.asyncio
async def test_fee_reserve_percent(settings: Settings):
    settings.lnbits_reserve_fee_percent = 1
    settings.lnbits_reserve_fee_min = 100
    fee = fee_reserve(100000)
    assert fee == 1000


@pytest.mark.asyncio
async def test_service_fee_no_wallet(settings: Settings):
    settings.lnbits_service_fee_wallet = ""
    fee = service_fee(10000)
    assert fee == 0


@pytest.mark.asyncio
async def test_service_fee_internal(settings: Settings):
    settings.lnbits_service_fee_wallet = "wallet_id"
    settings.lnbits_service_fee_ignore_internal = True
    fee = service_fee(10000, internal=True)
    assert fee == 0


@pytest.mark.asyncio
async def test_service_fee(settings: Settings):
    settings.lnbits_service_fee_wallet = "wallet_id"
    settings.lnbits_service_fee = 2
    fee = service_fee(10000)
    assert fee == 200


@pytest.mark.asyncio
async def test_service_fee_max(settings: Settings):
    settings.lnbits_service_fee_wallet = "wallet_id"
    settings.lnbits_service_fee = 2
    settings.lnbits_service_fee_max = 199
    fee = service_fee(100_000_000)
    assert fee / 1000 == 199


@pytest.mark.asyncio
async def test_fee_reserve_total(settings: Settings):
    settings.lnbits_reserve_fee_percent = 1
    settings.lnbits_reserve_fee_min = 100
    settings.lnbits_service_fee = 2
    settings.lnbits_service_fee_wallet = "wallet_id"
    amount = 100_000
    fee = service_fee(amount)
    reserve = fee_reserve(amount)
    total = fee_reserve_total(amount)
    assert fee + reserve == total
