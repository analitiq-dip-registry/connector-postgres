"""Shared fixtures for the postgres connector test suite."""

import pytest

from cdk.sql.dialects import TableAddress

from connector import PostgresDialect


@pytest.fixture()
def dialect() -> PostgresDialect:
    return PostgresDialect()


@pytest.fixture()
def stage() -> TableAddress:
    return TableAddress(schema="myschema", table="_stage_orders")


@pytest.fixture()
def target() -> TableAddress:
    return TableAddress(schema="myschema", table="orders")
