"""The `CDCStrategy` protocol (§6.1)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CDCStrategy(Protocol):
    name: str

    def setup(self, source_conn, table: str) -> None:
        """Prepare change capture for `table` (e.g. install a trigger). May be a
        no-op when capture needs no per-table setup (polling)."""
        ...

    def teardown(self, source_conn, table: str) -> None:
        ...

    def pending_events(self, source_conn, table: str, after) -> list[dict]:
        """Rows changed since `after`, as dicts (empty when nothing changed)."""
        ...
