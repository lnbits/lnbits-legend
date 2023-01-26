from typing import AsyncGenerator, Optional

from loguru import logger

from .base import (
    InvoiceResponse,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Unsupported,
    Wallet,
)


class VoidWallet(Wallet):
    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        raise Unsupported("")

    async def status(self) -> StatusResponse:
        logger.warning(
            "This backend does nothing, it is here just as a placeholder, you must configure an actual backend before being able to do anything useful with LNbits."
        )
        return StatusResponse(None, 0)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        raise Unsupported("")

    async def pay_with_keysend(self, public_key: str, pre_image: str, payment_hash: str, amount: int, fee_limit_msat: int, message: str) -> PaymentResponse:
        raise Unsupported("")

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        return PaymentStatus(None)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        yield ""
