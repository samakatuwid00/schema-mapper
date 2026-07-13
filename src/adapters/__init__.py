"""Target adapters (generic-ai-db-migration-engine, §0).

The engine treats the target's engine type, schema, and FK structure as
*discovered at runtime* from whatever adapter is connected — never hardcoded to
MySQL/LRMIS. Each adapter normalises its engine's native ``information_schema``
into the common row shape ``LrmisRegistry.from_discovery`` consumes, so the same
registry, FK graph, and seed logic serve a MySQL target today and a Postgres
target (e.g. restored from ``old-lrmis.backup``) tomorrow.
"""
from __future__ import annotations

from ._protocols import SourceAdapter, TargetAdapter
from .mysql_target import MySQLTargetAdapter
from .passthrough import PassthroughAdapter, same_engine
from .postgres_source import PostgresSourceAdapter
from .postgres_target import PostgresTargetAdapter


def get_target_adapter(engine_type: str, **kwargs) -> TargetAdapter:
    """Resolve a `TargetAdapter` from an engine-type string.

    `engine_type` comes from engine-level config, not code, so re-pointing the
    target at a different engine is a configuration change.
    """
    key = (engine_type or "").strip().lower()
    if key in ("mysql", "mariadb"):
        return MySQLTargetAdapter(**kwargs)
    if key in ("postgres", "postgresql", "pg"):
        return PostgresTargetAdapter(**kwargs)
    raise ValueError(f"unknown target engine_type: {engine_type!r}")


def get_source_adapter(engine_type: str, **kwargs) -> SourceAdapter:
    """Resolve a `SourceAdapter` from an engine-type string."""
    key = (engine_type or "").strip().lower()
    if key in ("postgres", "postgresql", "pg"):
        return PostgresSourceAdapter(**kwargs)
    raise ValueError(f"unknown source engine_type: {engine_type!r}")


__all__ = [
    "SourceAdapter",
    "TargetAdapter",
    "MySQLTargetAdapter",
    "PostgresTargetAdapter",
    "PostgresSourceAdapter",
    "PassthroughAdapter",
    "same_engine",
    "get_target_adapter",
    "get_source_adapter",
]
