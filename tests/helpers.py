import random
import string
from typing import Optional

from pydantic import BaseModel

from lnbits.wallets import get_funding_source, set_funding_source


class FakeError(Exception):
    pass


class DbTestModel(BaseModel):
    id: int
    name: str
    value: Optional[str] = None


class DbTestModel2(BaseModel):
    id: int
    label: str
    description: Optional[str] = None
    child: DbTestModel


class DbTestModel3(BaseModel):
    id: int
    child: DbTestModel2
    active: bool = False


def get_random_string(iterations: int = 10):
    return "".join(
        random.SystemRandom().choice(string.ascii_uppercase + string.digits)
        for _ in range(iterations)
    )


async def get_random_invoice_data():
    return {"out": False, "amount": 10, "memo": f"test_memo_{get_random_string(10)}"}


set_funding_source()
funding_source = get_funding_source()
is_fake: bool = funding_source.__class__.__name__ == "FakeWallet"
is_regtest: bool = not is_fake
