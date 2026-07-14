"""Path B delivery: fan a source row out into the real LRMIS tables (Phases 4-5).

Sits between the outbox worker / refresh and the multi-table writer. It turns a
source payload plus the entity's column mappings into per-table value dicts
(applying the same transforms the legacy single-table path uses), writes them
through `lrmis_writer.write_source_row`, and records a delivery_audit envelope
in the target database.

This is the NEW delivery path. It only runs for entities that have been
onboarded to the LRMIS target (onboarding_entity.lrmis_target_tables is set).
Legacy entities keep the untouched single-table staging path in worker.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from .lrmis_registry import get_registry
from .lrmis_writer import delete_entity_rows, write_source_row
from .transform_engine import _apply_transform

logger = logging.getLogger(__name__)

# Pulls the declared width out of a target column's data_type, e.g. varchar(75).
_VARCHAR_LEN = re.compile(r"(?:var)?char\s*\(\s*(\d+)\s*\)", re.IGNORECASE)


def _max_varchar_len(registry, table: str, column: str) -> int | None:
    """The declared width of a target CHAR/VARCHAR column, or None if the column
    is unknown or not width-bounded. Used to coerce an over-length source string
    to the target width rather than fail the whole entity's delivery."""
    if registry is None or not hasattr(registry, "has_table") or not registry.has_table(table):
        return None
    col = registry.get_table(table).get_column(column)
    if col is None:
        return None
    m = _VARCHAR_LEN.search(col.data_type or "")
    return int(m.group(1)) if m else None


class _LegacyWriter:
    """Adapts the module-level MySQL writer functions to the writer interface
    (`write_row` / `delete_entity_rows` / `row_exists`) so the delivery path can
    be driven by either it (default, MySQL target) or a `delivery.GenericWriter`
    (any dialect). The default keeps the existing behaviour byte-for-byte."""

    def __init__(self, registry):
        self.registry = registry

    def write_row(self, target_conn, central_conn, **kw):
        return write_source_row(target_conn, central_conn, registry=self.registry, **kw)

    def delete_entity_rows(self, target_conn, central_conn, *, source_entity,
                           target_system="LRMIS"):
        return delete_entity_rows(target_conn, central_conn, source_entity=source_entity,
                                  target_system=target_system, registry=self.registry)

    def row_exists(self, target_conn, table, target_id):
        return _target_row_exists(target_conn, self.registry, table, target_id)

    def record_delivery_audit(self, target_conn, event, active):
        _record_delivery_audit(target_conn, event, active)


# The source schema every entity is read from (used to recompute a referenced
# row's deterministic external_reference for crosswalk-based FK resolution).
SOURCE_SCHEMA = os.environ.get("SOURCE_SCHEMA", "irimsv")


def _writer_managed_columns(registry, table: str) -> set[str]:
    """Columns the multi-table writer fills itself — the auto-increment primary
    key and every foreign key. A source mapping onto one of these fights the
    writer's own (correct) value and almost always mismatches the type — e.g. a
    source UUID cast into an integer FK — so such mappings must be ignored."""
    if registry is None or not hasattr(registry, "has_table") or not registry.has_table(table):
        return set()
    meta = registry.get_table(table)
    managed = {fk.column for fk in meta.foreign_keys}
    if meta.auto_increment_column:
        managed.add(meta.auto_increment_column)
    return managed


def build_values_by_table(source_row: dict, mappings: list[dict],
                          registry=None) -> tuple[dict, list[str]]:
    """Group transformed source values by their target table.

    Returns ({table: {target_column: value}}, errors). A transform failure is
    collected as an error rather than raised, mirroring transform_row so the
    caller can quarantine the event. Mappings onto writer-managed PK/FK columns
    are skipped — the writer generates/propagates those.
    """
    values_by_table: dict[str, dict] = {}
    errors: list[str] = []
    managed_cache: dict[str, set[str]] = {}
    for m in mappings:
        target_table = m.get("target_table")
        target_col = m.get("target_column")
        source_col = m.get("source_column")
        if not target_table or not target_col:
            continue                      # rejected / ignored column
        if source_col not in source_row:
            continue
        managed = managed_cache.get(target_table)
        if managed is None:
            managed = _writer_managed_columns(registry, target_table)
            managed_cache[target_table] = managed
        if target_col in managed:
            continue                      # PK/FK — the writer fills this itself
        value = source_row[source_col]
        try:
            value = _apply_transform(value, m.get("transform", "none"))
        except Exception as exc:
            errors.append(f"transform failed for {source_col} -> {target_table}.{target_col}: {exc}")
            continue
        if isinstance(value, str):
            width = _max_varchar_len(registry, target_table, target_col)
            if width is not None and len(value) > width:
                logger.warning("truncating %s.%s: source value %d chars > column width %d",
                               target_table, target_col, len(value), width)
                value = value[:width]
        values_by_table.setdefault(target_table, {})[target_col] = value
    return values_by_table, errors


# ---------------------------------------------------------------------------
# Cross-entity foreign-key resolution
#
# A source FK (e.g. users.usertype_id, a source UUID) points at *another*
# source entity that was migrated separately. build_values_by_table drops such
# columns (they are writer-managed), so the target FK lands null. Here we fill
# it: recompute the referenced row's deterministic external_reference and read
# the target id straight out of id_crosswalk. The referenced entity is not
# known up front, so we probe every source entity that already has a crosswalk
# entry for the FK's target table — whichever key matches wins. No match leaves
# the column null and is reported, never guessed.
# ---------------------------------------------------------------------------

def _candidate_entities_for(central_conn, ref_table: str, target_system: str) -> list[str]:
    with central_conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT source_entity FROM integration.id_crosswalk
            WHERE target_table = %s AND target_system = %s AND target_id IS NOT NULL
        """, (ref_table, target_system))
        return [r[0] for r in cur.fetchall()]


def _crosswalk_target_id(central_conn, source_entity: str, external_reference: str,
                         target_system: str, target_table: str):
    with central_conn.cursor() as cur:
        cur.execute("""
            SELECT target_id FROM integration.id_crosswalk
            WHERE source_entity = %s AND external_reference = %s
              AND target_system = %s AND target_table = %s AND target_id IS NOT NULL
            LIMIT 1
        """, (source_entity, external_reference, target_system, target_table))
        row = cur.fetchone()
        return row[0] if row else None


def _target_row_exists(target_conn, registry, table: str, target_id) -> bool:
    """True if a row with this id still lives in the target table. Guards against
    a stale crosswalk entry pointing at an id that was since removed — injecting
    it would only trip the FK constraint. With no target_conn we cannot check, so
    trust the crosswalk (best-effort)."""
    if target_conn is None:
        return True
    meta = registry.get_table(table)
    pk = meta.primary_key[0] if meta.primary_key else None
    if not pk:
        return True
    with target_conn.cursor() as cur:
        cur.execute(f"SELECT 1 FROM `{table}` WHERE `{pk}` = %s LIMIT 1", (target_id,))
        return cur.fetchone() is not None


def resolve_cross_entity_fks(central_conn, source_row: dict, mappings: list[dict],
                             values_by_table: dict, registry, *,
                             target_conn=None, writer=None,
                             source_system: str = "IRIMSV_REGION_V",
                             source_schema: str | None = None,
                             target_system: str = "LRMIS") -> list[dict]:
    """Fill target FK columns by resolving their source value through the
    referenced entity's crosswalk. Mutates values_by_table in place and returns
    a list of unresolved refs: {target_table, target_column, source_column,
    source_value}. A value is injected only when it maps to a target row that
    still exists, so it always satisfies the FK constraint; anything else is
    left null and reported, never guessed."""
    if registry is None or not hasattr(registry, "has_table"):
        return []
    from .pipeline import generate_external_reference
    schema = source_schema or SOURCE_SCHEMA
    unresolved: list[dict] = []
    candidate_cache: dict[str, list[str]] = {}
    for m in mappings:
        table = m.get("target_table")
        col = m.get("target_column")
        scol = m.get("source_column")
        if not table or not col or scol not in source_row:
            continue
        if not registry.has_table(table):
            continue
        meta = registry.get_table(table)
        fk = next((f for f in meta.foreign_keys if f.column == col), None)
        if fk is None:
            continue                      # target column is not a foreign key
        src_val = source_row.get(scol)
        if src_val is None:
            continue                      # nothing to resolve
        ref_table = fk.ref_table
        candidates = candidate_cache.get(ref_table)
        if candidates is None:
            candidates = _candidate_entities_for(central_conn, ref_table, target_system)
            candidate_cache[ref_table] = candidates
        hit = None
        for ent in candidates:
            key = str(generate_external_reference(source_system, schema, ent, [src_val]))
            tid = _crosswalk_target_id(central_conn, ent, key, target_system, ref_table)
            if tid is None:
                continue
            exists = (writer.row_exists(target_conn, ref_table, tid) if writer
                      else _target_row_exists(target_conn, registry, ref_table, tid))
            if exists:
                hit = tid
                break
        if hit is not None:
            values_by_table.setdefault(table, {})[col] = hit
        else:
            unresolved.append({"target_table": table, "target_column": col,
                               "source_column": scol, "source_value": str(src_val)})
    return unresolved


def _record_delivery_audit(target_conn, event: dict, active: bool) -> None:
    """Write the envelope row for this event into lrmis_target.delivery_audit."""
    updated = event.get("source_updated_at")
    if isinstance(updated, datetime):
        updated = updated.astimezone(timezone.utc).replace(tzinfo=None)
    with target_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO delivery_audit
                (event_id, external_reference, source_system, operation,
                 source_updated_at, mapping_version, payload_checksum, active, accepted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                external_reference = VALUES(external_reference),
                operation = VALUES(operation),
                source_updated_at = VALUES(source_updated_at),
                payload_checksum = VALUES(payload_checksum),
                active = VALUES(active),
                accepted_at = VALUES(accepted_at)
        """, (
            str(event["event_id"]),
            str(event["external_reference"]),
            event.get("source_system"),
            event.get("operation"),
            updated,
            event.get("mapping_version"),
            event.get("payload_checksum"),
            1 if active else 0,
            datetime.now(timezone.utc).replace(tzinfo=None),
        ))


def deliver_event(target_conn, central_conn, *, entity_name: str, event: dict,
                  mappings: list[dict], source_system: str = "IRIMSV_REGION_V",
                  target_system: str = "LRMIS", registry=None, writer=None) -> dict:
    """Deliver one outbox event across the LRMIS tables. Returns a result dict.

    A 'deactivate' operation is not a delete: rows stay, and the delivery_audit
    envelope's `active` flag is set false, matching the project's rule that
    target rows are never destroyed.

    `writer` selects the target engine (default `_LegacyWriter` = MySQL; a
    `delivery.GenericWriter` delivers into a Postgres/other target), so the live
    streaming path is no longer MySQL-only.
    """
    registry = registry or get_registry()
    writer = writer or _LegacyWriter(registry)
    payload = event["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    values_by_table, errors = build_values_by_table(payload, mappings, registry)
    if errors:
        return {"status": "error", "errors": errors}
    if not values_by_table:
        return {"status": "error", "errors": ["mapping produced no target values"]}

    unresolved_fks = resolve_cross_entity_fks(
        central_conn, payload, mappings, values_by_table, registry,
        target_conn=target_conn, writer=writer,
        source_system=source_system, target_system=target_system)

    active = event.get("operation") != "deactivate"
    target_ids = writer.write_row(
        target_conn, central_conn,
        source_entity=entity_name,
        external_reference=str(event["external_reference"]),
        values_by_table=values_by_table,
        source_system=source_system, target_system=target_system,
        event_id=event.get("event_id"),
    )
    writer.record_delivery_audit(target_conn, event, active)
    return {"status": "delivered", "target_ids": target_ids, "active": active,
            "unresolved_fks": unresolved_fks}


def refresh_entity(target_conn, central_conn, *, entity_name: str,
                   mappings: list[dict], source_rows: list[dict],
                   external_reference_of, source_system: str = "IRIMSV_REGION_V",
                   target_system: str = "LRMIS", registry=None, writer=None) -> dict:
    """Rebuild an entity's LRMIS rows from the current source rows.

    Deletes only the rows this pipeline previously wrote (crosswalk-scoped,
    never TRUNCATE), then re-delivers every source row. `external_reference_of`
    maps a source row to its external_reference UUID (the caller owns the
    deterministic-UUID logic, as the backfill/trigger paths already do).

    `writer` selects the target engine: the default `_LegacyWriter` writes into
    the MySQL target exactly as before; a `delivery.GenericWriter` writes into a
    Postgres (or any dialect) target.
    """
    registry = registry or get_registry()
    writer = writer or _LegacyWriter(registry)
    deleted = writer.delete_entity_rows(target_conn, central_conn,
                                        source_entity=entity_name,
                                        target_system=target_system)
    written, failed = 0, []
    unresolved_fk_count = 0
    for row in source_rows:
        ext_ref = external_reference_of(row)
        values_by_table, errors = build_values_by_table(row, mappings, registry)
        if errors or not values_by_table:
            failed.append({"external_reference": str(ext_ref),
                           "errors": errors or ["no target values"]})
            continue
        unresolved = resolve_cross_entity_fks(
            central_conn, row, mappings, values_by_table, registry,
            target_conn=target_conn, writer=writer,
            source_system=source_system, target_system=target_system)
        unresolved_fk_count += len(unresolved)
        writer.write_row(
            target_conn, central_conn,
            source_entity=entity_name, external_reference=str(ext_ref),
            values_by_table=values_by_table,
            source_system=source_system, target_system=target_system,
        )
        written += 1
    return {"entity": entity_name, "deleted": deleted, "written": written,
            "failed": failed, "source_rows": len(source_rows),
            "unresolved_fks": unresolved_fk_count}


# ---------------------------------------------------------------------------
# Loading an entity's multi-table mappings from its approved proposal
# ---------------------------------------------------------------------------

def load_entity_mappings(central_conn, source_entity: str,
                         target_system: str = "LRMIS") -> list[dict]:
    """The accepted/resolved column mappings for a Path B entity, as
    {source_column, target_table, target_column, transform} dicts."""
    import psycopg2.extras
    with central_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Scope to the entity's LATEST approved proposal only. Merging accepted
        # fields across every historical proposal would mix a stale legacy
        # staging mapping (e.g. irimsv_*_staging) into the current LRMIS-target
        # mapping and make the writer reject the whole event.
        cur.execute("""
            SELECT r.source_column, r.suggested_target_table,
                   r.suggested_target_column, r.resolved_target_column,
                   r.transform, r.resolved_transform
            FROM integration.onboarding_field_review r
            WHERE r.proposal_id = (
                    SELECT p.id
                    FROM integration.onboarding_proposal p
                    JOIN integration.onboarding_entity e ON e.id = p.entity_id
                    WHERE e.source_table = %s AND e.target_system = %s
                      AND p.status IN ('approved', 'auto_approved')
                      AND EXISTS (
                        SELECT 1 FROM integration.onboarding_field_review rr
                        WHERE rr.proposal_id = p.id
                          AND rr.status IN ('accepted', 'resolved')
                          AND rr.suggested_target_table IS NOT NULL
                          AND rr.suggested_target_table NOT LIKE 'irimsv_%%_staging'
                      )
                    ORDER BY p.id DESC
                    LIMIT 1
                  )
              AND r.status IN ('accepted', 'resolved')
        """, (source_entity, target_system))
        rows = cur.fetchall()

    mappings = []
    for r in rows:
        target_col = r["resolved_target_column"] or r["suggested_target_column"]
        table = r["suggested_target_table"]
        if not target_col or not table:
            continue
        if table.startswith("irimsv_") and table.endswith("_staging"):
            continue  # legacy staging leftover — not a valid LRMIS target, ignore
        mappings.append({
            "source_column": r["source_column"],
            "target_table": table,
            "target_column": target_col,
            "transform": r["resolved_transform"] or r["transform"] or "none",
        })
    return mappings
