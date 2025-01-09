import functools
import importlib
import json
import operator

import pytest

from lnbits.core.models import BaseWallet
from tests.wallets.fixtures.models import FundingSourceConfig, WalletTest

wallets_module = importlib.import_module("lnbits.wallets")


def wallet_fixtures_from_json(path) -> list["WalletTest"]:
    with open(path) as f:
        data = json.load(f)

        funding_sources = [
            FundingSourceConfig(name=fs_name, **data["funding_sources"][fs_name])
            for fs_name in data["funding_sources"]
        ]
        tests: dict[str, list[WalletTest]] = {}
        for fn_name in data["functions"]:
            fn = data["functions"][fn_name]
            fn_tests = _tests_for_function(funding_sources, fn_name, fn)
            _merge_dict_of_lists(tests, fn_tests)

        all_tests: list[WalletTest] = functools.reduce(
            operator.iadd, [tests[fs_name] for fs_name in tests], []
        )
        return all_tests


def _tests_for_function(
    funding_sources: list[FundingSourceConfig], fn_name: str, fn
) -> dict[str, list[WalletTest]]:
    tests: dict[str, list[WalletTest]] = {}
    for test in fn["tests"]:
        """create an unit test for each funding source"""

        fs_tests = _tests_for_funding_source(funding_sources, fn_name, fn, test)
        _merge_dict_of_lists(tests, fs_tests)

    return tests


def _tests_for_funding_source(
    funding_sources: list[FundingSourceConfig], fn_name: str, fn, test
) -> dict[str, list[WalletTest]]:
    tests: dict[str, list[WalletTest]] = {fs.name: [] for fs in funding_sources}
    for fs in funding_sources:
        tests[fs.name] += WalletTest.tests_for_funding_source(fs, fn_name, fn, test)
    return tests


def build_test_id(test: WalletTest):
    return f"{test.funding_source.name}.{test.function}({test.description})"


def load_funding_source(funding_source: FundingSourceConfig) -> BaseWallet:
    custom_settings = funding_source.settings
    original_settings = {}

    settings = wallets_module.settings

    for s in custom_settings:
        original_settings[s] = getattr(settings, s)
        setattr(settings, s, custom_settings[s])

    fs_instance: BaseWallet = getattr(wallets_module, funding_source.wallet_class)()

    # rollback settings (global variable)
    for s in original_settings:
        setattr(settings, s, original_settings[s])

    return fs_instance


async def check_assertions(wallet, _test_data: WalletTest):
    test_data = _test_data.dict()
    tested_func = _test_data.function
    call_params = _test_data.call_params

    if "expect" in test_data:
        await _assert_data(
            wallet,
            tested_func,
            call_params,
            _test_data.expect,
            _test_data.description,
        )
        # if len(_test_data.mocks) == 0:
        #     # all calls should fail after this method is called
        #     await wallet.cleanup()
        #     # same behaviour expected is server canot be reached
        #     # or if the connection was closed
        #     await _assert_data(wallet, tested_func, call_params, _test_data.expect)
    elif "expect_error" in test_data:
        await _assert_error(wallet, tested_func, call_params, _test_data.expect_error)
    else:
        raise AssertionError("Expected outcome not specified")


async def _assert_data(wallet, tested_func, call_params, expect, description):
    resp = await getattr(wallet, tested_func)(**call_params)
    fn_prefix = "__eval__:"
    for key in expect:
        expected = expect[key]
        if key.startswith(fn_prefix):
            key = key[len(fn_prefix) :]
            received = getattr(resp, key)
            expected = expected.format(**{key: received, "description": description})
            _assert = eval(expected)
        else:
            received = getattr(resp, key)
            _assert = getattr(resp, key) == expect[key]

        assert _assert, (
            f""" Field "{key}"."""
            f""" Received: "{received}"."""
            f""" Expected: "{expected}"."""
        )


async def _assert_error(wallet, tested_func, call_params, expect_error):
    error_module = importlib.import_module(expect_error["module"])
    error_class = getattr(error_module, expect_error["class"])
    with pytest.raises(error_class) as e_info:
        await getattr(wallet, tested_func)(**call_params)

    assert e_info.match(expect_error["message"])


def _merge_dict_of_lists(v1: dict[str, list], v2: dict[str, list]):
    """Merge v2 into v1"""
    for k in v2:
        v1[k] = v2[k] if k not in v1 else v1[k] + v2[k]
