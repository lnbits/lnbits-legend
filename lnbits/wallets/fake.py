import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from hashlib import sha256
from os import urandom
from typing import Optional

from bolt11 import (
    Bolt11,
    Bolt11Exception,
    MilliSatoshi,
    TagChar,
    Tags,
    decode,
    encode,
)
from loguru import logger

from lnbits.settings import settings
from lnbits.utils.crypto import fake_privkey

from .base import (
    InvoiceResponse,
    PaymentFailedStatus,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
    StatusResponse,
    Wallet,
)


class FakeWallet(Wallet):

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(0)
        self.payment_secrets: dict[str, str] = {}
        self.paid_invoices: set[str] = set()
        self.secret = settings.fake_wallet_secret
        self.privkey = fake_privkey(self.secret)

    async def cleanup(self):
        pass

    async def status(self) -> StatusResponse:
        logger.info(
            "FakeWallet funding source is for using LNbits as a centralised,"
            " stand-alone payment system with brrrrrr."
        )
        return StatusResponse(None, 1000000000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        expiry: Optional[int] = None,
        payment_secret: Optional[bytes] = None,
        **_,
    ) -> InvoiceResponse:
        tags = Tags()

        if description_hash:
            tags.add(TagChar.description_hash, description_hash.hex())
        elif unhashed_description:
            tags.add(
                TagChar.description_hash,
                sha256(unhashed_description).hexdigest(),
            )
        else:
            tags.add(TagChar.description, memo or "")

        if expiry:
            tags.add(TagChar.expire_time, expiry)

        if payment_secret:
            secret = payment_secret.hex()
        else:
            secret = urandom(32).hex()
        tags.add(TagChar.payment_secret, secret)

        payment_hash = sha256(secret.encode()).hexdigest()

        tags.add(TagChar.payment_hash, payment_hash)

        self.payment_secrets[payment_hash] = secret

        bolt11 = Bolt11(
            currency="bc",
            amount_msat=MilliSatoshi(amount * 1000),
            date=int(datetime.now().timestamp()),
            tags=tags,
        )

        payment_request = encode(bolt11, self.privkey)

        return InvoiceResponse(
            ok=True, checking_id=payment_hash, payment_request=payment_request
        )

    async def pay_invoice(self, bolt11: str, _: int) -> PaymentResponse:
        try:
            invoice = decode(bolt11)
        except Bolt11Exception as exc:
            return PaymentResponse(ok=False, error_message=str(exc))

        if invoice.payment_hash in self.payment_secrets:
            await self.queue.put(invoice)
            self.paid_invoices.add(invoice.payment_hash)
            return PaymentResponse(
                ok=True,
                checking_id=invoice.payment_hash,
                fee_msat=0,
                preimage=self.payment_secrets.get(invoice.payment_hash) or "0" * 64,
            )
        else:
            return PaymentResponse(
                ok=False, error_message="Only internal invoices can be used!"
            )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        if checking_id in self.paid_invoices:
            return PaymentSuccessStatus()
        if checking_id in list(self.payment_secrets.keys()):
            return PaymentPendingStatus()
        return PaymentFailedStatus()

    async def get_payment_status(self, _: str) -> PaymentStatus:
        return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            value: Bolt11 = await self.queue.get()
            yield value.payment_hash
