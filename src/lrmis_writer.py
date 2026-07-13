"""Write one source row across several LRMIS tables (Path B, Phase 3).

Rules this module enforces, decided against the real LRMIS schema:

* **Parents before children.** Target tables are ordered by the registry's
  FK-aware topological sort, so a child's FK column can be filled from the id
  its parent just produced.
* **Reference tables are read-only.** `station` and `psgc` have no
  AUTO_INCREMENT primary key, so MySQL cannot generate their ids by itself.
  For a genuine external lookup table (`psgc` — the national PSGC geographic
  registry, which IRIMSV carries no form of) the writer resolves an existing
  row and never inserts one; an unmatched row is rejected rather than
  silently pointing a foreign key at id 0.
* **`station` is the one exception: writable with an app-assigned id.**
  Verified against real data: 0 of IRIMSV's 145 schools match an existing
  `station`/`beis` row by any natural key, so treating `station` as
  resolve-only would block onboarding entirely — IRIMSV is the authoritative
  source and almost every school is new to LRMIS. The writer first tries to
  join an existing station (a real match should never be duplicated); only
  when nothing matches does it allocate a new id from a reserved range
  (`integration.id_sequence`, starting at 10,000,000 — LRMIS's own ids are
  1..3921) and insert with that id explicit.
* **Never TRUNCATE.** A refresh deletes only rows this pipeline itself
  *inserted*, recorded in `integration.id_crosswalk`, children first, so
  LRMIS-owned and seeded reference rows survive. A `station` row the writer
  only matched (never inserted) is never deleted.
* **Idempotent.** The crosswalk keys `(external_reference, target_table)` to a
  target id, so re-delivering an event updates the same rows instead of
  duplicating them.

The writer is deliberately free of connection management: callers pass an open
MySQL connection and an open central (PostgreSQL) connection.
"""
from __future__ import annotations

from .connectors import safe_identifier
from .lrmis_registry import LrmisRegistry, get_registry
from .adapters.lrmis_plugin import LRMIS

# LRMIS domain config now lives in the plugin (§9); re-exported here so existing
# importers keep working. `APP_ASSIGNED_ID_TABLES` are no-AUTO_INCREMENT tables
# the pipeline may still CREATE rows in (an id is allocated from a reserved
# range starting at `DEFAULT_ID_SEQUENCE_START`); every other no-AUTO_INCREMENT
# table stays resolve-only (see resolve_reference_id).
APP_ASSIGNED_ID_TABLES = LRMIS.app_assigned_id_tables
DEFAULT_ID_SEQUENCE_START = LRMIS.id_sequence_start


class WriterError(RuntimeError):
    """Base class for multi-table write failures."""


class ReferenceRowNotFound(WriterError):
    """A read-only reference row (e.g. psgc) could not be resolved."""


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


def allocate_id(central_conn, table: str, start_at: int = DEFAULT_ID_SEQUENCE_START) -> int:
    """Atomically hand out the next app-assigned id for `table`.

    A single INSERT .. ON CONFLICT .. RETURNING statement, so two concurrent
    callers (a worker batch and a bulk onboard, say) can never be handed the
    same id — Postgres serializes the conflicting upsert at the row level.
    `start_at` only takes effect the first time a table is allocated for;
    later calls always continue from the stored next_value.
    """
    with central_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.id_sequence (table_name, next_value)
            VALUES (%s, %s)
            ON CONFLICT (table_name) DO UPDATE
                SET next_value = integration.id_sequence.next_value + 1
            RETURNING next_value
        """, (table, start_at))
        return cur.fetchone()[0]


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


def _write_app_assigned_row(mysql_conn, central_conn, table: str, values: dict,
                            registry: LrmisRegistry, *, source_system: str,
                            source_entity: str, external_reference: str,
                            target_system: str, event_id=None) -> object:
    """Write path for APP_ASSIGNED_ID_TABLES (today: only `station`)."""
    meta = registry.get_table(table)
    pk = meta.primary_key[0]

    existing = _crosswalk_lookup(central_conn, source_system, source_entity,
                                 external_reference, target_system, table)
    if existing is not None:
        _update(mysql_conn, table, pk, existing, values)
        return existing

    # A real match must never be duplicated: two IRIMSV schools resolving to
    # the same physical station is exactly the case natural-key matching
    # exists to catch, before minting a second id for the same place.
    try:
        matched = resolve_reference_id(mysql_conn, table, values, registry)
    except ReferenceRowNotFound:
        matched = None
    if matched is not None:
        _crosswalk_record(central_conn, source_system, source_entity,
                          external_reference, target_system, table,
                          matched, event_id)
        return matched

    new_id = allocate_id(central_conn, table)
    _insert(mysql_conn, table, {**values, pk: new_id})
    _crosswalk_record(central_conn, source_system, source_entity,
                      external_reference, target_system, table, new_id, event_id)
    return new_id


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

        if table in APP_ASSIGNED_ID_TABLES:
            target_ids[table] = _write_app_assigned_row(
                mysql_conn, central_conn, table, values, registry,
                source_system=source_system, source_entity=source_entity,
                external_reference=external_reference, target_system=target_system,
                event_id=event_id)
            continue

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

    Tables `registry.is_reference_table()` flags are skipped entirely,
    including `station`. For `psgc` that's exactly right: the pipeline never
    wrote it. For `station` it is deliberately conservative rather than
    correct: a station this pipeline *created* (app-assigned id, no natural
    match) is safe to remove, but one it only *matched* (a real pre-existing
    LRMIS station) must never be deleted, and the crosswalk alone can't tell
    the two apart cheaply. Until that distinction is added (e.g. by only
    deleting station ids >= DEFAULT_ID_SEQUENCE_START), any station rows a
    refresh creates are left behind on delete rather than risk deleting a
    station this pipeline doesn't own.
    """
    registry = _reg(registry)
    rows = crosswalk_rows_for_entity(central_conn, source_entity, target_system)
    if not rows:
        return {}

    by_table: dict[str, list] = {}
    for table, target_id in rows:
        if not registry.has_table(table):
            continue  # legacy staging crosswalk leftover (e.g. irimsv_*_staging):
                      # not an LRMIS table this Path B delete owns — leave it be
        if registry.is_reference_table(table):
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
