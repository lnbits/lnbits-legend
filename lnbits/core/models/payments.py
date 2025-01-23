from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import Query
from pydantic import BaseModel, Field, validator

from lnbits.db import FilterModel
from lnbits.utils.exchange_rates import allowed_currencies
from lnbits.wallets import get_funding_source
from lnbits.wallets.base import (
    PaymentFailedStatus,
    PaymentPendingStatus,
    PaymentStatus,
    PaymentSuccessStatus,
)


class PaymentState(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class PaymentExtra(BaseModel):
    comment: Optional[str] = None
    success_action: Optional[str] = None
    lnurl_response: Optional[str] = None


class PayInvoice(BaseModel):
    payment_request: str
    description: Optional[str] = None
    max_sat: Optional[int] = None
    extra: Optional[dict] = {}


class CreatePayment(BaseModel):
    wallet_id: str
    payment_hash: str
    bolt11: str
    amount_msat: int
    memo: str
    extra: Optional[dict] = {}
    preimage: Optional[str] = None
    expiry: Optional[datetime] = None
    webhook: Optional[str] = None
    fee: int = 0


class Payment(BaseModel):
    checking_id: str
    payment_hash: str
    wallet_id: str
    amount: int
    fee: int
    bolt11: str
    status: str = PaymentState.PENDING
    memo: Optional[str] = None
    expiry: Optional[datetime] = None
    webhook: Optional[str] = None
    webhook_status: Optional[int] = None
    preimage: Optional[str] = None
    tag: Optional[str] = None
    extension: Optional[str] = None
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict = {}

    @property
    def pending(self) -> bool:
        return self.status == PaymentState.PENDING.value

    @property
    def success(self) -> bool:
        return self.status == PaymentState.SUCCESS.value

    @property
    def failed(self) -> bool:
        return self.status == PaymentState.FAILED.value

    @property
    def msat(self) -> int:
        return self.amount

    @property
    def sat(self) -> int:
        return self.amount // 1000

    @property
    def is_in(self) -> bool:
        return self.amount > 0

    @property
    def is_out(self) -> bool:
        return self.amount < 0

    @property
    def is_expired(self) -> bool:
        return self.expiry < datetime.now(timezone.utc) if self.expiry else False

    @property
    def is_internal(self) -> bool:
        return self.checking_id.startswith("internal_")

    async def check_status(self) -> PaymentStatus:
        if self.is_internal:
            if self.success:
                return PaymentSuccessStatus()
            if self.failed:
                return PaymentFailedStatus()
            return PaymentPendingStatus()
        funding_source = get_funding_source()
        if self.is_out:
            status = await funding_source.get_payment_status(self.checking_id)
        else:
            status = await funding_source.get_invoice_status(self.checking_id)
        return status


class PaymentFilters(FilterModel):
    __search_fields__ = ["memo", "amount"]

    __sort_fields__ = ["created_at", "amount", "wallet_id", "memo", "status", "time"]

    status: str
    checking_id: str
    amount: int
    fee: int
    memo: Optional[str]
    time: datetime
    bolt11: str
    preimage: str
    payment_hash: str
    expiry: Optional[datetime]
    extra: dict = {}
    wallet_id: str
    webhook: Optional[str]
    webhook_status: Optional[int]


class PaymentHistoryPoint(BaseModel):
    date: datetime
    income: int
    spending: int
    balance: int


class DecodePayment(BaseModel):
    data: str
    filter_fields: Optional[list[str]] = []


class CreateInvoice(BaseModel):
    unit: str = "sat"
    internal: bool = False
    out: bool = True
    amount: float = Query(None, ge=0)
    memo: Optional[str] = None
    description_hash: Optional[str] = None
    unhashed_description: Optional[str] = None
    expiry: Optional[int] = None
    extra: Optional[dict] = None
    webhook: Optional[str] = None
    bolt11: Optional[str] = None
    lnurl_callback: Optional[str] = None

    @validator("unit")
    @classmethod
    def unit_is_from_allowed_currencies(cls, v):
        if v != "sat" and v not in allowed_currencies():
            raise ValueError("The provided unit is not supported")
        return v


class PaymentsStatusCount(BaseModel):
    incoming: int = 0
    outgoing: int = 0
    failed: int = 0
    pending: int = 0
