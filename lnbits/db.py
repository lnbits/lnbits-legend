from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar, Union

from loguru import logger
from pydantic import BaseModel, ValidationError, root_validator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql import text

from lnbits.settings import settings

POSTGRES = "POSTGRES"
COCKROACH = "COCKROACH"
SQLITE = "SQLITE"

if settings.lnbits_database_url:
    database_uri = settings.lnbits_database_url
    if database_uri.startswith("cockroachdb://"):
        DB_TYPE = COCKROACH
    else:
        if not database_uri.startswith("postgres://"):
            raise ValueError(
                "Please use the 'postgres://...' " "format for the database URL."
            )
        DB_TYPE = POSTGRES

else:
    if not os.path.isdir(settings.lnbits_data_folder):
        os.mkdir(settings.lnbits_data_folder)
        logger.info(f"Created {settings.lnbits_data_folder}")
    DB_TYPE = SQLITE


def compat_timestamp_placeholder(key: str):
    if DB_TYPE == POSTGRES:
        return f"to_timestamp(:{key})"
    elif DB_TYPE == COCKROACH:
        return f"cast(:{key} AS timestamp)"
    else:
        return f":{key}"


def get_placeholder(model: Any, field: str) -> str:
    type_ = model.__fields__[field].type_
    if type_ == datetime.datetime:
        return compat_timestamp_placeholder(field)
    else:
        return f":{field}"


class Compat:
    type: Optional[str] = "<inherited>"
    schema: Optional[str] = "<inherited>"

    def interval_seconds(self, seconds: int) -> str:
        if self.type in {POSTGRES, COCKROACH}:
            return f"interval '{seconds} seconds'"
        elif self.type == SQLITE:
            return f"{seconds}"
        return "<nothing>"

    def datetime_to_timestamp(self, date: datetime.datetime):
        if self.type in {POSTGRES, COCKROACH}:
            return date.strftime("%Y-%m-%d %H:%M:%S")
        elif self.type == SQLITE:
            return time.mktime(date.timetuple())
        return "<nothing>"

    @property
    def timestamp_now(self) -> str:
        if self.type in {POSTGRES, COCKROACH}:
            return "now()"
        elif self.type == SQLITE:
            return "(strftime('%s', 'now'))"
        return "<nothing>"

    @property
    def timestamp_column_default(self) -> str:
        if self.type in {POSTGRES, COCKROACH}:
            return self.timestamp_now
        return "NULL"

    @property
    def serial_primary_key(self) -> str:
        if self.type in {POSTGRES, COCKROACH}:
            return "SERIAL PRIMARY KEY"
        elif self.type == SQLITE:
            return "INTEGER PRIMARY KEY AUTOINCREMENT"
        return "<nothing>"

    @property
    def references_schema(self) -> str:
        if self.type in {POSTGRES, COCKROACH}:
            return f"{self.schema}."
        elif self.type == SQLITE:
            return ""
        return "<nothing>"

    @property
    def big_int(self) -> str:
        if self.type in {POSTGRES}:
            return "BIGINT"
        return "INT"

    def timestamp_placeholder(self, key: str) -> str:
        return compat_timestamp_placeholder(key)


class Connection(Compat):
    def __init__(self, conn: AsyncConnection, typ, name, schema):
        self.conn = conn
        self.type = typ
        self.name = name
        self.schema = schema

    def rewrite_query(self, query) -> str:
        if self.type in {POSTGRES, COCKROACH}:
            query = query.replace("%", "%%")
            query = query.replace("?", "%s")
        return query

    def rewrite_values(self, values: dict) -> dict:
        # strip html
        clean_regex = re.compile("<.*?>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});")
        clean_values: dict = {}
        for key, raw_value in values.items():
            if isinstance(raw_value, str):
                clean_values[key] = re.sub(clean_regex, "", raw_value)
            elif isinstance(raw_value, datetime.datetime):
                ts = raw_value.timestamp()
                if self.type == SQLITE:
                    clean_values[key] = int(ts)
                else:
                    clean_values[key] = ts
            else:
                clean_values[key] = raw_value
        return clean_values

    async def fetchall(
        self,
        query: str,
        values: Optional[dict] = None,
        model: Optional[type[TModel]] = None,
    ) -> list[TModel]:
        params = self.rewrite_values(values) if values else {}
        result = await self.conn.execute(text(self.rewrite_query(query)), params)
        row = result.mappings().all()
        result.close()
        if not row:
            return []
        if model:
            return [dict_to_model(r, model) for r in row]
        return row

    async def fetchone(
        self,
        query: str,
        values: Optional[dict] = None,
        model: Optional[type[TModel]] = None,
    ) -> TModel:
        params = self.rewrite_values(values) if values else {}
        result = await self.conn.execute(text(self.rewrite_query(query)), params)
        row = result.mappings().first()
        result.close()
        if model and row:
            return dict_to_model(row, model)
        return row

    async def update(
        self, table_name: str, model: BaseModel, where: str = "WHERE id = :id"
    ):
        await self.conn.execute(
            text(update_query(table_name, model, where)), model_to_dict(model)
        )
        await self.conn.commit()

    async def insert(self, table_name: str, model: BaseModel):
        await self.conn.execute(
            text(insert_query(table_name, model)), model_to_dict(model)
        )
        await self.conn.commit()

    async def fetch_page(
        self,
        query: str,
        where: Optional[list[str]] = None,
        values: Optional[dict] = None,
        filters: Optional[Filters] = None,
        model: Optional[type[TModel]] = None,
        group_by: Optional[list[str]] = None,
    ) -> Page[TModel]:
        if not filters:
            filters = Filters()
        clause = filters.where(where)
        parsed_values = filters.values(values)

        group_by_string = ""
        if group_by:
            for field in group_by:
                if not re.fullmatch(
                    r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?", field
                ):
                    raise ValueError("Value for GROUP BY is invalid")
            group_by_string = f"GROUP BY {', '.join(group_by)}"

        rows = await self.fetchall(
            f"""
            {query}
            {clause}
            {group_by_string}
            {filters.order_by()}
            {filters.pagination()}
            """,
            self.rewrite_values(parsed_values),
            model,
        )
        if rows:
            # no need for extra query if no pagination is specified
            if filters.offset or filters.limit:
                result = await self.execute(
                    f"""
                    SELECT COUNT(*) as count FROM (
                        {query}
                        {clause}
                        {group_by_string}
                    ) as count
                    """,
                    parsed_values,
                )
                row = result.mappings().first()
                result.close()
                count = int(row.get("count", 0))
            else:
                count = len(rows)
        else:
            count = 0

        return Page(
            data=rows,
            total=count,
        )

    async def execute(self, query: str, values: Optional[dict] = None):
        params = self.rewrite_values(values) if values else {}
        result = await self.conn.execute(text(self.rewrite_query(query)), params)
        await self.conn.commit()
        return result


class Database(Compat):
    def __init__(self, db_name: str):
        self.name = db_name
        self.schema = self.name
        self.type = DB_TYPE

        if DB_TYPE == SQLITE:
            self.path = os.path.join(
                settings.lnbits_data_folder, f"{self.name}.sqlite3"
            )
            database_uri = f"sqlite+aiosqlite:///{self.path}"
        else:
            database_uri = settings.lnbits_database_url.replace(
                "postgres://", "postgresql+asyncpg://"
            )

        if self.name.startswith("ext_"):
            self.schema = self.name[4:]
        else:
            self.schema = None

        self.engine: AsyncEngine = create_async_engine(
            database_uri, echo=settings.debug_database
        )

        if self.type in {POSTGRES, COCKROACH}:

            @event.listens_for(self.engine.sync_engine, "connect")
            def register_custom_types(dbapi_connection, *_):
                def _parse_date(value) -> datetime.datetime:
                    if value is None:
                        value = "1970-01-01 00:00:00"
                    f = "%Y-%m-%d %H:%M:%S.%f"
                    if "." not in value:
                        f = "%Y-%m-%d %H:%M:%S"
                    return datetime.datetime.strptime(value, f)

                dbapi_connection.run_async(
                    lambda connection: connection.set_type_codec(
                        "TIMESTAMP",
                        encoder=datetime.datetime,
                        decoder=_parse_date,
                        schema="pg_catalog",
                    )
                )

        self.lock = asyncio.Lock()

        logger.trace(f"database {self.type} added for {self.name}")

    @asynccontextmanager
    async def connect(self):
        await self.lock.acquire()
        try:
            async with self.engine.connect() as conn:
                if not conn:
                    raise Exception("Could not connect to the database")

                wconn = Connection(conn, self.type, self.name, self.schema)

                if self.schema:
                    if self.type in {POSTGRES, COCKROACH}:
                        await wconn.execute(
                            f"CREATE SCHEMA IF NOT EXISTS {self.schema}"
                        )
                    elif self.type == SQLITE:
                        await wconn.execute(f"ATTACH '{self.path}' AS {self.schema}")

                yield wconn
        finally:
            self.lock.release()

    async def fetchall(
        self,
        query: str,
        values: Optional[dict] = None,
        model: Optional[type[TModel]] = None,
    ) -> list[TModel]:
        async with self.connect() as conn:
            return await conn.fetchall(query, values, model)

    async def fetchone(
        self,
        query: str,
        values: Optional[dict] = None,
        model: Optional[type[TModel]] = None,
    ) -> TModel:
        async with self.connect() as conn:
            return await conn.fetchone(query, values, model)

    async def insert(self, table_name: str, model: BaseModel) -> None:
        async with self.connect() as conn:
            await conn.insert(table_name, model)

    async def update(
        self, table_name: str, model: BaseModel, where: str = "WHERE id = :id"
    ) -> None:
        async with self.connect() as conn:
            await conn.update(table_name, model, where)

    async def fetch_page(
        self,
        query: str,
        where: Optional[list[str]] = None,
        values: Optional[dict] = None,
        filters: Optional[Filters] = None,
        model: Optional[type[TModel]] = None,
        group_by: Optional[list[str]] = None,
    ) -> Page[TModel]:
        async with self.connect() as conn:
            return await conn.fetch_page(query, where, values, filters, model, group_by)

    async def execute(self, query: str, values: Optional[dict] = None):
        async with self.connect() as conn:
            return await conn.execute(query, values)

    @asynccontextmanager
    async def reuse_conn(self, conn: Connection):
        yield conn

    @classmethod
    async def clean_ext_db_files(cls, ext_id: str) -> bool:
        """
        If the extension DB is stored directly on the filesystem (like SQLite) then
        delete the files and return True. Otherwise do nothing and return False.
        """

        if DB_TYPE == SQLITE:
            db_file = os.path.join(settings.lnbits_data_folder, f"ext_{ext_id}.sqlite3")
            if os.path.isfile(db_file):
                os.remove(db_file)
            return True

        return False


class Operator(Enum):
    GT = "gt"
    LT = "lt"
    EQ = "eq"
    NE = "ne"
    GE = "ge"
    LE = "le"
    INCLUDE = "in"
    EXCLUDE = "ex"

    @property
    def as_sql(self):
        if self == Operator.EQ:
            return "="
        elif self == Operator.NE:
            return "!="
        elif self == Operator.INCLUDE:
            return "IN"
        elif self == Operator.EXCLUDE:
            return "NOT IN"
        elif self == Operator.GT:
            return ">"
        elif self == Operator.LT:
            return "<"
        elif self == Operator.GE:
            return ">="
        elif self == Operator.LE:
            return "<="
        else:
            raise ValueError("Unknown SQL Operator")


class FilterModel(BaseModel):
    __search_fields__: list[str] = []
    __sort_fields__: Optional[list[str]] = None


T = TypeVar("T")
TModel = TypeVar("TModel", bound=BaseModel)
TFilterModel = TypeVar("TFilterModel", bound=FilterModel)


class Page(BaseModel, Generic[T]):
    data: list[T]
    total: int


class Filter(BaseModel, Generic[TFilterModel]):
    field: str
    op: Operator = Operator.EQ
    model: Optional[type[TFilterModel]]
    values: Optional[dict] = None

    @classmethod
    def parse_query(
        cls, key: str, raw_values: list[Any], model: type[TFilterModel], i: int = 0
    ):
        # Key format:
        # key[operator]
        # e.g. name[eq]
        if key.endswith("]"):
            split = key[:-1].split("[")
            if len(split) != 2:
                raise ValueError("Invalid key")
            field = split[0]
            op = Operator(split[1])
        else:
            field = key
            op = Operator("eq")

        if field in model.__fields__:
            compare_field = model.__fields__[field]
            values: dict = {}
            for raw_value in raw_values:
                validated, errors = compare_field.validate(raw_value, {}, loc="none")
                if errors:
                    raise ValidationError(errors=[errors], model=model)
                values[f"{field}__{i}"] = validated
        else:
            raise ValueError("Unknown filter field")

        return cls(field=field, op=op, values=values, model=model)

    @property
    def statement(self):
        stmt = []
        for key in self.values.keys() if self.values else []:
            clean_key = key.split("__")[0]
            if (
                self.model
                and self.model.__fields__[clean_key].type_ == datetime.datetime
            ):
                placeholder = compat_timestamp_placeholder(key)
            else:
                placeholder = f":{key}"
            stmt.append(f"{clean_key} {self.op.as_sql} {placeholder}")
        return " OR ".join(stmt)


class Filters(BaseModel, Generic[TFilterModel]):
    """
    Generic helper class for filtering and sorting data.
    For usage in an api endpoint, use the `parse_filters` dependency.

    When constructing this class manually always make sure to pass a model so that
    the values can be validated. Otherwise, make sure to validate the inputs manually.
    """

    filters: list[Filter[TFilterModel]] = []
    search: Optional[str] = None

    offset: Optional[int] = None
    limit: Optional[int] = None

    sortby: Optional[str] = None
    direction: Optional[Literal["asc", "desc"]] = None

    model: Optional[type[TFilterModel]] = None

    @root_validator(pre=True)
    def validate_sortby(cls, values):
        sortby = values.get("sortby")
        model = values.get("model")
        if sortby and model:
            model = values["model"]
            # if no sort fields are specified explicitly all fields are allowed
            allowed = model.__sort_fields__ or model.__fields__
            if sortby not in allowed:
                raise ValueError("Invalid sort field")
        return values

    def pagination(self) -> str:
        stmt = ""
        if self.limit:
            stmt += f"LIMIT {self.limit} "
        if self.offset:
            stmt += f"OFFSET {self.offset}"
        return stmt

    def where(self, where_stmts: Optional[list[str]] = None) -> str:
        if not where_stmts:
            where_stmts = []
        if self.filters:
            for page_filter in self.filters:
                where_stmts.append(page_filter.statement)
        if self.search and self.model:
            fields = self.model.__search_fields__
            if DB_TYPE == POSTGRES:
                where_stmts.append(f"lower(concat({', '.join(fields)})) LIKE :search")
            elif DB_TYPE == SQLITE:
                where_stmts.append(f"lower({'||'.join(fields)}) LIKE :search")
        if where_stmts:
            return "WHERE " + " AND ".join(where_stmts)
        return ""

    def order_by(self) -> str:
        if self.sortby:
            return f"ORDER BY {self.sortby} {self.direction or 'asc'}"
        return ""

    def values(self, values: Optional[dict] = None) -> dict:
        if not values:
            values = {}
        if self.filters:
            for page_filter in self.filters:
                if page_filter.values:
                    for key, value in page_filter.values.items():
                        values[key] = value
        if self.search and self.model:
            values["search"] = f"%{self.search}%"
        return values


def insert_query(table_name: str, model: BaseModel) -> str:
    """
    Generate an insert query with placeholders for a given table and model
    :param table_name: Name of the table
    :param model: Pydantic model
    """
    placeholders = []
    keys = model_to_dict(model).keys()
    for field in keys:
        placeholders.append(get_placeholder(model, field))
    # add quotes to keys to avoid SQL conflicts (e.g. `user` is a reserved keyword)
    fields = ", ".join([f'"{key}"' for key in keys])
    values = ", ".join(placeholders)
    return f"INSERT INTO {table_name} ({fields}) VALUES ({values})"


def update_query(
    table_name: str, model: BaseModel, where: str = "WHERE id = :id"
) -> str:
    """
    Generate an update query with placeholders for a given table and model
    :param table_name: Name of the table
    :param model: Pydantic model
    :param where: Where string, default to `WHERE id = :id`
    """
    fields = []
    for field in model_to_dict(model).keys():
        placeholder = get_placeholder(model, field)
        # add quotes to keys to avoid SQL conflicts (e.g. `user` is a reserved keyword)
        fields.append(f'"{field}" = {placeholder}')
    query = ", ".join(fields)
    return f"UPDATE {table_name} SET {query} {where}"


def model_to_dict(model: BaseModel) -> dict:
    """
    Convert a Pydantic model to a dictionary with JSON-encoded nested models
    private fields starting with _ are ignored
    :param model: Pydantic model
    """
    _dict: dict = {}
    for key, value in model.dict().items():
        type_ = model.__fields__[key].type_
        if model.__fields__[key].field_info.extra.get("no_database", False):
            continue
        if isinstance(value, datetime.datetime):
            _dict[key] = value.timestamp()
            continue
        if type(type_) is type(BaseModel):
            _dict[key] = json.dumps(value)
            continue
        _dict[key] = value

    return _dict


def dict_to_submodel(model: type[TModel], value: Union[dict, str]) -> Optional[TModel]:
    """convert a dictionary or JSON string to a Pydantic model"""
    if isinstance(value, str):
        if value == "null":
            return None
        _subdict = json.loads(value)
    elif isinstance(value, dict):
        _subdict = value
    else:
        logger.warning(f"Expected str or dict, got {type(value)}")
        return None
    # recursively convert nested models
    return dict_to_model(_subdict, model)


def dict_to_model(_row: dict, model: type[TModel]) -> TModel:
    """
    Convert a dictionary with JSON-encoded nested models to a Pydantic model
    :param _dict: Dictionary from database
    :param model: Pydantic model
    """
    _dict: dict = {}
    for key, value in _row.items():
        if key not in model.__fields__:
            logger.warning(f"Converting {key} to model `{model}`.")
            continue
        type_ = model.__fields__[key].type_
        if issubclass(type_, bool):
            _dict[key] = bool(value)
            continue
        if issubclass(type_, BaseModel) and value:
            _dict[key] = dict_to_submodel(type_, value)
            continue
        if type_ is dict and value:
            _dict[key] = json.loads(value)
            continue
        _dict[key] = value
        continue
    _model = model.construct(**_dict)
    return _model
