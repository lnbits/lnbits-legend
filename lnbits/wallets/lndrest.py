import asyncio
import base64
import hashlib
import json
from pydoc import describe
from typing import AsyncGenerator, Dict, Optional

import httpx
from loguru import logger

from lnbits import bolt11 as lnbits_bolt11
from lnbits.settings import settings
from lnbits.helpers import b64_hex_transform, b64_transform

from .base import (
    InvoiceResponse,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)
from .macaroon import AESCipher, load_macaroon


class LndRestWallet(Wallet):
    """https://api.lightning.community/rest/index.html#lnd-rest-api-reference"""

    def __init__(self):
        endpoint = settings.lnd_rest_endpoint
        endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
        endpoint = (
            "https://" + endpoint if not endpoint.startswith("http") else endpoint
        )
        self.endpoint = endpoint

        macaroon = (
            settings.lnd_rest_macaroon
            or settings.lnd_admin_macaroon
            or settings.lnd_rest_admin_macaroon
            or settings.lnd_invoice_macaroon
            or settings.lnd_rest_invoice_macaroon
        )

        encrypted_macaroon = settings.lnd_rest_macaroon_encrypted
        if encrypted_macaroon:
            macaroon = AESCipher(description="macaroon decryption").decrypt(
                encrypted_macaroon
            )
        self.macaroon = load_macaroon(macaroon)

        self.auth = {"Grpc-Metadata-macaroon": self.macaroon}
        self.cert = settings.lnd_rest_cert

    async def status(self) -> StatusResponse:
        try:
            async with httpx.AsyncClient(verify=self.cert) as client:
                r = await client.get(
                    f"{self.endpoint}/v1/balance/channels", headers=self.auth
                )
        except (httpx.ConnectError, httpx.RequestError):
            return StatusResponse(f"Unable to connect to {self.endpoint}.", 0)

        try:
            data = r.json()
            if r.is_error:
                raise Exception
        except Exception:
            return StatusResponse(r.text[:200], 0)

        return StatusResponse(None, int(data["balance"]) * 1000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        data: Dict = {"value": amount, "private": True}
        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]
        if description_hash:
            data["description_hash"] = base64.b64encode(description_hash).decode(
                "ascii"
            )
        elif unhashed_description:
            data["description_hash"] = base64.b64encode(
                hashlib.sha256(unhashed_description).digest()
            ).decode("ascii")
        else:
            data["memo"] = memo or ""

        async with httpx.AsyncClient(verify=self.cert) as client:
            r = await client.post(
                url=f"{self.endpoint}/v1/invoices", headers=self.auth, json=data
            )

        if r.is_error:
            error_message = r.text
            try:
                error_message = r.json()["error"]
            except Exception:
                pass
            return InvoiceResponse(False, None, None, error_message)

        data = r.json()
        payment_request = data["payment_request"]
        payment_hash = base64.b64decode(data["r_hash"]).hex()
        checking_id = payment_hash

        return InvoiceResponse(True, checking_id, payment_request, None)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        async with httpx.AsyncClient(verify=self.cert) as client:
            # set the fee limit for the payment
            lnrpcFeeLimit = dict()
            lnrpcFeeLimit["fixed_msat"] = "{}".format(fee_limit_msat)

            r = await client.post(
                url=f"{self.endpoint}/v1/channels/transactions",
                headers=self.auth,
                json={"payment_request": bolt11, "fee_limit": lnrpcFeeLimit},
                timeout=None,
            )

        if r.is_error or r.json().get("payment_error"):
            error_message = r.json().get("payment_error") or r.text
            return PaymentResponse(False, None, None, None, error_message)

        data = r.json()
        checking_id = base64.b64decode(data["payment_hash"]).hex()
        fee_msat = int(data["payment_route"]["total_fees_msat"])
        preimage = base64.b64decode(data["payment_preimage"]).hex()
        return PaymentResponse(True, checking_id, fee_msat, preimage, None)

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        async with httpx.AsyncClient(verify=self.cert) as client:
            r = await client.get(
                url=f"{self.endpoint}/v1/invoice/{checking_id}", headers=self.auth
            )

        if r.is_error or not r.json().get("settled"):
            # this must also work when checking_id is not a hex recognizable by lnd
            # it will return an error and no "settled" attribute on the object
            return PaymentStatus(None)

        return PaymentStatus(True)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        """
        This routine checks the payment status using routerpc.TrackPaymentV2.
        """
        # convert checking_id from hex to base64 and some LND magic
        try:
            checking_id = base64.urlsafe_b64encode(bytes.fromhex(checking_id)).decode(
                "ascii"
            )
        except ValueError:
            return PaymentStatus(None)

        url = f"{self.endpoint}/v2/router/track/{checking_id}"

        # check payment.status:
        # https://api.lightning.community/?python=#paymentpaymentstatus
        statuses = {
            "UNKNOWN": None,
            "IN_FLIGHT": None,
            "SUCCEEDED": True,
            "FAILED": False,
        }

        async with httpx.AsyncClient(
            timeout=None, headers=self.auth, verify=self.cert
        ) as client:
            async with client.stream("GET", url) as r:
                async for l in r.aiter_lines():
                    try:
                        line = json.loads(l)
                        if line.get("error"):
                            logger.error(
                                line["error"]["message"]
                                if "message" in line["error"]
                                else line["error"]
                            )
                            return PaymentStatus(None)
                        payment = line.get("result")
                        if payment is not None and payment.get("status"):
                            return PaymentStatus(
                                paid=statuses[payment["status"]],
                                fee_msat=payment.get("fee_msat"),
                                preimage=payment.get("payment_preimage"),
                            )
                        else:
                            return PaymentStatus(None)
                    except:
                        continue

        return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while True:
            try:
                url = self.endpoint + "/v1/invoices/subscribe"
                async with httpx.AsyncClient(
                    timeout=None, headers=self.auth, verify=self.cert
                ) as client:
                    async with client.stream("GET", url) as r:
                        async for line in r.aiter_lines():
                            try:
                                inv = json.loads(line)["result"]
                                if not inv["settled"]:
                                    continue
                            except:
                                continue

                            payment_hash = base64.b64decode(inv["r_hash"]).hex()
                            yield payment_hash
            except Exception as exc:
                logger.error(
                    f"lost connection to lnd invoices stream: '{exc}', retrying in 5 seconds"
                )
                await asyncio.sleep(5)

    async def pay_with_keysend(self, public_key: str, pre_image: str, payment_hash: str, amount: int, fee_limit_msat: int, message: str) -> PaymentResponse:
        async with httpx.AsyncClient(verify=self.cert) as client:
            # set the fee limit for the payment
            lnrpcFeeLimit = dict()
            lnrpcFeeLimit["fixed_msat"] = "{}".format(fee_limit_msat)

            # Base 64 encoded destination bytes
            dest = b64_hex_transform(public_key)

            # The record 5482373484 is special: it carries the pre_image to
            # the destination so it can be compared with the hash we pass
            # via the payment_hash
            dest_custom_records = {
                5482373484: b64_hex_transform(pre_image),
                34349334: b64_transform(message),
            }

            r = await client.post(
                url=f"{self.endpoint}/v1/channels/transactions",
                headers=self.auth,
                json={
                    "dest": dest,
                    "amt_msat": amount,
                    "payment_hash": b64_hex_transform(payment_hash),
                    "dest_custom_records": dest_custom_records,
                    "fee_limit": lnrpcFeeLimit
                },
                timeout=None,
            )

        if r.is_error or r.json().get("payment_error"):
            error_message = r.json().get("payment_error") or r.text
            return PaymentResponse(False, None, None, None, error_message)

        data = r.json()
        checking_id = base64.b64decode(data["payment_hash"]).hex()
        fee_msat = int(data["payment_route"]["total_fees_msat"])
        preimage = base64.b64decode(data["payment_preimage"]).hex()
        return PaymentResponse(True, checking_id, fee_msat, preimage, None)
