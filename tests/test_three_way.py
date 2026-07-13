"""Three-way schema observability: Path B browsing, staging<->target compare,
and the staging->target drift scan mode.

Databases and the record_drift insert are stubbed, so what is exercised is the
routing/annotation/diff logic that the three-way feature adds.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.services import data_browser
from src.services import scan as scan_service

# LRMIS layout shared by Path A staging and Path B target.
STAGING_TABLES = {
    "irimsv_parcels_staging": [
        {"name": "parcel_id", "data_type": "integer", "nullable": False, "is_primary_key": True},
        {"name": "area", "data_type": "decimal", "nullable": True, "is_primary_key": False},
    ]
}
PATH_B_TABLES = {
    "parcels": [
        {"name": "parcel_id", "data_type": "integer", "nullable": False, "is_primary_key": True},
        {"name": "area", "data_type": "decimal", "nullable": True, "is_primary_key": False},
    ]
}
SOURCE_TABLES = {
    "parcels_src": [
        {"name": "id", "data_type": "integer", "nullable": False, "is_primary_key": True},
    ]
}


class _FakeConn:
    def commit(self):
        pass


class _FakeCentral:
    @contextmanager
    def connection(self):
        yield _FakeConn()

    def count_rows(self, schema, table):
        return 3

    def close(self):
        pass


class _FakeStaging:
    kind = "staging"

    def count_rows(self, table):
        return 7

    def fetch_rows(self, table, limit, offset, sort=None, direction="asc"):
        return [{"parcel_id": 1, "area": 10}]

    def fetch_row_by(self, table, column, value):
        return {"parcel_id": 1, "area": 10}


class _FakeTarget:
    kind = "path_b"

    def count_rows(self, table):
        return 5

    def fetch_rows(self, table, limit, offset, sort=None, direction="asc"):
        return [{"parcel_id": 1, "area": 99}]

    def fetch_row_by(self, table, column, value):
        return {"parcel_id": 1, "area": 99}


class _FakePipeline:
    @staticmethod
    def _query(conn, sql, params=None):
        return [{"source_table": "parcels_src", "staging_table": "irimsv_parcels_staging",
                 "status": "deployed", "target_system": "LRMIS"}]


@pytest.fixture()
def cols(monkeypatch):
    monkeypatch.setattr(data_browser, "_source_columns", lambda central, schema: SOURCE_TABLES)
    monkeypatch.setattr(
        data_browser, "_target_columns",
        lambda conn: PATH_B_TABLES if getattr(conn, "kind", None) == "path_b" else STAGING_TABLES)
    monkeypatch.setattr(data_browser, "_pipeline", lambda: _FakePipeline)


# ---------------------------------------------------------------------------
# 8.1 list_browsable_tables includes path_b with staging annotations
# ---------------------------------------------------------------------------

def test_list_tables_includes_path_b_with_staging_annotation(cols):
    result = data_browser.list_browsable_tables(
        central=_FakeCentral(), staging=_FakeStaging(), target=_FakeTarget())
    assert set(result) == {"source", "target", "path_b"}
    path_b = result["path_b"]["tables"]
    assert path_b == [{"table": "parcels", "columns": 2, "rows": 5,
                       "staging_table": "irimsv_parcels_staging"}]


# ---------------------------------------------------------------------------
# 8.2 fetch_rows(side="path_b") reads from lrmis_target
# ---------------------------------------------------------------------------

def test_fetch_rows_path_b_reads_target(cols):
    result = data_browser.fetch_rows(
        "path_b", "parcels", central=_FakeCentral(),
        staging=_FakeStaging(), target=_FakeTarget())
    assert result["side"] == "path_b"
    assert result["rows"] == [{"parcel_id": 1, "area": 99}]
    assert result["total"] == 5


def test_fetch_rows_path_b_rejects_unknown_table(cols):
    from src.services.common import NotFoundError
    with pytest.raises(NotFoundError):
        data_browser.fetch_rows("path_b", "not_a_table", central=_FakeCentral(),
                                staging=_FakeStaging(), target=_FakeTarget())


# ---------------------------------------------------------------------------
# 8.3 compare_staging_target flags matching / diverging fields
# ---------------------------------------------------------------------------

def test_compare_staging_target_flags_divergence(cols):
    result = data_browser.compare_staging_target(
        "irimsv_parcels_staging", 1, staging=_FakeStaging(), target=_FakeTarget())
    assert result["path_b_table"] == "parcels"
    assert result["primary_key"] == "parcel_id"
    by_field = {f["field"]: f for f in result["fields"]}
    assert by_field["parcel_id"]["matches"] is True       # 1 == 1
    assert by_field["area"]["matches"] is False           # 10 vs 99
    assert result["missing_in_target"] is False and result["missing_in_staging"] is False


# ---------------------------------------------------------------------------
# 8.4 / 8.5 scan(mode="staging") records a staging->target drift report
# ---------------------------------------------------------------------------

def _is_rows(table, cols):
    return [{"table_name": table, "column_name": c[0], "data_type": c[1],
             "is_nullable": "YES", "column_key": c[2]} for c in cols]


class _ScanCursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a):
        pass

    def fetchone(self):
        return None


class _ScanConn:
    def cursor(self, *a, **k):
        return _ScanCursor()

    def commit(self):
        pass


class _ScanCentral:
    @contextmanager
    def connection(self):
        yield _ScanConn()

    def close(self):
        pass


class _SchemaStaging:
    def information_schema(self, schema_name=None):
        return _is_rows("parcels", [("parcel_id", "int", "PRI"), ("area", "decimal", "")])


class _SchemaTarget:
    def information_schema(self, schema_name=None):
        # Path B has an extra column → a difference vs staging.
        return _is_rows("parcels", [("parcel_id", "int", "PRI"), ("area", "decimal", ""),
                                    ("owner_name", "varchar", "")])


def test_scan_staging_mode_records_staging_to_target_drift(monkeypatch):
    captured = {}

    def _fake_record_drift(conn, system, prev_fp, obs_fp, differences, drift_pair="source->staging"):
        captured["drift_pair"] = drift_pair
        captured["differences"] = differences
        return ["parcels"]

    monkeypatch.setattr(scan_service, "record_drift", _fake_record_drift)

    result = scan_service.scan(mode="staging", central=_ScanCentral(),
                               staging=_SchemaStaging(), target=_SchemaTarget())

    assert result["mode"] == "staging"
    assert result["drift_pair"] == "staging->target"
    assert result["drift_detected"] is True
    assert captured["drift_pair"] == "staging->target"
    # the added Path B column shows up as a difference
    assert any(d.get("column") == "owner_name" for d in captured["differences"])


def test_scan_staging_mode_no_drift_when_contracts_match(monkeypatch):
    monkeypatch.setattr(scan_service, "record_drift",
                        lambda *a, **k: pytest.fail("record_drift must not run without drift"))
    same = _SchemaStaging()
    result = scan_service.scan(mode="staging", central=_ScanCentral(),
                               staging=same, target=_SchemaStaging())
    assert result["drift_detected"] is False
    assert result["paused_entities"] == []
