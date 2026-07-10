"""Write one source row across several LRMIS tables (Path B, Phase 3).

Rules this module enforces, decided against the real LRMIS schema:

* **Parents before children.** Target tables are ordered by the registry's
  FK-aware topological sort, so a child's FK column can be filled from the id
  its parent just produced.
* **Reference tables are read-only.** `station` and `psgc` have no
  AUTO_INCREMENT primary key, so MySQL cannot generate their ids. The writer
  resolves an existing row and reuses its id; it never inserts one. If no row
  matches, the record is rejected rather than silently pointing a foreign key
  at id 0.
* **Never TRUNCATE.** A refresh deletes only rows this pipeline recorded in
  `integration.id_crosswalk`, children first, so LRMIS-owned and seeded
  reference rows survive.
* **Idempotent.** The crosswalk keys `(external_reference, target_table)` to a
  target id, so re-delivering an event updates the same rows instead of
  duplicating them.

The writer is deliberately free of connection management: callers pass an open
MySQL connection and an open central (PostgreSQL) connection.
"""
from __future__ import annotations

from .connectors import safe_identifier
from .lrmis_registry import LrmisRegistry, get_registry


class WriterError(RuntimeError):
    """Base class for multi-table write failures."""


class ReferenceRowNotFound(WriterError):
    """A read-only reference row (station/psgc) could not be resolved."""


class UnknownTargetTable(WriterError):
    """A mapping names a table that is not part of the LRMIS schema."""


def group_by_table(mappings: list[dict]) -> dict[str, list[dict]]:
    """Group column mappings by their `target_table`."""
    groups: dict[str, list[dict]] = {}
    for m in mappings:
        table = m.get("target_table")
        if not table:
            continue
        groups.setdefault(table, []).append(m)
    return groups


def _quote(name: str) -> str:
    return f"`{safe_identifier(name)}`"


def _reg(registry: LrmisRegistry | None) -> LrmisRegistry:
    return registry or get_registry()


# ---------------------------------------------------------------------------
# Crosswalk (central PostgreSQL)
# ---------------------------------------------------------------------------

def _crosswalk_lookup(central_conn, source_system: str, source_entity: str,
                      external_reference: str, target_system: str,
                      target_table: str) -> str | None:
    with central_conn.cursor() as cur:
        cur.execute("""
            SELECT target_id FROM integration.id_crosswalk
            WHERE source_system = %s AND source_entity = %s
              AND external_reference = %s AND target_system = %s
              AND target_table = %s
        """, (source_system, source_entity, external_reference,
              target_system, target_table))
        row = cur.fetchone()
    return row[0] if row else None


def _crosswalk_record(central_conn, source_system: str, source_entity: str,
                      external_reference: str, target_system: str,
                      target_table: str, target_id, event_id=None) -> None:
    with central_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.id_crosswalk
                (source_system, source_entity, external_reference, target_system,
                 target_table, target_id, last_event_id, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (source_system, source_entity, external_reference,
                         target_system, target_table)
            DO UPDATE SET target_id = EXCLUDED.target_id,
                          last_event_id = EXCLUDED.last_event_id,
                          updated_at = now()
        """, (source_system, source_entity, external_reference, target_system,
              target_table, str(target_id), event_id))


def crosswalk_rows_for_entity(central_conn, source_entity: str,
                              target_system: str) -> list[tuple[str, str]]:
    """[(target_table, target_id)] recorded for an entity."""
    with central_conn.cursor() as cur:
        cur.execute("""
            SELECT target_table, target_id FROM integration.id_crosswalk
            WHERE source_entity = %s AND target_system = %s
        """, (source_entity, target_system))
        return [(r[0], r[1]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Reference tables (read-only)
# ---------------------------------------------------------------------------

def resolve_reference_id(mysql_conn, table: str, values: dict,
                         registry: LrmisRegistry | None = None):
    """Find an existing row's primary key; never insert.

    If the mapping already supplies the primary key, verify it exists.
    Otherwise match on the supplied non-null columns as a natural key.
    """
    registry = _reg(registry)
    meta = registry.get_table(table)
    pk = meta.primary_key[0] if meta.primary_key else None
    if pk is None:
        raise ReferenceRowNotFound(f"{table} has no primary key to resolve")

    if values.get(pk) is not None:
        with mysql_conn.cursor() as cur:
            cur.execute(
                f"SELECT {_quote(pk)} FROM {_quote(table)} WHERE {_quote(pk)} = %s LIMIT 1",
                (values[pk],))
            row = cur.fetchone()
        if row:
            return row[0]
        raise ReferenceRowNotFound(
            f"{table}.{pk}={values[pk]!r} does not exist; "
            f"{table} is a read-only reference table")

    criteria = {k: v for k, v in values.items() if v is not None and k != pk}
    if not criteria:
        raise ReferenceRowNotFound(
            f"cannot resolve a row in reference table {table}: no lookup values mapped")
    where = " AND ".join(f"{_quote(c)} = %s" for c in criteria)
    with mysql_conn.cursor() as cur:
        cur.execute(
            f"SELECT {_quote(pk)} FROM {_quote(table)} WHERE {where} LIMIT 1",
            tuple(criteria.values()))
        row = cur.fetchone()
    if not row:
        raise ReferenceRowNotFound(
            f"no row in reference table {table} matches {criteria}; "
            f"the pipeline never inserts into {table}")
    return row[0]


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def _apply_foreign_keys(registry: LrmisRegistry, table: str, values: dict,
                        target_ids: dict) -> dict:
    """Fill FK columns from parents written earlier in this row's fan-out."""
    resolved = dict(values)
    for fk in registry.foreign_keys(table):
        if fk.ref_table == table:
            continue  # self-reference: never auto-filled
        parent_id = target_ids.get(fk.ref_table)
        if parent_id is not None and resolved.get(fk.column) is None:
            resolved[fk.column] = parent_id
    return resolved


def _insert(mysql_conn, table: str, values: dict):
    # NULLs are kept: an explicit NULL is a meaningful value for a nullable column.
    cols = list(values)
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (f"INSERT INTO {_quote(table)} "
           f"({', '.join(_quote(c) for c in cols)}) VALUES ({placeholders})")
    with mysql_conn.cursor() as cur:
        cur.execute(sql, tuple(values[c] for c in cols))
        return cur.lastrowid


def _update(mysql_conn, table: str, pk: str, pk_value, values: dict) -> None:
    assignable = {k: v for k, v in values.items() if k != pk}
    if not assignable:
        return
    sets = ", ".join(f"{_quote(c)} = %s" for c in assignable)
    sql = f"UPDATE {_quote(table)} SET {sets} WHERE {_quote(pk)} = %s"
    with mysql_conn.cursor() as cur:
        cur.execute(sql, (*assignable.values(), pk_value))


def write_source_row(mysql_conn, central_conn, *, source_entity: str,
                     external_reference: str, values_by_table: dict[str, dict],
                     source_system: str = "IRIMSV_REGION_V",
                     target_system: str = "LRMIS", event_id=None,
                     registry: LrmisRegistry | None = None) -> dict[str, object]:
    """Write one source row across N LRMIS tables; return {table: target_id}."""
    registry = _reg(registry)

    for table in values_by_table:
        if not registry.has_table(table):
            raise UnknownTargetTable(f"{table!r} is not part of the LRMIS schema")

    order = registry.topological_order(list(values_by_table))
    target_ids: dict[str, object] = {}

    for table in order:
        values = _apply_foreign_keys(registry, table, values_by_table[table], target_ids)

        if registry.is_reference_table(table):
            target_ids[table] = resolve_reference_id(mysql_conn, table, values, registry)
            continue

        meta = registry.get_table(table)
        pk = meta.primary_key[0] if meta.primary_key else None
        existing = _crosswalk_lookup(central_conn, source_system, source_entity,
                                     external_reference, target_system, table)
        if existing is not None and pk:
            _update(mysql_conn, table, pk, existing, values)
            target_id: object = existing
        else:
            target_id = _insert(mysql_conn, table, values)
            _crosswalk_record(central_conn, source_system, source_entity,
                              external_reference, target_system, table,
                              target_id, event_id)
        target_ids[table] = target_id

    return target_ids


# ---------------------------------------------------------------------------
# Refresh support: delete only what we wrote, children first
# ---------------------------------------------------------------------------

def delete_entity_rows(mysql_conn, central_conn, *, source_entity: str,
                       target_system: str = "LRMIS",
                       registry: LrmisRegistry | None = None) -> dict[str, int]:
    """Remove pipeline-written rows for an entity. Never truncates.

    Reference tables are skipped: the pipeline never wrote them.
    """
    registry = _reg(registry)
    rows = crosswalk_rows_for_entity(central_conn, source_entity, target_system)
    if not rows:
        return {}

    by_table: dict[str, list] = {}
    for table, target_id in rows:
        if registry.has_table(table) and registry.is_reference_table(table):
            continue
        by_table.setdefault(table, []).append(target_id)

    deleted: dict[str, int] = {}
    # children first: reverse the parent-first ordering
    for table in reversed(registry.topological_order(list(by_table))):
        ids = by_table[table]
        meta = registry.get_table(table)
        pk = meta.primary_key[0] if meta.primary_key else None
        if not pk:
            continue
        placeholders = ", ".join(["%s"] * len(ids))
        with mysql_conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {_quote(table)} WHERE {_quote(pk)} IN ({placeholders})",
                tuple(ids))
            deleted[table] = cur.rowcount

    with central_conn.cursor() as cur:
        cur.execute("""
            DELETE FROM integration.id_crosswalk
            WHERE source_entity = %s AND target_system = %s AND target_table = ANY(%s)
        """, (source_entity, target_system, list(by_table)))

    return deleted
