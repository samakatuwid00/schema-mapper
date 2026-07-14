"""Writer: parent-first order, read-only reference tables, app-assigned ids
for `station`, FK propagation, idempotent upsert via the crosswalk, and
crosswalk-scoped deletes.

Connections are faked so the SQL actually issued can be asserted; no database
is required. The fake keys responses by a substring marker in the SQL text
rather than call order — an earlier positional-queue version silently broke
when station's write path grew an extra crosswalk lookup, returning the wrong
row to a later, unrelated query instead of failing loudly.
"""
from __future__ import annotations

import pytest

from src.lrmis_registry import LrmisRegistry, parse_ddl
from src.lrmis_writer import (DEFAULT_ID_SEQUENCE_START, ReferenceRowNotFound,
                              UnknownTargetTable, allocate_id,
                              delete_entity_rows, group_by_table,
                              resolve_reference_id, write_source_row)

# `station` mirrors the real schema: self-referencing, NOT auto-increment,
# and app-assigned per APP_ASSIGNED_ID_TABLES. `psgc` is a second,
# independent no-auto-increment table that is NOT app-assigned, standing in
# for genuine external reference data (the real psgc registry) so tests don't
# rely on "station" being special only by coincidence of name.
DDL = """
CREATE TABLE `station` (
  `id` int NOT NULL,
  `geoloc` varchar(255) DEFAULT NULL,
  `parent_station` int DEFAULT NULL,
  `psgc_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_parent_station` FOREIGN KEY (`parent_station`) REFERENCES `station` (`id`),
  CONSTRAINT `station_psgc_fk` FOREIGN KEY (`psgc_id`) REFERENCES `psgc` (`id`)
) ENGINE=InnoDB;

CREATE TABLE `psgc` (
  `id` int NOT NULL,
  `name` varchar(100) DEFAULT NULL,
  `parent_psgc` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_parent_psgc` FOREIGN KEY (`parent_psgc`) REFERENCES `psgc` (`id`)
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
    def __init__(self, owner):
        self.owner = owner
        self.lastrowid = None
        self.rowcount = 1
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.owner.sql.append((norm, tuple(params)))
        self.lastrowid = self.owner.next_id
        self.owner.next_id += 1
        self._row = None
        # target_table is a bound parameter on crosswalk queries, not literal
        # SQL text, so "station" vs "beis" can only be told apart by exact
        # parameter value, not by substring-matching the query string.
        param_strs = {str(p) for p in params}
        for marker, row in self.owner.responses:
            if marker in param_strs or marker in norm:
                self._row = row() if callable(row) else row
                break

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class FakeConn:
    """Records SQL. `responses` is an ordered list of (marker, row) pairs;
    the first marker found *as a substring* of the executed SQL wins. `row`
    may be a value or a zero-arg callable (for a counter, e.g. allocate_id).
    """

    def __init__(self, responses=None, next_id=100):
        self.sql: list[tuple[str, tuple]] = []
        self.responses = list(responses or [])
        self.next_id = next_id

    def cursor(self, *a, **k):
        return _Cursor(self)

    def statements(self, verb: str) -> list[str]:
        return [s for s, _ in self.sql if s.upper().startswith(verb.upper())]

    def statements_on(self, table: str, verb: str = "") -> list[tuple[str, tuple]]:
        return [(s, p) for s, p in self.sql
                if f"`{table}`" in s and s.upper().startswith(verb.upper())]


def _mysql(responses=None, next_id=100):
    return FakeConn(responses, next_id)


def _sequence(start=DEFAULT_ID_SEQUENCE_START):
    """A stateful counter mimicking allocate_id's atomic increment."""
    box = {"n": start - 1}

    def _next():
        box["n"] += 1
        return (box["n"],)
    return _next


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
# Pure reference tables (psgc): resolve-only, exactly as before
# ---------------------------------------------------------------------------

def test_reference_table_resolved_by_primary_key():
    mysql = _mysql([("SELECT `id` FROM `psgc`", (7,))])
    assert resolve_reference_id(mysql, "psgc", {"id": 7}, REG) == 7
    assert mysql.statements("SELECT")
    assert not mysql.statements("INSERT")


def test_reference_table_resolved_by_natural_key():
    mysql = _mysql([("WHERE `name` = %s", (42,))])
    got = resolve_reference_id(mysql, "psgc", {"name": "Naga City"}, REG)
    assert got == 42
    sql, params = mysql.sql[0]
    assert sql.startswith("SELECT `id` FROM `psgc` WHERE `name` = %s")
    assert params == ("Naga City",)


def test_unresolvable_reference_row_raises_rather_than_inserting():
    mysql = _mysql()  # no marker matches -> fetchone() returns None
    with pytest.raises(ReferenceRowNotFound):
        resolve_reference_id(mysql, "psgc", {"name": "Nowhere"}, REG)
    assert not mysql.statements("INSERT")


def test_reference_row_with_missing_pk_raises():
    mysql = _mysql()  # SELECT ... WHERE id = 999 finds nothing
    with pytest.raises(ReferenceRowNotFound):
        resolve_reference_id(mysql, "psgc", {"id": 999}, REG)


def test_reference_row_without_any_lookup_value_raises():
    with pytest.raises(ReferenceRowNotFound):
        resolve_reference_id(_mysql(), "psgc", {}, REG)


def test_psgc_is_never_written_to():
    """The pipeline must never mint new geographic codes."""
    mysql = _mysql([("FROM `psgc`", None)])  # no match
    central = FakeConn()
    with pytest.raises(ReferenceRowNotFound):
        write_source_row(mysql, central, source_entity="schools",
                         external_reference="uuid-x",
                         values_by_table={"psgc": {"name": "Nowhere"}}, registry=REG)
    assert not mysql.statements("INSERT")


# ---------------------------------------------------------------------------
# station: app-assigned id path
# ---------------------------------------------------------------------------

def test_station_reuses_existing_row_when_pk_matches():
    mysql = _mysql([("SELECT `id` FROM `station`", (7,))])  # pk-based resolve succeeds
    central = FakeConn([("id_crosswalk", None)])  # no crosswalk row yet -> falls through

    ids = write_source_row(mysql, central, source_entity="schools",
                           external_reference="uuid-1",
                           values_by_table={"station": {"id": 7}}, registry=REG)

    assert ids["station"] == 7
    assert not mysql.statements_on("station", "INSERT")
    # the match was recorded so re-delivery short-circuits via the crosswalk
    assert central.statements("INSERT")


def test_station_with_no_match_allocates_and_inserts():
    mysql = _mysql()  # no crosswalk hit, no natural-key match either
    central = FakeConn(responses=[
        ("id_crosswalk", None),
        ("id_sequence", _sequence()),
    ])

    ids = write_source_row(mysql, central, source_entity="schools",
                           external_reference="uuid-2",
                           values_by_table={"station": {"geoloc": "13.6,123.2"}},
                           registry=REG)

    assert ids["station"] == DEFAULT_ID_SEQUENCE_START
    inserts = mysql.statements_on("station", "INSERT")
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "`id`" in sql
    assert DEFAULT_ID_SEQUENCE_START in params


def test_repeated_allocation_is_sequential():
    central = FakeConn([("id_sequence", _sequence())])
    first = allocate_id(central, "station")
    second = allocate_id(central, "station")
    assert second == first + 1
    assert first == DEFAULT_ID_SEQUENCE_START


def test_existing_crosswalk_row_updates_station_instead_of_reallocating():
    # crosswalk hit AND the target row still exists (SELECT 1 -> truthy)
    mysql = _mysql([("SELECT 1 FROM", (1,))])
    central = FakeConn([("id_crosswalk", ("55",))])

    ids = write_source_row(mysql, central, source_entity="schools",
                           external_reference="uuid-3",
                           values_by_table={"station": {"geoloc": "x"}}, registry=REG)

    assert ids["station"] == "55"
    assert mysql.statements_on("station", "UPDATE")
    assert not mysql.statements_on("station", "INSERT")


def test_stale_station_crosswalk_reallocates_when_target_row_missing():
    """A crosswalk entry left dangling by a target reset must not be trusted:
    the guard's SELECT 1 finds no row, so the writer re-allocates and re-inserts
    rather than handing a dead id to a child FK."""
    mysql = _mysql([("id_sequence", None)])  # no "SELECT 1 FROM" match -> row absent
    central = FakeConn([
        ("id_crosswalk", ("55",)),   # crosswalk points at a since-deleted station
        ("id_sequence", _sequence()),
    ])

    ids = write_source_row(mysql, central, source_entity="schools",
                           external_reference="uuid-3b",
                           values_by_table={"station": {"geoloc": "x"}}, registry=REG)

    assert ids["station"] == DEFAULT_ID_SEQUENCE_START      # fresh id, not "55"
    assert mysql.statements_on("station", "INSERT")
    assert not mysql.statements_on("station", "UPDATE")


def test_self_referencing_fk_is_never_auto_filled_on_station():
    mysql = _mysql([("SELECT `id` FROM `station`", (7,))])
    central = FakeConn([("id_crosswalk", None)])
    ids = write_source_row(mysql, central, source_entity="s", external_reference="u",
                           values_by_table={"station": {"id": 7}}, registry=REG)
    assert ids["station"] == 7
    assert not mysql.statements_on("station", "INSERT")


# ---------------------------------------------------------------------------
# write_source_row: ordering, FK propagation, normal auto-increment tables
# ---------------------------------------------------------------------------

def test_writes_parents_first_and_propagates_fk_to_children():
    mysql = _mysql([("SELECT `id` FROM `station`", (7,))])
    central = FakeConn([("id_crosswalk", None)])

    ids = write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-4",
        values_by_table={
            "beis": {"beis_id": "B-1"},
            "station_address": {"address": "Main St"},
            "station": {"id": 7},
        },
        registry=REG,
    )

    assert ids["station"] == 7
    beis_sql, beis_params = mysql.statements_on("beis", "INSERT")[0]
    assert "`station_id`" in beis_sql
    assert 7 in beis_params
    _, sa_params = mysql.statements_on("station_address", "INSERT")[0]
    assert 7 in sa_params


def test_beis_crosswalk_update_path_is_independent_of_station():
    """Regression: station's write path must not consume beis's crosswalk row."""
    mysql = _mysql([
        ("SELECT `id` FROM `station`", (7,)),
        ("SELECT 1 FROM", (1,)),   # beis's crosswalk row still exists in target
    ])
    central = FakeConn([
        ("station", None),   # station: no existing crosswalk row -> resolves by pk instead
        ("beis", ("55",)),   # beis: existing crosswalk row -> update, not insert
    ])

    ids = write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-5",
        values_by_table={"station": {"id": 7}, "beis": {"beis_id": "B-1"}},
        registry=REG,
    )

    assert ids["beis"] == "55"
    assert mysql.statements_on("beis", "UPDATE")
    assert not mysql.statements_on("beis", "INSERT")


def test_stale_beis_crosswalk_reinserts_when_target_row_missing():
    """The general (non-app-assigned) crosswalk path is guarded too: a stale beis
    crosswalk (target reset, crosswalk kept) re-inserts rather than UPDATE-ing a
    row that no longer exists and silently losing the write."""
    mysql = _mysql([("SELECT `id` FROM `station`", (7,))])  # no "SELECT 1" -> beis row absent
    central = FakeConn([("station", None), ("beis", ("55",))])

    write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-5b",
        values_by_table={"station": {"id": 7}, "beis": {"beis_id": "B-1"}},
        registry=REG,
    )

    assert mysql.statements_on("beis", "INSERT")
    assert not mysql.statements_on("beis", "UPDATE")


def test_new_beis_row_records_exactly_one_crosswalk_upsert():
    mysql = _mysql([("SELECT `id` FROM `station`", (7,))], next_id=321)
    central = FakeConn([("station", None), ("beis", None)])

    write_source_row(
        mysql, central,
        source_entity="schools", external_reference="uuid-9",
        values_by_table={"station": {"id": 7}, "beis": {"beis_id": "B"}},
        registry=REG,
    )
    assert len(mysql.statements_on("beis", "INSERT")) == 1


def test_unknown_target_table_rejected():
    with pytest.raises(UnknownTargetTable):
        write_source_row(_mysql(), FakeConn(),
                         source_entity="s", external_reference="u",
                         values_by_table={"not_lrmis": {"a": 1}}, registry=REG)


# ---------------------------------------------------------------------------
# delete_entity_rows: never TRUNCATE, children first, reference rows survive
# ---------------------------------------------------------------------------

def test_delete_is_scoped_children_first_and_skips_station():
    mysql = _mysql()
    central = FakeConn([
        ("SELECT target_table, target_id",
         [("beis", "1"), ("station_address", "2"), ("station", "7")]),
    ])

    deleted = delete_entity_rows(mysql, central, source_entity="schools", registry=REG)

    stmts = mysql.statements("DELETE")
    assert len(stmts) == 2                                  # station skipped
    assert not any("`station`" in s for s in stmts)          # station row survives
    assert not mysql.statements("TRUNCATE")
    assert set(deleted) == {"beis", "station_address"}

    order = [s.split("`")[1] for s in stmts]
    assert order == ["station_address", "beis"]              # children before parents


def test_delete_with_no_crosswalk_rows_is_a_noop():
    mysql = _mysql()
    central = FakeConn([("SELECT target_table, target_id", [])])
    assert delete_entity_rows(mysql, central, source_entity="x", registry=REG) == {}
    assert not mysql.sql
