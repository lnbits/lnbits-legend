import asyncio
from collections.abc import AsyncGenerator
from typing import Optional

from bolt11.decode import decode
from grpc.aio import AioRpcError
from loguru import logger

from lnbits.settings import settings
from lnbits.wallets.boltz_grpc_files import boltzrpc_pb2, boltzrpc_pb2_grpc
from lnbits.wallets.lnd_grpc_files.lightning_pb2_grpc import grpc
from lnbits.wallets.macaroon.macaroon import load_macaroon

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


class BoltzWallet(Wallet):
    """
    Utilizing Boltz Client gRPC interface

    gRPC Bindings can be updated by running lnbits/wallets/boltz_grpc_files/update.sh
    """

    async def cleanup(self):
        logger.warning("Cleaning up BoltzWallet...")

    def __init__(self):
        if not settings.boltz_client_endpoint:
            raise ValueError(
                "cannot initialize BoltzWallet: missing boltz_client_endpoint"
            )
        if not settings.boltz_client_wallet:
            raise ValueError(
                "cannot initialize BoltzWallet: missing boltz_client_wallet"
            )

        self.endpoint = self.normalize_endpoint(
            settings.boltz_client_endpoint, add_proto=True
        )

        if settings.boltz_client_macaroon:
            self.metadata = [
                ("macaroon", load_macaroon(settings.boltz_client_macaroon))
            ]
        else:
            self.metadata = None

        if settings.boltz_client_cert:
            cert = open(settings.boltz_client_cert, "rb").read()
            creds = grpc.ssl_channel_credentials(cert)
            channel = grpc.aio.secure_channel(settings.boltz_client_endpoint, creds)
        else:
            channel = grpc.aio.insecure_channel(settings.boltz_client_endpoint)

        self.rpc = boltzrpc_pb2_grpc.BoltzStub(channel)
        self.wallet_id: int = 0

    async def status(self) -> StatusResponse:
        try:
            request = boltzrpc_pb2.GetWalletRequest(name=settings.boltz_client_wallet)
            response: boltzrpc_pb2.Wallet = await self.rpc.GetWallet(
                request, metadata=self.metadata
            )
        except AioRpcError as exc:
            return StatusResponse(
                exc.details()
                + " make sure you have macaroon and certificate configured, unless your client runs without",  # noqa: E501
                0,
            )

        self.wallet_id = response.id

        return StatusResponse(None, response.balance.total * 1000)

    async def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **_,
    ) -> InvoiceResponse:
        pair = boltzrpc_pb2.Pair(to=boltzrpc_pb2.LBTC)
        request = boltzrpc_pb2.CreateReverseSwapRequest(
            amount=amount,
            pair=pair,
            wallet_id=self.wallet_id,
            accept_zero_conf=True,
            external_pay=True,
        )
        if memo is not None:
            # boltz rejects nbsp char (produced by JS Intl.NumberFormat api)
            request.description = memo.replace("\xa0", " ")
        response: boltzrpc_pb2.CreateReverseSwapResponse
        try:
            response = await self.rpc.CreateReverseSwap(request, metadata=self.metadata)
        except AioRpcError as exc:
            return InvoiceResponse(ok=False, error_message=exc.details())
        return InvoiceResponse(
            ok=True, checking_id=response.id, payment_request=response.invoice
        )

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        pair = boltzrpc_pb2.Pair(**{"from": boltzrpc_pb2.LBTC})
        try:
            pair_info: boltzrpc_pb2.PairInfo
            pair_request = boltzrpc_pb2.GetPairInfoRequest(
                type=boltzrpc_pb2.SUBMARINE, pair=pair
            )
            pair_info = await self.rpc.GetPairInfo(pair_request, metadata=self.metadata)
            invoice = decode(bolt11)

            assert invoice.amount_msat, "amountless invoice"
            service_fee: float = invoice.amount_msat * pair_info.fees.percentage / 100
            estimate = service_fee + pair_info.fees.miner_fees * 1000
            if estimate > fee_limit_msat:
                error = f"fee of {estimate} msat exceeds limit of {fee_limit_msat} msat"

                return PaymentResponse(ok=False, error_message=error)

            request = boltzrpc_pb2.CreateSwapRequest(
                invoice=bolt11,
                pair=pair,
                wallet_id=self.wallet_id,
                zero_conf=True,
                send_from_internal=True,
            )
            response: boltzrpc_pb2.CreateSwapResponse
            response = await self.rpc.CreateSwap(request, metadata=self.metadata)

            # empty swap id means that the invoice included a magic routing hint and was
            # paid on the liquid network directly
            # docs: https://docs.boltz.exchange/api/magic-routing-hints
            if response.id == "":
                # note that there is no way to provide a checking id here,
                # but there is no need since it immediately is considered as successfull
                return PaymentResponse(
                    ok=True,
                    checking_id=response.id,
                )
        except AioRpcError as exc:
            return PaymentResponse(ok=False, error_message=exc.details())

        try:
            info_request = boltzrpc_pb2.GetSwapInfoRequest(id=response.id)
            info: boltzrpc_pb2.GetSwapInfoResponse
            async for info in self.rpc.GetSwapInfoStream(
                info_request, metadata=self.metadata
            ):
                if info.swap.state == boltzrpc_pb2.SUCCESSFUL:
                    return PaymentResponse(
                        ok=True,
                        checking_id=response.id,
                        fee_msat=(info.swap.onchain_fee + info.swap.service_fee) * 1000,
                        preimage=info.swap.preimage,
                    )
                elif info.swap.error != "":
                    return PaymentResponse(ok=False, error_message=info.swap.error)
            return PaymentResponse(
                ok=False, error_message="stream stopped unexpectedly"
            )
        except AioRpcError as exc:
            return PaymentResponse(ok=False, error_message=exc.details())

    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            response: boltzrpc_pb2.GetSwapInfoResponse = await self.rpc.GetSwapInfo(
                boltzrpc_pb2.GetSwapInfoRequest(id=checking_id), metadata=self.metadata
            )
            swap = response.reverse_swap
        except AioRpcError:
            return PaymentPendingStatus()
        if swap.state == boltzrpc_pb2.SwapState.SUCCESSFUL:
            return PaymentSuccessStatus(
                fee_msat=(
                    (swap.service_fee + swap.onchain_fee) * 1000 + swap.routing_fee_msat
                ),
                preimage=swap.preimage,
            )
        elif swap.state == boltzrpc_pb2.SwapState.PENDING:
            return PaymentPendingStatus()

        return PaymentFailedStatus()

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            response: boltzrpc_pb2.GetSwapInfoResponse = await self.rpc.GetSwapInfo(
                boltzrpc_pb2.GetSwapInfoRequest(id=checking_id), metadata=self.metadata
            )
            swap = response.swap
        except AioRpcError:
            return PaymentPendingStatus()
        if swap.state == boltzrpc_pb2.SwapState.SUCCESSFUL:
            return PaymentSuccessStatus(
                fee_msat=(swap.service_fee + swap.onchain_fee) * 1000,
                preimage=swap.preimage,
            )
        elif swap.state == boltzrpc_pb2.SwapState.PENDING:
            return PaymentPendingStatus()

        return PaymentFailedStatus()

    async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        while True:
            try:
                request = boltzrpc_pb2.GetSwapInfoRequest()
                info: boltzrpc_pb2.GetSwapInfoResponse
                async for info in self.rpc.GetSwapInfoStream(
                    request, metadata=self.metadata
                ):
                    reverse = info.reverse_swap
                    if reverse and reverse.state == boltzrpc_pb2.SUCCESSFUL:
                        yield reverse.id
            except Exception as exc:
                logger.error(
                    f"lost connection to boltz client swap stream: '{exc}', retrying in"
                    " 5 seconds"
                )
                await asyncio.sleep(5)
