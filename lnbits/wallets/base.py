from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncGenerator, Coroutine, NamedTuple, Optional

if TYPE_CHECKING:
    from lnbits.nodes.base import Node


class StatusResponse(NamedTuple):
    error_message: Optional[str]
    balance_msat: int


class InvoiceResponse(NamedTuple):
    ok: bool
    checking_id: Optional[str] = None  # payment_hash, rpc_id
    payment_request: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.ok is True

    @property
    def pending(self) -> bool:
        return self.ok is None

    @property
    def failed(self) -> bool:
        return self.ok is False


class PaymentResponse(NamedTuple):
    # when ok is None it means we don't know if this succeeded
    ok: Optional[bool] = None
    checking_id: Optional[str] = None  # payment_hash, rcp_id
    fee_msat: Optional[int] = None
    preimage: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.ok is True

    @property
    def pending(self) -> bool:
        return self.ok is None

    @property
    def failed(self) -> bool:
        return self.ok is False


class PaymentStatus(NamedTuple):
    paid: Optional[bool] = None
    fee_msat: Optional[int] = None
    preimage: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.paid is True

    @property
    def pending(self) -> bool:
        return self.paid is not True

    @property
    def failed(self) -> bool:
        return self.paid is False

    def __str__(self) -> str:
        if self.success:
            return "success"
        if self.failed:
            return "failed"
        return "pending"


class PaymentSuccessStatus(PaymentStatus):
    paid = True


class PaymentFailedStatus(PaymentStatus):
    paid = False


class PaymentPendingStatus(PaymentStatus):
    paid = None


class Wallet(ABC):

    __node_cls__: Optional[type[Node]] = None

    @abstractmethod
    async def cleanup(self):
        pass

    @abstractmethod
    def status(self) -> Coroutine[None, None, StatusResponse]:
        pass

    @abstractmethod
    def create_invoice(
        self,
        amount: int,
        memo: Optional[str] = None,
        description_hash: Optional[bytes] = None,
        unhashed_description: Optional[bytes] = None,
        **kwargs,
    ) -> Coroutine[None, None, InvoiceResponse]:
        pass

    @abstractmethod
    def pay_invoice(
        self, bolt11: str, fee_limit_msat: int
    ) -> Coroutine[None, None, PaymentResponse]:
        pass

    @abstractmethod
    def get_invoice_status(
        self, checking_id: str
    ) -> Coroutine[None, None, PaymentStatus]:
        pass

    @abstractmethod
    def get_payment_status(
        self, checking_id: str
    ) -> Coroutine[None, None, PaymentStatus]:
        pass

    @abstractmethod
    def paid_invoices_stream(self) -> AsyncGenerator[str, None]:
        pass

    def normalize_endpoint(self, endpoint: str, add_proto=True) -> str:
        endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
        if add_proto:
            if endpoint.startswith("ws://") or endpoint.startswith("wss://"):
                return endpoint
            endpoint = (
                f"https://{endpoint}" if not endpoint.startswith("http") else endpoint
            )
        return endpoint


class UnsupportedError(Exception):
    pass
