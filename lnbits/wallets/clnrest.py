import asyncio
import json
import random
from typing import AsyncGenerator, Dict, Optional

import httpx
import ssl
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

from pathlib import Path

import base58
import base64
import json
import uuid
import hashlib

#decodeing runes now loosely based on https://github.com/ElementsProject/lightning/blob/master/devtools/rune.c

class Rune:
    class RuneCondition:
        IF_MISSING = 0
        EQUAL = 1
        NOT_EQUAL = 2
        BEGINS = 3
        ENDS = 4
        CONTAINS = 5
        INT_LESS = 6
        INT_GREATER = 7
        LEXO_BEFORE = 8
        LEXO_AFTER = 9
        COMMENT = 10

    def __init__(self, encoded_rune, binary_length=32):
        self.encoded_rune = encoded_rune
        self.binary_length = binary_length
        self.binary_part, self.text_part = self._decode_rune()
        self.id, self.text_parts = self._parse_text_part()

    def __str__(self):
        return self.encoded_rune

    def _decode_rune(self):
        padding = '=' * (-len(self.encoded_rune) % 4)
        encoded_rune_padded = self.encoded_rune + padding
        try:
            decoded_bytes = base64.urlsafe_b64decode(encoded_rune_padded)
        except Exception as e:
            print(f"Error decoding base64: {e}")
            return None, None

        # Extract the binary part of the specified length
        binary_part = decoded_bytes[:self.binary_length]
        try:
            text_part = decoded_bytes[self.binary_length:].decode('utf-8')
        except UnicodeDecodeError as e:
            print(f"Error decoding text part: {e}")
            return None, None

        return binary_part, text_part

    def _parse_text_part(self):
        if not self.text_part:
            return None, {}

        parts = self.text_part.split('&', 1)
        if len(parts) == 2:
            id_part = parts[0]
            remaining_text = parts[1]
        else:
            id_part = parts[0]
            remaining_text = ""

        text_parts = {}
        if remaining_text:
            for part in remaining_text.split('&'):
                key_value = part.replace('^', '=').split('=', 1)
                if len(key_value) == 2:
                    key, value = key_value
                    text_parts[key] = value
                else:
                    text_parts[key_value[0]] = None

        return id_part, text_parts

    def is_valid(self):
        return self.text_part is not None

    def to_dict(self):
        if not self.is_valid():
            return {"error": "Invalid rune"}

        return {
            "string_encoding": self.encoded_rune,
            "binary_part": self.binary_part.hex(),
            "id": self.id,
            "text_parts": self.text_parts
        }

# Provided URL-safe base64 encoded rune string
#rune_str = "rFurzBnRZT-C-1C6i7uEMsszl_1O73V80dnVNlPQ3Uk9Mzc5Jm1ldGhvZD1pbnZvaWNlJnBuYW1lYW1udW50X21zYXQ8MTAwMDAwMSZwbmFtZWxhYmVsXkxOYml0cyZyYXRlPTYw"

# Decode the rune and print the original encoded string
#rune = Rune(rune_str)
#logger.debug(rune)
#rune_dict = rune.to_dict()
#logger.debug(json.dumps(rune_dict, indent=4))

#TODO: write test
#{
#    "string_encoding": "rFurzBnRZT-C-1C6i7uEMsszl_1O73V80dnVNlPQ3Uk9Mzc5Jm1ldGhvZD1pbnZvaWNlJnBuYW1lYW1vdW50X21zYXQ8MTAwMDAwMSZwbmFtZWxhYmVsXkxOYml0cyZyYXRlPTYw",
#    "binary_part": "ac5babcc19d1653f82fb50ba8bbb8432cb3397fd4eef757cd1d9d53653d0dd49",
#    "text_parts": {
#        "": "379",
#        "method": "invoice",
#        "pnameamount_msat<1000001": null,
#        "pnamelabel": "LNbits",
#        "rate": "60"
#    }
#}


class CLNRestWallet(Wallet):

    def __init__(self):

        if not settings.clnrest_url:
            raise ValueError("Cannot initialize CLNRestWallet: missing CLNREST_URL")

        if not settings.clnrest_nodeid:
            raise ValueError("Cannot initialize CLNRestWallet: missing CLNREST_NODEID")

        if settings.clnrest_url.startswith("https://"):
            logger.info("Using SSL")
            if not settings.clnrest_cert:
                logger.warning(
                    "No certificate for the CLNRestWallet provided! "
                    "This only works if you have a publicly issued certificate."
                    "Set CLNREST_CERT to the certificate file (~/.lightning/bitcoin/server.pem)"
                )
            else:
                #The cert that is generated by core lightning by default is only valid for DNS=localhost, DNS=cln with core lightning by default
                #This will allow you to check the certificate but ignore the hostname issue
                self.bypass_ssl_hostname_check = True
            
        elif settings.clnrest_url.startswith("http://"):
            logger.warning("NOT Using SSL")
            raise ValueError ('#TODO: consider not allowing this unless the hostname is localhost')

        if settings.clnrest_readonly_rune:
            self.readonly_rune=Rune(settings.clnrest_readonly_rune)
            logger.debug(f"TODO: make sure that it has the correct permissions: {self.readonly_rune}:")
            logger.debug(self.readonly_rune)
            logger.debug(json.dumps(self.readonly_rune.to_dict()))

        else:
            raise ValueError(
                "Cannot initialize CLNRestWallet: missing CLNREST_READONLY_RUNE. Create one with:\n"
                """ lightning-cli createrune restrictions='[["method=listfunds", "method=listpays", "method=listinvoices", "method=getinfo", "method=summary", "method=waitanyinvoice"]]' """
            )

        if settings.clnrest_invoice_rune:
            self.invoice_endpoint = "v1/invoice"
            logger.debug(f"TODO: decode this invoice_rune and make sure that it has the correct permissions: {settings.clnrest_invoice_rune}:")
            self.invoice_rune=Rune(settings.clnrest_invoice_rune)
            logger.debug(self.invoice_rune)
            logger.debug(json.dumps(self.invoice_rune.to_dict()))
        else:
            self.invoice_endpoint = None
            self.invoice_rune=None
            logger.warning(
                "Will be unable to create any invoices without setting 'CLNREST_INVOICE_RUNE'. Please create one with one of the following commands:\n"
                """ lightning-cli createrune restrictions='[["method=invoice"], ["pnameamount_msat<1000001"], ["pname_label^LNbits"], ["rate=60"]]' """
                )

        if settings.clnrest_pay_rune and settings.clnrest_renepay_rune:
            raise ValueError( "Cannot initialize CLNRestWallet: both CLNREST_PAY_RUNE and CLNREST_RENEPAY_RUNE are set. Only one should be set.")
        elif not settings.clnrest_pay_rune and not settings.clnrest_renepay_rune:
            self.payment_endpoint = None
            logger.warning (
                    "Will be unable to pay any invoices without either setings either 'CLNREST_PAY_RUNE' or 'CLNREST_RENEPAY_RUNE'. Please create one with one of the following commands:\n"
                    """   lightning-cli createrune restrictions='[["method=pay"],     ["pinvbolt11_amount<1001"], ["pname_label^LNbits"], ["rate=1"]]' \n"""
                    """   lightning-cli createrune restrictions='[["method=renepay"], ["pinvbolt11_amount<1001"], ["pname_label^LNbits"], ["rate=1"]]' """
                    )
        elif settings.clnrest_pay_rune:
            self.payment_endpoint="v1/pay"
            logger.debug(f"TODO: sure that it has the correct permissions: {settings.clnrest_pay_rune}:")
            self.pay_rune=Rune(settings.clnrest_pay_rune)
            logger.debug(self.pay_rune)
            logger.debug(json.dumps(self.pay_rune.to_dict()))
        elif settings.clnrest_renepay_rune:
            self.payment_endpoint="v1/renepay"
            logger.debug(f"TODO: make sure that it has the correct permissions: {settings.clnrest_renepay_rune}:")
            self.pay_rune=Rune(settings.clnrest_renepay_rune)
            logger.debug(self.pay_rune)
            logger.debug(json.dumps(self.pay_rune.to_dict()))
        else:
            self.payment_endpoint = None
            self.pay_rune = None


        self.url = self.normalize_endpoint(settings.clnrest_url)

        self.base_headers = {
            "accept": "application/json",
            "User-Agent": settings.user_agent,
            "Content-Type": "application/json",
        }
        
        self.readonly_headers = {**self.base_headers, "rune": settings.clnrest_readonly_rune, "nodeid": settings.clnrest_nodeid}



        #todo: consider moving this somewhere else
        if settings.clnrest_renepay_rune:
            self.pay_headers = {**self.base_headers, "rune": settings.clnrest_renepay_rune, "nodeid": settings.clnrest_nodeid}
        elif settings.clnrest_pay_rune:
            self.pay_headers = {**self.base_headers, "rune": settings.clnrest_pay_rune, "nodeid": settings.clnrest_nodeid}
        else:
            self.pay_headers = None

        # https://docs.corelightning.org/reference/lightning-pay
        # 201: Already paid
        # 203: Permanent failure at destination.
        # 205: Unable to find a route.
        # 206: Route too expensive.
        # 207: Invoice expired.
        # 210: Payment timed out without a payment in progress.
        self.pay_failure_error_codes = [201, 203, 205, 206, 207, 210]

        self.cert  = settings.clnrest_cert or False
        self.client = self.create_client()
        self.last_pay_index = 0
        self.statuses = {
            "paid": True,
            "complete": True,
            "failed": False,
            "pending": None,
        }


    def create_client(self) -> httpx.AsyncClient:
        """Create an HTTP client with specified headers and SSL configuration."""

        if self.cert:
            ssl_context = ssl.create_default_context()
            cert_path = Path(self.cert)
            if cert_path.is_file():
                ssl_context.load_verify_locations(cert_path)
            else:
                ssl_context.load_verify_locations(cadata=self.cert)
            if self.bypass_ssl_hostname_check:
                ssl_context.check_hostname = False
            return httpx.AsyncClient(base_url=self.url, verify=ssl_context)
        else:
            assert self.url.startswith("http://localhost"), "URL must start with 'http://localhost' if you don't want to use SSL"
            return httpx.AsyncClient(base_url=self.url, verify=False)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as e:
            logger.warning(f"Error closing wallet connection: {e}")

    async def status(self) -> StatusResponse:
        try:
            logger.debug("REQUEST to /v1/getinfo")
            r = await self.client.post( "/v1/listfunds", timeout=15, headers=self.readonly_headers)
        except (httpx.ConnectError, httpx.RequestError) as e:
            logger.error(f"Connection error: {str(e)}")
            return StatusResponse(f"Unable to connect to '{self.endpoint}'", 0)

        try:
            response_data = r.json()
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {str(e)}")
            return StatusResponse(f"Failed to decode JSON response from {self.url}", 0)

        if r.is_error or "error" in response_data:
            error_message = response_data.get("error", r.text)
            return StatusResponse(f"Failed to connect to {self.url}, got: '{error_message}'...", 0)

        if not response_data:
            return StatusResponse("no data", 0)

        channels = response_data.get("channels")
        if not channels:
            return StatusResponse("no data or no channels available", 0)

        total_our_amount_msat = sum(channel["our_amount_msat"] for channel in channels)

        return StatusResponse(None, total_our_amount_msat)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        label_prefix: Optional[str] = "LNbits ",
        **kwargs,
    ) -> InvoiceResponse:

        if not self.invoice_rune:
            #todo: try to load the wallet specific invoice rune here instead of from self.invoice_headers which comes from self.settings.cln_invoice_rune
            return InvoiceResponse( False, None, None, "Unable to invoice without a valid invoice rune")
        else:
            self.invoice_headers = {**self.base_headers, "rune": self.invoice_rune, "nodeid": settings.clnrest_nodeid}

        #TODO: the identifier could be used to encode the LNBits user or the LNBits wallet that is creating the invoice

        identifier = base58.b58encode(hashlib.sha256(str(self.invoice_headers).encode('utf-8')).digest()[:16]).decode('utf-8').rstrip('=')
        label = label_prefix + identifier + ' ' + base58.b58encode(uuid.uuid4().bytes).rstrip(b'=').decode('utf-8')

        data: Dict = {
            "amount_msat": amount * 1000,
            "description": memo,
            "label": label,
        }
        if description_hash and not unhashed_description:
            raise Unsupported(
                "'description_hash' unsupported by CoreLightningRest, "
                "provide 'unhashed_description'"
            )

        if unhashed_description:
            data["description"] = unhashed_description.decode("utf-8")

        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]

        if kwargs.get("preimage"):
            data["preimage"] = kwargs["preimage"]

        logger.debug(f"REQUEST to {self.invoice_endpoint}: : {json.dumps(data)}")

        try:
            r = await self.client.post(
                self.invoice_endpoint,
                json=data,
                headers=self.invoice_headers,
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

    async def pay_invoice(
            self,
            bolt11: str,
            fee_limit_msat: int,
            label_prefix: Optional[str] = "LNbits ",
            ) -> PaymentResponse:
        #todo: rune restrictions will not be enforced for internal payments within the lnbits instance as they are not routed through to core lightning

        if not self.pay_headers:
            return InvoiceResponse( False, None, None, "Unable to invoice without a valid rune")

        try:
            invoice = decode(bolt11)
            logger.debug(invoice)
            logger.debug(invoice.description)

        except Bolt11Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))


        if not invoice.amount_msat or invoice.amount_msat <= 0:
            error_message = "0 amount invoices are not allowed"
            return PaymentResponse(False, None, None, None, error_message)

        #TODO: the identifier could be used to encode the LNBits user or the LNBits wallet that is pay the invoice
        identifier = base58.b58encode(hashlib.sha256(str(self.pay_headers).encode('utf-8')).digest()[:16]).decode('utf-8').rstrip('=')
        label = label_prefix + identifier + ' ' + base58.b58encode(uuid.uuid4().bytes).rstrip(b'=').decode('utf-8')

        if (self.payment_endpoint == "v1/renepay"):
            maxfee = fee_limit_msat
            maxdelay = 300
            data = {
                "invstring": bolt11,
                "label": label,
                "description": invoice.description,
                "maxfee": maxfee,
                "retry_for": 60,
            }
                #"amount_msat": invoice.amount_msat,
                #"maxdelay": maxdelay,
                # Add other necessary parameters like retry_for, description, label as required
        elif (self.payment_endpoint == "v1/pay"):
            fee_limit_percent = fee_limit_msat / invoice.amount_msat * 100
            data = {
                "bolt11": bolt11,
                "label": label,
                "description": invoice.description,
                "maxfeepercent": f"{fee_limit_percent:.11}",
                "exemptfee": 0,  # so fee_limit_percent is applied even on payments
                # with fee < 5000 millisatoshi (which is default value of exemptfee)
            }
        else:
            raise Error ("this should never happen")

        logger.debug(f"REQUEST to {self.payment_endpoint}: {json.dumps(data)}")

        r = await self.client.post(
            self.payment_endpoint,
            json=data,
            headers=self.pay_headers,
            timeout=None,
        )

        if r.is_error or "error" in r.json():
            try:
                data = r.json()
                logger.debug(f"RESPONSE with error: {data}")
                error_message = data["error"]
            except Exception:
                error_message = r.text
            return PaymentResponse(False, None, None, None, error_message)

        data = r.json()
        logger.debug(f"RESPONSE: {data}")

        if data["status"] != "complete":
            return PaymentResponse(False, None, None, None, "payment failed")

        #destination = data['destination']
        #created_at = data['created_at']
        #parts = data['parts']
        status = data['status']

        checking_id = data["payment_hash"]
        preimage = data["payment_preimage"]

        amount_sent_msat_int = data.get('amount_sent_msat')
        amount_msat_int = data.get('amount_msat')
        fee_msat = amount_sent_msat_int - amount_msat_int

        return PaymentResponse(
            self.statuses.get(data["status"]), checking_id, fee_msat, preimage, None
        )

        return PaymentResponse(status, checking_id, fee_msat, preimage, None)

    async def invoice_status(self, checking_id: str) -> PaymentStatus:
        logger.error("why is something calling invoice_status from clnrest.py")
        # Call get_invoice_status instead
        return await self.get_invoice_status(checking_id)

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        data: Dict = { "payment_hash": checking_id }
        logger.debug(f"REQUEST to /v1/listinvoices: {json.dumps(data)}")
        r = await self.client.post(
            "/v1/listinvoices",
            json=data,
            headers=self.readonly_headers,
        )
        try:
            r.raise_for_status()
            data = r.json()

            if r.is_error or "error" in data or data.get("invoices") is None:
                raise Exception("error in cln response")
            logger.debug(f"RESPONSE: invoice with payment_hash {data['invoices'][0]['payment_hash']} has status {data['invoices'][0]['status']}")
            return PaymentStatus(self.statuses.get(data["invoices"][0]["status"]))
        except Exception as e:
            logger.error(f"Error getting invoice status: {e}")
            return PaymentPendingStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        data: Dict = { "payment_hash": checking_id }

        logger.debug(f"REQUEST to /v1/listpays: {json.dumps(data)}")
        r = await self.client.post(
            "/v1/listpays",
            json=data,
            headers=self.readonly_headers,
        )
        try:
            r.raise_for_status()
            data = r.json()
            logger.debug(data)

            if r.is_error or "error" in data:
                logger.error(f"API response error: {data}")
                raise Exception("Error in corelightning-rest response")

            pays_list = data.get("pays", [])
            if not pays_list:
                logger.debug(f"No payments found for payment hash {checking_id}. Payment is pending.")
                return PaymentStatus(self.statuses.get("pending"))

            if len(pays_list) != 1:
                error_message = f"Expected one payment status, but found {len(pays_list)}"
                logger.error(error_message)
                raise Exception(error_message)

            pay = pays_list[0]
            logger.debug(f"Payment status from API: {pay['status']}")

            fee_msat, preimage = None, None
            if pay['status'] == 'complete':
                fee_msat = pay["amount_sent_msat"] - pay["amount_msat"]
                preimage = pay["preimage"]

            return PaymentStatus(self.statuses.get(pay["status"]), fee_msat, preimage)
        except Exception as e:
            logger.error(f"Error getting payment status: {e}")
            return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while True:
            try:
                read_timeout=None
                data: Dict = { "lastpay_index": self.last_pay_index, "timeout": read_timeout}
                request_timeout = httpx.Timeout(connect=5.0, read=read_timeout, write=60.0, pool=60.0)
                url = "/v1/waitanyinvoice"
                logger.debug(f"REQUEST(stream) to  /v1/waitanyinvoice with data: {data}.")
                async with self.client.stream("POST", url, json=data, headers=self.readonly_headers,  timeout=request_timeout) as r:
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
                        payment_hash = inv["payment_hash"]
                        yield payment_hash

                        #TODO: ask about why this might ever be needed

                        # hack to return payment_hash if the above shouldn't work
                        #r = await self.client.get(
                        #    "/v1/invoice/listInvoices",
                        #    params={"label": inv["label"]},
                        #)
                        #paid_invoice = r.json()
                        #logger.trace(f"paid invoice: {paid_invoice}")
                        #assert self.statuses[
                        #    paid_invoice["invoices"][0]["status"]
                        #], "streamed invoice not paid"
                        #assert "invoices" in paid_invoice, "no invoices in response"
                        #assert len(paid_invoice["invoices"]), "no invoices in response"
                        #logger.debug(inv)
                        #yield paid_invoice["invoices"][0]["payment_hash"]

            except Exception as exc:
                logger.debug(
                    f"lost connection to corelightning-rest invoices stream: '{exc}', "
                    "reconnecting..."
                )
                await asyncio.sleep(0.5)
