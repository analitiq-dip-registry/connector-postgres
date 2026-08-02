"""Unit tests for PostgresDialect write-path hooks and TLS configuration.

Covers the three entry points that have no other automated coverage:
- stage_table_sql     — DDL renderer for the staging relation
- merge_statement_sql — upsert renderer (ON CONFLICT)
- build_tls_connect_arg — libpq ssl_mode → asyncpg SSLContext mapping

Plus two contract pins that guard against silent regressions:
- max_identifier_length must equal sql_capabilities.limits.max_identifier_len (63)
- empty_table_sql must not use TRUNCATE (the CDK conformance kit rejects it)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cdk.sql.dialects import TableAddress


# ---------------------------------------------------------------------------
# stage_table_sql
# ---------------------------------------------------------------------------


class TestStageTableSql:
    def test_real_table(self, dialect, stage, target):
        sql = dialect.stage_table_sql(stage, target, temp=False)
        assert sql == (
            'CREATE TABLE "myschema"."_stage_orders" '
            '(LIKE "myschema"."orders" INCLUDING DEFAULTS)'
        )

    def test_temporary_table(self, dialect, stage, target):
        sql = dialect.stage_table_sql(stage, target, temp=True)
        assert sql == (
            'CREATE TEMPORARY TABLE "myschema"."_stage_orders" '
            '(LIKE "myschema"."orders" INCLUDING DEFAULTS)'
        )

    def test_identifiers_are_quoted(self, dialect):
        """Identifiers must go through quote_table, not interpolated raw."""
        tricky_stage = TableAddress(schema='my schema', table='stage; DROP TABLE x')
        tricky_target = TableAddress(schema='my schema', table='orders; DROP TABLE x')
        sql = dialect.stage_table_sql(tricky_stage, tricky_target, temp=False)
        assert '"my schema"."stage; DROP TABLE x"' in sql
        assert '"my schema"."orders; DROP TABLE x"' in sql
        assert "DROP TABLE x" in sql  # present but safely quoted
        # The outer structure is still the CREATE statement
        assert sql.startswith("CREATE TABLE")

    def test_create_keyword_is_conditional_on_temp(self, dialect, stage, target):
        real = dialect.stage_table_sql(stage, target, temp=False)
        tmp = dialect.stage_table_sql(stage, target, temp=True)
        assert "TEMPORARY" not in real
        assert "TEMPORARY" in tmp


# ---------------------------------------------------------------------------
# merge_statement_sql
# ---------------------------------------------------------------------------


class TestMergeStatementSql:
    def test_do_update_set(self, dialect, stage, target):
        columns = ["id", "name", "value"]
        conflict_keys = ["id"]
        sql = dialect.merge_statement_sql(stage, target, conflict_keys, columns)
        assert sql == (
            'INSERT INTO "myschema"."orders" ("id", "name", "value") '
            'SELECT "id", "name", "value" FROM "myschema"."_stage_orders" '
            'ON CONFLICT ("id") '
            'DO UPDATE SET "name" = EXCLUDED."name", "value" = EXCLUDED."value"'
        )

    def test_do_nothing_when_all_columns_are_conflict_keys(self, dialect, stage, target):
        # Every landed column is a conflict key → empty SET list would be a syntax
        # error; the dialect must degrade to DO NOTHING.
        columns = ["id", "name"]
        conflict_keys = ["id", "name"]
        sql = dialect.merge_statement_sql(stage, target, conflict_keys, columns)
        assert "DO NOTHING" in sql
        assert "DO UPDATE SET" not in sql

    def test_composite_conflict_keys(self, dialect, stage, target):
        columns = ["tenant_id", "order_id", "amount"]
        conflict_keys = ["tenant_id", "order_id"]
        sql = dialect.merge_statement_sql(stage, target, conflict_keys, columns)
        assert 'ON CONFLICT ("tenant_id", "order_id")' in sql
        assert '"amount" = EXCLUDED."amount"' in sql
        assert '"tenant_id" = EXCLUDED."tenant_id"' not in sql

    def test_column_order_preserved(self, dialect, stage, target):
        """Columns appear in the INSERT list in the order supplied."""
        columns = ["z_col", "a_col", "m_col"]
        conflict_keys = ["z_col"]
        sql = dialect.merge_statement_sql(stage, target, conflict_keys, columns)
        insert_list_start = sql.index("(") + 1
        insert_list_end = sql.index(")")
        insert_list = sql[insert_list_start:insert_list_end]
        assert insert_list == '"z_col", "a_col", "m_col"'

    def test_identifiers_are_quoted(self, dialect):
        """Column names must be quoted, not interpolated raw."""
        tricky_stage = TableAddress(schema="s", table="st")
        tricky_target = TableAddress(schema="s", table="t")
        sql = dialect.merge_statement_sql(
            tricky_stage,
            tricky_target,
            conflict_keys=["id"],
            columns=["id", "col; DROP TABLE x"],
        )
        assert '"col; DROP TABLE x"' in sql
        assert 'EXCLUDED."col; DROP TABLE x"' in sql


# ---------------------------------------------------------------------------
# build_tls_connect_arg
# ---------------------------------------------------------------------------

_FAKE_CA = "-----BEGIN CERTIFICATE-----\nMIIBxxx\n-----END CERTIFICATE-----\n"


class TestBuildTlsConnectArg:
    @pytest.mark.parametrize("mode", ["disable", "allow", "prefer"])
    def test_passthrough_modes_return_mode_string(self, dialect, mode):
        result = dialect.build_tls_connect_arg(mode, None)
        assert result == mode

    def test_require_without_ca_returns_mode_string(self, dialect):
        result = dialect.build_tls_connect_arg("require", None)
        assert result == "require"

    def test_require_with_ca_verifies(self, dialect):
        sentinel = MagicMock(name="ssl_ctx")
        with patch("connector.ca_ssl_context", return_value=sentinel) as mock_fn:
            result = dialect.build_tls_connect_arg("require", _FAKE_CA)
        mock_fn.assert_called_once_with(_FAKE_CA, check_hostname=False)
        assert result is sentinel

    def test_verify_ca_requires_ca(self, dialect):
        with pytest.raises(ValueError, match="ssl_ca_certificate"):
            dialect.build_tls_connect_arg("verify-ca", None)

    def test_verify_ca_with_ca_builds_context_without_hostname(self, dialect):
        sentinel = MagicMock(name="ssl_ctx")
        with patch("connector.ca_ssl_context", return_value=sentinel) as mock_fn:
            result = dialect.build_tls_connect_arg("verify-ca", _FAKE_CA)
        mock_fn.assert_called_once_with(_FAKE_CA, check_hostname=False)
        assert result is sentinel

    def test_verify_full_requires_ca(self, dialect):
        with pytest.raises(ValueError, match="ssl_ca_certificate"):
            dialect.build_tls_connect_arg("verify-full", None)

    def test_verify_full_with_ca_enables_hostname_check(self, dialect):
        sentinel = MagicMock(name="ssl_ctx")
        with patch("connector.ca_ssl_context", return_value=sentinel) as mock_fn:
            result = dialect.build_tls_connect_arg("verify-full", _FAKE_CA)
        mock_fn.assert_called_once_with(_FAKE_CA, check_hostname=True)
        assert result is sentinel

    @pytest.mark.parametrize("bad_mode", ["REQUIRE", "VERIFY-CA", "VERIFY-FULL", "Prefer", "ssl", ""])
    def test_unrecognized_mode_raises(self, dialect, bad_mode):
        # Security property: an unrecognized or case-variant mode must raise
        # rather than silently bypassing CA verification.
        with pytest.raises(ValueError):
            dialect.build_tls_connect_arg(bad_mode, None)


# ---------------------------------------------------------------------------
# Contract pins
# ---------------------------------------------------------------------------


class TestContractPins:
    def test_max_identifier_length_matches_sql_capabilities(self, dialect):
        # sql_capabilities.limits.max_identifier_len = 63 (definition/connector.json).
        # A silent divergence causes tier-1 conformance failures.
        assert dialect.max_identifier_length == 63

    def test_empty_table_sql_uses_delete_not_truncate(self, dialect, target):
        # The CDK conformance kit rejects TRUNCATE unconditionally; the base
        # implementation already renders DELETE FROM, which PostgreSQL accepts.
        sql = dialect.empty_table_sql(target)
        assert "TRUNCATE" not in sql.upper()
        assert "DELETE" in sql.upper()
