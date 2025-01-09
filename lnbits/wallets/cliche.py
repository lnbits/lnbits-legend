import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Optional

from loguru import logger
from websocket import create_connection

from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)


class ClicheWallet(Wallet):
    """https://github.com/fiatjaf/cliche"""

    def __init__(self):
        if not settings.cliche_endpoint:
            raise ValueError("cannot initialize ClicheWallet: missing cliche_endpoint")

        self.endpoint = self.normalize_endpoint(settings.cliche_endpoint)

    async def cleanup(self):
        pass

    async def status(self) -> StatusResponse:
        try:
            ws = create_connection(self.endpoint)
            ws.send("get-info")
            r = ws.recv()
        except Exception as exc:
            return StatusResponse(
                f"Failed to connect to {self.endpoint} due to: {exc}", 0
            )
        try:
            data = json.loads(r)
        except Exception:
            return StatusResponse(
                f"Failed to connect to {self.endpoint}, got: '{r[:200]}...'", 0
            )

        return StatusResponse(None, data["result"]["wallets"][0]["balance"])

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        if unhashed_description or description_hash:
            description_hash_str = (
                description_hash.hex()
                if description_hash
                else (
                    hashlib.sha256(unhashed_description).hexdigest()
                    if unhashed_description
                    else None
                )
            )
            ws = create_connection(self.endpoint)
            ws.send(
                f"create-invoice --msatoshi {amount*1000} --description_hash"
                f" {description_hash_str}"
            )
            r = ws.recv()
        else:
            ws = create_connection(self.endpoint)
            ws.send(f"create-invoice --msatoshi {amount*1000} --description {memo}")
            r = ws.recv()
        data = json.loads(r)
        checking_id = None
        payment_request = None
        error_message = None

        if data.get("error") is not None and data["error"].get("message"):
            logger.error(data["error"]["message"])
            error_message = data["error"]["message"]
            return InvoiceResponse(False, checking_id, payment_request, error_message)

        if data.get("result") is not None:
            checking_id, payment_request = (
                data["result"]["payment_hash"],
                data["result"]["invoice"],
            )
        else:
            return InvoiceResponse(False, None, None, "Could not get payment hash")

        return InvoiceResponse(True, checking_id, payment_request, error_message)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        ws = create_connection(self.endpoint)
        ws.send(f"pay-invoice --invoice {bolt11}")
        checking_id, fee_msat, preimage, error_message, payment_ok = (
            None,
            None,
            None,
            None,
            None,
        )
        for _ in range(2):
            r = ws.recv()
            data = json.loads(r)
            checking_id, fee_msat, preimage, error_message, payment_ok = (
                None,
                None,
                None,
                None,
                None,
            )

            if data.get("error") is not None:
                error_message = data["error"].get("message")
                return PaymentResponse(False, None, None, None, error_message)

            if data.get("method") == "payment_succeeded":
                payment_ok = True
                checking_id = data["params"]["payment_hash"]
                fee_msat = data["params"]["fee_msatoshi"]
                preimage = data["params"]["preimage"]
                continue

            if data.get("result") is None:
                return PaymentResponse(None)

        return PaymentResponse(
            payment_ok, checking_id, fee_msat, preimage, error_message
        )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        ws = create_connection(self.endpoint)
        ws.send(f"check-payment --hash {checking_id}")
        r = ws.recv()
        data = json.loads(r)

        if data.get("error") is not None and data["error"].get("message"):
            logger.error(data["error"]["message"])
            return PaymentPendingStatus()

        statuses = {"pending": None, "complete": True, "failed": False}
        return PaymentStatus(statuses[data["result"]["status"]])

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        ws = create_connection(self.endpoint)
        ws.send(f"check-payment --hash {checking_id}")
        r = ws.recv()
        data = json.loads(r)

        if data.get("error") is not None and data["error"].get("message"):
            logger.error(data["error"]["message"])
            return PaymentPendingStatus()
        payment = data["result"]
        statuses = {"pending": None, "complete": True, "failed": False}
        return PaymentStatus(
            statuses[payment["status"]],
            payment.get("fee_msatoshi"),
            payment.get("preimage"),
        )

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                ws = create_connection(self.endpoint)
                while settings.lnbits_running:
                    r = ws.recv()
                    data = json.loads(r)
                    print(data)
                    try:
                        if data["result"]["status"]:
                            yield data["result"]["payment_hash"]
                    except Exception:
                        continue
            except Exception as exc:
                logger.error(
                    f"lost connection to cliche's invoices stream: '{exc}', retrying in"
                    " 5 seconds"
                )
                await asyncio.sleep(5)
                continue
