import asyncio
import base64
import hashlib
import json
import urllib.parse
from collections.abc import AsyncGenerator
from typing import Any, Optional

import httpx
from loguru import logger
from websockets.client import connect

from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)


class EclairError(Exception):
    pass


class UnknownError(Exception):
    pass


class EclairWallet(Wallet):
    def __init__(self):
        if not settings.eclair_url:
            raise ValueError("cannot initialize EclairWallet: missing eclair_url")
        if not settings.eclair_pass:
            raise ValueError("cannot initialize EclairWallet: missing eclair_pass")

        self.url = self.normalize_endpoint(settings.eclair_url)
        self.ws_url = f"ws://{urllib.parse.urlsplit(self.url).netloc}/ws"

        password = settings.eclair_pass
        encoded_auth = base64.b64encode(f":{password}".encode())
        auth = str(encoded_auth, "utf-8")
        self.headers = {
            "Authorization": f"Basic {auth}",
            "User-Agent": settings.user_agent,
        }
        self.client = httpx.AsyncClient(base_url=self.url, headers=self.headers)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.post("/globalbalance", timeout=5)
            r.raise_for_status()

            data = r.json()

            if len(data) == 0:
                return StatusResponse("no data", 0)

            if "error" in data:
                return StatusResponse(f"""Server error: '{data["error"]}'""", 0)

            if r.is_error or "total" not in data:
                return StatusResponse(f"Server error: '{r.text}'", 0)

            return StatusResponse(None, int(data.get("total") * 100_000_000_000))
        except json.JSONDecodeError:
            return StatusResponse("Server error: 'invalid json response'", 0)
        except Exception as exc:
            logger.warning(exc)
            return StatusResponse(f"Unable to connect to {self.url}.", 0)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        data: dict[str, Any] = {
            "amountMsat": amount * 1000,
        }
        if kwargs.get("expiry"):
            data["expireIn"] = kwargs["expiry"]

        # Either 'description' (string) or 'descriptionHash' must be supplied
        if description_hash:
            data["descriptionHash"] = description_hash.hex()
        elif unhashed_description:
            data["descriptionHash"] = hashlib.sha256(unhashed_description).hexdigest()
        else:
            data["description"] = memo

        try:
            r = await self.client.post("/createinvoice", data=data, timeout=40)
            r.raise_for_status()
            data = r.json()

            if len(data) == 0:
                return InvoiceResponse(False, None, None, "no data")

            if "error" in data:
                return InvoiceResponse(
                    False, None, None, f"""Server error: '{data["error"]}'"""
                )

            if r.is_error:
                return InvoiceResponse(False, None, None, f"Server error: '{r.text}'")

            return InvoiceResponse(True, data["paymentHash"], data["serialized"], None)
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
                False, None, None, f"Unable to connect to {self.url}."
            )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            r = await self.client.post(
                "/payinvoice",
                data={"invoice": bolt11, "blocking": True},
                timeout=None,
            )
            r.raise_for_status()
            data = r.json()

            if "error" in data:
                return PaymentResponse(None, None, None, None, data["error"])
            if r.is_error:
                return PaymentResponse(None, None, None, None, r.text)

            if data["type"] == "payment-failed":
                return PaymentResponse(False, None, None, None, "payment failed")

            checking_id = data["paymentHash"]
            preimage = data["paymentPreimage"]

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
                None, None, None, None, f"Unable to connect to {self.url}."
            )

        payment_status: PaymentStatus = await self.get_payment_status(checking_id)
        success = True if payment_status.success else None
        return PaymentResponse(
            success, checking_id, payment_status.fee_msat, preimage, None
        )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = await self.client.post(
                "/getreceivedinfo",
                data={"paymentHash": checking_id},
            )

            r.raise_for_status()
            data = r.json()

            if r.is_error or "error" in data or data.get("status") is None:
                raise Exception("error in eclair response")

            statuses = {
                "received": True,
                "expired": False,
                "pending": None,
            }
            return PaymentStatus(statuses.get(data["status"]["type"]))
        except Exception:
            return PaymentPendingStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = await self.client.post(
                "/getsentinfo",
                data={"paymentHash": checking_id},
                timeout=40,
            )

            r.raise_for_status()

            data = r.json()[-1]

            if r.is_error or "error" in data or data.get("status") is None:
                raise Exception("error in eclair response")

            fee_msat, preimage = None, None
            if data["status"]["type"] == "sent":
                fee_msat = -data["status"]["feesPaid"]
                preimage = data["status"]["paymentPreimage"]

            statuses = {
                "sent": True,
                "failed": False,
                "pending": None,
            }
            return PaymentStatus(
                statuses.get(data["status"]["type"]), fee_msat, preimage
            )
        except Exception:
            return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                async with connect(
                    self.ws_url,
                    extra_headers=[("Authorization", self.headers["Authorization"])],
                ) as ws:
                    while settings.lnbits_running:
                        message = await ws.recv()
                        message_json = json.loads(message)

                        if message_json and message_json["type"] == "payment-received":
                            yield message_json["paymentHash"]

            except Exception as exc:
                logger.error(
                    f"lost connection to eclair invoices stream: '{exc}'"
                    "retrying in 5 seconds"
                )
                await asyncio.sleep(5)
