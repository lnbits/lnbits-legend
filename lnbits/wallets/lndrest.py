import asyncio
import base64
import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Any, Optional

import httpx
from loguru import logger

from lnbits.nodes.lndrest import LndRestNode
from lnbits.settings import settings
from lnbits.utils.crypto import AESCipher

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
from .macaroon import load_macaroon


class LndRestWallet(Wallet):
    """https://api.lightning.community/rest/index.html#lnd-rest-api-reference"""

    __node_cls__ = LndRestNode

    def __init__(self):
        if not settings.lnd_rest_endpoint:
            raise ValueError(
                "cannot initialize LndRestWallet: missing lnd_rest_endpoint"
            )

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
        if not macaroon:
            raise ValueError(
                "cannot initialize LndRestWallet: "
                "missing lnd_rest_macaroon or lnd_admin_macaroon or "
                "lnd_rest_admin_macaroon or lnd_invoice_macaroon or "
                "lnd_rest_invoice_macaroon or lnd_rest_macaroon_encrypted"
            )

        if not settings.lnd_rest_cert:
            logger.warning(
                "No certificate for LndRestWallet provided! "
                "This only works if you have a publicly issued certificate."
            )

        self.endpoint = self.normalize_endpoint(settings.lnd_rest_endpoint)

        # if no cert provided it should be public so we set verify to True
        # and it will still check for validity of certificate and fail if its not valid
        # even on startup
        cert = settings.lnd_rest_cert or True

        macaroon = load_macaroon(macaroon)
        headers = {
            "Grpc-Metadata-macaroon": macaroon,
            "User-Agent": settings.user_agent,
        }
        self.client = httpx.AsyncClient(
            base_url=self.endpoint, headers=headers, verify=cert
        )

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get("/v1/balance/channels")
            r.raise_for_status()

            data = r.json()
            if len(data) == 0:
                return StatusResponse("no data", 0)
            if r.is_error or "balance" not in data:
                return StatusResponse(f"Server error: '{r.text}'", 0)

        except json.JSONDecodeError:
            return StatusResponse("Server error: 'invalid json response'", 0)
        except Exception as exc:
            logger.warning(exc)
            return StatusResponse(f"Unable to connect to {self.endpoint}.", 0)

        return StatusResponse(None, int(data["balance"]) * 1000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        data: dict = {
            "value": amount,
            "private": settings.lnd_rest_route_hints,
            "memo": memo or "",
        }
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

        try:
            r = await self.client.post(url="/v1/invoices", json=data)
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

            if "payment_request" not in data or "r_hash" not in data:
                return InvoiceResponse(
                    False, None, None, "Server error: 'missing required fields'"
                )

            payment_request = data["payment_request"]
            payment_hash = base64.b64decode(data["r_hash"]).hex()
            checking_id = payment_hash

            return InvoiceResponse(True, checking_id, payment_request, None)
        except json.JSONDecodeError:
            return InvoiceResponse(
                False, None, None, "Server error: 'invalid json response'"
            )
        except Exception as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, f"Unable to connect to {self.endpoint}."
            )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        # set the fee limit for the payment
        lnrpc_fee_limit = {}
        lnrpc_fee_limit["fixed_msat"] = f"{fee_limit_msat}"

        try:
            json_: dict[str, Any] = {
                "payment_request": bolt11,
                "fee_limit": lnrpc_fee_limit,
            }
            if settings.lnd_rest_allow_self_payment:
                json_["allow_self_payment"] = 1
            r = await self.client.post(
                url="/v1/channels/transactions",
                json=json_,
                timeout=None,
            )
            r.raise_for_status()
            data = r.json()

            payment_error = data.get("payment_error")
            if payment_error:
                logger.warning(f"LndRestWallet payment_error: {payment_error}.")
                return PaymentResponse(False, None, None, None, payment_error)

            checking_id = base64.b64decode(data["payment_hash"]).hex()
            fee_msat = int(data["payment_route"]["total_fees_msat"])
            preimage = base64.b64decode(data["payment_preimage"]).hex()
            return PaymentResponse(True, checking_id, fee_msat, preimage, None)
        except KeyError as exc:
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, "Server error: 'missing required fields'"
            )
        except json.JSONDecodeError:
            return PaymentResponse(
                None, None, None, None, "Server error: 'invalid json response'"
            )
        except Exception as exc:
            logger.warning(f"LndRestWallet pay_invoice POST error: {exc}.")
            return PaymentResponse(
                None, None, None, None, f"Unable to connect to {self.endpoint}."
            )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(url=f"/v1/invoice/{checking_id}")

        try:
            r.raise_for_status()
            data = r.json()

            if r.is_error or not data.get("settled"):
                # this must also work when checking_id is not a hex recognizable by lnd
                # it will return an error and no "settled" attribute on the object
                return PaymentPendingStatus()
        except Exception as e:
            logger.error(f"Error getting invoice status: {e}")
            return PaymentPendingStatus()
        return PaymentSuccessStatus()

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
            return PaymentPendingStatus()

        url = f"/v2/router/track/{checking_id}"

        # check payment.status:
        # https://api.lightning.community/?python=#paymentpaymentstatus
        statuses = {
            "UNKNOWN": None,
            "IN_FLIGHT": None,
            "SUCCEEDED": True,
            "FAILED": False,
        }

        async with self.client.stream("GET", url, timeout=None) as r:
            async for json_line in r.aiter_lines():
                try:
                    line = json.loads(json_line)
                    if line.get("error"):
                        logger.error(
                            line["error"]["message"]
                            if "message" in line["error"]
                            else line["error"]
                        )
                        if (
                            line["error"].get("code") == 5
                            and line["error"].get("message")
                            == "payment isn't initiated"
                        ):
                            return PaymentFailedStatus()
                        return PaymentPendingStatus()
                    payment = line.get("result")
                    if payment is not None and payment.get("status"):
                        return PaymentStatus(
                            paid=statuses[payment["status"]],
                            fee_msat=payment.get("fee_msat"),
                            preimage=payment.get("payment_preimage"),
                        )
                    else:
                        return PaymentPendingStatus()
                except Exception:
                    continue

        return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                url = "/v1/invoices/subscribe"
                async with self.client.stream("GET", url, timeout=None) as r:
                    async for line in r.aiter_lines():
                        try:
                            inv = json.loads(line)["result"]
                            if not inv["settled"]:
                                continue
                        except Exception:
                            continue

                        payment_hash = base64.b64decode(inv["r_hash"]).hex()
                        yield payment_hash
            except Exception as exc:
                logger.error(
                    f"lost connection to lnd invoices stream: '{exc}', retrying in 5"
                    " seconds"
                )
                await asyncio.sleep(5)
