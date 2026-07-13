"""The `TargetAdapter` protocol + shared discovery wiring.

A target adapter's job is to hand back the target's schema as an
`LrmisRegistry`, *discovered from the live database* — engine type, tables,
columns, and foreign keys included. It normalises its engine's native
`information_schema` into a common row shape so `LrmisRegistry.from_discovery`
builds the same FK graph / topological order / seed set regardless of engine.

Common row shapes (dict keys are matched case-insensitively):

* column row: ``table_name, column_name, data_type, is_nullable,
  ordinal_position`` (required) plus optional ``column_key`` ('PRI'),
  ``extra`` ('auto_increment'), ``column_default``.
* fk row: ``table_name, column_name, ref_table, ref_column``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..lrmis_registry import LrmisRegistry


@runtime_checkable
class SourceAdapter(Protocol):
    """A read side the engine pulls schema + rows from."""
    engine_type: str

    def discover_schema(self):
        """The source schema as a `schema_models.Schema` (metadata only)."""
        ...

    def count_rows(self, table: str) -> int:
        ...

    def get_pk_columns(self, table: str) -> list[str]:
        ...

    def fetch_rows(self, table: str, columns: list[str] | None = None,
                   batch_size: int = 1000):
        """Yield row dicts, streamed in batches."""
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class TargetAdapter(Protocol):
    engine_type: str

    def column_rows(self) -> list[dict]:
        """Discovered columns, normalised to the common column-row shape."""
        ...

    def fk_rows(self) -> list[dict]:
        """Discovered foreign keys, normalised to the common fk-row shape."""
        ...

    def discover_registry(self) -> LrmisRegistry:
        """Build an `LrmisRegistry` from the live target schema."""
        ...

    def dialect(self):
        """The engine's `Dialect` for engine-agnostic SQL generation."""
        ...

    def close(self) -> None:
        ...


class _DiscoveryMixin:
    """Shared `discover_registry`/`close` for concrete adapters.

    Concrete adapters only implement the engine-specific `column_rows()` and
    `fk_rows()`; the registry assembly is identical across engines.
    """

    def discover_registry(self) -> LrmisRegistry:
        return LrmisRegistry.from_discovery(self.column_rows(), self.fk_rows())

    def dialect(self):
        from ..dialect import get_dialect
        return get_dialect(self.engine_type)

    def close(self) -> None:
        return None
