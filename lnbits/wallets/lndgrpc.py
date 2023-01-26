imports_ok = True
try:
    import grpc
    from google import protobuf
    from grpc import RpcError
except ImportError:  # pragma: nocover
    imports_ok = False

import asyncio
import base64
import hashlib
from os import environ, error
from typing import AsyncGenerator, Dict, Optional

from loguru import logger

from .macaroon import AESCipher, load_macaroon

if imports_ok:
    import lnbits.wallets.lnd_grpc_files.lightning_pb2 as ln
    import lnbits.wallets.lnd_grpc_files.lightning_pb2_grpc as lnrpc
    import lnbits.wallets.lnd_grpc_files.router_pb2 as router
    import lnbits.wallets.lnd_grpc_files.router_pb2_grpc as routerrpc

from lnbits.settings import settings

from .base import (
    InvoiceResponse,
    PaymentResponse,
    PaymentStatus,
    StatusResponse,
    Wallet,
)


def get_ssl_context(cert_path: str):
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS)
    context.options |= ssl.OP_NO_SSLv2
    context.options |= ssl.OP_NO_SSLv3
    context.options |= ssl.OP_NO_TLSv1
    context.options |= ssl.OP_NO_TLSv1_1
    context.options |= ssl.OP_NO_COMPRESSION
    context.set_ciphers(
        ":".join(
            [
                "ECDHE+AESGCM",
                "ECDHE+CHACHA20",
                "DHE+AESGCM",
                "DHE+CHACHA20",
                "ECDH+AESGCM",
                "DH+AESGCM",
                "ECDH+AES",
                "DH+AES",
                "RSA+AESGCM",
                "RSA+AES",
                "!aNULL",
                "!eNULL",
                "!MD5",
                "!DSS",
            ]
        )
    )
    context.load_verify_locations(capath=cert_path)
    return context


def b64_to_bytes(checking_id: str) -> bytes:
    return base64.b64decode(checking_id.replace("_", "/"))


def bytes_to_b64(r_hash: bytes) -> str:
    return base64.b64encode(r_hash).decode().replace("/", "_")


def hex_to_b64(hex_str: str) -> str:
    try:
        return base64.b64encode(bytes.fromhex(hex_str)).decode()
    except ValueError:
        return ""


def hex_to_bytes(hex_str: str) -> bytes:
    try:
        return bytes.fromhex(hex_str)
    except:
        return b""


def bytes_to_hex(b: bytes) -> str:
    return b.hex()


# Due to updated ECDSA generated tls.cert we need to let gprc know that
# we need to use that cipher suite otherwise there will be a handhsake
# error when we communicate with the lnd rpc server.
environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


class LndWallet(Wallet):
    def __init__(self):
        if not imports_ok:  # pragma: nocover
            raise ImportError(
                "The `grpcio` and `protobuf` library must be installed to use `GRPC LndWallet`. Alternatively try using the LndRESTWallet."
            )

        endpoint = settings.lnd_grpc_endpoint
        self.endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
        self.port = int(settings.lnd_grpc_port)
        self.cert_path = settings.lnd_grpc_cert or settings.lnd_cert

        macaroon = (
            settings.lnd_grpc_macaroon
            or settings.lnd_grpc_admin_macaroon
            or settings.lnd_admin_macaroon
            or settings.lnd_grpc_invoice_macaroon
            or settings.lnd_invoice_macaroon
        )

        encrypted_macaroon = settings.lnd_grpc_macaroon_encrypted
        if encrypted_macaroon:
            macaroon = AESCipher(description="macaroon decryption").decrypt(
                encrypted_macaroon
            )
        self.macaroon = load_macaroon(macaroon)

        cert = open(self.cert_path, "rb").read()
        creds = grpc.ssl_channel_credentials(cert)
        auth_creds = grpc.metadata_call_credentials(self.metadata_callback)
        composite_creds = grpc.composite_channel_credentials(creds, auth_creds)
        channel = grpc.aio.secure_channel(
            f"{self.endpoint}:{self.port}", composite_creds
        )
        self.rpc = lnrpc.LightningStub(channel)
        self.routerpc = routerrpc.RouterStub(channel)

    def metadata_callback(self, _, callback):
        callback([("macaroon", self.macaroon)], None)

    async def status(self) -> StatusResponse:
        try:
            resp = await self.rpc.ChannelBalance(ln.ChannelBalanceRequest())
        except RpcError as exc:
            return StatusResponse(str(exc._details), 0)
        except Exception as exc:
            return StatusResponse(str(exc), 0)

        return StatusResponse(None, resp.balance * 1000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        params: Dict = {"value": amount, "private": True}
        if kwargs.get("expiry"):
            params["expiry"] = kwargs["expiry"]
        if description_hash:
            params["description_hash"] = description_hash
        elif unhashed_description:
            params["description_hash"] = hashlib.sha256(
                unhashed_description
            ).digest()  # as bytes directly
        else:
            params["memo"] = memo or ""

        try:
            req = ln.Invoice(**params)
            resp = await self.rpc.AddInvoice(req)
        except Exception as exc:
            error_message = str(exc)
            return InvoiceResponse(False, None, None, error_message)

        checking_id = bytes_to_hex(resp.r_hash)
        payment_request = str(resp.payment_request)
        return InvoiceResponse(True, checking_id, payment_request, None)

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        # fee_limit_fixed = ln.FeeLimit(fixed=fee_limit_msat // 1000)
        req = router.SendPaymentRequest(
            payment_request=bolt11,
            fee_limit_msat=fee_limit_msat,
            timeout_seconds=30,
            no_inflight_updates=True,
        )
        try:
            resp = await self.routerpc.SendPaymentV2(req).read()
        except RpcError as exc:
            return PaymentResponse(False, None, None, None, exc._details)
        except Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        # PaymentStatus from https://github.com/lightningnetwork/lnd/blob/master/channeldb/payments.go#L178
        statuses = {
            0: None,  # NON_EXISTENT
            1: None,  # IN_FLIGHT
            2: True,  # SUCCEEDED
            3: False,  # FAILED
        }

        failure_reasons = {
            0: "No error given.",
            1: "Payment timed out.",
            2: "No route to destination.",
            3: "Error.",
            4: "Incorrect payment details.",
            5: "Insufficient balance.",
        }

        fee_msat = None
        preimage = None
        error_message = None
        checking_id = None

        if statuses[resp.status] == True:  # SUCCEEDED
            fee_msat = -resp.htlcs[-1].route.total_fees_msat
            preimage = resp.payment_preimage
            checking_id = resp.payment_hash
        elif statuses[resp.status] == False:
            error_message = failure_reasons[resp.failure_reason]

        return PaymentResponse(
            statuses[resp.status], checking_id, fee_msat, preimage, error_message
        )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r_hash = hex_to_bytes(checking_id)
            if len(r_hash) != 32:
                raise ValueError
        except ValueError:
            # this may happen if we switch between backend wallets
            # that use different checking_id formats
            return PaymentStatus(None)
        try:
            resp = await self.rpc.LookupInvoice(ln.PaymentHash(r_hash=r_hash))
        except RpcError as exc:
            return PaymentStatus(None)
        if resp.settled:
            return PaymentStatus(True)

        return PaymentStatus(None)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        """
        This routine checks the payment status using routerpc.TrackPaymentV2.
        """
        try:
            r_hash = hex_to_bytes(checking_id)
            if len(r_hash) != 32:
                raise ValueError
        except ValueError:
            # this may happen if we switch between backend wallets
            # that use different checking_id formats
            return PaymentStatus(None)

        resp = self.routerpc.TrackPaymentV2(
            router.TrackPaymentRequest(payment_hash=r_hash)
        )

        # # HTLCAttempt.HTLCStatus:
        # # https://github.com/lightningnetwork/lnd/blob/master/lnrpc/lightning.proto#L3641
        # htlc_statuses = {
        #     0: None,  # IN_FLIGHT
        #     1: True,  # "SUCCEEDED"
        #     2: False,  # "FAILED"
        # }
        statuses = {
            0: None,  # NON_EXISTENT
            1: None,  # IN_FLIGHT
            2: True,  # SUCCEEDED
            3: False,  # FAILED
        }

        try:
            async for payment in resp:
                if len(payment.htlcs) and statuses[payment.status]:
                    return PaymentStatus(
                        True,
                        -payment.htlcs[-1].route.total_fees_msat,
                        bytes_to_hex(payment.htlcs[-1].preimage),
                    )
                return PaymentStatus(statuses[payment.status])
        except:  # most likely the payment wasn't found
            return PaymentStatus(None)

        return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while True:
            try:
                request = ln.InvoiceSubscription()
                async for i in self.rpc.SubscribeInvoices(request):
                    if not i.settled:
                        continue

                    checking_id = bytes_to_hex(i.r_hash)
                    yield checking_id
            except Exception as exc:
                logger.error(
                    f"lost connection to lnd invoices stream: '{exc}', retrying in 5 seconds"
                )
                await asyncio.sleep(5)

    async def pay_with_keysend(self, public_key: str, pre_image: str, payment_hash: str, amount: int, fee_limit_msat: int, message: str) -> PaymentResponse:
        # The record 5482373484 is special: it carries the pre_image to
        # the destination so it can be compared with the hash we pass
        # via the payment_hash
        dest_custom_records = {
            5482373484: bytes.fromhex(pre_image),
            34349334: message.encode(),
        }

        req = router.SendPaymentRequest(
            dest=bytes.fromhex(public_key),
            amt_msat=amount,
            payment_hash=bytes.fromhex(payment_hash),
            dest_custom_records=dest_custom_records,
            fee_limit_msat=fee_limit_msat,
            timeout_seconds=30,
            no_inflight_updates=True,
        )
        try:
            resp = await self.routerpc.SendPaymentV2(req).read()
        except RpcError as exc:
            return PaymentResponse(False, None, None, None, exc._details)
        except Exception as exc:
            return PaymentResponse(False, None, None, None, str(exc))

        # PaymentStatus from https://github.com/lightningnetwork/lnd/blob/master/channeldb/payments.go#L178
        statuses = {
            0: None,  # NON_EXISTENT
            1: None,  # IN_FLIGHT
            2: True,  # SUCCEEDED
            3: False,  # FAILED
        }

        failure_reasons = {
            0: "No error given.",
            1: "Payment timed out.",
            2: "No route to destination.",
            3: "Error.",
            4: "Incorrect payment details.",
            5: "Insufficient balance.",
        }

        fee_msat = None
        preimage = None
        error_message = None
        checking_id = None

        if statuses[resp.status] == True:  # SUCCEEDED
            fee_msat = -resp.htlcs[-1].route.total_fees_msat
            preimage = resp.payment_preimage
            checking_id = resp.payment_hash
        elif statuses[resp.status] == False:
            error_message = failure_reasons[resp.failure_reason]

        return PaymentResponse(
            statuses[resp.status], checking_id, fee_msat, preimage, error_message
        )
