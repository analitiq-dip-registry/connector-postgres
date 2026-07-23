"""PostgreSQL connector package for Analitiq.

``PostgresConnector`` is the class resolved via the ``postgres`` entry
points (``analitiq.source_connectors`` /
``analitiq.destination_connectors``); ``PostgresDialect`` is attached
through its ``dialect_class``, not entry-point-resolved. Transports: ADBC
(libpq, COPY-based Arrow ingestion — the default) and async SQLAlchemy
(asyncpg). The write-direction type vocabulary lives entirely in
``definition/type-map-write.json``; this module carries only the
structural dialect hooks the transports require.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from cdk.sql.dialects import SqlDialect
from cdk.sql.generic import GenericSQLConnector
from cdk.transport_factory import ca_ssl_context

_PASSTHROUGH_MODES = ("disable", "allow", "prefer", "require")
_VERIFY_MODES = ("verify-ca", "verify-full")


class PostgresDialect(SqlDialect):
    """PostgreSQL dialect: libpq TLS vocabulary, ON CONFLICT upsert, ADBC staging."""

    name = "postgres"

    system_schemas = ("information_schema", "pg_catalog", "pg_toast")

    supports_upsert_sqlalchemy = True
    supports_upsert_adbc = True

    def schemas_query(self, catalog: str = "") -> tuple[str, list[object]]:
        """List user schemas as a ``(sql, params)`` query tuple.

        A PostgreSQL connection can only see its own database, so a
        non-empty ``catalog`` is rejected up front via ``_check_catalog``
        (``CatalogAddressingError`` — this dialect does not declare
        ``supports_catalog_addressing``) rather than filtered into a
        silently empty result.

        ``system_schemas`` is exact-match only, so the numbered per-session
        schemas (``pg_temp_N`` / ``pg_toast_temp_N``) cannot be enumerated
        in the NOT IN list — they are excluded with NOT LIKE filters.
        """
        self._check_catalog(catalog)
        placeholders = ", ".join("?" for _ in self.system_schemas)
        sql = (
            "SELECT schema_name FROM information_schema.schemata "
            f"WHERE schema_name NOT IN ({placeholders}) "
            "AND schema_name NOT LIKE 'pg_temp_%' "
            "AND schema_name NOT LIKE 'pg_toast_temp_%' "
            "ORDER BY schema_name"
        )
        return sql, list(self.system_schemas)

    def schema_is_implicit_default(self, schema_name: str) -> bool:
        """``public`` needs no CREATE SCHEMA: it exists in every database.

        Emitting ``CREATE SCHEMA IF NOT EXISTS \"public\"`` would require
        database-level CREATE privilege (PostgreSQL checks the ACL before
        the IF NOT EXISTS short-circuit), breaking least-privilege writers
        whose rights live inside ``public`` only.
        """
        return not schema_name or schema_name.lower() == "public"

    def sqlalchemy_pre_ddl(self, schema_name: str) -> list[str]:
        if self.schema_is_implicit_default(schema_name):
            return []
        return [f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"']

    def build_tls_connect_arg(self, mode: str, ca_pem: str | None) -> object:
        """Interpret the connector's libpq ``ssl_mode`` vocabulary for asyncpg.

        ``disable`` / ``allow`` / ``prefer`` / ``require`` pass through as
        libpq mode strings. The verification modes require a CA bundle and
        build an ``SSLContext`` pinned to it; hostname checking is enabled
        only for ``verify-full``. Any other mode raises: the engine performs
        no vocabulary validation before the dialect sees the value (the
        connector.json enum is control-plane only), so an unrecognized or
        case-variant mode must fail here rather than silently bypass the
        CA-verification path.
        """
        if mode in _VERIFY_MODES:
            if not ca_pem:
                raise ValueError(
                    f"ssl_mode '{mode}' requires the ssl_ca_certificate "
                    "connection input to be provided"
                )
            return ca_ssl_context(ca_pem, check_hostname=(mode == "verify-full"))
        if mode in _PASSTHROUGH_MODES:
            return mode
        raise ValueError(
            f"Unsupported ssl_mode '{mode}'; expected one of "
            f"{', '.join(_PASSTHROUGH_MODES + _VERIFY_MODES)}"
        )

    def build_sqlalchemy_upsert(self, table, records, conflict_keys):
        """PostgreSQL upsert: ``INSERT ... ON CONFLICT (...) DO UPDATE``.

        The SET clause is built from the record keys (the endpoint
        contract's columns), not the reflected table's columns: physical
        columns added out-of-band must never be clobbered with their
        DEFAULT/NULL on conflict. The accepted trade-off is that
        engine-managed columns absent from the records (``_synced_at``)
        keep their insert-time value on conflicting rows. Falls back to
        ``DO NOTHING`` when every record column is a conflict key.
        """
        statement = pg_insert(table).values(records)
        conflict = list(conflict_keys)
        record_columns = list(records[0].keys()) if records else []
        update_columns = {
            name: statement.excluded[name]
            for name in record_columns
            if name not in conflict
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
