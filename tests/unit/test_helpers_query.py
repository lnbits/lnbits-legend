import json

import pytest

from lnbits.db import (
    dict_to_model,
    insert_query,
    model_to_dict,
    update_query,
)
from tests.helpers import DbTestModel, DbTestModel2

test_data = DbTestModel2(
    id=1,
    label="test",
    description="mydesc",
    child=DbTestModel(id=2, name="myname", value="myvalue"),
)


@pytest.mark.asyncio
async def test_helpers_insert_query():
    q = insert_query("test_helpers_query", test_data)
    assert (
        q == "INSERT INTO test_helpers_query (id, label, description, child) "
        "VALUES (:id, :label, :description, :child)"
    )


@pytest.mark.asyncio
async def test_helpers_update_query():
    q = update_query("test_helpers_query", test_data)
    assert (
        q == "UPDATE test_helpers_query "
        "SET id = :id, label = :label, description = :description, child = :child "
        "WHERE id = :id"
    )


child_dict = json.dumps({"id": 2, "name": "myname", "value": "myvalue"})
test_dict = {"id": 1, "label": "test", "description": "mydesc", "child": child_dict}


@pytest.mark.asyncio
async def test_helpers_model_to_dict():
    d = model_to_dict(test_data)
    assert d == test_dict


@pytest.mark.asyncio
async def test_helpers_dict_to_model():
    m = dict_to_model(test_dict, DbTestModel2)
    assert m == test_data
    assert type(m) is DbTestModel2
    assert type(m.child) is DbTestModel
