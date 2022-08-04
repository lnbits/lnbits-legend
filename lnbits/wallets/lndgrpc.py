imports_ok = True
try:
    import grpc
    from grpc import RpcError
    from google import protobuf
except ImportError:  # pragma: nocover
    imports_ok = False

import time
import asyncio
import base64
import binascii
import hashlib
from os import environ, error, getenv
from typing import AsyncGenerator, Dict, Optional

from loguru import logger

from .macaroon import AESCipher, load_macaroon

if imports_ok:
    import lnbits.wallets.lnd_grpc_files.lightning_pb2 as ln
    import lnbits.wallets.lnd_grpc_files.lightning_pb2_grpc as lnrpc
    import lnbits.wallets.lnd_grpc_files.router_pb2 as router
    import lnbits.wallets.lnd_grpc_files.router_pb2_grpc as routerrpc

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


def parse_checking_id(checking_id: str) -> bytes:
    return base64.b64decode(checking_id.replace("_", "/"))


def stringify_checking_id(r_hash: bytes) -> str:
    return base64.b64encode(r_hash).decode("utf-8").replace("/", "_")


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

        endpoint = getenv("LND_GRPC_ENDPOINT")
        self.endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
        self.port = int(getenv("LND_GRPC_PORT"))
        self.cert_path = getenv("LND_GRPC_CERT") or getenv("LND_CERT")

        macaroon = (
            getenv("LND_GRPC_MACAROON")
            or getenv("LND_GRPC_ADMIN_MACAROON")
            or getenv("LND_ADMIN_MACAROON")
            or getenv("LND_GRPC_INVOICE_MACAROON")
            or getenv("LND_INVOICE_MACAROON")
        )

        encrypted_macaroon = getenv("LND_GRPC_MACAROON_ENCRYPTED")
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
    ) -> InvoiceResponse:
        params: Dict = {"value": amount, "expiry": 600, "private": True}

        if description_hash:
            params["description_hash"] = hashlib.sha256(
                description_hash
            ).digest()  # as bytes directly

        else:
            params["memo"] = memo or ""

        try:
            req = ln.Invoice(**params)
            resp = await self.rpc.AddInvoice(req)
        except Exception as exc:
            error_message = str(exc)
            return InvoiceResponse(False, None, None, error_message)

        checking_id = stringify_checking_id(resp.r_hash)
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
            print(resp)
        except RpcError as exc:
            return PaymentResponse(False, "", 0, None, exc._details)
        except Exception as exc:
            return PaymentResponse(False, "", 0, None, str(exc))

        checking_id = resp.payment_hash

        if resp.status == 0:  # IN FLIGHT
            return PaymentResponse(None, checking_id, fee_limit_msat, None, None)
        elif resp.status == 1:  # SUCCEEDED
            fee_msat = resp.htlcs[-1].route.total_fees_msat
            preimage = resp.payment_preimage
            return PaymentResponse(True, checking_id, fee_msat, preimage, None)
        elif resp.status == 2:  # FAILED
            return PaymentResponse(False, checking_id, 0, None, resp.failure_reason)
        else:
            return PaymentResponse(None, checking_id, 0, None, "Unknown status")

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r_hash = parse_checking_id(checking_id)
            if len(r_hash) != 32:
                raise binascii.Error
        except binascii.Error:
            # this may happen if we switch between backend wallets
            # that use different checking_id formats
            return PaymentStatus(None)

        resp = await self.rpc.LookupInvoice(ln.PaymentHash(r_hash=r_hash))
        if resp.settled:
            return PaymentStatus(True)

        return PaymentStatus(None)

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        """
        This routine checks the payment status using routerpc.TrackPaymentV2.
        It blocks until the payment is either settled or failed.
        """
        # TODO: remove
        BLOCKING = True
        try:
            r_hash = parse_checking_id(checking_id)
            if len(r_hash) != 32:
                raise binascii.Error
        except binascii.Error:
            # this may happen if we switch between backend wallets
            # that use different checking_id formats
            return PaymentStatus(None)

        # for some reason our checking_ids are in base64 but the payment hashes
        # returned here are in hex, lnd is weird
        checking_id = checking_id.replace("_", "/")
        checking_id = base64.b64decode(checking_id).hex()

        resp = self.routerpc.TrackPaymentV2(
            router.TrackPaymentRequest(payment_hash=r_hash)
        )

        # HTLCAttempt.HTLCStatus:
        # https://github.com/lightningnetwork/lnd/blob/master/lnrpc/lightning.proto#L3641
        statuses = {
            0: None,  # IN_FLIGHT
            1: True,  # "SUCCEEDED"
            2: False,  # "FAILED"
        }

        try:
            async for payment in resp:
                if BLOCKING and payment.htlcs[-1].status == 0:  # IN_FLIGHT
                    logger.debug("payment is in flight, checking again in 1 second")
                    await asyncio.sleep(1)
                    continue

                return PaymentStatus(statuses[payment.htlcs[-1].status])
        except:  # most likely the payment wasn't found
            return PaymentStatus(None)

        return PaymentStatus(None)

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while True:
            request = ln.InvoiceSubscription()
            try:
                async for i in self.rpc.SubscribeInvoices(request):
                    if not i.settled:
                        continue

                    checking_id = stringify_checking_id(i.r_hash)
                    yield checking_id
            except Exception as exc:
                logger.error(
                    f"lost connection to lnd invoices stream: '{exc}', retrying in 5 seconds"
                )
                await asyncio.sleep(5)
