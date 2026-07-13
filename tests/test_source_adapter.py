"""Tests for the source adapter + passthrough (§2.2, §2.5)."""
from contextlib import contextmanager

import pytest

from src.adapters import (
    PostgresSourceAdapter, PassthroughAdapter, same_engine,
    get_source_adapter,
)


# --- fake PostgresCentralConnector for hermetic discovery ---
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        pass

    def fetchall(self):
        return self._rows


class _DBConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, cursor_factory=None, name=None):
        return _Cur(self._rows)


class _FakeConnector:
    def __init__(self, rows):
        self._rows = rows

    @contextmanager
    def connection(self):
        yield _DBConn(self._rows)

    def close(self):
        pass


def test_postgres_source_discovers_schema():
    rows = [
        {"table_name": "authors", "column_name": "id", "data_type": "uuid",
         "is_nullable": "NO", "ordinal_position": 1},
        {"table_name": "authors", "column_name": "author_name", "data_type": "varchar",
         "is_nullable": "NO", "ordinal_position": 2},
    ]
    ad = PostgresSourceAdapter(connector=_FakeConnector(rows), schema="irimsv")
    schema = ad.discover_schema()
    t = schema.get_table("authors")
    assert t is not None
    assert {c.name for c in t.columns} == {"id", "author_name"}


def test_get_source_adapter_factory():
    assert isinstance(get_source_adapter("postgres", connector=_FakeConnector([])),
                      PostgresSourceAdapter)
    with pytest.raises(ValueError):
        get_source_adapter("oracle")


def test_same_engine_normalises_aliases():
    assert same_engine("postgres", "postgresql") is True
    assert same_engine("pg", "postgres") is True
    assert same_engine("mysql", "mariadb") is True
    assert same_engine("postgres", "mysql") is False


def test_passthrough_delegates_and_marks():
    class _Inner:
        engine_type = "postgres"

        def discover_registry(self):
            return "REG"

        def dialect(self):
            return "DIALECT"

        def close(self):
            self.closed = True

    inner = _Inner()
    pa = PassthroughAdapter(inner)
    assert pa.passthrough is True and pa.engine_type == "postgres"
    assert pa.discover_registry() == "REG" and pa.dialect() == "DIALECT"
    pa.close()
    assert inner.closed is True
