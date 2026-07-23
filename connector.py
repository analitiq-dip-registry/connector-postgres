"""PostgreSQL connector package for Analitiq.

Provides the PostgreSQL dialect and connector classes resolved via the
``postgres`` entry points. Transports: ADBC (libpq, COPY-based Arrow
ingestion — the default) and async SQLAlchemy (asyncpg). The
write-direction type vocabulary lives entirely in
``definition/type-map-write.json``; this module carries only the
structural dialect hooks the transports require.
"""

from __future__ import annotations

import ssl

from sqlalchemy.dialects.postgresql import insert as pg_insert

from cdk.sql.dialects import SqlDialect
from cdk.sql.generic import GenericSQLConnector
from cdk.transport_factory import ca_ssl_context

_VERIFY_MODES = ("verify-ca", "verify-full")


class PostgresDialect(SqlDialect):
    """PostgreSQL dialect: libpq TLS vocabulary, ON CONFLICT upsert, ADBC staging."""

    name = "postgres"
    is_async = True

    system_schemas = ("information_schema", "pg_catalog", "pg_toast")

    supports_upsert_sqlalchemy = True
    supports_upsert_adbc = True

    def schemas_query(self) -> str:
        excluded = ", ".join(f"'{schema}'" for schema in self.system_schemas)
        return (
            "SELECT schema_name FROM information_schema.schemata "
            f"WHERE schema_name NOT IN ({excluded})"
        )

    def sqlalchemy_pre_ddl(self, schema_name: str) -> str:
        return f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'

    def build_tls_connect_arg(self, mode: str, ca_pem: str | None) -> object:
        """Interpret the connector's libpq ``ssl_mode`` vocabulary for asyncpg.

        ``disable`` / ``allow`` / ``prefer`` / ``require`` pass through as
        libpq mode strings. The verification modes require a CA bundle and
        build an ``SSLContext`` pinned to it: ``verify-ca`` disables
        hostname checking, ``verify-full`` keeps it.
        """
        if mode in _VERIFY_MODES:
            if not ca_pem:
                raise ValueError(
                    f"ssl_mode '{mode}' requires the ssl_ca_certificate "
                    "connection input to be provided"
                )
            context: ssl.SSLContext = ca_ssl_context(ca_pem)
            if mode == "verify-ca":
                context.check_hostname = False
            return context
        return mode

    def build_sqlalchemy_upsert(self, table, records, conflict_keys):
        """PostgreSQL upsert: INSERT ... ON CONFLICT (...) DO UPDATE."""
        statement = pg_insert(table).values(records)
        conflict = list(conflict_keys)
        update_columns = {
            column.name: statement.excluded[column.name]
            for column in table.columns
            if column.name not in conflict
        }
        if not update_columns:
            return statement.on_conflict_do_nothing(index_elements=conflict)
        return statement.on_conflict_do_update(
            index_elements=conflict, set_=update_columns
        )

    def adbc_stage_table_sql(
        self, stage_qualified: str, target_qualified: str
    ) -> str:
        """Stage table for ADBC upsert: clone the target's shape and defaults."""
        return (
            f"CREATE TABLE {stage_qualified} "
            f"(LIKE {target_qualified} INCLUDING DEFAULTS)"
        )


class PostgresConnector(GenericSQLConnector):
    """PostgreSQL connector: generic SQL behavior over the Postgres dialect."""

    dialect_class = PostgresDialect
