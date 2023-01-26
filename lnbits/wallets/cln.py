try:
    from pyln.client import LightningRpc, RpcError  # type: ignore
except ImportError:  # pragma: nocover
    LightningRpc = None

import asyncio
import hashlib
import random
import time
from functools import partial, wraps
from typing import AsyncGenerator, Optional

from loguru import logger

from lnbits import bolt11 as lnbits_bolt11
from lnbits.settings import settings
from lnbits.helpers import b64_hex_transform, b64_transform

from .base import (
    InvoiceResponse,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Unsupported,
    Wallet,
)


def async_wrap(func):
    @wraps(func)
    async def run(*args, loop=None, executor=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        partial_func = partial(func, *args, **kwargs)
        return await loop.run_in_executor(executor, partial_func)

    return run


def _pay_invoice(ln, payload):
    return ln.call("pay", payload)

def _paid_invoices_stream(ln, last_pay_index):
    return ln.waitanyinvoice(last_pay_index)

def _pay_keysend(ln, payload):
    return ln.call("keysend", payload)


class CoreLightningWallet(Wallet):
    def __init__(self):
        if LightningRpc is None:  # pragma: nocover
            raise ImportError(
                "The `pyln-client` library must be installed to use `CoreLightningWallet`."
            )

        self.rpc = settings.corelightning_rpc or settings.clightning_rpc
        self.ln = LightningRpc(self.rpc)

        # check if description_hash is supported (from CLN>=v0.11.0)
        self.supports_description_hash = (
            "deschashonly" in self.ln.help("invoice")["help"][0]["command"]
        )

        # check last payindex so we can listen from that point on
        self.last_pay_index = 0
        invoices = self.ln.listinvoices()
        for inv in invoices["invoices"][::-1]:
            if "pay_index" in inv:
                self.last_pay_index = inv["pay_index"]
                break

    async def status(self) -> StatusResponse:
        try:
            funds = self.ln.listfunds()
            return StatusResponse(
                None, sum([ch["channel_sat"] * 1000 for ch in funds["channels"]])
            )
        except RpcError as exc:
            error_message = f"lightningd '{exc.method}' failed with '{exc.error}'."
            return StatusResponse(error_message, 0)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        label = "lbl{}".format(random.random())
        msat: int = int(amount * 1000)
        try:
            if description_hash and not unhashed_description:
                raise Unsupported(
                    "'description_hash' unsupported by CLN, provide 'unhashed_description'"
                )
            if unhashed_description and not self.supports_description_hash:
                raise Unsupported("unhashed_description")
            r = self.ln.invoice(
                msatoshi=msat,
                label=label,
                description=unhashed_description.decode()
                if unhashed_description
                else memo,
                exposeprivatechannels=True,
                deschashonly=True
                if unhashed_description
                else False,  # we can't pass None here
                expiry=kwargs.get("expiry"),
            )

            if r.get("code") and r.get("code") < 0:
                raise Exception(r.get("message"))

            return InvoiceResponse(True, r["payment_hash"], r["bolt11"], "")
        except RpcError as exc:
            error_message = f"CLN method '{exc.method}' failed with '{exc.error.get('message') or exc.error}'."
            return InvoiceResponse(False, None, None, error_message)
        except Exception as e:
            return InvoiceResponse(False, None, None, str(e))

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        invoice = lnbits_bolt11.decode(bolt11)

        previous_payment = await self.get_payment_status(invoice.payment_hash)
        if previous_payment.paid:
            return PaymentResponse(False, None, None, None, "invoice already paid")

        fee_limit_percent = fee_limit_msat / invoice.amount_msat * 100

        payload = {
            "bolt11": bolt11,
            "maxfeepercent": "{:.11}".format(fee_limit_percent),
            "exemptfee": 0,  # so fee_limit_percent is applied even on payments with fee < 5000 millisatoshi (which is default value of exemptfee)
        }
        try:
            wrapped = async_wrap(_pay_invoice)
            r = await wrapped(self.ln, payload)
        except RpcError as exc:
            try:
                error_message = exc.error["attempts"][-1]["fail_reason"]
            except:
                error_message = f"CLN method '{exc.method}' failed with '{exc.error.get('message') or exc.error}'."
            return PaymentResponse(False, None, None, None, error_message)
        except Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        fee_msat = -int(r["msatoshi_sent"] - r["msatoshi"])
        return PaymentResponse(
            True, r["payment_hash"], fee_msat, r["payment_preimage"], None
        )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = self.ln.listinvoices(payment_hash=checking_id)
        except:
            return PaymentStatus(None)
        if not r["invoices"]:
            return PaymentStatus(None)

        invoice_resp = r["invoices"][-1]

        if invoice_resp["payment_hash"] == checking_id:
            if invoice_resp["status"] == "paid":
                return PaymentStatus(True)
            elif invoice_resp["status"] == "unpaid":
                return PaymentStatus(None)
        logger.warning(f"supplied an invalid checking_id: {checking_id}")
        return PaymentStatus(None)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            r = self.ln.call("listpays", {"payment_hash": checking_id})
        except:
            return PaymentStatus(None)
        if not r["pays"]:
            return PaymentStatus(None)
        payment_resp = r["pays"][-1]

        if payment_resp["payment_hash"] == checking_id:
            status = payment_resp["status"]
            if status == "complete":
                fee_msat = -int(
                    payment_resp["amount_sent_msat"] - payment_resp["amount_msat"]
                )

                return PaymentStatus(True, fee_msat, payment_resp["preimage"])
            elif status == "failed":
                return PaymentStatus(False)
            return PaymentStatus(None)
        logger.warning(f"supplied an invalid checking_id: {checking_id}")
        return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while True:
            try:
                wrapped = async_wrap(_paid_invoices_stream)
                paid = await wrapped(self.ln, self.last_pay_index)
                self.last_pay_index = paid["pay_index"]
                yield paid["payment_hash"]
            except Exception as exc:
                logger.error(
                    f"lost connection to cln invoices stream: '{exc}', retrying in 5 seconds"
                )
                await asyncio.sleep(5)

    async def pay_with_keysend(self, public_key: str, pre_image: str, payment_hash: str, amount_msat: int, fee_limit_msat: int, message: str) -> PaymentResponse:
        # The record 5482373484 is special: it carries the pre_image to
        # the destination so it can be compared with the hash we pass
        # via the payment_hash

        # custom tvl's are possible with cln > 0.11.1,
        # this means we'll have checking_id mismatch
        dest_custom_records = {
            5482373484: b64_hex_transform(pre_image),
            34349334: b64_transform(message),
        }

        fee_limit_percent = fee_limit_msat / amount_msat * 100

        # needs update with cln/pyln 0.12
        # umbrel defaults to 0.11.0 now
        payload = {
            "destination": public_key,
            # renamed to "amount_msat" with pyln 0.12
            "msatoshi": amount_msat,
            "label": message,
            "maxfeepercent": "{:.11}".format(fee_limit_percent),
            "exemptfee": 0,  # so fee_limit_percent is applied even on payments with fee < 5000 millisatoshi (which is default value of exemptfee)
            # requires cln > 0.11.1, unsupported earlier
            # "extratlvs": dest_custom_records,
            "retry_for": 30,
        }

        try:
            wrapped = async_wrap(_pay_keysend)
            r = await wrapped(self.ln, payload)
        except RpcError as exc:
            try:
                error_message = exc.error["attempts"][-1]["fail_reason"]
            except:
                error_message = f"CLN method '{exc.method}' failed with '{exc.error.get('message') or exc.error}'."
            return PaymentResponse(False, None, None, None, error_message)
        except Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        fee_msat = -int(r["msatoshi_sent"] - r["msatoshi"])
        return PaymentResponse(
            True, r["payment_hash"], fee_msat, r["payment_preimage"], None
        )
