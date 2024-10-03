# ruff: noqa: E402
import asyncio

import uvloop

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

from time import time
from uuid import uuid4

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from lnbits.app import create_app
from lnbits.core.crud import (
    create_wallet,
    delete_account,
    get_account,
    get_account_by_username,
    get_user,
    update_payment_status,
)
from lnbits.core.models import Account, CreateInvoice, PaymentState, User
from lnbits.core.services import create_user_account, update_wallet_balance
from lnbits.core.views.payment_api import api_payments_create_invoice
from lnbits.db import DB_TYPE, SQLITE, Database
from lnbits.settings import AuthMethods, Settings
from lnbits.settings import settings as lnbits_settings
from tests.helpers import (
    get_random_invoice_data,
)


@pytest_asyncio.fixture(scope="session")
def settings():
    # override settings for tests
    lnbits_settings.lnbits_admin_extensions = []
    lnbits_settings.lnbits_data_folder = "./tests/data"
    lnbits_settings.lnbits_admin_ui = True
    lnbits_settings.lnbits_extensions_default_install = []
    lnbits_settings.lnbits_extensions_deactivate_all = True

    return lnbits_settings


@pytest.fixture(autouse=True)
def run_before_and_after_tests(settings: Settings):
    """Fixture to execute asserts before and after a test is run"""
    ##### BEFORE TEST RUN #####

    _settings_cleanup(settings)

    yield  # this is where the testing happens
    ##### AFTER TEST RUN #####
    _settings_cleanup(settings)


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()


# use session scope to run once before and once after all tests
@pytest_asyncio.fixture(scope="session")
async def app(settings: Settings):
    app = create_app()
    async with LifespanManager(app) as manager:
        settings.first_install = False
        yield manager.app


@pytest_asyncio.fixture(scope="session")
async def client(app, settings: Settings):
    url = f"http://{settings.host}:{settings.port}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=url) as client:
        yield client


# function scope
@pytest_asyncio.fixture(scope="function")
async def http_client(app, settings: Settings):
    url = f"http://{settings.host}:{settings.port}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=url) as client:
        yield client


@pytest.fixture(scope="session")
def test_client(app):
    return TestClient(app)


@pytest_asyncio.fixture(scope="session")
async def db():
    yield Database("database")


@pytest_asyncio.fixture(scope="session")
async def user_alan():
    account = await get_account_by_username("alan")
    if account:
        await delete_account(account.id)

    account = Account(
        id=uuid4().hex,
        email="alan@lnbits.com",
        username="alan",
    )
    account.hash_password("secret1234")
    user = await create_user_account(account)

    yield user


@pytest_asyncio.fixture(scope="session")
async def from_user():
    user = await create_user_account()
    yield user


@pytest_asyncio.fixture(scope="session")
async def from_wallet(from_user):
    user = from_user

    wallet = await create_wallet(user_id=user.id, wallet_name="test_wallet_from")
    await update_wallet_balance(
        wallet_id=wallet.id,
        amount=999999999,
    )
    yield wallet


@pytest_asyncio.fixture
async def from_wallet_ws(from_wallet, test_client):
    # wait a bit in order to avoid receiving topup notification
    await asyncio.sleep(0.1)
    with test_client.websocket_connect(f"/api/v1/ws/{from_wallet.inkey}") as ws:
        yield ws


@pytest_asyncio.fixture(scope="session")
async def to_user():
    user = await create_user_account()
    yield user


@pytest.fixture()
def from_super_user(from_user: User, settings: Settings):
    prev = settings.super_user
    settings.super_user = from_user.id
    yield from_user
    settings.super_user = prev


@pytest_asyncio.fixture(scope="session")
async def superuser(settings: Settings):
    account = await get_account(settings.super_user)
    assert account, "Superuser not found"
    user = await get_user(account)
    yield user


@pytest_asyncio.fixture(scope="session")
async def to_wallet(to_user):
    user = to_user
    wallet = await create_wallet(user_id=user.id, wallet_name="test_wallet_to")
    await update_wallet_balance(
        wallet_id=wallet.id,
        amount=999999999,
    )
    yield wallet


@pytest_asyncio.fixture
async def to_wallet_ws(to_wallet, test_client):
    # wait a bit in order to avoid receiving topup notification
    await asyncio.sleep(0.1)
    with test_client.websocket_connect(f"/api/v1/ws/{to_wallet.inkey}") as ws:
        yield ws


@pytest_asyncio.fixture(scope="session")
async def inkey_headers_from(from_wallet):
    wallet = from_wallet
    yield {
        "X-Api-Key": wallet.inkey,
        "Content-type": "application/json",
    }


@pytest_asyncio.fixture(scope="session")
async def adminkey_headers_from(from_wallet):
    wallet = from_wallet
    yield {
        "X-Api-Key": wallet.adminkey,
        "Content-type": "application/json",
    }


@pytest_asyncio.fixture(scope="session")
async def inkey_headers_to(to_wallet):
    wallet = to_wallet
    yield {
        "X-Api-Key": wallet.inkey,
        "Content-type": "application/json",
    }


@pytest_asyncio.fixture(scope="session")
async def adminkey_headers_to(to_wallet):
    wallet = to_wallet
    yield {
        "X-Api-Key": wallet.adminkey,
        "Content-type": "application/json",
    }


@pytest_asyncio.fixture(scope="session")
async def invoice(to_wallet):
    data = await get_random_invoice_data()
    invoice_data = CreateInvoice(**data)
    invoice = await api_payments_create_invoice(invoice_data, to_wallet)
    yield invoice
    del invoice


@pytest_asyncio.fixture(scope="session")
async def fake_payments(client, adminkey_headers_from):
    # Because sqlite only stores timestamps with milliseconds
    # we have to wait a second to ensure a different timestamp than previous invoices
    if DB_TYPE == SQLITE:
        await asyncio.sleep(1)
    ts = time()

    fake_data = [
        CreateInvoice(amount=10, memo="aaaa", out=False),
        CreateInvoice(amount=100, memo="bbbb", out=False),
        CreateInvoice(amount=1000, memo="aabb", out=False),
    ]

    for invoice in fake_data:
        response = await client.post(
            "/api/v1/payments", headers=adminkey_headers_from, json=invoice.dict()
        )
        assert response.is_success
        data = response.json()
        assert data["checking_id"]
        await update_payment_status(data["checking_id"], status=PaymentState.SUCCESS)

    params = {"time[ge]": ts, "time[le]": time()}
    return fake_data, params


def _settings_cleanup(settings: Settings):
    settings.lnbits_allow_new_accounts = True
    settings.lnbits_allowed_users = []
    settings.auth_allowed_methods = AuthMethods.all()
    settings.auth_credetials_update_threshold = 120
    settings.lnbits_reserve_fee_percent = 1
    settings.lnbits_reserve_fee_min = 2000
    settings.lnbits_service_fee = 0
    settings.lnbits_wallet_limit_daily_max_withdraw = 0
