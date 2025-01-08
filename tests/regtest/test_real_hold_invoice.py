import hashlib
import os

import pytest

from lnbits.core.services.payments import (
    cancel_hold_invoice,
    create_hold_invoice,
    settle_hold_invoice,
)
from lnbits.exceptions import InvoiceError

from ..helpers import funding_source, is_fake


@pytest.mark.anyio
@pytest.mark.skipif(is_fake, reason="this only works in regtest")
@pytest.mark.skipif(
    funding_source.__class__.__name__ in ["LndRestWallet", "LndWallet"],
    reason="this should not raise for lnd",
)
async def test_pay_raise_unsupported():
    rhash = "0" * 32
    with pytest.raises(InvoiceError):
        await create_hold_invoice(
            wallet_id="fake_wallet_id",
            amount=1000,
            memo="fake_holdinvoice",
            rhash=rhash,
        )
    with pytest.raises(InvoiceError):
        await settle_hold_invoice(preimage=rhash)
    with pytest.raises(InvoiceError):
        await cancel_hold_invoice(rhash)


@pytest.mark.anyio
@pytest.mark.skipif(is_fake, reason="this only works in regtest")
@pytest.mark.skipif(
    funding_source.__class__.__name__ not in ["LndRestWallet"],
    reason="this only works for lndrest",
)
async def test_pay_real_hold_invoice(from_wallet):

    preimage = os.urandom(32)
    preimage_hash = hashlib.sha256(preimage).hexdigest()
    payment = await create_hold_invoice(
        wallet_id=from_wallet.id,
        amount=1000,
        memo="test_holdinvoice",
        rhash=preimage_hash,
    )
    assert payment.amount == 1000 * 1000
    assert payment.memo == "test_holdinvoice"
    assert payment.status == "pending"
    assert payment.wallet_id == from_wallet.id

    payment = await cancel_hold_invoice(payment_hash=preimage_hash)
    assert payment.status == "failed"
