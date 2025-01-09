import base64

try:
    import breez_sdk  # type: ignore

    BREEZ_SDK_INSTALLED = True
except ImportError:
    BREEZ_SDK_INSTALLED = False

if not BREEZ_SDK_INSTALLED:

    class BreezSdkWallet:  # pyright: ignore
        def __init__(self):
            raise RuntimeError(
                "Breez SDK is not installed. "
                "Ask admin to run `poetry install -E breez` to install it."
            )

else:
    import asyncio
    from collections.abc import AsyncGenerator
    from pathlib import Path
    from typing import Optional

    import bolt11 as lnbits_bolt11
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
        UnsupportedError,
        Wallet,
    )

    breez_event_queue: asyncio.Queue = asyncio.Queue()

    def load_bytes(source: str, extension: str) -> Optional[bytes]:
        # first check if it can be read from a file
        if source.split(".")[-1] == extension:
            with open(source, "rb") as f:
                source_bytes = f.read()
                return source_bytes
        else:
            # else check the source string can be converted from hex
            try:
                return bytes.fromhex(source)
            except ValueError:
                pass
            # else convert from base64
            try:
                return base64.b64decode(source)
            except Exception:
                pass
        return None

    def load_greenlight_credentials() -> (
        Optional[
            breez_sdk.GreenlightCredentials  # pyright: ignore[reportUnboundVariable]
        ]
    ):
        if (
            settings.breez_greenlight_device_key
            and settings.breez_greenlight_device_cert
        ):
            device_key_bytes = load_bytes(settings.breez_greenlight_device_key, "pem")
            device_cert_bytes = load_bytes(settings.breez_greenlight_device_cert, "crt")
            if not device_key_bytes or not device_cert_bytes:
                raise ValueError(
                    "cannot initialize BreezSdkWallet: "
                    "cannot decode breez_greenlight_device_key "
                    "or breez_greenlight_device_cert"
                )
            return breez_sdk.GreenlightCredentials(  # pyright: ignore[reportUnboundVariable]
                developer_key=list(device_key_bytes),
                developer_cert=list(device_cert_bytes),
            )
        return None

    class SDKListener(
        breez_sdk.EventListener  # pyright: ignore[reportUnboundVariable]
    ):
        def on_event(self, event):
            logger.debug(event)
            breez_event_queue.put_nowait(event)

    class BreezSdkWallet(Wallet):  # type: ignore[no-redef]
        def __init__(self):
            if not settings.breez_greenlight_seed:
                raise ValueError(
                    "cannot initialize BreezSdkWallet: missing breez_greenlight_seed"
                )
            if not settings.breez_api_key:
                raise ValueError(
                    "cannot initialize BreezSdkWallet: missing breez_api_key"
                )
            if (
                settings.breez_greenlight_device_key
                and not settings.breez_greenlight_device_cert
            ):
                raise ValueError(
                    "cannot initialize BreezSdkWallet: "
                    "missing breez_greenlight_device_cert"
                )
            if (
                settings.breez_greenlight_device_cert
                and not settings.breez_greenlight_device_key
            ):
                raise ValueError(
                    "cannot initialize BreezSdkWallet: "
                    "missing breez_greenlight_device_key"
                )

            self.config = breez_sdk.default_config(
                breez_sdk.EnvironmentType.PRODUCTION,
                settings.breez_api_key,
                breez_sdk.NodeConfig.GREENLIGHT(
                    config=breez_sdk.GreenlightNodeConfig(
                        partner_credentials=load_greenlight_credentials(),
                        invite_code=settings.breez_greenlight_invite_code,
                    )
                ),
            )

            breez_sdk_working_dir = Path(settings.lnbits_data_folder, "breez-sdk")
            breez_sdk_working_dir.mkdir(parents=True, exist_ok=True)
            self.config.working_dir = breez_sdk_working_dir.absolute().as_posix()

            try:
                seed = breez_sdk.mnemonic_to_seed(settings.breez_greenlight_seed)
                connect_request = breez_sdk.ConnectRequest(self.config, seed)
                self.sdk_services = breez_sdk.connect(connect_request, SDKListener())
            except Exception as exc:
                logger.warning(exc)
                raise ValueError(f"cannot initialize BreezSdkWallet: {exc!s}") from exc

        async def cleanup(self):
            self.sdk_services.disconnect()

        async def status(self) -> StatusResponse:
            try:
                node_info: breez_sdk.NodeState = self.sdk_services.node_info()
            except Exception as exc:
                return StatusResponse(f"Failed to connect to breez, got: '{exc}...'", 0)

            return StatusResponse(None, int(node_info.channels_balance_msat))

        async def create_invoice(
            self,
            amount: int,
            memo: Optional[str] = None,
            description_hash: Optional[bytes] = None,
            unhashed_description: Optional[bytes] = None,
            **kwargs,
        ) -> InvoiceResponse:
            # if description_hash or unhashed_description:
            #     raise UnsupportedError("description_hash and unhashed_description")
            try:
                if description_hash and not unhashed_description:
                    raise UnsupportedError(
                        "'description_hash' unsupported by Greenlight, provide"
                        " 'unhashed_description'"
                    )
                breez_invoice: breez_sdk.ReceivePaymentResponse = (
                    self.sdk_services.receive_payment(
                        breez_sdk.ReceivePaymentRequest(
                            amount * 1000,  # breez uses msat
                            (
                                unhashed_description.decode()
                                if unhashed_description
                                else memo
                            ),
                            preimage=kwargs.get("preimage"),
                            opening_fee_params=None,
                            use_description_hash=True if unhashed_description else None,
                        )
                    )
                )

                return InvoiceResponse(
                    True,
                    breez_invoice.ln_invoice.payment_hash,
                    breez_invoice.ln_invoice.bolt11,
                    None,
                )
            except Exception as e:
                logger.warning(e)
                return InvoiceResponse(False, None, None, str(e))

        async def pay_invoice(
            self, bolt11: str, fee_limit_msat: int
        ) -> PaymentResponse:
            invoice = lnbits_bolt11.decode(bolt11)

            try:
                send_payment_request = breez_sdk.SendPaymentRequest(bolt11=bolt11)
                send_payment_response: breez_sdk.SendPaymentResponse = (
                    self.sdk_services.send_payment(send_payment_request)
                )
                payment: breez_sdk.Payment = send_payment_response.payment
            except Exception as exc:
                logger.warning(exc)
                try:
                    # try to report issue to Breez to improve LSP routing
                    self.sdk_services.report_issue(
                        breez_sdk.ReportIssueRequest.PAYMENT_FAILURE(
                            breez_sdk.ReportPaymentFailureDetails(invoice.payment_hash)
                        )
                    )
                except Exception as ex:
                    logger.info(ex)
                # assume that payment failed?
                return PaymentResponse(
                    False, None, None, None, f"payment failed: {exc}"
                )

            if payment.status != breez_sdk.PaymentStatus.COMPLETE:
                return PaymentResponse(False, None, None, None, "payment is pending")

            # let's use the payment_hash as the checking_id
            checking_id = invoice.payment_hash

            return PaymentResponse(
                True,
                checking_id,
                payment.fee_msat,
                payment.details.data.payment_preimage,
                None,
            )

        async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
            try:
                payment: breez_sdk.Payment = self.sdk_services.payment_by_hash(
                    checking_id
                )
                if payment is None:
                    return PaymentPendingStatus()
                if payment.payment_type != breez_sdk.PaymentType.RECEIVED:
                    logger.warning(f"unexpected payment type: {payment.status}")
                    return PaymentPendingStatus()
                if payment.status == breez_sdk.PaymentStatus.FAILED:
                    return PaymentFailedStatus()
                if payment.status == breez_sdk.PaymentStatus.COMPLETE:
                    return PaymentSuccessStatus()
                return PaymentPendingStatus()
            except Exception as exc:
                logger.warning(exc)
                return PaymentPendingStatus()

        async def get_payment_status(self, checking_id: str) -> PaymentStatus:
            try:
                payment: breez_sdk.Payment = self.sdk_services.payment_by_hash(
                    checking_id
                )
                if payment is None:
                    return PaymentPendingStatus()
                if payment.payment_type != breez_sdk.PaymentType.SENT:
                    logger.warning(f"unexpected payment type: {payment.status}")
                    return PaymentPendingStatus()
                if payment.status == breez_sdk.PaymentStatus.COMPLETE:
                    return PaymentSuccessStatus(
                        fee_msat=payment.fee_msat,
                        preimage=payment.details.data.payment_preimage,
                    )
                if payment.status == breez_sdk.PaymentStatus.FAILED:
                    return PaymentFailedStatus()
                return PaymentPendingStatus()
            except Exception as exc:
                logger.warning(exc)
                return PaymentPendingStatus()

        async def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
            while True:
                event = await breez_event_queue.get()
                if event.is_invoice_paid():
                    yield event.details.payment_hash
