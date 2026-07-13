"""Adapter-backed schema registry (generic engine, §5).

`SchemaRegistry` gives the engine the design's registry API — `load()`,
`get_table()`, `foreign_keys()`, `topological_order()` — sourced from a
`TargetAdapter`'s live discovery rather than a hardcoded DDL file. It delegates
the FK graph / topological sort to the existing `LrmisRegistry` (which already
implements them), so this is a thin, non-breaking facade: existing code keeps
using `get_registry()`; new engine-agnostic code can construct
`SchemaRegistry(adapter)`.
"""
from __future__ import annotations


class SchemaRegistry:
    def __init__(self, adapter):
        self._adapter = adapter
        self._registry = None

    def load(self) -> "SchemaRegistry":
        self._registry = self._adapter.discover_registry()
        return self

    @classmethod
    def from_registry(cls, registry) -> "SchemaRegistry":
        obj = cls.__new__(cls)
        obj._adapter = None
        obj._registry = registry
        return obj

    @property
    def registry(self):
        if self._registry is None:
            self.load()
        return self._registry

    # -- delegated lookups --------------------------------------------------
    @property
    def table_names(self) -> list[str]:
        return self.registry.table_names

    def has_table(self, name: str) -> bool:
        return self.registry.has_table(name)

    def get_table(self, name):
        return self.registry.get_table(name)

    def foreign_keys(self, table: str):
        return self.registry.foreign_keys(table)

    def topological_order(self, subset=None):
        return self.registry.topological_order(subset)

    def is_reference_table(self, table: str) -> bool:
        return self.registry.is_reference_table(table)

    def seed_tables(self, write_set):
        return self.registry.seed_tables(write_set)
