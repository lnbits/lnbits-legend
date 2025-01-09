import asyncio
import hashlib
from collections.abc import AsyncGenerator
from typing import Optional

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


class LNPayWallet(Wallet):
    """https://docs.lnpay.co/"""

    def __init__(self):
        if not settings.lnpay_api_endpoint:
            raise ValueError(
                "cannot initialize LNPayWallet: missing lnpay_api_endpoint"
            )
        if not settings.lnpay_api_key:
            raise ValueError("cannot initialize LNPayWallet: missing lnpay_api_key")

        super().__init__()
        wallet_key = settings.lnpay_wallet_key or settings.lnpay_admin_key
        if not wallet_key:
            raise ValueError(
                "cannot initialize LNPayWallet: "
                "missing lnpay_wallet_key or lnpay_admin_key"
            )
        self.wallet_key = wallet_key
        self.endpoint = self.normalize_endpoint(settings.lnpay_api_endpoint)

        headers = {
            "X-Api-Key": settings.lnpay_api_key,
            "User-Agent": settings.user_agent,
        }
        self.client = httpx.AsyncClient(base_url=self.endpoint, headers=headers)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        url = f"/wallet/{self.wallet_key}"
        try:
            r = await self.client.get(url, timeout=60)
        except (httpx.ConnectError, httpx.RequestError):
            return StatusResponse(f"Unable to connect to '{url}'", 0)

        if r.is_error:
            return StatusResponse(r.text[:250], 0)

        data = r.json()
        if data["statusType"]["name"] != "active":
            return StatusResponse(
                f"Wallet {data['user_label']} (data['id']) not active, but"
                f" {data['statusType']['name']}",
                0,
            )

        return StatusResponse(None, data["balance"] * 1000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **_,
    ) -> InvoiceResponse:
        data: dict = {"num_satoshis": f"{amount}"}
        if description_hash:
            data["description_hash"] = description_hash.hex()
        elif unhashed_description:
            data["description_hash"] = hashlib.sha256(unhashed_description).hexdigest()
        else:
            data["memo"] = memo or ""

        r = await self.client.post(
            f"/wallet/{self.wallet_key}/invoice",
            json=data,
            timeout=60,
        )
        ok, checking_id, payment_request, error_message = (
            r.status_code == 201,
            None,
            None,
            r.text,
        )

        if ok:
            data = r.json()
            checking_id, payment_request = data["id"], data["payment_request"]

            self.pending_invoices.append(checking_id)

        return InvoiceResponse(ok, checking_id, payment_request, error_message)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        r = await self.client.post(
            f"/wallet/{self.wallet_key}/withdraw",
            json={"payment_request": bolt11},
            timeout=None,
        )

        try:
            data = r.json()
        except Exception:
            return PaymentResponse(
                False, None, 0, None, f"Got invalid JSON: {r.text[:200]}"
            )

        if r.is_error:
            return PaymentResponse(False, None, None, None, data["message"])

        checking_id = data["lnTx"]["id"]
        fee_msat = 0
        preimage = data["lnTx"]["payment_preimage"]
        return PaymentResponse(True, checking_id, fee_msat, preimage, None)

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        return await self.get_payment_status(checking_id)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(
            url=f"/lntx/{checking_id}",
        )

        if r.is_error:
            return PaymentPendingStatus()

        data = r.json()
        preimage = data["payment_preimage"]
        fee_msat = data["fee_msat"]
        statuses = {0: None, 1: True, -1: False}
        return PaymentStatus(statuses[data["settled"]], fee_msat, preimage)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        self.queue: asyncio.Queue = asyncio.Queue(0)
        while settings.lnbits_running:
            value = await self.queue.get()
            yield value
