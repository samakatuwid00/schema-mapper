"""Registry: DDL parsing, reference-table detection, self-loop-safe topo sort.

The inline fixture mirrors the real LRMIS shape: `station` is a
self-referencing parent with a NON auto-increment primary key (so it is a
read-only reference table), while its children auto-increment.
"""
from __future__ import annotations

import os

import pytest

from src.lrmis_registry import (LrmisRegistry, SchemaCycleError, parse_ddl)

DDL = """
CREATE TABLE `station` (
  `id` int NOT NULL,
  `stationtype_id` int DEFAULT NULL,
  `parent_station` int DEFAULT NULL,
  `geoloc` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `fk_parent_station` FOREIGN KEY (`parent_station`) REFERENCES `station` (`id`),
  CONSTRAINT `station_ibfk_1` FOREIGN KEY (`stationtype_id`) REFERENCES `station_type` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `station_type` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `beis` (
  `id` int NOT NULL AUTO_INCREMENT,
  `station_id` int DEFAULT NULL,
  `beis_id` varchar(20) DEFAULT NULL,
  `status` enum('active','closed') DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `beis_ibfk_1` FOREIGN KEY (`station_id`) REFERENCES `station` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `station_address` (
  `id` int NOT NULL AUTO_INCREMENT,
  `station_id` int NOT NULL,
  `address` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `sa_ibfk_1` FOREIGN KEY (`station_id`) REFERENCES `station` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CYCLIC_DDL = """
CREATE TABLE `a` (
  `id` int NOT NULL AUTO_INCREMENT,
  `b_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `a_fk` FOREIGN KEY (`b_id`) REFERENCES `b` (`id`)
) ENGINE=InnoDB;

CREATE TABLE `b` (
  `id` int NOT NULL AUTO_INCREMENT,
  `a_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `b_fk` FOREIGN KEY (`a_id`) REFERENCES `a` (`id`)
) ENGINE=InnoDB;
"""


@pytest.fixture()
def registry() -> LrmisRegistry:
    return LrmisRegistry(parse_ddl(DDL))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parses_all_tables(registry):
    assert registry.table_names == ["beis", "station", "station_address", "station_type"]


def test_parses_columns_and_nullability(registry):
    beis = registry.get_table("beis")
    assert [c.name for c in beis.columns] == ["id", "station_id", "beis_id", "status"]
    assert beis.get_column("id").nullable is False
    assert beis.get_column("station_id").nullable is True
    assert beis.get_column("id").is_primary_key is True


def test_parses_enum_values(registry):
    status = registry.get_table("beis").get_column("status")
    assert status.enum_values == ("active", "closed")
    assert status.base_type == "enum"


def test_key_and_constraint_lines_are_not_columns(registry):
    names = [c.name for c in registry.get_table("station").columns]
    assert names == ["id", "stationtype_id", "parent_station", "geoloc"]


def test_foreign_keys_parsed(registry):
    fks = {(fk.column, fk.ref_table) for fk in registry.foreign_keys("station")}
    assert fks == {("parent_station", "station"), ("stationtype_id", "station_type")}


# ---------------------------------------------------------------------------
# Reference tables: the reason station/psgc must never be inserted into
# ---------------------------------------------------------------------------

def test_station_is_a_reference_table(registry):
    assert registry.auto_increment_column("station") is None
    assert registry.is_reference_table("station") is True


def test_auto_increment_tables_are_writable(registry):
    assert registry.auto_increment_column("beis") == "id"
    assert registry.is_reference_table("beis") is False


def test_reference_tables_listing(registry):
    assert registry.reference_tables() == ["station"]


def test_self_referencing_detected(registry):
    assert registry.self_referencing_tables() == ["station"]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_topological_order_is_parent_first_despite_self_loop(registry):
    order = registry.topological_order()
    pos = {t: i for i, t in enumerate(order)}
    assert len(order) == 4  # the self-loop must not strand `station`
    assert pos["station_type"] < pos["station"]
    assert pos["station"] < pos["beis"]
    assert pos["station"] < pos["station_address"]


def test_subset_ordering_ignores_out_of_subset_parents(registry):
    # station_type is not in the subset; station must still come first.
    assert registry.topological_order(["station_address", "beis", "station"]) == [
        "station", "beis", "station_address"]


def test_ordering_is_deterministic(registry):
    assert registry.topological_order() == registry.topological_order()


def test_real_cycle_raises(registry):
    cyclic = LrmisRegistry(parse_ddl(CYCLIC_DDL))
    with pytest.raises(SchemaCycleError):
        cyclic.topological_order()


def test_unknown_table_rejected(registry):
    with pytest.raises(KeyError):
        registry.get_table("not_a_table")


def test_fk_column_and_relations(registry):
    assert registry.get_fk_column("beis", "station") == "station_id"
    assert registry.get_fk_column("beis", "station_type") is None
    assert registry.get_parent_tables("station") == ["station_type"]  # self excluded
    assert registry.get_referencing_tables("station") == ["beis", "station_address"]


# ---------------------------------------------------------------------------
# Real DDL, when it is available on this machine
# ---------------------------------------------------------------------------

_REAL = os.environ.get("LRMIS_DDL_PATH",
                       r"C:\Users\deped\Documents\lrmis-main\lrmis_db\lrmis.sql")


@pytest.mark.skipif(not os.path.exists(_REAL), reason="lrmis.sql not present")
def test_real_schema_shape():
    reg = LrmisRegistry.from_sql_file(_REAL)
    assert len(reg.table_names) == 51
    # These two, and only these two, lack an AUTO_INCREMENT primary key.
    assert reg.reference_tables() == ["psgc", "station"]
    assert reg.self_referencing_tables() == ["psgc", "station"]
    order = reg.topological_order()
    assert len(order) == 51
    pos = {t: i for i, t in enumerate(order)}
    for child in reg.get_referencing_tables("station"):
        assert pos["station"] < pos[child]
