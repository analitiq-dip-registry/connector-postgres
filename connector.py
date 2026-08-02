"""PostgreSQL connector package for Analitiq.

``PostgresConnector`` is the class resolved via the ``postgres`` entry
points (``analitiq.source_connectors`` /
``analitiq.destination_connectors``); ``PostgresDialect`` is attached
through its ``dialect_class``, not entry-point-resolved.

Transports: ADBC (libpq, COPY-based Arrow ingestion - the default) and
async SQLAlchemy (asyncpg - the engine-side SQL/metadata path). The
write-direction type vocabulary lives entirely in
``definition/type-map-write.json``; this module ships no Python
type-rendering table and carries only the structural dialect hooks the
two transports require.

The write path is the CDK's uniform stage-then-apply cycle, expressed
through the sanctioned ``SqlDialect`` hooks: ``stage_table_sql`` renders
the stage DDL and ``merge_statement_sql`` renders the upsert. Emptying
(``empty_table_sql``) and native landing (``bulk_land``) are deliberately
left on the base - the ANSI ``DELETE FROM`` is already correct for
PostgreSQL, and ``bulk_load.adbc: adbc_ingest`` is the ADBC backend's own
landing rather than a dialect-implemented mechanism.
"""

from __future__ import annotations

from collections.abc import Sequence

from cdk.sql.dialects import SqlDialect, TableAddress
from cdk.sql.generic import GenericSQLConnector
from cdk.transport_factory import ca_ssl_context

# The connector's declared ssl_mode enum (connection_contract.inputs),
# split by how the dialect has to realize each mode for asyncpg.
_PASSTHROUGH_MODES = ("disable", "allow", "prefer", "require")
_VERIFY_MODES = ("verify-ca", "verify-full")


class PostgresDialect(SqlDialect):
    """PostgreSQL dialect: libpq TLS vocabulary, stage DDL, ON CONFLICT upsert."""

    name = "postgres"

    system_schemas = ("information_schema", "pg_catalog", "pg_toast")

    def schemas_query(self, catalog: str = "") -> tuple[str, list[object]]:
        """List user schemas as a ``(sql, params)`` query tuple.

        A PostgreSQL connection can only see its own database, so a
        non-empty ``catalog`` is rejected up front via ``_check_catalog``
        (``CatalogAddressingError`` - this dialect does not declare
        ``supports_catalog_addressing``, and the definition declares
        ``sql_capabilities.catalog: "none"`` to match) rather than
        filtered into a silently empty result.

        ``system_schemas`` is exact-match only, so the numbered
        per-session schemas (``pg_temp_N`` / ``pg_toast_temp_N``) cannot
        be enumerated in the NOT IN list - they are excluded with NOT
        LIKE filters.
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

        Emitting CREATE SCHEMA IF NOT EXISTS "public" would require
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

        asyncpg takes TLS through a single connect parameter, so the
        singular hook is the right one (the CDK lands the return value
        under ``connect_args["ssl"]``). This hook serves the ``sqlalchemy``
        transport only; the ADBC transport does not come through here and
        carries no TLS options of its own. The libpq ADBC driver rejects
        every ``adbc.postgresql.*`` database option with ``NOT_IMPLEMENTED``
        before any network I/O, and libpq's ``sslrootcert`` expects a
        filesystem path a stored PEM secret cannot supply, so under ADBC
        ``verify-ca`` / ``verify-full`` fall back to libpq's default CA
        lookup. CA pinning requires the ``sqlalchemy`` transport.

        Mode handling:

        * ``verify-ca`` / ``verify-full`` require a CA bundle and build an
          ``SSLContext`` pinned to it; hostname checking is enabled only
          for ``verify-full``.
        * ``require`` **with** a CA bundle also verifies, mirroring
          libpq's documented semantics: "only try an SSL connection. If a
          root CA file is present, verify the certificate in the same way
          as if verify-ca was specified." Without a bundle it passes
          through as an encryption-only requirement.
        * ``disable`` / ``allow`` / ``prefer`` pass through as libpq mode
          strings.

        Anything else raises: the engine performs no vocabulary
        validation before the dialect sees the value (the connector.json
        enum is control-plane only), so an unrecognized or case-variant
        mode must fail here rather than silently bypass the
        CA-verification path.
        """
        if mode in _VERIFY_MODES:
            if not ca_pem:
                raise ValueError(
                    f"ssl_mode '{mode}' requires the ssl_ca_certificate "
                    "connection input to be provided"
                )
            return ca_ssl_context(ca_pem, check_hostname=(mode == "verify-full"))
        if mode == "require" and ca_pem:
            return ca_ssl_context(ca_pem, check_hostname=False)
        if mode in _PASSTHROUGH_MODES:
            return mode
        raise ValueError(
            f"Unsupported ssl_mode '{mode}'; expected one of "
            f"{', '.join(_PASSTHROUGH_MODES + _VERIFY_MODES)}"
        )

    def stage_table_sql(
        self, stage: TableAddress, target: TableAddress, *, temp: bool
    ) -> str:
        """``CREATE [TEMPORARY] TABLE`` *stage* shaped like *target*.

        PostgreSQL clones a table's shape with ``(LIKE target INCLUDING
        DEFAULTS)``, which reproduces column names, types and order plus
        the DEFAULT expressions. Constraints and indexes are deliberately
        not copied: a stage needs neither, and copying a UNIQUE constraint
        would make the landing INSERT reject rows the merge is supposed to
        resolve.

        The connector declares ``stage.scope: real`` and ``stage.schema:
        target``, so ``temp`` arrives ``False`` and the stage is an
        ordinary relation in the target's own schema - the ``temp`` branch
        is kept genuinely conditional rather than hard-coded, so the
        rendered DDL always matches the declared scope. PostgreSQL DDL is
        transactional (``transactional_ddl: true``), so the create and the
        matching drop ride inside the write transaction and a dead cycle
        leaves nothing behind.
        """
        create = "CREATE TEMPORARY TABLE" if temp else "CREATE TABLE"
        return (
            f"{create} {self.quote_table(stage)} "
            f"(LIKE {self.quote_table(target)} INCLUDING DEFAULTS)"
        )

    def merge_statement_sql(
        self,
        stage: TableAddress,
        target: TableAddress,
        conflict_keys: Sequence[str],
        columns: Sequence[str],
    ) -> str:
        """Render the upsert from *stage* to *target*.

        ``INSERT ... ON CONFLICT (keys) DO UPDATE SET col = EXCLUDED.col``,
        matching the declared ``merge_form: insert_on_conflict``. This form
        is portable to every supported server; SQL-standard ``MERGE`` only
        exists from PostgreSQL 15 and is deliberately not used.

        The source is the stage table, referenced once, so no batch value
        is ever rendered into SQL text - values reach the stage as bound
        parameters or through the ADBC ingest path. Updated columns are the
        landed columns minus the conflict keys: a target column the batch
        did not land (a physical column added out-of-band, the
        engine-managed ``_synced_at``) keeps its stored value on a matched
        row and takes its DEFAULT on an inserted one.

        When every landed column is a conflict key there is nothing to
        update, and ``DO UPDATE SET`` with an empty list is a syntax error,
        so the contract's insert-only degradation renders ``DO NOTHING``.

        Known precondition, inherited from the grammar: PostgreSQL resolves
        an ON CONFLICT target only against a real UNIQUE / PRIMARY KEY
        constraint or index, so an upsert into a target without one fails
        loudly rather than silently appending duplicates.
        """
        column_list = ", ".join(self.quote_ident(c) for c in columns)
        conflict_list = ", ".join(self.quote_ident(k) for k in conflict_keys)
        keys = set(conflict_keys)
        update_columns = [c for c in columns if c not in keys]
        if update_columns:
            set_clause = ", ".join(
                f"{self.quote_ident(c)} = EXCLUDED.{self.quote_ident(c)}"
                for c in update_columns
            )
            action = f"DO UPDATE SET {set_clause}"
        else:
            action = "DO NOTHING"
        # Dialect-quoted identifiers only; batch values never enter this
        # text (they reach the stage as bound parameters / ADBC ingest).
        return (
            f"INSERT INTO {self.quote_table(target)} ({column_list}) "  # nosec B608
            f"SELECT {column_list} FROM {self.quote_table(stage)} "
            f"ON CONFLICT ({conflict_list}) {action}"
        )


class PostgresConnector(GenericSQLConnector):
    """PostgreSQL connector: generic SQL behavior over the Postgres dialect."""

    dialect_class = PostgresDialect
