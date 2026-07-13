"""Adapter protocol conformance (§11.1) — every adapter satisfies its protocol."""
from src.adapters import (
    MySQLTargetAdapter, PostgresTargetAdapter, PostgresSourceAdapter,
    PassthroughAdapter,
)
from src.adapters._protocols import SourceAdapter, TargetAdapter

_TARGET_METHODS = ("discover_registry", "dialect", "column_rows", "fk_rows", "close")
_SOURCE_METHODS = ("discover_schema", "count_rows", "get_pk_columns", "fetch_rows", "close")


class _FakeConnector:
    def connection(self):
        raise NotImplementedError

    def close(self):
        pass


def test_target_adapters_conform_to_protocol():
    adapters = [PostgresTargetAdapter(dsn="postgresql://x/y"), MySQLTargetAdapter()]
    for ad in adapters:
        assert ad.engine_type in ("postgres", "mysql")
        for m in _TARGET_METHODS:
            assert callable(getattr(ad, m))
        assert isinstance(ad, TargetAdapter)          # runtime_checkable


def test_passthrough_conforms_to_target_protocol():
    inner = PostgresTargetAdapter(dsn="postgresql://x/y")
    pa = PassthroughAdapter(inner)
    for m in _TARGET_METHODS:
        assert callable(getattr(pa, m))
    assert isinstance(pa, TargetAdapter)


def test_postgres_source_conforms_to_protocol():
    ad = PostgresSourceAdapter(connector=_FakeConnector(), schema="irimsv")
    assert ad.engine_type == "postgres"
    for m in _SOURCE_METHODS:
        assert callable(getattr(ad, m))
    assert isinstance(ad, SourceAdapter)
