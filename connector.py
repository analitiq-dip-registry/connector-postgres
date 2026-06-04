"""PostgreSQL connector — dialect + connector class for the Analitiq CDK.

Everything PostgreSQL-specific lives here, in the connector package:
identifier/schema conventions, the ``ON CONFLICT`` upsert statement, the
``CREATE SCHEMA`` pre-DDL, and the stage-table
syntax for the ADBC MERGE upsert. The CDK base (`GenericSQLConnector` /
`SqlDialect`) is vendor-neutral and never branches on this system.

Registered under connector_id ``postgres`` via the package entry points
(``analitiq.source_connectors`` / ``analitiq.destination_connectors``).
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.dialects.postgresql import insert as pg_insert

from cdk.sql.dialects import Query, SqlDialect
from cdk.transport_factory import ca_ssl_context
from cdk.sql.generic import GenericSQLConnector


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

    def build_tls_connect_arg(self, mode: str, ca_pem: str | None) -> Any:
        """libpq-native SSL modes for asyncpg / libpq-compatible drivers.

        ``disable``/``allow``/``prefer``/``require`` pass through as
        strings; ``verify-ca``/``verify-full`` need an explicit SSLContext
        built from the connection's CA bundle.
        """
        if mode in ("disable", "allow", "prefer", "require"):
            return mode
        if mode == "verify-ca":
            if not ca_pem:
                raise ValueError(
                    "tls.mode='verify-ca' requires tls.ca_certificate to "
                    "resolve to a PEM certificate bundle"
                )
            return ca_ssl_context(ca_pem, check_hostname=False)
        if mode == "verify-full":
            if not ca_pem:
                raise ValueError(
                    "tls.mode='verify-full' requires tls.ca_certificate to "
                    "resolve to a PEM certificate bundle"
                )
            return ca_ssl_context(ca_pem, check_hostname=True)
        raise ValueError(
            f"{self.name} tls.mode {mode!r} not recognized; expected one of: "
            "disable, allow, prefer, require, verify-ca, verify-full"
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
