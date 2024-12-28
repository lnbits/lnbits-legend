from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import Enum
from sqlite3 import Row
from typing import Callable, Optional

from ecdsa import SECP256k1, SigningKey
from fastapi import Query
from pydantic import BaseModel, validator

from lnbits.db import FilterModel, FromRowModel
from lnbits.helpers import url_for
from lnbits.lnurl import encode as lnurl_encode
from lnbits.settings import settings
from lnbits.utils.exchange_rates import allowed_currencies
from lnbits.wallets import get_funding_source
from lnbits.wallets.base import (
    PaymentPendingStatus,
    PaymentStatus,
)


class BaseWallet(BaseModel):
    id: str
    name: str
    adminkey: str
    inkey: str
    balance_msat: int


class Wallet(BaseWallet):
    user: str
    currency: Optional[str]
    deleted: bool
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    @property
    def balance(self) -> int:
        return self.balance_msat // 1000

    @property
    def withdrawable_balance(self) -> int:
        from .services import fee_reserve

        return self.balance_msat - fee_reserve(self.balance_msat)

    @property
    def lnurlwithdraw_full(self) -> str:
        url = url_for("/withdraw", external=True, usr=self.user, wal=self.id)
        try:
            return lnurl_encode(url)
        except Exception:
            return ""

    def lnurlauth_key(self, domain: str) -> SigningKey:
        hashing_key = hashlib.sha256(self.id.encode()).digest()
        linking_key = hmac.digest(hashing_key, domain.encode(), "sha256")

        return SigningKey.from_string(
            linking_key, curve=SECP256k1, hashfunc=hashlib.sha256
        )

    async def get_payment(self, payment_hash: str) -> Optional[Payment]:
        from .crud import get_standalone_payment

        return await get_standalone_payment(payment_hash)


class KeyType(Enum):
    admin = 0
    invoice = 1
    invalid = 2

    # backwards compatibility
    def __eq__(self, other):
        return self.value == other


@dataclass
class WalletTypeInfo:
    key_type: KeyType
    wallet: Wallet


class UserConfig(BaseModel):
    email_verified: Optional[bool] = False
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    picture: Optional[str] = None
    # Auth provider, possible values:
    # - "env": the user was created automatically by the system
    # - "lnbits": the user was created via register form (username/pass or user_id only)
    # - "google | github | ...": the user was created using an SSO provider
    provider: Optional[str] = "lnbits"  # auth provider


class Account(FromRowModel):
    id: str
    is_super_user: Optional[bool] = False
    is_admin: Optional[bool] = False
    username: Optional[str] = None
    email: Optional[str] = None
    balance_msat: Optional[int] = 0
    transaction_count: Optional[int] = 0
    wallet_count: Optional[int] = 0
    last_payment: Optional[datetime.datetime] = None


class AccountFilters(FilterModel):
    __search_fields__ = ["id", "email", "username"]
    __sort_fields__ = [
        "balance_msat",
        "email",
        "username",
        "transaction_count",
        "wallet_count",
        "last_payment",
    ]

    id: str
    last_payment: Optional[datetime.datetime] = None
    transaction_count: Optional[int] = None
    wallet_count: Optional[int] = None
    username: Optional[str] = None
    email: Optional[str] = None


class User(BaseModel):
    id: str
    email: Optional[str] = None
    username: Optional[str] = None
    extensions: list[str] = []
    wallets: list[Wallet] = []
    admin: bool = False
    super_user: bool = False
    has_password: bool = False
    config: Optional[UserConfig] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    @property
    def wallet_ids(self) -> list[str]:
        return [wallet.id for wallet in self.wallets]

    def get_wallet(self, wallet_id: str) -> Optional[Wallet]:
        w = [wallet for wallet in self.wallets if wallet.id == wallet_id]
        return w[0] if w else None

    @classmethod
    def is_extension_for_user(cls, ext: str, user: str) -> bool:
        if ext not in settings.lnbits_admin_extensions:
            return True
        if user == settings.super_user:
            return True
        if user in settings.lnbits_admin_users:
            return True
        return False


class CreateUser(BaseModel):
    email: Optional[str] = Query(default=None)
    username: str = Query(default=..., min_length=2, max_length=20)
    password: str = Query(default=..., min_length=8, max_length=50)
    password_repeat: str = Query(default=..., min_length=8, max_length=50)


class UpdateUser(BaseModel):
    user_id: str
    email: Optional[str] = Query(default=None)
    username: Optional[str] = Query(default=..., min_length=2, max_length=20)
    config: Optional[UserConfig] = None


class UpdateUserPassword(BaseModel):
    user_id: str
    password: str = Query(default=..., min_length=8, max_length=50)
    password_repeat: str = Query(default=..., min_length=8, max_length=50)
    password_old: Optional[str] = Query(default=None, min_length=8, max_length=50)
    username: Optional[str] = Query(default=..., min_length=2, max_length=20)


class UpdateSuperuserPassword(BaseModel):
    username: str = Query(default=..., min_length=2, max_length=20)
    password: str = Query(default=..., min_length=8, max_length=50)
    password_repeat: str = Query(default=..., min_length=8, max_length=50)


class LoginUsr(BaseModel):
    usr: str


class LoginUsernamePassword(BaseModel):
    username: str
    password: str


class PaymentState(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class Payment(FromRowModel):
    status: str
    # TODO should be removed in the future, backward compatibility
    pending: bool
    checking_id: str
    amount: int
    fee: int
    memo: Optional[str]
    time: int
    bolt11: str
    preimage: str
    payment_hash: str
    expiry: Optional[float]
    extra: dict = {}
    wallet_id: str
    webhook: Optional[str]
    webhook_status: Optional[int]

    @property
    def success(self) -> bool:
        return self.status == PaymentState.SUCCESS.value

    @property
    def failed(self) -> bool:
        return self.status == PaymentState.FAILED.value

    @classmethod
    def from_row(cls, row: Row):
        return cls(
            checking_id=row["checking_id"],
            payment_hash=row["hash"] or "0" * 64,
            bolt11=row["bolt11"] or "",
            preimage=row["preimage"] or "0" * 64,
            extra=json.loads(row["extra"] or "{}"),
            status=row["status"],
            # TODO should be removed in the future, backward compatibility
            pending=row["status"] == PaymentState.PENDING.value,
            amount=row["amount"],
            fee=row["fee"],
            memo=row["memo"],
            time=row["time"],
            expiry=row["expiry"],
            wallet_id=row["wallet"],
            webhook=row["webhook"],
            webhook_status=row["webhook_status"],
        )

    @property
    def tag(self) -> Optional[str]:
        if self.extra is None:
            return ""
        return self.extra.get("tag")

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
        return self.expiry < time.time() if self.expiry else False

    @property
    def is_uncheckable(self) -> bool:
        return self.checking_id.startswith("internal_")

    async def check_status(self) -> PaymentStatus:
        if self.is_uncheckable:
            return PaymentPendingStatus()
        funding_source = get_funding_source()
        if self.is_out:
            status = await funding_source.get_payment_status(self.checking_id)
        else:
            status = await funding_source.get_invoice_status(self.checking_id)
        return status


class PaymentFilters(FilterModel):
    __search_fields__ = ["memo", "amount"]

    checking_id: str
    amount: int
    fee: int
    memo: Optional[str]
    time: datetime.datetime
    bolt11: str
    preimage: str
    payment_hash: str
    expiry: Optional[datetime.datetime]
    extra: dict = {}
    wallet_id: str
    webhook: Optional[str]
    webhook_status: Optional[int]


class PaymentHistoryPoint(BaseModel):
    date: datetime.datetime
    income: int
    spending: int
    balance: int


def _do_nothing(*_):
    pass


class CoreAppExtra:
    register_new_ext_routes: Callable = _do_nothing
    register_new_ratelimiter: Callable


class TinyURL(BaseModel):
    id: str
    url: str
    endless: bool
    wallet: str
    time: float

    @classmethod
    def from_row(cls, row: Row):
        return cls(**dict(row))


class ConversionData(BaseModel):
    from_: str = "sat"
    amount: float
    to: str = "usd"


class Callback(BaseModel):
    callback: str


class DecodePayment(BaseModel):
    data: str
    filter_fields: Optional[list[str]] = []


class CreateLnurl(BaseModel):
    description_hash: str
    callback: str
    amount: int
    comment: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None


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


class CreateHoldInvoice(BaseModel):
    unit: str = "sat"
    amount: float = Query(None, ge=0)
    hash: str
    memo: Optional[str] = None
    description_hash: Optional[str] = None
    extra: Optional[dict] = None
    webhook: Optional[str] = None

    @validator("unit")
    @classmethod
    def unit_is_from_allowed_currencies(cls, v):
        if v != "sat" and v not in allowed_currencies():
            raise ValueError("The provided unit is not supported")

        return v


class CreateTopup(BaseModel):
    id: str
    amount: int


class CreateLnurlAuth(BaseModel):
    callback: str


class CreateWallet(BaseModel):
    name: Optional[str] = None


class CreateWebPushSubscription(BaseModel):
    subscription: str


class WebPushSubscription(BaseModel):
    endpoint: str
    user: str
    data: str
    host: str
    timestamp: str


class BalanceDelta(BaseModel):
    lnbits_balance_msats: int
    node_balance_msats: int

    @property
    def delta_msats(self):
        return self.node_balance_msats - self.lnbits_balance_msats


class SimpleStatus(BaseModel):
    success: bool
    message: str


class SettleInvoice(BaseModel):
    preimage: str


class CancelInvoice(BaseModel):
    payment_hash: str
