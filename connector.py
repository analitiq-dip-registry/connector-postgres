"""PostgreSQL connector — dialect + connector class for the Analitiq CDK.

Everything PostgreSQL-specific lives here, in the connector package:
identifier/schema conventions, the ``ON CONFLICT`` upsert statement, the
``CREATE SCHEMA`` pre-DDL, the ADBC DDL type renderer, and the stage-table
syntax for the ADBC MERGE upsert. The CDK base (`GenericSQLConnector` /
`SqlDialect`) is vendor-neutral and never branches on this system.

Registered under connector_id ``postgres`` via the package entry points
(``analitiq.source_connectors`` / ``analitiq.destination_connectors``).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pyarrow as pa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cdk.sql.dialects import Query, SqlDialect
from cdk.sql.generic import GenericSQLConnector
from cdk.type_map import TypeMapper, parse_arrow_type


def arrow_to_postgres_native(dtype: pa.DataType) -> str:
    """Return a PostgreSQL DDL type string for an Arrow ``DataType``.

    Used by the ADBC-only destination path when the connection rides the
    libpq-compatible ADBC driver. On the SQLAlchemy transport, DDL renders
    generically through the dialect compiler and this renderer never fires.
    """
    if pa.types.is_boolean(dtype):
        return "BOOLEAN"
    if pa.types.is_int8(dtype) or pa.types.is_int16(dtype):
        return "SMALLINT"
    if pa.types.is_int32(dtype) or pa.types.is_uint16(dtype):
        return "INTEGER"
    if (
        pa.types.is_int64(dtype)
        or pa.types.is_uint32(dtype)
        or pa.types.is_uint64(dtype)
    ):
        return "BIGINT"
    if pa.types.is_uint8(dtype):
        return "SMALLINT"
    if pa.types.is_floating(dtype):
        return "DOUBLE PRECISION"
    if pa.types.is_decimal(dtype):
        return f"NUMERIC({dtype.precision}, {dtype.scale})"
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return "TEXT"
    if (
        pa.types.is_binary(dtype)
        or pa.types.is_large_binary(dtype)
        or pa.types.is_fixed_size_binary(dtype)
    ):
        return "BYTEA"
    if pa.types.is_date(dtype):
        return "DATE"
    if pa.types.is_time(dtype):
        return "TIME"
    if pa.types.is_timestamp(dtype):
        return "TIMESTAMP WITH TIME ZONE" if dtype.tz is not None else "TIMESTAMP"
    if (
        pa.types.is_struct(dtype)
        or pa.types.is_list(dtype)
        or pa.types.is_large_list(dtype)
    ):
        return "JSONB"
    raise ValueError(f"Arrow type {dtype!s} has no PostgreSQL DDL mapping")


class PostgresDialect(SqlDialect):
    """PostgreSQL SQL strategy: ANSI quoting, ``public`` default schema,
    ``ON CONFLICT`` upsert, libpq-flavored ADBC DDL."""

    name = "postgresql"
    system_schemas = ("information_schema", "pg_catalog", "pg_toast")
    supports_upsert_sqlalchemy = True
    supports_upsert_adbc = True

    # ---- discovery ---------------------------------------------------------
    def schemas_query(self) -> Query:
        # Exclude the catalog schemas plus the per-session temp schemas
        # (``pg_temp_N`` / ``pg_toast_temp_N``) that NOT IN cannot enumerate.
        placeholders = ", ".join("?" for _ in self.system_schemas)
        sql = (
            "SELECT schema_name FROM information_schema.schemata "
            f"WHERE schema_name NOT IN ({placeholders}) "
            "AND schema_name NOT LIKE 'pg_temp_%' "
            "AND schema_name NOT LIKE 'pg_toast_temp_%' "
            "ORDER BY schema_name"
        )
        return sql, list(self.system_schemas)

    # ---- SQLAlchemy write path ---------------------------------------------
    def build_sqlalchemy_upsert(
        self,
        table: Any,
        records: List[Dict[str, Any]],
        conflict_keys: List[str],
    ) -> Any:
        stmt = pg_insert(table).values(records)
        record_columns = set(records[0].keys())
        update_cols = {
            c.name: c
            for c in stmt.excluded
            if c.name not in conflict_keys and c.name in record_columns
        }
        return stmt.on_conflict_do_update(
            index_elements=conflict_keys,
            set_=update_cols,
        )

    def sqlalchemy_pre_ddl(self, schema_name: str) -> List[str]:
        # ``public`` always exists; any other schema must be created before
        # ``MetaData.create_all`` references it.
        if schema_name and schema_name != "public":
            return [f"CREATE SCHEMA IF NOT EXISTS {schema_name}"]
        return []

    # ---- schema semantics ----------------------------------------------------
    def schema_is_implicit_default(self, schema_name: str) -> bool:
        return not schema_name or schema_name.lower() == "public"

    # ---- ADBC-only write path -------------------------------------------------
    def adbc_column_type(self, native_type: str, type_mapper: TypeMapper) -> str:
        arrow_type = type_mapper.to_arrow_type(native_type)
        if arrow_type == "Json":
            return "JSONB"
        return arrow_to_postgres_native(parse_arrow_type(arrow_type))

    def adbc_synced_at_type(self) -> str:
        return "TIMESTAMP WITH TIME ZONE"

    def adbc_binary_type(self) -> str:
        return "BYTEA"

    def adbc_commit_timestamp_type(self) -> str:
        return "TIMESTAMP"

    def adbc_text_type(self) -> str:
        return "VARCHAR(255)"

    def adbc_stage_table_sql(
        self, stage_qualified: str, target_qualified: str
    ) -> str:
        return (
            f"CREATE TABLE {stage_qualified} "
            f"(LIKE {target_qualified} INCLUDING DEFAULTS)"
        )


class PostgresConnector(GenericSQLConnector):
    """PostgreSQL connector: the CDK SQL base wired to the postgres dialect."""

    dialect_class = PostgresDialect
