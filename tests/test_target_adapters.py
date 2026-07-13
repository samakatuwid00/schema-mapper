"""Tests for the target-adapter layer + FK-aware registry discovery (§0).

These are engine-agnostic: they exercise `LrmisRegistry.from_discovery`, the
Postgres normalisation, and the adapter wiring without a live database.
"""
import pytest

from src.lrmis_registry import LrmisRegistry
from src.adapters import (
    MySQLTargetAdapter, PostgresTargetAdapter, get_target_adapter,
)
from src.adapters.postgres_target import normalize_pg_columns
from src.adapters._protocols import _DiscoveryMixin

# A tiny 3-table schema shaped like the real LRMIS one:
#   station    - non-auto-increment PK (reference table), self-FK
#   geo_level  - auto-increment lookup
#   school     - writable, FKs to station + geo_level
COLS = [
    {"table_name": "station", "column_name": "id", "data_type": "int",
     "is_nullable": "NO", "ordinal_position": 1, "column_key": "PRI",
     "extra": "", "column_default": None},
    {"table_name": "station", "column_name": "parent_station", "data_type": "int",
     "is_nullable": "YES", "ordinal_position": 2, "column_key": "",
     "extra": "", "column_default": None},
    {"table_name": "geo_level", "column_name": "id", "data_type": "int",
     "is_nullable": "NO", "ordinal_position": 1, "column_key": "PRI",
     "extra": "auto_increment", "column_default": None},
    {"table_name": "geo_level", "column_name": "name", "data_type": "varchar(50)",
     "is_nullable": "NO", "ordinal_position": 2, "column_key": "",
     "extra": "", "column_default": None},
    {"table_name": "school", "column_name": "id", "data_type": "int",
     "is_nullable": "NO", "ordinal_position": 1, "column_key": "PRI",
     "extra": "auto_increment", "column_default": None},
    {"table_name": "school", "column_name": "station_id", "data_type": "int",
     "is_nullable": "NO", "ordinal_position": 2, "column_key": "",
     "extra": "", "column_default": None},
    {"table_name": "school", "column_name": "geo_level_id", "data_type": "int",
     "is_nullable": "NO", "ordinal_position": 3, "column_key": "",
     "extra": "", "column_default": None},
]
FKS = [
    {"table_name": "station", "column_name": "parent_station",
     "ref_table": "station", "ref_column": "id"},
    {"table_name": "school", "column_name": "station_id",
     "ref_table": "station", "ref_column": "id"},
    {"table_name": "school", "column_name": "geo_level_id",
     "ref_table": "geo_level", "ref_column": "id"},
]


def test_from_discovery_builds_fk_graph_and_ordering():
    reg = LrmisRegistry.from_discovery(COLS, FKS)
    assert set(reg.table_names) == {"station", "geo_level", "school"}
    # FK graph is populated (the whole point of FK-aware discovery)
    assert reg.get_fk_column("school", "station") == "station_id"
    assert reg.get_parent_tables("school") == ["geo_level", "station"]
    # parents ordered before the child; the station self-loop does not cycle
    order = reg.topological_order()
    assert order.index("station") < order.index("school")
    assert order.index("geo_level") < order.index("school")


def test_reference_table_and_seed_set_from_discovery():
    reg = LrmisRegistry.from_discovery(COLS, FKS)
    # station has no auto-increment PK -> reference (resolve-only) table
    assert reg.is_reference_table("station") is True
    assert reg.is_reference_table("school") is False
    # seed set = FK-closure of the write set, minus the writes themselves
    assert reg.seed_tables(["school"]) == ["geo_level", "station"]


def test_from_discovery_requires_rows():
    with pytest.raises(ValueError):
        LrmisRegistry.from_discovery([], [])


def test_columns_only_discovery_has_empty_fk_graph():
    # Without fk_rows the graph is empty — this is why discovery must read FKs.
    reg = LrmisRegistry.from_discovery(COLS)
    assert reg.get_parent_tables("school") == []


def test_pg_normalization_marks_identity_serial_and_pk():
    pg_cols = [
        {"table_name": "geo_level", "column_name": "id", "data_type": "integer",
         "is_nullable": "NO", "ordinal_position": 1,
         "column_default": "nextval('geo_level_id_seq'::regclass)",
         "is_identity": "NO"},
        {"table_name": "beis", "column_name": "id", "data_type": "integer",
         "is_nullable": "NO", "ordinal_position": 1, "column_default": None,
         "is_identity": "YES"},
        {"table_name": "station", "column_name": "id", "data_type": "integer",
         "is_nullable": "NO", "ordinal_position": 1, "column_default": None,
         "is_identity": "NO"},
    ]
    pk = {("geo_level", "id"), ("beis", "id"), ("station", "id")}
    by = {r["table_name"]: r for r in normalize_pg_columns(pg_cols, pk)}
    assert by["geo_level"]["extra"] == "auto_increment"   # serial default
    assert by["beis"]["extra"] == "auto_increment"        # identity column
    assert by["station"]["extra"] == ""                   # plain PK
    assert by["geo_level"]["column_key"] == "PRI"

    reg = LrmisRegistry.from_discovery(normalize_pg_columns(pg_cols, pk), [])
    assert reg.auto_increment_column("geo_level") == "id"
    assert reg.auto_increment_column("beis") == "id"
    assert reg.is_reference_table("station") is True      # no auto-increment PK


class _StubAdapter(_DiscoveryMixin):
    engine_type = "stub"

    def column_rows(self):
        return COLS

    def fk_rows(self):
        return FKS


def test_discovery_mixin_wires_registry():
    reg = _StubAdapter().discover_registry()
    assert reg.get_parent_tables("school") == ["geo_level", "station"]


def test_factory_resolves_engines():
    assert isinstance(
        get_target_adapter("postgresql", dsn="postgresql://x/y"),
        PostgresTargetAdapter)
    assert isinstance(get_target_adapter("mysql"), MySQLTargetAdapter)
    with pytest.raises(ValueError):
        get_target_adapter("oracle")
