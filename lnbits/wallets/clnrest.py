import asyncio
import json
import random
from typing import AsyncGenerator, Dict, Optional

import httpx
import ssl
import os
from bolt11 import Bolt11Exception
from bolt11.decode import decode
from loguru import logger

from urllib.parse import urlparse
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

import base64
import uuid
#from pathlib import Path


class CLNRestWallet(Wallet):
    def __init__(self):
        if not settings.clnrest_url:
            raise ValueError(
                "cannot initialize CLNRestWallet: "
                "missing clnrest_url"
            )
            raise ValueError("Cannot initialize CLNRestWallet: missing CLNREST_URL")


        if not settings.clnrest_readonly_rune:
            raise ValueError(
                "cannot initialize CLNRestWallet: "
                "missing clnrest_readonly_rune"
            )

        logger.debug(f"TODO: validate and check permissions of readonly_rune: {settings.clnrest_readonly_rune[:4]}")



        self.url = self.normalize_endpoint(settings.clnrest_url)

        if not settings.clnrest_nodeid:
            logger.info("missing CLNREST_NODEID, but this is only needed for v23.08")

        self.base_headers = {
            "accept": "application/json",
            "User-Agent": settings.user_agent,
            "Content-Type": "application/json",
        }

        if hasattr(settings, "clnrest_nodeid") and settings.clnrest_nodeid is not None:
            self.base_headers["nodeid"] = settings.clnrest_nodeid

        # Ensure the readonly rune is set
        if hasattr(settings, "clnrest_readonly_rune") and settings.clnrest_readonly_rune is not None:
            self.readonly_headers = {**self.base_headers, "rune": settings.clnrest_readonly_rune}
        else:
            logger.error("Readonly rune 'CLNREST_READONLY_RUNE' is required but not set.")

        if hasattr(settings, "clnrest_invoice_rune") and settings.clnrest_invoice_rune is not None:
            logger.debug( f"TODO: decode this invoice_rune and make sure that it has the correct permissions: {settings.clnrest_invoice_rune[:4]}")
            self.invoice_headers = {**self.base_headers, "rune": settings.clnrest_invoice_rune}
        else:
            logger.warning( "Will be unable to create any invoices without setting 'CLNREST_INVOICE_RUNE[:4]'")

        if hasattr(settings, "clnrest_pay_rune") and settings.clnrest_pay_rune is not None:
            logger.debug( f"TODO: decode this pay_rune and make sure that it has the correct permissions: {settings.clnrest_pay_rune[:4]}")
            self.pay_headers = {**self.base_headers, "rune": settings.clnrest_pay_rune}
        else:
            logger.warning( "Will be unable to call pay endpoint without setting 'CLNREST_PAY_RUNE'")

        if hasattr(settings, "clnrest_renepay_rune") and settings.clnrest_renepay_rune is not None:
            logger.debug( f"TODO: decode this renepay_rune and make sure that it has the correct permissions: {settings.clnrest_renepay_rune[:4]}")
            self.renepay_headers = {**self.base_headers, "rune": settings.clnrest_renepay_rune}
        else:
            logger.warning( "Will be unable to call renepay endpoint without setting 'CLNREST_RENEPAY_RUNE'")

        # https://docs.corelightning.org/reference/lightning-pay
        # -32602: Invalid bolt11: Prefix bc is not for regtest
        # -1: Catchall nonspecific error.
        # 201: Already paid
        # 203: Permanent failure at destination.
        # 205: Unable to find a route.
        # 206: Route too expensive.
        # 207: Invoice expired.
        # 210: Payment timed out without a payment in progress.
        # 401: Unauthorized. Probably a rune issue

        self.pay_failure_error_codes = [-32602, 201, 203, 205, 206, 207, 210, 401]

        self.client = self.create_client()

        self.last_pay_index = settings.clnrest_last_pay_index
        


        self.statuses = {
            "paid": True,
            "complete": True,
            "failed": False,
            "pending": None,
        }

    def create_client(self) -> httpx.AsyncClient:
        """Create an HTTP client with specified headers and SSL configuration."""

        parsed_url = urlparse(self.url)

        # Validate the URL scheme
        if parsed_url.scheme == 'http':
            if parsed_url.hostname in ('localhost', '127.0.0.1', '::1'):
                logger.warning("Not using SSL for connection to CLNRestWallet")
            else:
                raise ValueError(
                    "Insecure HTTP connections are only allowed for localhost or equivalent IP addresses. "
                    "Set CLNREST_URL to https:// for external connections or use localhost."
                )
            return httpx.AsyncClient(base_url=self.url, verify=False)

        elif parsed_url.scheme == 'https':
            logger.info(f"Using SSL to connect to {self.url}")

            # Check for CA certificate
            if not settings.clnrest_ca:
                logger.warning(
                    "No CA certificate provided for CLNRestWallet. "
                    "This setup requires a CA certificate for server authentication and trust. "
                    "Set CLNREST_CA to the CA certificate file path or the contents of the certificate."
                )
                raise ValueError("CA certificate is required for secure communication.")
            else:
                logger.info(f"CA Certificate provided: {settings.clnrest_ca}")

            # Create an SSL context and load the CA certificate
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

            # Load CA certificate
            if os.path.isfile(settings.clnrest_ca):
                logger.info(f"Using CA certificate file: {settings.clnrest_ca}")
                ssl_context.load_verify_locations(cafile=settings.clnrest_ca)
            else:
                logger.info("Using CA certificate from PEM string: ")
                ca_content = settings.clnrest_ca.replace('\\n', '\n')
                logger.info(ca_content)
                ssl_context.load_verify_locations(cadata=ca_content)

            # Optional: Disable hostname checking if necessary
            # especially for ip addresses
            ssl_context.check_hostname = False

            # Create the HTTP client without a client certificate
            client = httpx.AsyncClient(base_url=self.url, verify=ssl_context)

            return client

        else:
            raise ValueError("CLNREST_URL must start with http:// or https://")

    async def cleanup(self):
        try:
            await self.client.aclose()
        except RuntimeError as exc:
            logger.warning(f"Error closing wallet connection: {exc}")

    async def status(self) -> StatusResponse:
        try:
            logger.debug("REQUEST to /v1/listfunds")

            r = await self.client.post( "/v1/listfunds", timeout=15, headers=self.readonly_headers)
            r.raise_for_status()

            response_data = r.json()

            if not response_data:
                logger.error("Received empty response data")
                return StatusResponse("no data", 0)

            channels = response_data.get("channels", [])
            total_our_amount_msat = sum( channel.get("our_amount_msat", 0) for channel in channels)

            return StatusResponse(None, total_our_amount_msat)

        except json.JSONDecodeError as exc:
            logger.error(f"JSON decode error: {str(exc)}")
            return StatusResponse(f"Failed to decode JSON response from {self.url}", 0)

        except httpx.ReadTimeout:
            logger.error(
                "Timeout error: The server did not respond in time. "
                "This can happen if the server is running HTTPS but the client is using HTTP."
            )
            return StatusResponse(f"Unable to connect to 'v1/listfunds' due to timeout", 0)

        except (httpx.ConnectError, httpx.RequestError) as exc:
            logger.error(f"Connection error: {exc}")
            return StatusResponse(f"Unable to connect to 'v1/listfunds'", 0)

        except httpx.HTTPStatusError as exc:
            logger.error(
                f"HTTP error: {exc.response.status_code} {exc.response.reason_phrase} "
                f"while accessing {exc.request.url}"
            )
            return StatusResponse(
                f"Failed with HTTP {exc.response.status_code} on 'v1/listfunds'", 0
            )


    async def create_invoice(
            self,
            #identifier: str,
            amount: int,
            memo: Optional[str] = None,
            description_hash: Optional[bytes] = None,
            unhashed_description: Optional[bytes] = None,
            **kwargs,
        ) -> InvoiceResponse:

        logger.debug( f"Creating invoice with parameters: amount={amount}, memo={memo}, description_hash={description_hash}, unhashed_description={unhashed_description}, kwargs={kwargs}")

        if not settings.clnrest_invoice_rune:
            return InvoiceResponse( False, None, None, "Unable to invoice without an invoice rune")

        label_prefix = "LNbits_testing"
        identifier = "{wallet.user}_wallet.id}"
        random_uuid= base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b'=').decode('utf-8')
        label = f"{label_prefix} {identifier} {random_uuid}"

        data: Dict = {
            "amount_msat": int(amount * 1000),
            "description": memo,
            "label": label,
        }
        logger.error(data)

        if description_hash and not unhashed_description:
            raise UnsupportedError(
                "'description_hash' unsupported by CoreLightningRest, "
                "provide 'unhashed_description'"
                "TODO: find out if this is also the case with CLNRest"
            )

        if unhashed_description:
            data["description"] = unhashed_description.decode("utf-8")

        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]

        if kwargs.get("preimage"):
            data["preimage"] = kwargs["preimage"]

        logger.debug(f"REQUEST to /v1/invoice: {json.dumps(data)}")

        try:
            r = await self.client.post(
                "/v1/invoice",
                json=data,
                headers=self.invoice_headers,
            )
            r.raise_for_status()

            response_data = r.json()

            if "error" in response_data:
                return InvoiceResponse(False, None, None, f"Server error: '{response_data['error']}'")

            if "payment_hash" not in response_data or "bolt11" not in response_data:
                return InvoiceResponse(False, None, None, "Server error: 'missing required fields'")

            return InvoiceResponse(True, response_data["payment_hash"], response_data["bolt11"], None)

        except json.JSONDecodeError:
            return InvoiceResponse(False, None, None, "Server error: 'invalid json response'")
        except Exception as exc:
            logger.warning(f"Unable to connect to {self.url}: {exc}")
            return InvoiceResponse(False, None, None, f"Unable to connect to {self.url}.")


    async def pay_invoice_via_endpoint(
            self,
            bolt11: str,
            fee_limit_msat: int,
            #identifier: str,
            payment_endpoint: str,
            label_prefix: Optional[str] = "LNbits",
            **kwargs,
            ) -> PaymentResponse:

        # todo: rune restrictions will not be enforced for payments that are internal to LNbits.
        # maybe there should be a way to disable internal invoice settlement to always force settlement to the backing wallet

        label_prefix = "LNbits_testing"
        identifier = "{wallet.user}_allet.id goes here"
        random_uuid= base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b'=').decode('utf-8')
        label = f"{label_prefix} {identifier} {random_uuid}"

        logger.error(amount)

        data: Dict = {
            "amount_msat": int(amount * 1000),
            "description": memo,
            "label": label,
        }
        logger.error(data)

        if description_hash and not unhashed_description:
            raise UnsupportedError(
                "'description_hash' unsupported by CoreLightningRest, "
                "provide 'unhashed_description'"
                "TODO: find out if this is also the case with CLNRest"
            )

        if unhashed_description:
            data["description"] = unhashed_description.decode("utf-8")

        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]

        if kwargs.get("preimage"):
            data["preimage"] = kwargs["preimage"]

        logger.debug(f"REQUEST to /v1/invoice: {json.dumps(data)}")

        try:
            r = await self.client.post(
                "/v1/invoice",
                json=data,
                headers=self.invoice_headers,
            )
            r.raise_for_status()

            response_data = r.json()

            if "error" in response_data:
                return InvoiceResponse(False, None, None, f"Server error: '{response_data['error']}'")

            if "payment_hash" not in response_data or "bolt11" not in response_data:
                return InvoiceResponse(False, None, None, "Server error: 'missing required fields'")

            return InvoiceResponse(True, response_data["payment_hash"], response_data["bolt11"], None)

        except json.JSONDecodeError:
            return InvoiceResponse(False, None, None, "Server error: 'invalid json response'")
        except Exception as exc:
            logger.warning(f"Unable to connect to {self.url}: {exc}")
            return InvoiceResponse(False, None, None, f"Unable to connect to {self.url}.")


    async def pay_invoice_via_endpoint(
            self,
            bolt11: str,
            fee_limit_msat: int,
            #identifier: str,
            payment_endpoint: str,
            label_prefix: Optional[str] = "LNbits",
            **kwargs,
            ) -> PaymentResponse:

        # todo: rune restrictions will not be enforced for payments that are internal to LNbits.
        # maybe there should be a way to disable internal invoice settlement to always force settlement to the backing wallet

        label_prefix = "LNbits_testing"
        identifier = "{wallet.user}_wallet.id}"
        random_uuid= base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b'=').decode('utf-8')
        label = f"{label_prefix} {identifier} {random_uuid}"

        logger.debug( f"Pay invoice with parameters: identifier={identifier}, bolt11={bolt11}, label_prefix={label_prefix}, kwargs={kwargs}")

        try:
            invoice = decode(bolt11)
        except Bolt11Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        if not invoice.amount_msat or invoice.amount_msat <= 0:
            error_message = "0 amount invoices are not allowed"
            return PaymentResponse(False, None, None, None, error_message)

        random_uuid= base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b'=').decode('utf-8')
        label = f"{label_prefix} {identifier} {random_uuid}"

        data = {
            "label": f"LNbits {identifier} {random_uuid}",
            "description": invoice.description,
            "maxfee": fee_limit_msat,
        }

        if payment_endpoint == "v1/renepay":
            if not settings.clnrest_renepay_rune:
                return InvoiceResponse( False, None, None, "Unable to invoice without a valid renepay rune")
            data["invstring"] = bolt11
            header_to_use_for_payment = self.renepay_headers

        if payment_endpoint == "v1/pay":
            if not settings.clnrest_pay_rune:
                return InvoiceResponse( False, None, None, "Unable to invoice without a valid pay rune")
            data["bolt11"] = bolt11
            header_to_use_for_payment = self.pay_headers

        assert header_to_use_for_payment != None

        logger.debug(f"REQUEST to {payment_endpoint}: {json.dumps(data)}")

        try:
            r = await self.client.post(
                payment_endpoint,
                json=data,
                headers=header_to_use_for_payment,
                timeout=None,
            )

            r.raise_for_status()
            data = r.json()
            logger.debug(data)

            status = self.statuses.get(data["status"])
            if "payment_preimage" not in data:
                error_message = data.get("error", "Payment failed without an error message.")
                logger.error (error_message)
                return PaymentResponse(
                    status=status,
                    checking_id=None,
                    fee_msat=None,
                    preimage=None,
                    error_message=error_message,
                )

            checking_id = data["payment_hash"]
            preimage = data["payment_preimage"]
            fee_msat = data["amount_sent_msat"] - data["amount_msat"]


            return PaymentResponse(status, checking_id, fee_msat, preimage, None)
        except httpx.HTTPStatusError as exc:
            try:
                data = exc.response.json()
                error = data.get('error', {})
                error_code = int(error.get("code", 0))
                error_message = error.get("message", "Unknown error")

                if error_code in self.pay_failure_error_codes:
                    # TODO: which error codes indicate that the payment failed and which indicate that it is still pending or already happended
                    error_message = f"Payment failed: {error_message}"
                    return PaymentResponse(False, None, None, None, error_message)
                else:
                    error_message = f"REST failed with {error_message}."
                    return PaymentResponse(None, None, None, None, error_message)
            except json.JSONDecodeError:
                # The response is not JSON
                error_message = f"Server error: '{exc.response.text}'"
                return PaymentResponse(None, None, None, None, error_message)
            except Exception as e:
                # Any other exception during parsing
                error_message = f"Unable to connect to {self.url}. Exception: {str(exc)}"
                return PaymentResponse(None, None, None, None, error_message)
        except Exception as exc:
            logger.info(f"Failed to pay invoice {bolt11}")
            logger.warning(exc)
            error_message = f"Unable to connect to {self.url}. Exception: {str(exc)}"
            return PaymentResponse(None, None, None, None, error_message)

    async def pay_invoice(
        self,
        bolt11: str,
        fee_limit_msat: int,
  #      identifier: str,
        **kwargs
        ) -> PaymentResponse:

        identifier = "{wallet.user}_wallet.id}"
        logger.debug(f"request to pay_invoice bolt11 {bolt11} identifier {identifier}")

        try:
            invoice = decode(bolt11)
        except Bolt11Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        # Determine the endpoint based on the use_rene flag
        default_to_renepay = True
        if invoice.description is not None and settings.clnrest_renepay_rune and default_to_renepay:
            payment_endpoint = "v1/renepay"
        elif settings.clnrest_pay_rune:
            payment_endpoint = "v1/pay"
        else:
            return PaymentResponse(False, None, None, None, "Unable to pay invoice without a pay or renepay rune")


        response = await self.pay_invoice_via_endpoint(
            bolt11=bolt11,
            fee_limit_msat=fee_limit_msat,
            #identifier=identifier,
            payment_endpoint=payment_endpoint,
            **kwargs
        )
        return response


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
        except Exception as exc:
            logger.error(f"Error getting invoice status: {exc}")
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

            pay = pays_list[-1]
            logger.trace(f"Payment status from API: {pay['status']}")

            fee_msat, preimage = None, None
            if pay['status'] == 'complete':
                fee_msat = pay["amount_sent_msat"] - pay["amount_msat"]
                return PaymentSuccessStatus( fee_msat=fee_msat, preimage=payment_resp["preimage"])

        except Exception as exc:
            logger.error(f"Error getting payment status: {exc}")
            return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                waitanyinvoice_timeout=None
                request_timeout = httpx.Timeout(connect=5.0, read=waitanyinvoice_timeout, write=60.0, pool=60.0)
                data: Dict = { "lastpay_index": self.last_pay_index, "timeout": waitanyinvoice_timeout}
                url = "/v1/waitanyinvoice"
                #logger.debug(f"REQUEST(stream) to  /v1/waitanyinvoice with data: {data}.")
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
                        logger.debug(f"paid invoice: {inv}")

                        # NOTE: use payment_hash when corelightning-rest returns it
                        # when using waitanyinvoice
                        there_is_no_reason_to_also_check_with_listinvoices_because_we_already_know_the_payment_hash=True

                        if ("payment_hash" in inv) and there_is_no_reason_to_also_check_with_listinvoices_because_we_already_know_the_payment_hash:
                            payment_hash_from_waitanyinvoice = inv["payment_hash"]
                            yield payment_hash_from_waitanyinvoice
                            continue


                        logger.error("if this error never shows up, the below code can safely be removed")
                        # TODO: explain why this would ever happen. It appears this is not necessary to run as the above code should be sufficient?
                        # corelightningrest.py has the same logic
                        # if we need this logic we should maybe use the get_invoice_status function instead of rewriting it
                        # hack to return payment_hash if the above shouldn't work
                        data: Dict = { "label": inv["label"]}
                        r = await self.client.post(
                            "/v1/listinvoices",
                            json=data,
                            headers=self.readonly_headers,
                        )
                        paid_invoice = r.json()
                        logger.trace(f"paid invoice: {paid_invoice}")
                        assert self.statuses[
                            paid_invoice["invoices"][-1]["status"]
                        ], "streamed invoice not paid"
                        assert "invoices" in paid_invoice, "no invoices in response"
                        assert len(paid_invoice["invoices"]), "no invoices in response"
                        assert paid_invoice["invoices"][0]["payment_hash"] == payment_hash_from_waitanyinvoice
                        yield paid_invoice["invoices"][0]["payment_hash"]

            except Exception as exc:
                logger.debug(
                    f"lost connection to corelightning-rest invoices stream: '{exc}', "
                    "reconnecting..."
                )
                await asyncio.sleep(0.02)
