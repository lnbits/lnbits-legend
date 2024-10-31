import asyncio
import json
import random
from typing import AsyncGenerator, Dict, Optional

import httpx
from bolt11 import Bolt11Exception
from bolt11.decode import decode
from loguru import logger

from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    UnsupportedError,
    Wallet,
)
from .macaroon import load_macaroon


class CoreLightningRestWallet(Wallet):
    def __init__(self):
        if not settings.corelightning_rest_url:
            raise ValueError(
                "cannot initialize CoreLightningRestWallet: "
                "missing corelightning_rest_url"
            )
        if not settings.corelightning_rest_macaroon:
            raise ValueError(
                "cannot initialize CoreLightningRestWallet: "
                "missing corelightning_rest_macaroon"
            )
        macaroon = load_macaroon(settings.corelightning_rest_macaroon)
        if not macaroon:
            raise ValueError(
                "cannot initialize CoreLightningRestWallet: "
                "invalid corelightning_rest_macaroon provided"
            )

        self.url = self.normalize_endpoint(settings.corelightning_rest_url)
        headers = {
            "macaroon": macaroon,
            "encodingtype": "hex",
            "accept": "application/json",
            "User-Agent": settings.user_agent,
        }

        # https://docs.corelightning.org/reference/lightning-pay
        # -32602: Invalid bolt11: Prefix bc is not for regtest
        # -1: Catchall nonspecific error.
        # 201: Already paid
        # 203: Permanent failure at destination.
        # 205: Unable to find a route.
        # 206: Route too expensive.
        # 207: Invoice expired.
        # 210: Payment timed out without a payment in progress.
        self.pay_failure_error_codes = [-32602, 201, 203, 205, 206, 207, 210]

        self.cert = settings.corelightning_rest_cert or False
        self.client = httpx.AsyncClient(verify=self.cert, headers=headers)
        self.last_pay_index = 0
        self.statuses = {
            "paid": True,
            "complete": True,
            "failed": False,
            "pending": None,
        }

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            r = await self.client.get(
                f"{self.url}/v1/channel/localremotebal", timeout=5
            )
            r.raise_for_status()
            data = r.json()

            if len(data) == 0:
                return StatusResponse("no data", 0)

            if "error" in data:
                return StatusResponse(f"""Server error: '{data["error"]}'""", 0)

            if r.is_error or "localBalance" not in data:
                return StatusResponse(f"Server error: '{r.text}'", 0)

            return StatusResponse(None, int(data.get("localBalance") * 1000))
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
        label = kwargs.get("label", f"lbl{random.random()}")
        data: Dict = {
            "amount": amount * 1000,
            "description": memo,
            "label": label,
        }
        if description_hash and not unhashed_description:
            raise UnsupportedError(
                "'description_hash' unsupported by CoreLightningRest, "
                "provide 'unhashed_description'"
            )

        if unhashed_description:
            data["description"] = unhashed_description.decode("utf-8")

        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]

        if kwargs.get("preimage"):
            data["preimage"] = kwargs["preimage"]

        try:
            r = await self.client.post(
                f"{self.url}/v1/invoice/genInvoice",
                data=data,
            )
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

            if "payment_hash" not in data or "bolt11" not in data:
                return InvoiceResponse(
                    False, None, None, "Server error: 'missing required fields'"
                )

            return InvoiceResponse(True, data["payment_hash"], data["bolt11"], None)
        except json.JSONDecodeError:
            return InvoiceResponse(
                False, None, None, "Server error: 'invalid json response'"
            )
        except Exception as exc:
            logger.warning(exc)
            return InvoiceResponse(
                False, None, None, f"Unable to connect to {self.url}."
            )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            invoice = decode(bolt11)
        except Bolt11Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        if not invoice.amount_msat or invoice.amount_msat <= 0:
            error_message = "0 amount invoices are not allowed"
            return PaymentResponse(False, None, None, None, error_message)
        try:
            r = await self.client.post(
                f"{self.url}/v1/pay",
                data={
                    "invoice": bolt11,
                    "maxfee": fee_limit_msat,
                },
                timeout=None,
            )

            r.raise_for_status()
            data = r.json()

            status = self.statuses.get(data["status"])
            if "payment_preimage" not in data:
                return PaymentResponse(
                    status,
                    None,
                    None,
                    None,
                    data.get("error"),
                )

            checking_id = data["payment_hash"]
            preimage = data["payment_preimage"]
            fee_msat = data["msatoshi_sent"] - data["msatoshi"]

            return PaymentResponse(status, checking_id, fee_msat, preimage, None)
        except httpx.HTTPStatusError as exc:
            try:
                logger.debug(exc)
                data = exc.response.json()
                error_code = int(data["error"]["code"])
                if error_code in self.pay_failure_error_codes:
                    error_message = f"Payment failed: {data['error']['message']}"
                    return PaymentResponse(False, None, None, None, error_message)
                error_message = f"REST failed with {data['error']['message']}."
                return PaymentResponse(None, None, None, None, error_message)
            except Exception as exc:
                error_message = f"Unable to connect to {self.url}."
                return PaymentResponse(None, None, None, None, error_message)

        except json.JSONDecodeError:
            return PaymentResponse(
                None, None, None, None, "Server error: 'invalid json response'"
            )
        except KeyError as exc:
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, "Server error: 'missing required fields'"
            )
        except Exception as exc:
            logger.info(f"Failed to pay invoice {bolt11}")
            logger.warning(exc)
            return PaymentResponse(
                None, None, None, None, f"Unable to connect to {self.url}."
            )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(
            f"{self.url}/v1/invoice/listInvoices",
            params={"payment_hash": checking_id},
        )
        try:
            r.raise_for_status()
            data = r.json()

            if r.is_error or "error" in data or data.get("invoices") is None:
                raise Exception("error in cln response")
            return PaymentStatus(self.statuses.get(data["invoices"][0]["status"]))
        except Exception as e:
            logger.error(f"Error getting invoice status: {e}")
            return PaymentPendingStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        r = await self.client.get(
            f"{self.url}/v1/pay/listPays",
            params={"payment_hash": checking_id},
        )
        try:
            r.raise_for_status()
            data = r.json()

            if r.is_error or "error" in data or not data.get("pays"):
                raise Exception("error in corelightning-rest response")

            pay = data["pays"][0]

            fee_msat, preimage = None, None
            if self.statuses[pay["status"]]:
                # cut off "msat" and convert to int
                fee_msat = -int(pay["amount_sent_msat"][:-4]) - int(
                    pay["amount_msat"][:-4]
                )
                preimage = pay["preimage"]

            return PaymentStatus(self.statuses.get(pay["status"]), fee_msat, preimage)
        except Exception as e:
            logger.error(f"Error getting payment status: {e}")
            return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                url = f"{self.url}/v1/invoice/waitAnyInvoice/{self.last_pay_index}"
                async with self.client.stream("GET", url, timeout=None) as r:
                    async for line in r.aiter_lines():
                        inv = json.loads(line)
                        if "error" in inv and "message" in inv["error"]:
                            logger.error("Error in paid_invoices_stream:", inv)
                            raise Exception(inv["error"]["message"])
                        try:
                            paid = inv["status"] == "paid"
                            self.last_pay_index = inv["pay_index"]
                            if not paid:
                                continue
                        except Exception:
                            continue
                        logger.trace(f"paid invoice: {inv}")

                        # NOTE: use payment_hash when corelightning-rest returns it
                        # when using waitAnyInvoice
                        # payment_hash = inv["payment_hash"]
                        # yield payment_hash
                        # hack to return payment_hash if the above shouldn't work
                        r = await self.client.get(
                            f"{self.url}/v1/invoice/listInvoices",
                            params={"label": inv["label"]},
                        )
                        paid_invoice = r.json()
                        logger.trace(f"paid invoice: {paid_invoice}")
                        assert self.statuses[
                            paid_invoice["invoices"][0]["status"]
                        ], "streamed invoice not paid"
                        assert "invoices" in paid_invoice, "no invoices in response"
                        assert len(paid_invoice["invoices"]), "no invoices in response"
                        yield paid_invoice["invoices"][0]["payment_hash"]

            except Exception as exc:
                logger.debug(
                    f"lost connection to corelightning-rest invoices stream: '{exc}', "
                    "reconnecting..."
                )
                await asyncio.sleep(0.02)
