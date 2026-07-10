"""Multi-table mapping validation (Path B, Phase 2).

Inline DDL fixture shaped like the real school fan-out: an app-assigned
`station` (no auto-increment PK) with a required non-FK column and a required
FK to a seeded lookup, plus an auto-increment child.
"""
from __future__ import annotations

import pytest

from src.lrmis_registry import LrmisRegistry, parse_ddl
from src.services.common import ValidationError
from src.services.lrmis_mapping import (columns_a_mapping_must_supply,
                                        coverage_report, store_target_tables,
                                        system_handled_columns,
                                        target_tables_for, validate_deployment)

DDL = """
CREATE TABLE `region` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;

CREATE TABLE `station` (
  `id` int NOT NULL,
  `region_id` int NOT NULL,
  `label` varchar(50) NOT NULL,
  `note` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `s_region_fk` FOREIGN KEY (`region_id`) REFERENCES `region` (`id`)
) ENGINE=InnoDB;

CREATE TABLE `addr` (
  `id` int NOT NULL AUTO_INCREMENT,
  `station_id` int NOT NULL,
  `street` varchar(100) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `a_station_fk` FOREIGN KEY (`station_id`) REFERENCES `station` (`id`)
) ENGINE=InnoDB;
"""

REG = LrmisRegistry(parse_ddl(DDL))
SEEDED = {"region"}   # region is a seeded lookup in this fixture


def m(src, table, col):
    return {"source_column": src, "target_table": table, "target_column": col}


# ---------------------------------------------------------------------------
# Column classification
# ---------------------------------------------------------------------------

def test_target_tables_for():
    assert target_tables_for([m("a", "station", "label"), m("b", "addr", "street"),
                              {"source_column": "c", "target_table": None}]) == \
        ["addr", "station"]


def test_system_handled_includes_fk_and_app_assigned_pk():
    handled = system_handled_columns("station", REG)
    assert "region_id" in handled     # FK, writer fills from parent
    assert "id" in handled            # station is app-assigned


def test_must_supply_excludes_fk_and_pk():
    # station: id (app-pk) + region_id (fk) are system-handled; label is required.
    assert columns_a_mapping_must_supply("station", REG) == {"label"}
    # addr: station_id (fk) handled; street required; id is auto-increment.
    assert columns_a_mapping_must_supply("addr", REG) == {"street"}


# ---------------------------------------------------------------------------
# coverage_report / validate_deployment
# ---------------------------------------------------------------------------

def _valid():
    return [m("school_name", "station", "label"),
            m("addr_line", "addr", "street")]


def test_valid_mapping_passes():
    report = coverage_report(_valid(), REG, seeded_tables=SEEDED)
    assert report.ok
    validate_deployment(_valid(), REG, seeded_tables=SEEDED)  # does not raise


def test_unknown_target_table_blocks():
    bad = _valid() + [m("x", "nope", "y")]
    report = coverage_report(bad, REG, seeded_tables=SEEDED)
    assert "nope" in report.unknown_target_tables
    assert not report.ok
    with pytest.raises(ValidationError):
        validate_deployment(bad, REG, seeded_tables=SEEDED)


def test_unknown_target_column_blocks():
    bad = _valid() + [m("x", "station", "does_not_exist")]
    report = coverage_report(bad, REG, seeded_tables=SEEDED)
    tc = next(t for t in report.tables if t.table == "station")
    assert "does_not_exist" in tc.unknown_target_columns
    assert not report.ok


def test_required_column_missing_blocks():
    # drop the station.label mapping -> its required column is unmet
    bad = [m("addr_line", "addr", "street")]
    report = coverage_report(bad + [m("s", "station", "note")], REG, seeded_tables=SEEDED)
    tc = next(t for t in report.tables if t.table == "station")
    assert "label" in tc.required_missing
    assert not report.ok


def test_required_fk_with_no_seeded_parent_blocks():
    # region is required by station.region_id, unmapped; if region is neither
    # written nor seeded, the writer has no parent id -> unsatisfiable.
    report = coverage_report(_valid(), REG, seeded_tables=set())
    assert any(f["column"] == "region_id" and f["ref_table"] == "region"
               for f in report.fk_unsatisfiable)
    assert not report.ok


def test_required_fk_satisfied_when_parent_is_written():
    # if the mapping also writes region, the FK is satisfiable without seeding.
    mapping = _valid() + [m("region_name", "region", "name")]
    report = coverage_report(mapping, REG, seeded_tables=set())
    assert report.fk_unsatisfiable == []
    assert report.ok


def test_explicitly_mapped_fk_column_is_not_flagged():
    # if the source supplies region_id directly, no parent lookup is needed.
    mapping = _valid() + [m("region_code", "station", "region_id")]
    report = coverage_report(mapping, REG, seeded_tables=set())
    assert report.fk_unsatisfiable == []


def test_blocking_messages_are_human_readable():
    report = coverage_report([m("x", "station", "ghost")], REG, seeded_tables=SEEDED)
    joined = "\n".join(report.blocking)
    assert "station.ghost is not a column" in joined
    assert "station.label is required" in joined  # label still unmet here


# ---------------------------------------------------------------------------
# store_target_tables
# ---------------------------------------------------------------------------

class _Cur:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.owner.executed.append((" ".join(sql.split()), params))


class _Conn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _Cur(self)


def test_store_target_tables_writes_distinct_sorted_footprint():
    conn = _Conn()
    stored = store_target_tables(conn, 42, _valid())
    assert stored == ["addr", "station"]
    sql, params = conn.executed[0]
    assert "UPDATE integration.onboarding_entity" in sql
    assert "lrmis_target_tables" in sql
    import json
    assert json.loads(params[0]) == ["addr", "station"]
    assert params[1] == 42


# ---------------------------------------------------------------------------
# Against the real LRMIS schema
# ---------------------------------------------------------------------------

def test_real_schema_station_has_no_required_non_system_columns():
    from src.lrmis_registry import get_registry
    reg = get_registry()
    # station's id is app-assigned and its other columns are FK/nullable.
    assert columns_a_mapping_must_supply("station", reg) == set()


def test_real_schema_valid_school_mapping_passes():
    from src.lrmis_registry import get_registry
    reg = get_registry()
    mapping = [
        {"source_column": "school_name", "target_table": "station", "target_column": "geoloc"},
        {"source_column": "address", "target_table": "station_address", "target_column": "street"},
    ]
    report = coverage_report(mapping, reg)
    assert report.ok, report.blocking
