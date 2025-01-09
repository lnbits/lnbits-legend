import asyncio
import base64
import hashlib
import json
import urllib.parse
from collections.abc import AsyncGenerator
from typing import Optional

import httpx
from loguru import logger
from websockets.client import connect

from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
    StatusResponse,
    Wallet,
)


class PhoenixdWallet(Wallet):
    """https://phoenix.acinq.co/server/api"""

    def __init__(self):
        if not settings.phoenixd_api_endpoint:
            raise ValueError(
                "cannot initialize PhoenixdWallet: missing phoenixd_api_endpoint"
            )
        if not settings.phoenixd_api_password:
            raise ValueError(
                "cannot initialize PhoenixdWallet: missing phoenixd_api_password"
            )

        self.endpoint = self.normalize_endpoint(settings.phoenixd_api_endpoint)
        parsed_url = urllib.parse.urlparse(settings.phoenixd_api_endpoint)

        if parsed_url.scheme == "http":
            ws_protocol = "ws"
        elif parsed_url.scheme == "https":
            ws_protocol = "wss"
        else:
            raise ValueError(f"Unsupported scheme: {parsed_url.scheme}")

        self.ws_url = (
            f"{ws_protocol}://{urllib.parse.urlsplit(self.endpoint).netloc}/websocket"
        )
        password = settings.phoenixd_api_password
        encoded_auth = base64.b64encode(f":{password}".encode())
        auth = str(encoded_auth, "utf-8")
        self.headers = {
            "Authorization": f"Basic {auth}",
            "User-Agent": settings.user_agent,
        }

        self.client = httpx.AsyncClient(base_url=self.endpoint, headers=self.headers)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get("/getinfo", timeout=10)
            r.raise_for_status()
            data = r.json()

            if len(data) == 0:
                return StatusResponse("no data", 0)

            if r.is_error or "channels" not in data:
                error_message = data["message"] if "message" in data else r.text
                return StatusResponse(f"Server error: '{error_message}'", 0)

            if len(data["channels"]) == 0:
                # todo: add custom unit-test for this
                return StatusResponse(None, 0)

            balance_msat = int(data["channels"][0]["balanceSat"]) * 1000
            return StatusResponse(None, balance_msat)
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

        try:
            msats_amount = amount
            data: dict = {
                "amountSat": f"{msats_amount}",
                "externalId": "",
            }

            # Either 'description' (string) or 'descriptionHash' must be supplied
            # PhoenixD description limited to 128 characters
            if description_hash:
                data["descriptionHash"] = description_hash.hex()
            else:
                desc = memo
                if desc is None and unhashed_description:
                    desc = unhashed_description.decode()
                desc = desc or ""
                if len(desc) > 128:
                    data["descriptionHash"] = hashlib.sha256(desc.encode()).hexdigest()
                else:
                    data["description"] = desc

            r = await self.client.post(
                "/createinvoice",
                data=data,
                timeout=40,
            )
            r.raise_for_status()
            data = r.json()

            if r.is_error or "paymentHash" not in data:
                error_message = data["message"]
                return InvoiceResponse(
                    False, None, None, f"Server error: '{error_message}'"
                )

            checking_id = data["paymentHash"]
            payment_request = data["serialized"]
            return InvoiceResponse(True, checking_id, payment_request, None)
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
                "/payinvoice",
                data={
                    "invoice": bolt11,
                },
                timeout=40,
            )

            r.raise_for_status()
            data = r.json()

            if "routingFeeSat" not in data and "reason" in data:
                return PaymentResponse(None, None, None, None, data["reason"])

            if r.is_error or "paymentHash" not in data:
                error_message = data["message"] if "message" in data else r.text
                return PaymentResponse(None, None, None, None, error_message)

            checking_id = data["paymentHash"]
            fee_msat = -int(data["routingFeeSat"])
            preimage = data["paymentPreimage"]

            return PaymentResponse(True, checking_id, fee_msat, preimage, None)

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
            r = await self.client.get(f"/payments/incoming/{checking_id}")
            if r.is_error:
                return PaymentPendingStatus()
            data = r.json()

            if data["isPaid"]:
                fee_msat = data["fees"]
                preimage = data["preimage"]
                return PaymentSuccessStatus(fee_msat=fee_msat, preimage=preimage)

            return PaymentPendingStatus()
        except Exception as e:
            logger.error(f"Error getting invoice status: {e}")
            return PaymentPendingStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        # TODO: how can we detect a failed payment?
        try:
            r = await self.client.get(f"/payments/outgoing/{checking_id}")
            if r.is_error:
                return PaymentPendingStatus()
            data = r.json()

            if data["isPaid"]:
                fee_msat = data["fees"]
                preimage = data["preimage"]
                return PaymentSuccessStatus(fee_msat=fee_msat, preimage=preimage)

            return PaymentPendingStatus()
        except Exception as e:
            logger.error(f"Error getting invoice status: {e}")
            return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                async with connect(
                    self.ws_url,
                    extra_headers=[("Authorization", self.headers["Authorization"])],
                ) as ws:
                    logger.info("connected to phoenixd invoices stream")
                    while settings.lnbits_running:
                        message = await ws.recv()
                        message_json = json.loads(message)
                        if (
                            message_json
                            and message_json.get("type") == "payment_received"
                        ):
                            logger.info(
                                f'payment-received: {message_json["paymentHash"]}'
                            )
                            yield message_json["paymentHash"]

            except Exception as exc:
                logger.error(
                    f"lost connection to phoenixd invoices stream: '{exc}'"
                    "retrying in 5 seconds"
                )
                await asyncio.sleep(5)
