"""Tests for pluggable CDC (§6)."""
import pytest

from src.cdc import PollingCDC, TriggerCDC, DebeziumCDC, get_cdc_strategy


class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.executed = (sql, params)

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.cur = None

    def cursor(self, cursor_factory=None):
        self.cur = _Cur(self._rows)
        return self.cur


def test_polling_cdc_builds_timestamp_query_and_returns_rows():
    cdc = PollingCDC(schema="irimsv", timestamp_column="updated_at")
    sql = cdc.build_sql("authors")
    assert 'FROM "irimsv"."authors"' in sql and '"updated_at" > %s' in sql
    conn = _Conn([{"id": 1}, {"id": 2}])
    events = cdc.pending_events(conn, "authors", after="2026-07-01")
    assert events == [{"id": 1}, {"id": 2}]
    assert conn.cur.executed[1] == ("2026-07-01",)


def test_polling_cdc_rejects_unsafe_identifier():
    with pytest.raises(ValueError):
        PollingCDC(timestamp_column="updated_at; DROP").build_sql("authors")


def test_trigger_cdc_reads_outbox():
    cdc = TriggerCDC()
    conn = _Conn([{"event_id": "e1"}])
    events = cdc.pending_events(conn, "authors", after="2026-07-01")
    assert events == [{"event_id": "e1"}]
    assert "integration.outbox" in conn.cur.executed[0]


def test_debezium_needs_a_consumer():
    with pytest.raises(NotImplementedError):
        DebeziumCDC().pending_events(None, "authors", after=None)


def test_get_cdc_strategy_factory():
    assert isinstance(get_cdc_strategy(), TriggerCDC)                 # default
    p = get_cdc_strategy({"strategy": "polling", "options": {"timestamp_column": "modified_at"}})
    assert isinstance(p, PollingCDC) and p.timestamp_column == "modified_at"
    with pytest.raises(ValueError):
        get_cdc_strategy({"strategy": "kafkaesque"})
