import asyncio
import hashlib
import json
import random
from collections.abc import AsyncGenerator
from typing import Optional

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


class SparkError(Exception):
    pass


class UnknownError(Exception):
    pass


class SparkWallet(Wallet):
    def __init__(self):
        if not settings.spark_url:
            raise ValueError("cannot initialize SparkWallet: missing spark_url")
        if not settings.spark_token:
            raise ValueError("cannot initialize SparkWallet: missing spark_token")

        url = self.normalize_endpoint(settings.spark_url)
        url = url.replace("/rpc", "")
        self.token = settings.spark_token

        headers = {"X-Access": self.token, "User-Agent": settings.user_agent}
        self.client = httpx.AsyncClient(base_url=url, headers=headers)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    def __getattr__(self, key):
        async def call(*args, **kwargs):
            if args and kwargs:
                raise TypeError(
                    "must supply either named arguments or a list of arguments, not"
                    f" both: {args} {kwargs}"
                )
            elif args:
                params = args
            elif kwargs:
                params = kwargs
            else:
                params = {}

            try:
                r = await self.client.post(
                    "/rpc",
                    json={"method": key, "params": params},
                    timeout=60 * 60 * 24,
                )
                r.raise_for_status()
            except (
                OSError,
                httpx.ConnectError,
                httpx.RequestError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ) as exc:
                raise UnknownError(f"error connecting to spark: {exc}") from exc

            try:
                data = r.json()
            except Exception as exc:
                raise UnknownError(r.text) from exc

            if r.is_error:
                if r.status_code == 401:
                    raise SparkError("Access key invalid!")

                raise SparkError(data["message"])

            return data

        return call

    async def status(self) -> StatusResponse:
        try:
            funds = await self.listfunds()
        except (httpx.ConnectError, httpx.RequestError):
            return StatusResponse("Couldn't connect to Spark server", 0)
        except (SparkError, UnknownError) as e:
            return StatusResponse(str(e), 0)

        return StatusResponse(
            None, sum([ch["channel_sat"] * 1000 for ch in funds["channels"]])
        )

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        label = f"lbs{random.random()}"
        checking_id = label

        try:
            if description_hash:
                r = await self.invoicewithdescriptionhash(
                    msatoshi=amount * 1000,
                    label=label,
                    description_hash=description_hash.hex(),
                )
            elif unhashed_description:
                r = await self.invoicewithdescriptionhash(
                    msatoshi=amount * 1000,
                    label=label,
                    description_hash=hashlib.sha256(unhashed_description).hexdigest(),
                )
            else:
                r = await self.invoice(
                    msatoshi=amount * 1000,
                    label=label,
                    description=memo or "",
                    exposeprivatechannels=True,
                    expiry=kwargs.get("expiry"),
                )
            ok, payment_request, error_message = True, r["bolt11"], ""
        except (SparkError, UnknownError) as e:
            ok, payment_request, error_message = False, None, str(e)

        return InvoiceResponse(ok, checking_id, payment_request, error_message)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            r = await self.pay(
                bolt11=bolt11,
                maxfee=fee_limit_msat,
            )
            fee_msat = -int(r["msatoshi_sent"] - r["msatoshi"])
            preimage = r["payment_preimage"]
            return PaymentResponse(True, r["payment_hash"], fee_msat, preimage, None)

        except (SparkError, UnknownError) as exc:
            listpays = await self.listpays(bolt11)
            if not listpays:
                return PaymentResponse(False, None, None, None, str(exc))

            pays = listpays["pays"]

            if len(pays) == 0:
                return PaymentResponse(False, None, None, None, str(exc))

            pay = pays[0]
            payment_hash = pay["payment_hash"]

            if len(pays) > 1:
                raise SparkError(
                    f"listpays({payment_hash}) returned an unexpected response:"
                    f" {listpays}"
                ) from exc

            if pay["status"] == "failed":
                return PaymentResponse(False, None, None, None, str(exc))

            if pay["status"] == "pending":
                return PaymentResponse(None, payment_hash, None, None, None)

            if pay["status"] == "complete":
                r = pay
                r["payment_preimage"] = pay["preimage"]
                r["msatoshi"] = int(pay["amount_msat"][0:-4])
                r["msatoshi_sent"] = int(pay["amount_sent_msat"][0:-4])
                # this may result in an error if it was paid previously
                # our database won't allow the same payment_hash to be added twice
                # this is good
                fee_msat = -int(r["msatoshi_sent"] - r["msatoshi"])
                preimage = r["payment_preimage"]
                return PaymentResponse(
                    True, r["payment_hash"], fee_msat, preimage, None
                )
            else:
                return PaymentResponse(False, None, None, None, str(exc))

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = await self.listinvoices(label=checking_id)
        except (SparkError, UnknownError):
            return PaymentPendingStatus()

        if not r or not r.get("invoices"):
            return PaymentPendingStatus()

        if r["invoices"][0]["status"] == "paid":
            return PaymentSuccessStatus()
        else:
            return PaymentFailedStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        # check if it's 32 bytes hex
        if len(checking_id) != 64:
            return PaymentPendingStatus()
        try:
            int(checking_id, 16)
        except ValueError:
            return PaymentPendingStatus()

        # ask sparko
        try:
            r = await self.listpays(payment_hash=checking_id)
        except (SparkError, UnknownError):
            return PaymentPendingStatus()

        if not r["pays"]:
            return PaymentFailedStatus()
        if r["pays"][0]["payment_hash"] == checking_id:
            status = r["pays"][0]["status"]
            if status == "complete":
                fee_msat = -(
                    int(r["pays"][0]["amount_sent_msat"][0:-4])
                    - int(r["pays"][0]["amount_msat"][0:-4])
                )
                return PaymentSuccessStatus(
                    fee_msat=fee_msat, preimage=r["pays"][0]["preimage"]
                )
            if status == "failed":
                return PaymentFailedStatus()
            return PaymentPendingStatus()
        raise KeyError("supplied an invalid checking_id")

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        url = f"/stream?access-key={self.token}"

        while settings.lnbits_running:
            try:
                async with self.client.stream("GET", url, timeout=None) as r:
                    async for line in r.aiter_lines():
                        if line.startswith("data:"):
                            data = json.loads(line[5:])
                            if "pay_index" in data and data.get("status") == "paid":
                                yield data["label"]
            except (
                OSError,
                httpx.ReadError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.HTTPError,
            ):
                pass

            logger.error("lost connection to spark /stream, retrying in 5 seconds")
            await asyncio.sleep(5)
