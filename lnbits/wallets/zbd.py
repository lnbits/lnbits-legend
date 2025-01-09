import asyncio
import hashlib
from collections.abc import AsyncGenerator
from typing import Optional

import bolt11
import httpx
from loguru import logger

from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)


class ZBDWallet(Wallet):
    """https://zbd.dev/api-reference/"""

    def __init__(self):
        if not settings.zbd_api_endpoint:
            raise ValueError("cannot initialize ZBDWallet: missing zbd_api_endpoint")
        if not settings.zbd_api_key:
            raise ValueError("cannot initialize ZBDWallet: missing zbd_api_key")

        self.endpoint = self.normalize_endpoint(settings.zbd_api_endpoint)
        headers = {
            "apikey": settings.zbd_api_key,
            "User-Agent": settings.user_agent,
        }
        self.client = httpx.AsyncClient(base_url=self.endpoint, headers=headers)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get("wallet", timeout=10)
        except (httpx.ConnectError, httpx.RequestError):
            return StatusResponse(f"Unable to connect to '{self.endpoint}'", 0)

        if r.is_error:
            error_message = r.json()["message"]
            return StatusResponse(error_message, 0)

        data = int(r.json()["data"]["balance"])
        # ZBD returns everything as a str not int
        # balance is returned in msats already in ZBD
        return StatusResponse(None, data)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        # https://api.zebedee.io/v0/charges

        msats_amount = amount * 1000
        data: dict = {
            "amount": f"{msats_amount}",
            "expiresIn": 3600,
            "callbackUrl": "",
            "internalId": "",
        }

        ## handle description_hash and unhashed for ZBD
        if description_hash:
            data["description"] = description_hash.hex()
        elif unhashed_description:
            data["description"] = hashlib.sha256(unhashed_description).hexdigest()
        else:
            data["description"] = memo or ""

        r = await self.client.post(
            "charges",
            json=data,
            timeout=40,
        )

        if r.is_error:
            error_message = r.json()["message"]
            return InvoiceResponse(False, None, None, error_message)

        data = r.json()["data"]
        checking_id = data["id"]  # this is a zbd id
        payment_request = data["invoice"]["request"]
        return InvoiceResponse(True, checking_id, payment_request, None)

    async def pay_invoice(
        self, bolt11_invoice: str, fee_limit_msat: int
    ) -> PaymentResponse:
        # https://api.zebedee.io/v0/payments
        r = await self.client.post(
            "payments",
            json={
                "invoice": bolt11_invoice,
                "description": "",
                "amount": "",
                "internalId": "",
                "callbackUrl": "",
            },
            timeout=40,
        )

        if r.is_error:
            error_message = r.json()["message"]
            return PaymentResponse(False, None, None, None, error_message)

        data = r.json()

        checking_id = bolt11.decode(bolt11_invoice).payment_hash
        fee_msat = -int(data["data"]["fee"])
        preimage = data["data"]["preimage"]

        return PaymentResponse(True, checking_id, fee_msat, preimage, None)

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(f"charges/{checking_id}")
        if r.is_error:
            return PaymentPendingStatus()
        data = r.json()["data"]

        statuses = {
            "pending": None,
            "paid": True,
            "unpaid": None,
            "expired": False,
            "completed": True,
        }
        return PaymentStatus(statuses[data.get("status")])

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(f"payments/{checking_id}")
        if r.is_error:
            return PaymentPendingStatus()

        data = r.json()["data"]

        statuses = {
            "initial": None,
            "pending": None,
            "completed": True,
            "error": None,
            "expired": False,
            "failed": False,
        }

        return PaymentStatus(statuses[data.get("status")], fee_msat=None, preimage=None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        self.queue: asyncio.Queue = asyncio.Queue(0)
        while settings.lnbits_running:
            value = await self.queue.get()
            yield value
