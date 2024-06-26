import asyncio
import json
from typing import AsyncGenerator

from loguru import logger

from lnbits.settings import settings
from lnbits.wallets.lnbits import LNbitsWallet

from .base import (
    StatusResponse,
    http_tunnel_client,
)


class LNbitsReverseWallet(LNbitsWallet):
    """https://github.com/lnbits/lnbits"""

    def __init__(self):
        self.client = http_tunnel_client

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        if not self.client.connected:
            return StatusResponse(None, 0)
        return await super().status()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        url = f"{self.endpoint}/api/v1/payments/sse"

        while settings.lnbits_running:
            try:
                async with self.client.stream("GET", url) as r:
                    async for value in r.aiter_text():
                        data = json.loads(value)
                        if "payment_hash" in data:
                            yield data["payment_hash"]

            except Exception as exc:
                logger.warning(exc)

            logger.error("lost connection to lnbits tunnel, retrying in 5 seconds")
            await asyncio.sleep(5)
