"""Passthrough target adapter (§2.5) — same-engine migrations.

When source and target run the same engine, the native→generic→native type
round-trip is unnecessary. `PassthroughAdapter` wraps a concrete target adapter,
delegates discovery/dialect/connection to it, and marks `passthrough=True` so
callers (e.g. target-DDL creation) can reuse the source's native column types
directly instead of remapping through `GenericType`.
"""
from __future__ import annotations


class PassthroughAdapter:
    passthrough = True

    def __init__(self, inner):
        self._inner = inner
        self.engine_type = getattr(inner, "engine_type", None)

    def discover_registry(self):
        return self._inner.discover_registry()

    def dialect(self):
        return self._inner.dialect()

    def connection(self):
        return self._inner.connection()

    def column_rows(self):
        return self._inner.column_rows()

    def fk_rows(self):
        return self._inner.fk_rows()

    def close(self) -> None:
        self._inner.close()


def same_engine(source_engine: str, target_engine: str) -> bool:
    """True when source and target are the same engine family (passthrough-eligible)."""
    def _norm(e):
        e = (e or "").strip().lower()
        return "postgres" if e in ("postgres", "postgresql", "pg") else \
               "mysql" if e in ("mysql", "mariadb") else e
    return _norm(source_engine) == _norm(target_engine)
