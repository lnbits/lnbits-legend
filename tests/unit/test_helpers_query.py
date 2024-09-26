import pytest

from lnbits.db import (
    dict_to_model,
    insert_query,
    model_to_dict,
    update_query,
)
from tests.helpers import DbTestModel2, DbTestModelInner

test_data = DbTestModel2(
    id=1,
    name="test",
    value="myvalue",
    child=DbTestModelInner(id=2, label="mylabel", description="mydesc"),
)


@pytest.mark.asyncio
async def test_helpers_insert_query():
    q = insert_query("test_helpers_query", test_data)
    assert (
        q == "INSERT INTO test_helpers_query (id, name, value, child) "
        "VALUES (:id, :name, :value, :child)"
    )


@pytest.mark.asyncio
async def test_helpers_update_query():
    q = update_query("test_helpers_query", test_data)
    assert (
        q == "UPDATE test_helpers_query "
        "SET id = :id, name = :name, value = :value, child = :child "
        "WHERE id = :id"
    )


@pytest.mark.asyncio
async def test_helpers_model_to_dict():
    d = model_to_dict(test_data)
    assert d == {
        "id": 1,
        "name": "test",
        "value": "myvalue",
        "child": {"id": 2, "label": "mylabel", "description": "mydesc"},
    }


@pytest.mark.asyncio
async def test_helpers_dict_to_model():
    m = dict_to_model(model_to_dict(test_data), DbTestModel2)
    assert m == test_data
