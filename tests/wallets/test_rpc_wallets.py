import importlib
from typing import Optional
from unittest.mock import AsyncMock, Mock

import pytest
from loguru import logger
from pytest_mock.plugin import MockerFixture

from lnbits.core.models import BaseWallet
from tests.wallets.fixtures.models import DataObject
from tests.wallets.fixtures.models import Mock as RpcMock
from tests.wallets.helpers import (
    WalletTest,
    build_test_id,
    check_assertions,
    load_funding_source,
    wallet_fixtures_from_json,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_data",
    wallet_fixtures_from_json("tests/wallets/fixtures/json/fixtures_rpc.json"),
    ids=build_test_id,
)
async def test_wallets(mocker: MockerFixture, test_data: WalletTest):
    test_id = build_test_id(test_data)
    logger.info(f"[{test_id}]: test start")

    try:
        if test_data.skip:
            logger.info(f"[{test_id}]: test skip")
            pytest.skip()

        logger.info(f"[{test_id}]: apply {len(test_data.mocks)} mocks")
        for mock in test_data.mocks:
            _apply_rpc_mock(mocker, mock)

        logger.info(f"[{test_id}]: load funding source")
        wallet = load_funding_source(test_data.funding_source)

        logger.info(f"[{test_id}]: spy mocks")
        expected_calls = _spy_mocks(mocker, test_data, wallet)

        logger.info(f"[{test_id}]: check assertions")
        await check_assertions(wallet, test_data)

        logger.info(f"[{test_id}]: check calls")
        _check_calls(expected_calls)

    except Exception as exc:
        logger.info(f"[{test_id}]: test failed: {exc}")
        raise exc
    finally:
        logger.info(f"[{test_id}]: test end")


def _apply_rpc_mock(mocker: MockerFixture, mock: RpcMock):
    return_value = {}
    assert isinstance(mock.response, dict), "Expected data RPC response"
    for field_name in mock.response:
        value = mock.response[field_name]
        values = value if isinstance(value, list) else [value]

        _mock_class = (
            AsyncMock if values[0]["request_type"] == "async-function" else Mock
        )
        return_value[field_name] = _mock_class(
            side_effect=[_mock_field(f) for f in values]
        )

    m = _data_mock(return_value)
    assert mock.method, "Missing method for RPC mock."
    mocker.patch(mock.method, m)


def _check_calls(expected_calls):
    for func in expected_calls:
        func_calls = expected_calls[func]
        for func_call in func_calls:
            req = func_call["request_data"]
            args = req["args"] if "args" in req else {}
            kwargs = _eval_dict(req["kwargs"]) if "kwargs" in req else {}

            if "klass" in req:
                *rest, cls = req["klass"].split(".")
                req_module = importlib.import_module(".".join(rest))
                req_class = getattr(req_module, cls)
                func_call["spy"].assert_called_with(req_class(*args, **kwargs))
            else:
                func_call["spy"].assert_called_with(*args, **kwargs)


def _spy_mocks(mocker: MockerFixture, test_data: WalletTest, wallet: BaseWallet):
    expected_calls: dict[str, list] = {}
    for mock in test_data.mocks:
        client_field = getattr(wallet, mock.name)
        spy = _spy_mock(mocker, mock, client_field)
        expected_calls |= spy

    return expected_calls


def _spy_mock(mocker: MockerFixture, mock: RpcMock, client_field):

    expected_calls: dict[str, list] = {}
    assert isinstance(mock.response, dict), "Expected data RPC response"
    for field_name in mock.response:
        value = mock.response[field_name]
        values = value if isinstance(value, list) else [value]

        expected_calls[field_name] = [
            {
                "spy": mocker.spy(client_field, field_name),
                "request_data": f["request_data"],
            }
            for f in values
            if (
                f["request_type"] == "function" or f["request_type"] == "async-function"
            )
            and "request_data" in f
        ]
    return expected_calls


def _async_generator(data):
    async def f1():
        for d in data:
            value = _eval_dict(d)
            yield _dict_to_object(value)

    return f1()


def _mock_field(field):
    response_type = field["response_type"]
    request_type = field["request_type"]
    response = _eval_dict(field["response"])

    if request_type == "data":
        return _dict_to_object(response)

    if request_type == "function" or request_type == "async-function":
        if response_type == "data":
            return _dict_to_object(response)

        if response_type == "exception":
            return _raise(response)

        if response_type == "__aiter__":
            # todo: support dict
            return _async_generator(field["response"])

        if response_type == "function" or response_type == "async-function":
            return_value = {}
            for field_name in field["response"]:
                value = field["response"][field_name]
                _mock_class = (
                    AsyncMock if value["request_type"] == "async-function" else Mock
                )

                return_value[field_name] = _mock_class(side_effect=[_mock_field(value)])

            return _dict_to_object(return_value)

    return response


def _eval_dict(data: Optional[dict]) -> Optional[dict]:
    fn_prefix = "__eval__:"
    if not data:
        return data
    # if isinstance(data, list):
    #     return [_eval_dict(i) for i in data]
    if not isinstance(data, dict):
        return data

    d = {}
    for k in data:
        if k.startswith(fn_prefix):
            field = k[len(fn_prefix) :]
            d[field] = eval(data[k])
        elif isinstance(data[k], dict):
            d[k] = _eval_dict(data[k])
        elif isinstance(data[k], list):
            d[k] = [_eval_dict(i) for i in data[k]]
        else:
            d[k] = data[k]
    return d


def _dict_to_object(data: Optional[dict]) -> Optional[DataObject]:
    if not data:
        return None
    # if isinstance(data, list):
    #     return [_dict_to_object(i) for i in data]
    if not isinstance(data, dict):
        return data

    d = {**data}
    for k in data:
        value = data[k]
        if isinstance(value, dict):
            d[k] = _dict_to_object(value)
        elif isinstance(value, list):
            d[k] = [_dict_to_object(v) for v in value]

    return DataObject(**d)


def _data_mock(data: dict) -> Mock:
    return Mock(return_value=_dict_to_object(data))


def _raise(error: Optional[dict]):
    if not error:
        return Exception()
    data = error["data"] if "data" in error else None
    if "module" not in error or "class" not in error:
        return Exception(data)

    error_module = importlib.import_module(error["module"])
    error_class = getattr(error_module, error["class"])

    return error_class(**data)
