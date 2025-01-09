import asyncio
import hashlib
import json
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


class AlbyWallet(Wallet):
    """https://guides.getalby.com/alby-wallet-api/reference/api-reference"""

    def __init__(self):
        if not settings.alby_api_endpoint:
            raise ValueError("cannot initialize AlbyWallet: missing alby_api_endpoint")
        if not settings.alby_access_token:
            raise ValueError("cannot initialize AlbyWallet: missing alby_access_token")

        self.endpoint = self.normalize_endpoint(settings.alby_api_endpoint)
        self.auth = {
            "Authorization": "Bearer " + settings.alby_access_token,
            "User-Agent": settings.user_agent,
        }
        self.client = httpx.AsyncClient(base_url=self.endpoint, headers=self.auth)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get("/balance", timeout=10)
            r.raise_for_status()

            data = r.json()

            if len(data) == 0:
                return StatusResponse("no data", 0)

            if r.is_error or data["unit"] != "sat":
                error_message = data["message"] if "message" in data else r.text
                return StatusResponse(f"Server error: '{error_message}'", 0)

            # multiply balance by 1000 to get msats balance
            return StatusResponse(None, data["balance"] * 1000)
        except KeyError as exc:
            logger.warning(exc)
            return StatusResponse("Server error: 'missing required fields'", 0)
        except json.JSONDecodeError as exc:
            logger.warning(exc)
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
        # https://api.getalby.com/invoices
        data: dict = {"amount": f"{amount}"}
        if description_hash:
            data["description_hash"] = description_hash.hex()
        elif unhashed_description:
            data["description_hash"] = hashlib.sha256(unhashed_description).hexdigest()
        else:
            data["memo"] = memo or ""

        try:
            r = await self.client.post(
                "/invoices",
                json=data,
                timeout=40,
            )
            r.raise_for_status()

            data = r.json()

            if r.is_error:
                error_message = data["message"] if "message" in data else r.text
                return InvoiceResponse(False, None, None, error_message)

            checking_id = data["payment_hash"]
            payment_request = data["payment_request"]
            return InvoiceResponse(True, checking_id, payment_request, None)
        except KeyError as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, "Server error: 'missing required fields'"
            )
        except json.JSONDecodeError as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, "Server error: 'invalid json response'"
            )
        except Exception as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, f"Unable to connect to {self.endpoint}."
            )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            # https://api.getalby.com/payments/bolt11
            r = await self.client.post(
                "/payments/bolt11",
                json={"invoice": bolt11},  # assume never need amount in body
                timeout=None,
            )
            r.raise_for_status()
            data = r.json()

            if r.is_error:
                error_message = data["message"] if "message" in data else r.text
                return PaymentResponse(None, None, None, None, error_message)

            checking_id = data["payment_hash"]
            # todo: confirm with bitkarrot that having the minus is fine
            # other funding sources return a positive fee value
            fee_msat = -data["fee"]
            preimage = data["payment_preimage"]

            return PaymentResponse(True, checking_id, fee_msat, preimage, None)
        except KeyError as exc:
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, "Server error: 'missing required fields'"
            )
        except json.JSONDecodeError as exc:
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, "Server error: 'invalid json response'"
            )
        except Exception as exc:
            logger.info(f"Failed to pay invoice {bolt11}")
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, f"Unable to connect to {self.endpoint}."
            )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        return await self.get_payment_status(checking_id)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = await self.client.get(f"/invoices/{checking_id}")

            if r.is_error:
                return PaymentPendingStatus()

            data = r.json()

            # TODO: how can we detect a failed payment?
            statuses = {
                "CREATED": None,
                "SETTLED": True,
            }
            # todo: extract fee and preimage
            # maybe use the more specific endpoints:
            #  - https://api.getalby.com/invoices/incoming
            #  - https://api.getalby.com/invoices/outgoing
            return PaymentStatus(
                statuses[data.get("state")], fee_msat=None, preimage=None
            )
        except Exception as e:
            logger.error(f"Error getting invoice status: {e}")
            return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        self.queue: asyncio.Queue = asyncio.Queue(0)
        while settings.lnbits_running:
            value = await self.queue.get()
            yield value
