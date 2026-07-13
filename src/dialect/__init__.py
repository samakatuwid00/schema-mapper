"""Generic type system + dialect-aware SQL (generic engine, §4).

The writer and refresh path are engine-agnostic: they ask the target adapter for
its `Dialect` and generate SQL through it, instead of hardcoding MySQL backticks
/ `ON DUPLICATE KEY UPDATE` or Postgres `ON CONFLICT` / `gen_random_uuid()`.
This is the foundation the generic writer (§7) builds on to deliver into a
Postgres target.
"""
from __future__ import annotations

from .types import GenericType, native_to_generic, native_to_generic_any, cast_value
from .builder import (
    Dialect, MySQLDialect, PostgresDialect, MSSQLDialect, get_dialect,
)

__all__ = [
    "GenericType",
    "native_to_generic",
    "native_to_generic_any",
    "cast_value",
    "Dialect",
    "MySQLDialect",
    "PostgresDialect",
    "MSSQLDialect",
    "get_dialect",
]
