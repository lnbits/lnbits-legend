import asyncio
import base64
import hashlib
from collections.abc import AsyncGenerator
from os import environ
from typing import Optional

import grpc
from loguru import logger

import lnbits.wallets.lnd_grpc_files.lightning_pb2 as ln
import lnbits.wallets.lnd_grpc_files.lightning_pb2_grpc as lnrpc
import lnbits.wallets.lnd_grpc_files.router_pb2 as router
import lnbits.wallets.lnd_grpc_files.router_pb2_grpc as routerrpc
from lnbits.settings import settings
from lnbits.utils.crypto import AESCipher

from .base import (
    InvoiceResponse,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
    StatusResponse,
    Wallet,
)
from .macaroon import load_macaroon


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
    except Exception:
        return b""


def bytes_to_hex(b: bytes) -> str:
    return b.hex()


# Due to updated ECDSA generated tls.cert we need to let gprc know that
# we need to use that cipher suite otherwise there will be a handhsake
# error when we communicate with the lnd rpc server.
environ["GRPC_SSL_CIPHER_SUITES"] = "HIGH+ECDSA"


class LndWallet(Wallet):
    def __init__(self):
        if not settings.lnd_grpc_endpoint:
            raise ValueError("cannot initialize LndWallet: missing lnd_grpc_endpoint")
        if not settings.lnd_grpc_port:
            raise ValueError("cannot initialize LndWallet: missing lnd_grpc_port")

        cert_path = settings.lnd_grpc_cert or settings.lnd_cert
        if not cert_path:
            raise ValueError(
                "cannot initialize LndWallet: missing lnd_grpc_cert or lnd_cert"
            )

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
        if not macaroon:
            raise ValueError(
                "cannot initialize LndWallet: "
                "missing lnd_grpc_macaroon or lnd_grpc_admin_macaroon or "
                "lnd_admin_macaroon or lnd_grpc_invoice_macaroon or "
                "lnd_invoice_macaroon or lnd_grpc_macaroon_encrypted"
            )

        self.endpoint = self.normalize_endpoint(
            settings.lnd_grpc_endpoint, add_proto=False
        )
        self.port = int(settings.lnd_grpc_port)
        self.macaroon = load_macaroon(macaroon)
        cert = open(cert_path, "rb").read()
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

    async def cleanup(self):
        pass

    async def status(self) -> StatusResponse:
        try:
            resp = await self.rpc.ChannelBalance(ln.ChannelBalanceRequest())
        except Exception as exc:
            return StatusResponse(f"Unable to connect, got: '{exc}'", 0)

        return StatusResponse(None, resp.balance * 1000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> InvoiceResponse:
        data: dict = {
            "description_hash": b"",
            "value": amount,
            "private": True,
            "memo": memo or "",
        }
        if kwargs.get("expiry"):
            data["expiry"] = kwargs["expiry"]
        if description_hash:
            data["description_hash"] = description_hash
        elif unhashed_description:
            data["description_hash"] = hashlib.sha256(
                unhashed_description
            ).digest()  # as bytes directly

        try:
            req = ln.Invoice(**data)
            resp = await self.rpc.AddInvoice(req)
        except Exception as exc:
            logger.warning(exc)
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
        except Exception as exc:
            logger.warning(exc)
            return PaymentResponse(None, None, None, None, str(exc))

        # PaymentStatus from https://github.com/lightningnetwork/lnd/blob/master/channeldb/payments.go#L178
        statuses = {
            0: None,  # NON_EXISTENT
            1: None,  # IN_FLIGHT
            2: True,  # SUCCEEDED
            3: False,  # FAILED
        }

        failure_reasons = {
            0: "Payment failed: No error given.",
            1: "Payment failed: Payment timed out.",
            2: "Payment failed: No route to destination.",
            3: "Payment failed: Error.",
            4: "Payment failed: Incorrect payment details.",
            5: "Payment failed: Insufficient balance.",
        }

        fee_msat = None
        preimage = None
        error_message = None
        checking_id = None

        if statuses[resp.status] is True:  # SUCCEEDED
            fee_msat = -resp.htlcs[-1].route.total_fees_msat
            preimage = resp.payment_preimage
            checking_id = resp.payment_hash
        elif statuses[resp.status] is False:
            error_message = failure_reasons[resp.failure_reason]

        return PaymentResponse(
            statuses[resp.status], checking_id, fee_msat, preimage, error_message
        )

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            r_hash = hex_to_bytes(checking_id)
            if len(r_hash) != 32:
                # this may happen if we switch between backend wallets
                # that use different checking_id formats
                raise ValueError

            resp = await self.rpc.LookupInvoice(ln.PaymentHash(r_hash=r_hash))

            # todo: where is the FAILED status
            if resp.settled:
                return PaymentSuccessStatus()

            return PaymentPendingStatus()
        except grpc.RpcError as exc:
            logger.warning(exc)
            return PaymentPendingStatus()
        except Exception as exc:
            logger.warning(exc)
            return PaymentPendingStatus()

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
            return PaymentPendingStatus()

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
            resp = self.routerpc.TrackPaymentV2(
                router.TrackPaymentRequest(payment_hash=r_hash)
            )
            async for payment in resp:
                if len(payment.htlcs) and statuses[payment.status]:
                    return PaymentSuccessStatus(
                        fee_msat=-payment.htlcs[-1].route.total_fees_msat,
                        preimage=bytes_to_hex(payment.htlcs[-1].preimage),
                    )
                return PaymentStatus(statuses[payment.status])
        except Exception:  # most likely the payment wasn't found
            return PaymentPendingStatus()

        return PaymentPendingStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while settings.lnbits_running:
            try:
                request = ln.InvoiceSubscription()
                async for i in self.rpc.SubscribeInvoices(request):
                    if not i.settled:
                        continue

                    checking_id = bytes_to_hex(i.r_hash)
                    yield checking_id
            except Exception as exc:
                logger.error(
                    f"lost connection to lnd invoices stream: '{exc}', "
                    "retrying in 5 seconds"
                )
                await asyncio.sleep(5)
