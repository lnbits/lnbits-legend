import pytest
from bolt11 import decode

from lnbits.core.services import (
    PaymentStatus,
    create_invoice,
)
from lnbits.wallets import get_funding_source

description = "test create invoice"


@pytest.mark.asyncio
async def test_create_invoice(from_wallet):
    payment = await create_invoice(
        wallet_id=from_wallet.id,
        amount=1000,
        memo=description,
    )
    invoice = decode(payment.bolt11)
    assert invoice.payment_hash == payment.payment_hash
    assert invoice.amount_msat == 1000000
    assert invoice.description == description

    funding_source = get_funding_source()
    status = await funding_source.get_invoice_status(payment.payment_hash)
    assert isinstance(status, PaymentStatus)
    assert status.pending


@pytest.mark.asyncio
async def test_create_internal_invoice(from_wallet):
    payment = await create_invoice(
        wallet_id=from_wallet.id, amount=1000, memo=description, internal=True
    )
    invoice = decode(payment.bolt11)
    assert invoice.payment_hash == payment.payment_hash
    assert invoice.amount_msat == 1000000
    assert invoice.description == description

    # Internal invoices are not on fundingsource. so we should get some kind of error
    # that the invoice is not found, but we get status pending
    funding_source = get_funding_source()
    status = await funding_source.get_invoice_status(payment.payment_hash)
    assert isinstance(status, PaymentStatus)
    assert status.pending
