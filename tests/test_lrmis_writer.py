"""Writer: parent-first order, read-only reference tables, FK propagation,
idempotent upsert via the crosswalk, and crosswalk-scoped deletes.

Connections are faked so the SQL actually issued can be asserted; no database
is required.
"""
from __future__ import annotations

import pytest

from src.lrmis_registry import LrmisRegistry, parse_ddl
from src.lrmis_writer import (ReferenceRowNotFound, UnknownTargetTable,
                              delete_entity_rows, group_by_table,
                              resolve_reference_id, write_source_row)

DDL = """
CREATE TABLE `station` (
  `id` int NOT NULL,
  `geoloc` varchar(255) DEFAULT NULL,
  `parent_station` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_parent_station` FOREIGN KEY (`parent_station`) REFERENCES `station` (`id`)
) ENGINE=InnoDB;

CREATE TABLE `beis` (
  `id` int NOT NULL AUTO_INCREMENT,
  `station_id` int DEFAULT NULL,
  `beis_id` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `beis_fk` FOREIGN KEY (`station_id`) REFERENCES `station` (`id`)
) ENGINE=InnoDB;

CREATE TABLE `station_address` (
  `id` int NOT NULL AUTO_INCREMENT,
  `station_id` int NOT NULL,
  `address` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `sa_fk` FOREIGN KEY (`station_id`) REFERENCES `station` (`id`)
) ENGINE=InnoDB;
"""

REG = LrmisRegistry(parse_ddl(DDL))


class _Cursor:
    def __init__(self, owner, results):
        self.owner, self._results = owner, results
        self.lastrowid = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.owner.sql.append((" ".join(sql.split()), tuple(params)))
        self.owner.counter += 1
        self.lastrowid = self.owner.next_id
        self.owner.next_id += 1
        self.rowcount = 1
        self._row = self._results.pop(0) if self._results else None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class FakeConn:
    """Records SQL; `results` queues the row returned by each fetchone()."""

    def __init__(self, results=None, next_id=100):
        self.sql: list[tuple[str, tuple]] = []
        self.results = list(results or [])
        self.counter = 0
        self.next_id = next_id

    def cursor(self, *a, **k):
        return _Cursor(self, self.results)

    def statements(self, verb: str) -> list[str]:
        return [s for s, _ in self.sql if s.upper().startswith(verb.upper())]


def _mysql(results=None, next_id=100):
    return FakeConn(results, next_id)


# ---------------------------------------------------------------------------
# group_by_table
# ---------------------------------------------------------------------------

def test_group_by_table_splits_columns():
    groups = group_by_table([
        {"source_column": "name", "target_table": "station", "target_column": "geoloc"},
        {"source_column": "beis", "target_table": "beis", "target_column": "beis_id"},
        {"source_column": "x", "target_table": None, "target_column": None},
    ])
    assert set(groups) == {"station", "beis"}
    assert len(groups["station"]) == 1


# ---------------------------------------------------------------------------
# Reference tables are never inserted into
# ---------------------------------------------------------------------------

def test_reference_table_resolved_by_primary_key():
    mysql = _mysql(results=[(7,)])
    assert resolve_reference_id(mysql, "station", {"id": 7}, REG) == 7
    assert mysql.statements("SELECT")
    assert not mysql.statements("INSERT")


def test_reference_table_resolved_by_natural_key():
    mysql = _mysql(results=[(42,)])
    got = resolve_reference_id(mysql, "station", {"geoloc": "12,121"}, REG)
    assert got == 42
    sql, params = mysql.sql[0]
    assert sql.startswith("SELECT `id` FROM `station` WHERE `geoloc` = %s")
    assert params == ("12,121",)


def test_unresolvable_reference_row_raises_rather_than_inserting():
    mysql = _mysql(results=[None])
    with pytest.raises(ReferenceRowNotFound):
        resolve_reference_id(mysql, "station", {"geoloc": "nowhere"}, REG)
    assert not mysql.statements("INSERT")


def test_reference_row_with_missing_pk_raises():
    mysql = _mysql(results=[None])
    with pytest.raises(ReferenceRowNotFound):
        resolve_reference_id(mysql, "station", {"id": 999}, REG)


def test_reference_row_without_any_lookup_value_raises():
    with pytest.raises(ReferenceRowNotFound):
        resolve_reference_id(_mysql(), "station", {}, REG)


# ---------------------------------------------------------------------------
# write_source_row
# ---------------------------------------------------------------------------

def test_writes_parents_first_and_propagates_fk():
    # station resolves to id 7; beis + station_address are inserted with station_id=7
    mysql = _mysql(results=[(7,)], next_id=100)
    central = FakeConn(results=[None, None])  # no crosswalk rows yet

    ids = write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-1",
        values_by_table={
            "beis": {"beis_id": "B-1"},
            "station_address": {"address": "Main St"},
            "station": {"id": 7},
        },
        registry=REG,
    )

    assert ids["station"] == 7            # reference table: reused, never inserted
    inserts = mysql.statements("INSERT")
    assert len(inserts) == 2
    assert not any("INSERT INTO `station`" in s for s in inserts)

    # FK column filled from the resolved parent id
    beis_insert = next((s, p) for s, p in mysql.sql if s.startswith("INSERT INTO `beis`"))
    assert "`station_id`" in beis_insert[0]
    assert 7 in beis_insert[1]


def test_existing_crosswalk_row_updates_instead_of_duplicating():
    mysql = _mysql(results=[(7,)])
    # station lookup returns nothing from central; beis has an existing target id 55
    central = FakeConn(results=[("55",)])

    ids = write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-1",
        values_by_table={"station": {"id": 7}, "beis": {"beis_id": "B-1"}},
        registry=REG,
    )

    assert ids["beis"] == "55"
    assert mysql.statements("UPDATE")
    assert not mysql.statements("INSERT")


def test_new_row_records_crosswalk():
    mysql = _mysql(results=[(7,)], next_id=321)
    central = FakeConn(results=[None])

    write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-9",
        values_by_table={"station": {"id": 7}, "beis": {"beis_id": "B"}},
        registry=REG,
    )
    upserts = [s for s in central.statements("INSERT")
               if "id_crosswalk" in s and "ON CONFLICT" in s]
    assert len(upserts) == 1
    assert "target_table" in upserts[0]


def test_unknown_target_table_rejected():
    with pytest.raises(UnknownTargetTable):
        write_source_row(_mysql(), FakeConn(),
                         source_entity="s", external_reference="u",
                         values_by_table={"not_lrmis": {"a": 1}}, registry=REG)


def test_self_referencing_fk_is_never_auto_filled():
    # station is a reference table; parent_station must not be silently set.
    mysql = _mysql(results=[(7,)])
    central = FakeConn(results=[])
    write_source_row(mysql, central, source_entity="s", external_reference="u",
                     values_by_table={"station": {"id": 7}}, registry=REG)
    assert not mysql.statements("INSERT")


# ---------------------------------------------------------------------------
# delete_entity_rows: never TRUNCATE, children first, reference rows survive
# ---------------------------------------------------------------------------

def test_delete_is_scoped_children_first_and_skips_reference_tables():
    mysql = _mysql()
    central = FakeConn(results=[[("beis", "1"), ("station_address", "2"), ("station", "7")]])

    deleted = delete_entity_rows(mysql, central, source_entity="schools", registry=REG)

    stmts = mysql.statements("DELETE")
    assert len(stmts) == 2                                  # station skipped
    assert not any("`station`" in s for s in stmts)         # reference row survives
    assert all(s.startswith("DELETE FROM") for s in stmts)
    assert not mysql.statements("TRUNCATE")
    assert set(deleted) == {"beis", "station_address"}

    # children before parents: station_address and beis both reference station,
    # so ordering among them follows reversed topological order.
    order = [s.split("`")[1] for s in stmts]
    assert order == ["station_address", "beis"]


def test_delete_with_no_crosswalk_rows_is_a_noop():
    mysql = _mysql()
    central = FakeConn(results=[[]])
    assert delete_entity_rows(mysql, central, source_entity="x", registry=REG) == {}
    assert not mysql.sql
