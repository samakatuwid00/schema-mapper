"""Generic, dialect-aware delivery (generic engine, §7).

`GenericWriter` is the engine-agnostic successor to `lrmis_writer`: it writes one
source row across N target tables (parents-first, FK propagation, crosswalk
idempotency, app-assigned ids) using a `Dialect` for all SQL and for reading
back generated primary keys — so it delivers into a Postgres target as well as
MySQL. The engine-agnostic pieces (crosswalk, id allocation, FK propagation) are
reused from `lrmis_writer`; only the target SQL + generated-id retrieval are
generalised here.
"""
from __future__ import annotations

from .writer import GenericWriter

__all__ = ["GenericWriter"]
