"""Path B delivery (Phases 4-5): value grouping, transforms, deliver/refresh.

The writer and connections are faked so no database is needed; the delivery
logic and its transform/deactivate/refresh behavior are what is exercised.
"""
from __future__ import annotations

from datetime import date

import pytest

from src import lrmis_delivery as D


def mp(src, table, col, transform="none"):
    return {"source_column": src, "target_table": table,
            "target_column": col, "transform": transform}


# ---------------------------------------------------------------------------
# build_values_by_table
# ---------------------------------------------------------------------------

def test_groups_values_by_target_table():
    row = {"school_name": "Naga ES", "addr": "Main St", "opened": date(2020, 1, 2)}
    mappings = [mp("school_name", "station", "geoloc"),
                mp("addr", "station_address", "street"),
                mp("opened", "beis", "date_established", "cast:date->datetime")]
    values, errors = D.build_values_by_table(row, mappings)
    assert errors == []
    assert values["station"] == {"geoloc": "Naga ES"}
    assert values["station_address"] == {"street": "Main St"}
    assert values["beis"]["date_established"].isoformat().startswith("2020-01-02")


def test_missing_source_column_is_skipped():
    values, errors = D.build_values_by_table({"a": 1}, [mp("b", "station", "geoloc")])
    assert values == {} and errors == []


def test_rejected_mapping_without_target_is_skipped():
    values, _ = D.build_values_by_table(
        {"a": 1}, [{"source_column": "a", "target_table": None, "target_column": None}])
    assert values == {}


def test_transform_failure_is_collected_not_raised():
    values, errors = D.build_values_by_table(
        {"n": "notanumber"}, [mp("n", "beis", "count", "cast:str->int")])
    assert values == {}
    assert errors and "transform failed" in errors[0]


# ---------------------------------------------------------------------------
# deliver_event
# ---------------------------------------------------------------------------

class FakeTargetConn:
    def __init__(self):
        self.audit = []

    def cursor(self):
        outer = self

        class _C:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql, params=()):
                if "delivery_audit" in sql:
                    outer.audit.append(params)
        return _C()


def _event(op="insert"):
    return {"event_id": "e1", "external_reference": "uuid-1", "operation": op,
            "source_system": "IRIMSV_REGION_V", "source_updated_at": None,
            "mapping_version": 3, "payload_checksum": "abc", "attempts": 0,
            "payload": {"school_name": "Naga ES", "addr": "Main St"}}


def test_deliver_event_writes_and_audits(monkeypatch):
    captured = {}

    def fake_write(target_conn, central_conn, **kw):
        captured.update(kw)
        return {"station": 10_000_001, "station_address": 5}

    monkeypatch.setattr(D, "write_source_row", fake_write)
    target = FakeTargetConn()
    mappings = [mp("school_name", "station", "geoloc"),
                mp("addr", "station_address", "street")]

    out = D.deliver_event(target, object(), entity_name="schools", event=_event(),
                          mappings=mappings, registry=object())

    assert out["status"] == "delivered"
    assert out["active"] is True
    assert captured["source_entity"] == "schools"
    assert set(captured["values_by_table"]) == {"station", "station_address"}
    assert len(target.audit) == 1
    assert target.audit[0][-2] == 1        # active flag = 1


def test_deactivate_marks_audit_inactive_but_still_writes(monkeypatch):
    monkeypatch.setattr(D, "write_source_row", lambda *a, **k: {"station": 1})
    target = FakeTargetConn()
    out = D.deliver_event(target, object(), entity_name="schools",
                          event=_event(op="deactivate"),
                          mappings=[mp("school_name", "station", "geoloc")], registry=object())
    assert out["active"] is False
    assert target.audit[0][-2] == 0        # active flag = 0


def test_deliver_event_returns_error_on_transform_failure(monkeypatch):
    monkeypatch.setattr(D, "write_source_row",
                        lambda *a, **k: pytest.fail("must not write on error"))
    target = FakeTargetConn()
    bad = _event()
    bad["payload"] = {"n": "x"}
    out = D.deliver_event(target, object(), entity_name="schools", event=bad,
                          mappings=[mp("n", "beis", "count", "cast:str->int")],
                          registry=object())
    assert out["status"] == "error"


def test_deliver_event_errors_when_no_target_values(monkeypatch):
    monkeypatch.setattr(D, "write_source_row",
                        lambda *a, **k: pytest.fail("must not write"))
    out = D.deliver_event(FakeTargetConn(), object(), entity_name="schools",
                          event=_event(), mappings=[mp("absent", "station", "geoloc")],
                          registry=object())
    assert out["status"] == "error"


# ---------------------------------------------------------------------------
# refresh_entity
# ---------------------------------------------------------------------------

def test_refresh_deletes_then_rewrites(monkeypatch):
    calls = {"delete": 0, "write": 0}

    def fake_delete(*a, **k):
        calls["delete"] += 1
        return {"beis": 3}

    def fake_write(*a, **k):
        calls["write"] += 1
        return {"station": 1}

    monkeypatch.setattr(D, "delete_entity_rows", fake_delete)
    monkeypatch.setattr(D, "write_source_row", fake_write)

    rows = [{"school_name": "A"}, {"school_name": "B"}]
    out = D.refresh_entity(object(), object(), entity_name="schools",
                           mappings=[mp("school_name", "station", "geoloc")],
                           source_rows=rows,
                           external_reference_of=lambda r: f"uuid-{r['school_name']}",
                           registry=object())
    assert calls["delete"] == 1              # delete happens once, before writes
    assert out["written"] == 2
    assert out["deleted"] == {"beis": 3}


def test_refresh_records_failures_and_continues(monkeypatch):
    monkeypatch.setattr(D, "delete_entity_rows", lambda *a, **k: {})
    monkeypatch.setattr(D, "write_source_row", lambda *a, **k: {"station": 1})
    rows = [{"school_name": "ok"}, {"other": "x"}]   # 2nd has no mapped source col
    out = D.refresh_entity(object(), object(), entity_name="schools",
                           mappings=[mp("school_name", "station", "geoloc")],
                           source_rows=rows,
                           external_reference_of=lambda r: "u", registry=object())
    assert out["written"] == 1
    assert len(out["failed"]) == 1
