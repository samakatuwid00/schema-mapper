"""Tests for the adapter-backed SchemaRegistry (§5)."""
from src.lrmis_registry import LrmisRegistry
from src.schema_registry import SchemaRegistry


def _col(t, n, p, key="", extra=""):
    return {"table_name": t, "column_name": n, "data_type": "int",
            "is_nullable": "YES", "ordinal_position": p,
            "column_key": key, "extra": extra, "column_default": None}


REG = LrmisRegistry.from_discovery(
    [_col("region", "id", 1, key="PRI", extra="auto_increment"),
     _col("school", "id", 1, key="PRI", extra="auto_increment"),
     _col("school", "region_id", 2)],
    [{"table_name": "school", "column_name": "region_id",
      "ref_table": "region", "ref_column": "id"}])


class _FakeAdapter:
    def __init__(self, registry):
        self._registry = registry
        self.loads = 0

    def discover_registry(self):
        self.loads += 1
        return self._registry


def test_schema_registry_loads_from_adapter_and_delegates():
    ad = _FakeAdapter(REG)
    sr = SchemaRegistry(ad).load()
    assert ad.loads == 1
    assert set(sr.table_names) == {"region", "school"}
    assert sr.foreign_keys("school")[0].ref_table == "region"
    order = sr.topological_order()
    assert order.index("region") < order.index("school")


def test_schema_registry_lazy_load_once():
    ad = _FakeAdapter(REG)
    sr = SchemaRegistry(ad)              # not loaded yet
    assert ad.loads == 0
    _ = sr.get_table("region")          # triggers load
    _ = sr.table_names                  # reuses
    assert ad.loads == 1


def test_from_registry_wraps_without_adapter():
    sr = SchemaRegistry.from_registry(REG)
    assert sr.has_table("school") and not sr.is_reference_table("region")
