import json

import pytest

from lnbits.db import (
    dict_to_model,
    insert_query,
    model_to_dict,
    update_query,
)
from tests.helpers import DbTestModel, DbTestModel2, DbTestModel3

test_data = DbTestModel3(
    id=1,
    user="userid",
    child=DbTestModel2(
        id=2,
        label="test",
        description="mydesc",
        child=DbTestModel(id=3, name="myname", value="myvalue"),
    ),
    active=True,
)


@pytest.mark.asyncio
async def test_helpers_insert_query():
    q = insert_query("test_helpers_query", test_data)
    assert q == (
        """INSERT INTO test_helpers_query ("id", "user", "child", "active") """
        "VALUES (:id, :user, :child, :active)"
    )


@pytest.mark.asyncio
async def test_helpers_update_query():
    q = update_query("test_helpers_query", test_data)
    assert q == (
        """UPDATE test_helpers_query SET "id" = :id, "user" = """
        """:user, "child" = :child, "active" = :active WHERE id = :id"""
    )


child_json = json.dumps(
    {
        "id": 2,
        "label": "test",
        "description": "mydesc",
        "child": {"id": 3, "name": "myname", "value": "myvalue"},
    }
)
test_dict = {"id": 1, "user": "userid", "child": child_json, "active": True}


@pytest.mark.asyncio
async def test_helpers_model_to_dict():
    d = model_to_dict(test_data)
    assert d.get("id") == test_data.id
    assert d.get("active") == test_data.active
    assert d.get("child") == child_json
    assert d.get("user") == test_data.user
    assert d == test_dict


@pytest.mark.asyncio
async def test_helpers_dict_to_model():
    m = dict_to_model(test_dict, DbTestModel3)
    assert m == test_data
    assert type(m) is DbTestModel3
    assert m.active is True
    assert type(m.child) is DbTestModel2
    assert type(m.child.child) is DbTestModel
