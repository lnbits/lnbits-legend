import asyncio
import json
from typing import AsyncGenerator, Dict, Optional

import httpx
from loguru import logger

from lnbits.settings import settings

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


class LNbitsWallet(Wallet):
    """https://github.com/lnbits/lnbits"""

    def __init__(self):
        if not settings.lnbits_endpoint:
            raise ValueError("cannot initialize LNbitsWallet: missing lnbits_endpoint")
        key = (
            settings.lnbits_key
            or settings.lnbits_admin_key
            or settings.lnbits_invoice_key
        )
        if not key:
            raise ValueError(
                "cannot initialize LNbitsWallet: "
                "missing lnbits_key or lnbits_admin_key or lnbits_invoice_key"
            )
        self.endpoint = self.normalize_endpoint(settings.lnbits_endpoint)
        self.headers = {"X-Api-Key": key, "User-Agent": settings.user_agent}
        self.client = httpx.AsyncClient(base_url=self.endpoint, headers=self.headers)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get(url="/api/v1/wallet", timeout=15)
            r.raise_for_status()
            data = r.json()

            if len(data) == 0:
                return StatusResponse("no data", 0)

            if r.is_error or "balance" not in data:
                return StatusResponse(f"Server error: '{r.text}'", 0)

            return StatusResponse(None, data["balance"])
        except json.JSONDecodeError:
            return StatusResponse("Server error: 'invalid json response'", 0)
        except Exception as exc:
            logger.warning(exc)
            return StatusResponse(f"Unable to connect to {self.endpoint}.", 0)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        data: Dict = {"out": False, "amount": amount, "memo": memo or ""}
        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]
        if description_hash:
            data["description_hash"] = description_hash.hex()
        if unhashed_description:
            data["unhashed_description"] = unhashed_description.hex()

        try:
            r = await self.client.post(url="/api/v1/payments", json=data)
            r.raise_for_status()
            data = r.json()

            if r.is_error or "payment_request" not in data:
                error_message = data["detail"] if "detail" in data else r.text
                return InvoiceResponse(
                    False, None, None, f"Server error: '{error_message}'"
                )

            return InvoiceResponse(
                True, data["checking_id"], data["payment_request"], None
            )
        except json.JSONDecodeError:
            return InvoiceResponse(
                False, None, None, "Server error: 'invalid json response'"
            )
        except KeyError as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, "Server error: 'missing required fields'"
            )
        except Exception as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, f"Unable to connect to {self.endpoint}."
            )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            r = await self.client.post(
                url="/api/v1/payments",
                json={"out": True, "bolt11": bolt11},
                timeout=None,
            )

            r.raise_for_status()
            data = r.json()

            checking_id = data["payment_hash"]

            # we do this to get the fee and preimage
            payment: PaymentStatus = await self.get_payment_status(checking_id)

            success = True if payment.success else None
            return PaymentResponse(
                success, checking_id, payment.fee_msat, payment.preimage
            )

        except httpx.HTTPStatusError as exc:
            try:
                logger.debug(exc)
                data = exc.response.json()
                error_message = f"Payment {data['status']}: {data['detail']}."
                if data["status"] == "failed":
                    return PaymentResponse(False, None, None, None, error_message)
                return PaymentResponse(None, None, None, None, error_message)
            except Exception as exc:
                error_message = f"Unable to connect to {self.endpoint}."
                return PaymentResponse(None, None, None, None, error_message)

        except json.JSONDecodeError:
            return PaymentResponse(
                None, None, None, None, "Server error: 'invalid json response'"
            )
        except KeyError:
            return PaymentResponse(
                None, None, None, None, "Server error: 'missing required fields'"
            )
        except Exception as exc:
            logger.info(f"Failed to pay invoice {bolt11}")
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, f"Unable to connect to {self.endpoint}."
            )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = await self.client.get(
                url=f"/api/v1/payments/{checking_id}",
            )
            r.raise_for_status()

            data = r.json()

            if data.get("paid", False) is True:
                return PaymentSuccessStatus()
            return PaymentPendingStatus()
        except Exception:
            return PaymentPendingStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = await self.client.get(url=f"/api/v1/payments/{checking_id}")

            if r.is_error:
                return PaymentPendingStatus()
            data = r.json()

            if data.get("status") == "failed":
                return PaymentFailedStatus()

            if "paid" not in data or not data["paid"]:
                return PaymentPendingStatus()

            if "details" not in data:
                return PaymentPendingStatus()

            return PaymentSuccessStatus(
                fee_msat=data["details"]["fee"], preimage=data["preimage"]
            )
        except Exception:
            return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        url = f"{self.endpoint}/api/v1/payments/sse"

        while settings.lnbits_running:
            try:
                async with httpx.AsyncClient(
                    timeout=None, headers=self.headers
                ) as client:
                    del client.headers[
                        "accept-encoding"
                    ]  # we have to disable compression for SSEs
                    async with client.stream(
                        "GET", url, content="text/event-stream"
                    ) as r:
                        sse_trigger = False
                        async for line in r.aiter_lines():
                            # The data we want to listen to is of this shape:
                            # event: payment-received
                            # data: {.., "payment_hash" : "asd"}
                            if line.startswith("event: payment-received"):
                                sse_trigger = True
                                continue
                            elif sse_trigger and line.startswith("data:"):
                                data = json.loads(line[len("data:") :])
                                sse_trigger = False
                                yield data["payment_hash"]
                            else:
                                sse_trigger = False

            except (OSError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout):
                pass

            logger.error(
                "lost connection to lnbits /payments/sse, retrying in 5 seconds"
            )
            await asyncio.sleep(5)
