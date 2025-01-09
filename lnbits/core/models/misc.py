from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel


def _do_nothing(*_):
    pass


class CoreAppExtra:
    register_new_ext_routes: Callable = _do_nothing
    register_new_ratelimiter: Callable


class ConversionData(BaseModel):
    from_: str = "sat"
    amount: float
    to: str = "usd"


class Callback(BaseModel):
    callback: str


class BalanceDelta(BaseModel):
    lnbits_balance_msats: int
    node_balance_msats: int

    @property
    def delta_msats(self):
        return self.node_balance_msats - self.lnbits_balance_msats


class SimpleStatus(BaseModel):
    success: bool
    message: str


class DbVersion(BaseModel):
    db: str
    version: int
