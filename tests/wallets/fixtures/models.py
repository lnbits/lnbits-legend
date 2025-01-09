from typing import Optional, Union

from pydantic import BaseModel


class FundingSourceConfig(BaseModel):
    name: str
    skip: Optional[bool]
    wallet_class: str
    settings: dict
    mock_settings: Optional[dict]


class FunctionMock(BaseModel):
    uri: Optional[str]
    query_params: Optional[dict]
    headers: Optional[dict]
    method: Optional[str]


class TestMock(BaseModel):
    skip: Optional[bool]
    description: Optional[str]
    request_type: Optional[str]
    request_body: Optional[dict]
    response_type: str
    response: Union[str, dict, list]


class Mock(FunctionMock, TestMock):
    name: str

    @staticmethod
    def combine_mocks(mock_name, fs_mock, test_mock):
        _mock = fs_mock | test_mock
        if "response" in _mock and "response" in fs_mock:
            _mock["response"] |= fs_mock["response"]
        m = Mock(name=mock_name, **_mock)

        return m


class FunctionMocks(BaseModel):
    mocks: dict[str, FunctionMock]


class FunctionTest(BaseModel):
    description: str
    call_params: dict
    expect: dict
    mocks: dict[str, list[dict[str, TestMock]]]


class FunctionData(BaseModel):
    """Data required for testing this function"""

    "Function level mocks that apply for all tests of this function"
    mocks: list[FunctionMock] = []

    "All the tests for this function"
    tests: list[FunctionTest] = []


class WalletTest(BaseModel):
    skip: Optional[bool]
    function: str
    description: str
    funding_source: FundingSourceConfig
    call_params: Optional[dict] = {}
    expect: Optional[dict]
    expect_error: Optional[dict]
    mocks: list[Mock] = []

    @staticmethod
    def tests_for_funding_source(
        fs: FundingSourceConfig,
        fn_name: str,
        fn,
        test,
    ) -> list["WalletTest"]:
        t = WalletTest(
            **{
                "funding_source": fs,
                "function": fn_name,
                **test,
                "mocks": [],
                "skip": fs.skip,
            }
        )
        if "mocks" in test:
            if fs.name not in test["mocks"]:
                t.skip = True
                return [t]

            return t._tests_from_fs_mocks(fn, test, fs.name)

        return [t]

    def _tests_from_fs_mocks(self, fn, test, fs_name: str) -> list["WalletTest"]:

        fs_mocks = fn["mocks"][fs_name]
        test_mocks = test["mocks"][fs_name]

        all_test_mocks = [test_mocks] if isinstance(test_mocks, dict) else test_mocks

        mocks = []
        for tm in all_test_mocks:
            mocks += self._build_mock_objects(list(fs_mocks), fs_mocks, tm)

        return [self._tests_from_mock(m) for m in mocks]

    def _build_mock_objects(self, mock_names, fs_mocks, test_mocks):
        mocks = []

        for mock_name in mock_names:
            if mock_name not in test_mocks:
                continue
            for test_mock in test_mocks[mock_name]:
                mock = {"fs_mock": fs_mocks[mock_name], "test_mock": test_mock}

                if len(mock_names) == 1:
                    mocks.append({mock_name: mock})
                else:
                    sub_mocks = self._build_mock_objects(
                        mock_names[1:], fs_mocks, test_mocks
                    )
                    for sub_mock in sub_mocks:
                        mocks.append({mock_name: mock} | sub_mock)
            return mocks

        return mocks

    def _tests_from_mock(self, mock_obj) -> "WalletTest":

        test_mocks: list[Mock] = [
            Mock.combine_mocks(
                mock_name,
                mock_obj[mock_name]["fs_mock"],
                mock_obj[mock_name]["test_mock"],
            )
            for mock_name in mock_obj
        ]

        any_mock_skipped = len([m for m in test_mocks if m.skip])
        extra_description = ";".join(
            [m.description for m in test_mocks if m.description]
        )

        return WalletTest(
            **(
                self.dict()
                | {
                    "description": f"{self.description}:{extra_description}",
                    "mocks": test_mocks,
                    "skip": self.skip or any_mock_skipped,
                }
            )
        )


class DataObject:
    def __init__(self, **kwargs):
        for k in kwargs:
            setattr(self, k, kwargs[k])

    def __str__(self):
        data = []
        for k in self.__dict__:
            value = getattr(self, k)
            if isinstance(value, list):
                value = [f"{k}={v}" for v in value]
            data.append(f"{k}={value}")
        return ";".join(data)
