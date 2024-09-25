import pytest

from lnbits.db import (
    insert_query,
    update_query,
)
from tests.helpers import DbTestModel

test = DbTestModel(id=1, name="test", value="yes")


@pytest.mark.asyncio
async def test_helpers_insert_query():
    q = insert_query("test_helpers_query", test)
    assert (
        q == "INSERT INTO test_helpers_query (id, name, value) "
        "VALUES (:id, :name, :value)"
    )


@pytest.mark.asyncio
async def test_helpers_update_query():
    q = update_query("test_helpers_query", test)
    assert (
        q == "UPDATE test_helpers_query "
        "SET id = :id, name = :name, value = :value "
        "WHERE id = :id"
    )
