import importlib
import json
from typing import Dict, List

import pytest

from lnbits.core.models import BaseWallet
from tests.wallets.fixtures.models import FundingSourceConfig, WalletTest

wallets_module = importlib.import_module("lnbits.wallets")


def rest_wallet_fixtures_from_json(path) -> List["WalletTest"]:
    with open(path) as f:
        data = json.load(f)

        funding_sources = data["funding_sources"]

        tests = {}
        for fn_name in data["functions"]:
            fn = data["functions"][fn_name]
            fs_tests = x1(funding_sources, fn_name, fn)

            _merge_dict_of_lists(tests, fs_tests)

        all_tests = sum([tests[fs_name] for fs_name in tests], [])
        return all_tests


def x1(funding_sources, fn_name, fn):
    tests: Dict[str, List[WalletTest]] = {}
    for test in fn["tests"]:
        """create an unit test for each funding source"""

        fs_tests = x2(funding_sources, fn_name, fn, test)
        _merge_dict_of_lists(tests, fs_tests)

    return tests


def x2(funding_sources, fn_name, fn, test) -> Dict[str, List[WalletTest]]:
    tests: Dict[str, List[WalletTest]] = {fs_name: [] for fs_name in funding_sources}
    for fs_name in funding_sources:
        funding_source = FundingSourceConfig(**funding_sources[fs_name])
        tests[fs_name] += WalletTest.tests_for_funding_source(
            fn_name, fs_name, fn, test, funding_source
        )
    return tests


def build_test_id(test: WalletTest):
    return f"{test.funding_source}.{test.function}({test.description})"


def load_funding_source(funding_source: FundingSourceConfig) -> BaseWallet:
    custom_settings = funding_source.settings
    original_settings = {}

    settings = getattr(wallets_module, "settings")

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
        await _assert_data(wallet, tested_func, call_params, _test_data.expect)
        # if len(_test_data.mocks) == 0:
        #     # all calls should fail after this method is called
        #     await wallet.cleanup()
        #     # same behaviour expected is server canot be reached
        #     # or if the connection was closed
        #     await _assert_data(wallet, tested_func, call_params, _test_data.expect)
    elif "expect_error" in test_data:
        await _assert_error(wallet, tested_func, call_params, _test_data.expect_error)
    else:
        assert False, "Expected outcome not specified"


async def _assert_data(wallet, tested_func, call_params, expect):
    resp = await getattr(wallet, tested_func)(**call_params)
    for key in expect:
        received = getattr(resp, key)
        expected = expect[key]
        assert (
            getattr(resp, key) == expect[key]
        ), f"""Field "{key}". Received: "{received}". Expected: "{expected}"."""


async def _assert_error(wallet, tested_func, call_params, expect_error):
    error_module = importlib.import_module(expect_error["module"])
    error_class = getattr(error_module, expect_error["class"])
    with pytest.raises(error_class) as e_info:
        await getattr(wallet, tested_func)(**call_params)

    assert e_info.match(expect_error["message"])


def _merge_dict_of_lists(v1: Dict[str, List], v2: Dict[str, List]):
    """Merge v2 into v1"""
    for k in v2:
        v1[k] = v2[k] if k not in v1 else v1[k] + v2[k]
