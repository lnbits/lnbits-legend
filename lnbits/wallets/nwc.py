import asyncio
import hashlib
import json
import random
import time
from collections.abc import AsyncGenerator
from typing import Optional, Union, cast
from urllib.parse import parse_qs, unquote, urlparse

import secp256k1
from bolt11 import decode as bolt11_decode
from loguru import logger
from websockets.client import connect as ws_connect

from lnbits.settings import settings
from lnbits.utils.nostr import (
    decrypt_content,
    encrypt_content,
    json_dumps,
    sign_event,
    verify_event,
)

from .base import (
    InvoiceResponse,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)


class NWCError(Exception):
    """
    An exception from NWC
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(self.__str__())

    def __str__(self):
        return f"{self.code} {self.message}"


class NWCWallet(Wallet):
    """
    A funding source that connects to a Nostr Wallet Connect (NWC) service provider.
    https://nwc.dev/
    """

    def __init__(self):
        self.shutdown = False
        nwc_data = parse_nwc(settings.nwc_pairing_url)
        self.conn = NWCConnection(
            nwc_data["pubkey"], nwc_data["secret"], nwc_data["relay"]
        )
        # pending payments for paid_invoices_stream.
        # They are tracked until they expire or are settled
        self.pending_payments = []
        # interval in seconds between checks for pending payments
        self.pending_payments_lookup_interval = 10
        # track paid invoices for paid_invoices_stream
        self.paid_invoices_queue: asyncio.Queue = asyncio.Queue(0)
        # This task periodically checks if pending payments have been settled
        self.pending_payments_lookup_task = asyncio.create_task(
            self._handle_pending_payments()
        )

    def _is_shutting_down(self) -> bool:
        """
        Returns True if the wallet is shutting down.
        """
        return self.shutdown or not settings.lnbits_running

    async def _handle_pending_payments(self):
        """
        Periodically checks if any pending payments have been settled.
        """
        while not self._is_shutting_down():
            await asyncio.sleep(self.pending_payments_lookup_interval)
            # Check if any pending payments have been settled or timed out
            now = time.time()
            for payment in self.pending_payments:
                try:
                    if not payment["settled"]:
                        payment_data = await self.conn.call(
                            "lookup_invoice", {"payment_hash": payment["checking_id"]}
                        )
                        settled = (
                            "settled_at" in payment_data
                            and payment_data["settled_at"]
                            and int(payment_data["settled_at"]) > 0
                            and "preimage" in payment_data
                            and payment_data["preimage"]
                        )
                        if settled:
                            logger.debug(
                                "Pending payment " + payment["checking_id"] + " settled"
                            )
                            payment["settled"] = True
                            self.paid_invoices_queue.put_nowait(payment["checking_id"])
                except Exception as e:
                    logger.error("Error handling pending payment: " + str(e))
                try:
                    if now > payment["expires_at"]:
                        logger.warning(
                            "Pending payment " + payment["checking_id"] + " timed out"
                        )
                        payment["expired"] = True
                except Exception as e:
                    logger.error("Error handling pending payment: " + str(e))

            # Remove all settled or expired payments
            self.pending_payments = [
                payment
                for payment in self.pending_payments
                if not payment["settled"] and not payment["expired"]
            ]

    async def cleanup(self):
        self.shutdown = True
        try:
            self.pending_payments_lookup_task.cancel()
        except Exception as e:
            logger.warning("Error cancelling pending payments lookup task: " + str(e))
        await self.conn.close()

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        desc = ""
        desc_hash = None
        if description_hash:
            desc_hash = description_hash.hex()
            desc = (unhashed_description or b"").decode()
        elif unhashed_description:
            desc = unhashed_description.decode()
            desc_hash = hashlib.sha256(desc.encode()).hexdigest()
        else:
            desc = memo or ""
        try:
            info = await self.conn.get_info()
            if "make_invoice" not in info["supported_methods"]:
                return InvoiceResponse(
                    False,
                    None,
                    None,
                    "make_invoice is not supported by this NWC service.",
                )
            resp = await self.conn.call(
                "make_invoice",
                {
                    "amount": int(amount * 1000),  # nwc uses msats denominations
                    "description_hash": desc_hash,
                    "description": desc,
                },
            )
            checking_id = str(resp["payment_hash"])
            payment_request = resp.get("invoice", None)
            # if lookup_invoice is not supported, we can't track the payment
            if "lookup_invoice" in info["supported_methods"]:
                created_at = int(resp.get("created_at", time.time()))
                expires_at = int(resp.get("expires_at", created_at + 3600))
                self.pending_payments.append(
                    {  # Start tracking
                        "checking_id": checking_id,
                        "expires_at": expires_at,
                        "settled": False,
                        "expired": False,
                    }
                )
            return InvoiceResponse(True, checking_id, payment_request, None)
        except Exception as e:
            return InvoiceResponse(ok=False, error_message=str(e))

    async def status(self) -> StatusResponse:
        try:
            info = await self.conn.get_info()
            if "get_balance" not in info["supported_methods"]:
                logger.debug("get_balance is not supported by this NWC service.")
                return StatusResponse(None, 0)
            resp = await self.conn.call("get_balance", {})
            balance = int(resp["balance"])
            return StatusResponse(None, balance)
        except Exception as e:
            return StatusResponse(str(e), 0)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            resp = await self.conn.call("pay_invoice", {"invoice": bolt11})
            preimage = resp.get("preimage", None)
            invoice_data = bolt11_decode(bolt11)
            payment_hash = invoice_data.payment_hash
            # pay_invoice doesn't return payment data, so we need
            # to call lookup_invoice too (if supported)
            info = await self.conn.get_info()

            if "lookup_invoice" not in info["supported_methods"]:
                # if not supported, we assume it succeeded
                return PaymentResponse(True, payment_hash, None, preimage, None)

            try:
                payment_data = await self.conn.call(
                    "lookup_invoice", {"invoice": bolt11}
                )
                settled = payment_data.get("settled_at", None) and payment_data.get(
                    "preimage", None
                )
                if not settled:
                    return PaymentResponse(None, payment_hash, None, None, None)
                else:
                    fee_msat = payment_data.get("fees_paid", None)
                    return PaymentResponse(True, payment_hash, fee_msat, preimage, None)
            except Exception:
                # Workaround: some nwc service providers might not store the invoice
                # right away, so this call may raise an exception.
                # We will assume the payment is pending anyway
                return PaymentResponse(None, payment_hash, None, None, None)
        except NWCError as e:
            logger.error("Error paying invoice: " + str(e))
            failure_codes = [
                "RATE_LIMITED",
                "NOT_IMPLEMENTED",
                "INSUFFICIENT_BALANCE",
                "QUOTA_EXCEEDED",
                "RESTRICTED",
                "UNAUTHORIZED",
                "INTERNAL",
                "OTHER",
                "PAYMENT_FAILED",
            ]
            failed = e.code in failure_codes
            return PaymentResponse(
                None if not failed else False,
                error_message=e.message if failed else None,
            )
        except Exception as e:
            logger.error("Error paying invoice: " + str(e))
            # assume pending
            return PaymentResponse(None)

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        return await self.get_payment_status(checking_id)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            info = await self.conn.get_info()
            if "lookup_invoice" in info["supported_methods"]:
                payment_data = await self.conn.call(
                    "lookup_invoice", {"payment_hash": checking_id}
                )
                settled = payment_data.get("settled_at", None) and payment_data.get(
                    "preimage", None
                )
                fee_msat = payment_data.get("fees_paid", None)
                preimage = payment_data.get("preimage", None)
                created_at = int(payment_data.get("created_at", time.time()))
                expires_at = int(payment_data.get("expires_at", created_at + 3600))
                expired = expires_at and time.time() > expires_at
                if expired and not settled:
                    return PaymentStatus(False, fee_msat=fee_msat, preimage=preimage)
                else:
                    return PaymentStatus(
                        True if settled else None, fee_msat=fee_msat, preimage=preimage
                    )
            else:
                return PaymentStatus(None, fee_msat=None, preimage=None)
        except NWCError as e:
            logger.error("Error getting payment status: " + str(e))
            failed = e.code == "NOT_FOUND"
            return PaymentStatus(
                None if not failed else False, fee_msat=None, preimage=None
            )
        except Exception as e:
            logger.error("Error getting payment status: " + str(e))
            # assume pending (eg. exception due to network error)
            return PaymentStatus(None, fee_msat=None, preimage=None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while not self._is_shutting_down():
            value = await self.paid_invoices_queue.get()
            yield value


class NWCConnection:
    """
    A connection to a Nostr Wallet Connect (NWC) service provider.
    """

    def __init__(self, pubkey, secret, relay):
        # Parse pairing url (if invalid an exception is raised)

        # Extract keys (used to sign nwc events+identify NWC user)
        self.account_private_key = secp256k1.PrivateKey(bytes.fromhex(secret))
        self.account_private_key_hex = secret
        self.account_public_key = self.account_private_key.pubkey
        self.account_public_key_hex = self.account_public_key.serialize().hex()[2:]

        # Extract service key (used for encryption to identify the nwc service provider)
        self.service_pubkey = secp256k1.PublicKey(bytes.fromhex("02" + pubkey), True)
        self.service_pubkey_hex = pubkey

        # Extract relay url
        self.relay = relay

        # Create temporary subscriptions, stored until the response is received/expires
        self.subscriptions = {}
        # Timeout in seconds after which a subscription is closed
        # if no response is received
        self.subscription_timeout = 10
        # Incremental counter to generate unique subscription ids for the connection
        self.subscriptions_count = 0

        # websocket connection
        self.ws = None
        # if True the websocket is connected
        self.connected = False
        # if True the connection is shutting down
        self.shutdown = False

        # cached info about the service provider
        self.info = None

        # This task handles connection and reconnection to the relay
        self.connection_task = asyncio.create_task(self._connect_to_relay())

        # This task periodically checks and removes subscriptions
        # and pending payments that have timed out
        self.timeout_task = asyncio.create_task(self._handle_timeouts())

        logger.info(
            "NWCConnection is ready. relay: "
            + self.relay
            + " account: "
            + self.account_public_key_hex
            + " service: "
            + self.service_pubkey_hex
        )

    def _is_shutting_down(self) -> bool:
        """
        Returns True if the connection is shutting down.
        """
        return self.shutdown or not settings.lnbits_running

    async def _send(self, data: list[Union[str, dict]]):
        """
        Sends data to the NWC relay.

        Args:
            data (Dict): The data to be sent.
        """
        if self._is_shutting_down():
            logger.warning("Trying to send data while shutting down")
            return
        if not self.ws:
            logger.warning("Trying to send data without a connection")
            return
        await self._wait_for_connection()  # ensure the connection is established
        tx = json_dumps(data)
        await self.ws.send(tx)

    def _get_new_subid(self) -> str:
        """
        Generates a unique subscription id.

        Returns:
            str: The generated 64 characters long subscription id (eg. lnbits0abc...)
        """
        subid = "lnbits" + str(self.subscriptions_count)
        self.subscriptions_count += 1
        max_length = 64
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        n = max_length - len(subid)
        if n > 0:
            for _ in range(n):
                subid += chars[random.randint(0, len(chars) - 1)]
        return subid

    async def _close_subscription_by_subid(
        self, sub_id: str, send_event: bool = True
    ) -> Optional[dict]:
        """
        Closes a subscription by its sub_id.

        Args:
            sub_id (str): The subscription id.
            sendEvent (bool): If True, sends a CLOSE event to the relay.

        Returns:
            Dict: The subscription that was closed.
        """
        logger.debug("Closing subscription " + sub_id)
        sub_to_close = None
        for subscription in self.subscriptions.values():
            if subscription["sub_id"] == sub_id:
                sub_to_close = subscription
                # send CLOSE event to the relay if the subscription
                # is not already closed and sendEvent is True
                if not subscription["closed"] and send_event:
                    await self._send(["CLOSE", sub_id])
                # mark as closed
                subscription["closed"] = True
                break
        # remove the subscription from the list
        if sub_to_close:
            self.subscriptions.pop(sub_to_close["event_id"], None)
        return sub_to_close

    async def _close_subscription_by_eventid(
        self, event_id, send_event=True
    ) -> Optional[dict]:
        """
        Closes a subscription associated to an event_id.

        Args:
            event_id (str): The event id associated to the subscription.
            sendEvent (bool): If True, sends a CLOSE event to the relay.

        Returns:
            Dict: The subscription that was closed.
        """
        logger.debug("Closing subscription for event " + event_id)
        # find and remove the subscription
        subscription = self.subscriptions.pop(event_id, None)
        if subscription:
            # send CLOSE event to the relay if the subscription
            # is not already closed and sendEvent is True
            if not subscription["closed"] and send_event:
                await self._send(["CLOSE", subscription["sub_id"]])
            # mark as closed
            subscription["closed"] = True
        return subscription

    async def _wait_for_connection(self):
        """
        Waits until the connection is ready
        """
        while not self.connected:
            if self._is_shutting_down():
                raise Exception("Connection is closing")
            logger.debug("Waiting for connection...")
            await asyncio.sleep(1)

    async def _handle_timeouts(self):
        """
        Periodically checks if any subscriptions and pending
        payments have timed out, and removes them.
        """
        try:
            while not self._is_shutting_down():
                try:
                    await asyncio.sleep(int(self.subscription_timeout * 0.5))
                    # skip if connection is not established
                    if not self.connected:
                        continue
                    # Find all subscriptions that have timed out
                    now = time.time()
                    subscriptions_to_close = []
                    for subscription in self.subscriptions.values():
                        t = now - subscription["timestamp"]
                        if t > self.subscription_timeout:
                            logger.warning(
                                "Subscription " + subscription["sub_id"] + " timed out"
                            )
                            subscriptions_to_close.append(subscription["sub_id"])
                            # if not already closed, pass the "time out"
                            # exception to the future
                            if not subscription["closed"]:
                                subscription["future"].set_exception(
                                    Exception("timed out")
                                )
                    # Close all timed out subscriptions
                    for sub_id in subscriptions_to_close:
                        await self._close_subscription_by_subid(sub_id)
                except Exception as e:
                    logger.error("Error handling subscription timeout: " + str(e))
        except Exception as e:
            logger.error("Error handling subscription timeout: " + str(e))

    async def _on_ok_message(self, msg: list[str]):
        """
        Handles OK messages from the relay.
        """
        event_id = msg[1]
        status = msg[2]
        info = (msg[3] or "") if len(msg) > 3 else ""
        if not status:
            # close subscription and pass an exception
            # if the event was rejected by the relay
            subscription = await self._close_subscription_by_eventid(event_id)
            if subscription:  # Check if the subscription exists first
                subscription["future"].set_exception(Exception(info))

    async def _on_event_message(self, msg: list[Union[str, dict]]):
        """
        Handles EVENT messages from the relay.
        """
        sub_id = cast(str, msg[1])
        event = cast(dict, msg[2])
        if not verify_event(event):  # Ensure the event is valid (do not trust relays)
            raise Exception("Invalid event signature")
        tags = event["tags"]
        if event["kind"] == 13194:  # An info event
            # info events are handled specially,
            # they are stored in the subscriptions list
            # using the subscription id for both sub_id and event_id
            subscription = await self._close_subscription_by_eventid(
                sub_id
            )  # sub_id is the event_id for info events
            if subscription:  # Check if the subscription exists first
                if (
                    subscription["method"] != "info_sub"
                ):  # Ensure the subscription is for an info event
                    raise Exception("Unexpected info event")
                # create an info dictionary with the supported
                # methods that is passed to the future
                content = event["content"]
                subscription["future"].set_result(
                    {"supported_methods": content.split(" ")}
                )
        else:  # A response event
            subscription = None
            # find the first "e" tag that is handled by
            # a registered subscription
            # Note: usually we expect only one "e" tag, but we are
            # handling multiple "e" tags just in case
            for tag in tags:
                if tag[0] == "e":
                    subscription = await self._close_subscription_by_eventid(tag[1])
                    if subscription:
                        break
            # if a subscription was found, pass the result to the future
            if subscription:
                content = decrypt_content(
                    event["content"], self.service_pubkey, self.account_private_key_hex
                )
                content = json.loads(content)
                result_type = content.get("result_type", "")
                error = content.get("error", None)
                result = content.get("result", None)
                if error:  # if an error occurred, pass the error to the future
                    nwc_exception = NWCError(error["code"], error["message"])
                    subscription["future"].set_exception(nwc_exception)
                else:
                    # ensure the result is for the expected method
                    if result_type != subscription["method"]:
                        raise Exception("Unexpected result type")
                    if not result:
                        raise Exception("Malformed response")
                    else:
                        subscription["future"].set_result(result)

    async def _on_closed_message(self, msg: list[str]):
        """
        Handles CLOSED messages from the relay.
        """
        # The change is reflected in the subscriptions list.
        sub_id = msg[1]
        info = msg[2] or ""
        if info:
            logger.warning("Subscription " + sub_id + " closed remotely: " + info)
        # Note: sendEvent=false because the action was initiated by the relay
        await self._close_subscription_by_subid(sub_id, send_event=False)

    async def _on_message(self, ws, message: str):
        """
        Handle incoming messages from the relay.
        """
        try:
            msg = json.loads(message)
            if msg[0] == "OK":  # Event status message
                await self._on_ok_message(msg)
            elif msg[0] == "EVENT":  # Event message
                await self._on_event_message(msg)
            elif msg[0] == "EOSE":
                # Do nothing. No need to handle this message type for NWC
                pass
            elif msg[0] == "CLOSED":
                # Subscription was closed remotely.
                await self._on_closed_message(msg)
            elif msg[0] == "NOTICE":
                # A message from the relay, mostly useless, but we log it anyway
                logger.info("Notice from relay " + self.relay + ": " + str(msg[1]))
            else:
                raise Exception("Unknown message type")
        except Exception as e:
            logger.error("Error parsing event: " + str(e))

    async def _connect_to_relay(self):
        """
        Initiate websocket connection to the relay.
        """
        logger.debug("Connecting to NWC relay " + self.relay)
        while (
            not self._is_shutting_down()
        ):  # Reconnect until the connection is shutting down
            logger.debug("Creating new connection...")
            try:
                async with ws_connect(self.relay) as ws:
                    self.ws = ws
                    self.connected = True
                    while (
                        not self._is_shutting_down()
                    ):  # receive messages until the connection is shutting down
                        try:
                            reply = await ws.recv()
                            reply_str = ""
                            if isinstance(reply, bytes):
                                reply_str = reply.decode("utf-8")
                            else:
                                reply_str = reply
                            await self._on_message(ws, reply_str)
                        except Exception as e:
                            logger.debug("Error receiving message: " + str(e))
                            break
                logger.debug("Connection to NWC relay closed")
            except Exception as e:
                logger.error("Error connecting to NWC relay: " + str(e))
            # the connection was closed, so we set the connected flag to False
            # this will make the methods calling _wait_for_connection()
            # to wait until the connection is re-established
            self.connected = False
            if not self._is_shutting_down():
                # Wait some time before reconnecting
                logger.debug("Reconnecting to NWC relay in 5 seconds...")
                await asyncio.sleep(5)

    async def call(self, method: str, params: dict) -> dict:
        """
        Call a NWC method.

        Args:
            method (str): The method name.
            params (Dict): The method parameters.

        Returns:
            Dict: The result of the method call.
        """
        await self._wait_for_connection()
        logger.debug("Calling " + method + " with params: " + str(params))
        # Prepare the content
        content = json_dumps(
            {
                "method": method,
                "params": params,
            }
        )
        # Encrypt
        content = encrypt_content(
            content, self.service_pubkey, self.account_private_key_hex
        )
        # Prepare the NWC event
        event = {
            "kind": 23194,
            "content": content,
            "created_at": int(time.time()),
            "tags": [["p", self.service_pubkey_hex]],
        }
        # Sign
        sign_event(event, self.account_public_key_hex, self.account_private_key)
        # Subscribe for a response to this event
        sub_filter = {
            "kinds": [23195],
            "#p": [self.account_public_key_hex],
            "#e": [event["id"]],
            "since": event["created_at"],
        }
        sub_id = self._get_new_subid()
        # register a future to receive the response asynchronously
        future = asyncio.get_event_loop().create_future()
        # Check if the subscription already exists
        # (this means there is a bug somewhere, should not happen)
        if event["id"] in self.subscriptions:
            raise Exception("Subscription for this event id already exists?")
        # Store the subscription in the list
        self.subscriptions[event["id"]] = {
            "method": method,
            "future": future,
            "sub_id": sub_id,
            "event_id": event["id"],
            "timestamp": time.time(),
            "closed": False,
        }
        # Send the events
        await self._send(["REQ", sub_id, sub_filter])
        await self._send(["EVENT", event])
        # Wait for the response
        return await future

    async def get_info(self) -> dict:
        """
        Get the info about the service provider and cache it.

        Returns:
            Dict: The info about the service provider.
        """
        if not self.info:  # if not cached
            try:
                await self._wait_for_connection()
                # Prepare filter to request the info note
                sub_filter = {"kinds": [13194], "authors": [self.service_pubkey_hex]}
                # We register a special subscription using the sub_id as the event_id
                sub_id = self._get_new_subid()
                future = asyncio.get_event_loop().create_future()
                self.subscriptions[sub_id] = {
                    "method": "info_sub",
                    "future": future,
                    "sub_id": sub_id,
                    "event_id": sub_id,
                    "timestamp": time.time(),
                    "closed": False,
                }
                # Send the request
                await self._send(["REQ", sub_id, sub_filter])
                # Wait for the response
                service_info = await future
                # Get account info when possible
                if "get_info" in service_info["supported_methods"]:
                    try:
                        account_info = await self.call("get_info", {})
                        # cache
                        self.info = service_info
                        self.info["alias"] = account_info.get("alias", "")
                        self.info["color"] = account_info.get("color", "")
                        self.info["pubkey"] = account_info.get("pubkey", "")
                        self.info["network"] = account_info.get("network", "")
                        self.info["block_height"] = account_info.get("block_height", 0)
                        self.info["block_hash"] = account_info.get("block_hash", "")
                        self.info["supported_methods"] = account_info.get(
                            "methods",
                            service_info.get("supported_methods", ["pay_invoice"]),
                        )
                    except Exception as e:
                        # If there is an error, fallback to using service info
                        logger.error(
                            "Error getting account info: "
                            + str(e)
                            + " Using service info only"
                        )
                        self.info = service_info
                else:
                    # get_info is not supported,
                    # so we will make do with the service info
                    self.info = service_info  # cache
            except Exception as e:
                logger.error("Error getting info: " + str(e))
                # The error could mean that the service provider does
                # not provide an info note
                # So we just assume it supports the bare minimum to be Nip47 compliant
                self.info = {
                    "supported_methods": ["pay_invoice"],
                }
        return self.info

    async def close(self):
        logger.debug("Closing NWCConnection")
        self.shutdown = True  # Mark for shutdown
        # cancel all tasks
        try:
            self.timeout_task.cancel()
        except Exception as e:
            logger.warning("Error cancelling subscription timeout task: " + str(e))
        try:
            self.connection_task.cancel()
        except Exception as e:
            logger.warning("Error cancelling connection task: " + str(e))
        # close the websocket
        try:
            if self.ws:
                await self.ws.close()
        except Exception as e:
            logger.warning("Error closing connection: " + str(e))


def parse_nwc(nwc) -> dict:
    """
    Parses a NWC URL (nostr+walletconnect://...) and extracts relevant information.

    Args:
        nwc (str): The Nostr Wallet Connect URL to be parsed.

    Returns:
        Dict[str, str]: A dict containing:'pubkey', 'relay', and 'secret'.
        If the URL is invalid, an exception is raised.

    Example:
        >>> parse_nwc("nostr+walletconnect://000000...000000?relay=example.com&secret=123")
        {'pubkey': '000000...000000', 'relay': 'example.com', 'secret': '123'}
    """
    data = {}
    prefix = "nostr+walletconnect://"
    if nwc and nwc.startswith(prefix):
        nwc = nwc[len(prefix) :]
        parsed_url = urlparse(nwc)
        data["pubkey"] = parsed_url.path
        query_params = parse_qs(parsed_url.query)
        for key, value in query_params.items():
            if key in ["relay", "secret"] and value:
                data[key] = unquote(value[0])
        if "pubkey" not in data or "relay" not in data or "secret" not in data:
            raise ValueError("Invalid NWC pairing url")
    else:
        raise ValueError("Invalid NWC pairing url")
    return data
