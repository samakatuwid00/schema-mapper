"""Two-way data browser: source <-> the real LRMIS target.

Databases are stubbed, so what is exercised is the routing/annotation logic the
browser adds on top of the connectors (the legacy three-way staging side is gone).
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.services import data_browser

TARGET_TABLES = {
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


class _FakeTarget:
    def count_rows(self, table):
        return 5

    def fetch_rows(self, table, limit, offset, sort=None, direction="asc"):
        return [{"parcel_id": 1, "area": 99}]


class _FakePipeline:
    @staticmethod
    def _query(conn, sql, params=None):
        return [{"source_table": "parcels_src", "status": "deployed",
                 "target_system": "LRMIS", "lrmis_target_tables": ["parcels"]}]


@pytest.fixture()
def cols(monkeypatch):
    monkeypatch.setattr(data_browser, "_source_columns", lambda central, schema: SOURCE_TABLES)
    monkeypatch.setattr(data_browser, "_target_columns", lambda target: TARGET_TABLES)
    monkeypatch.setattr(data_browser, "_pipeline", lambda: _FakePipeline)


def test_list_tables_is_two_way_with_entity_link(cols):
    result = data_browser.list_browsable_tables(central=_FakeCentral(), target=_FakeTarget())
    assert set(result) == {"source", "target"}
    # target tables carry the delivering source entity
    assert result["target"]["tables"] == [{
        "table": "parcels", "columns": 2, "rows": 5,
        "entity_status": "deployed", "source_table": "parcels_src"}]
    # source tables carry the entity's target footprint
    src = result["source"]["tables"][0]
    assert src["table"] == "parcels_src" and src["target_tables"] == ["parcels"]


def test_fetch_rows_target_reads_the_target(cols):
    result = data_browser.fetch_rows("target", "parcels",
                                     central=_FakeCentral(), target=_FakeTarget())
    assert result["side"] == "target"
    assert result["rows"] == [{"parcel_id": 1, "area": 99}]
    assert result["total"] == 5


def test_fetch_rows_target_rejects_unknown_table(cols):
    from src.services.common import NotFoundError
    with pytest.raises(NotFoundError):
        data_browser.fetch_rows("target", "not_a_table",
                                central=_FakeCentral(), target=_FakeTarget())
