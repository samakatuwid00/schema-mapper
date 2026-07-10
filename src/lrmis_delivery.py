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
from datetime import datetime, timezone

from .lrmis_registry import get_registry
from .lrmis_writer import delete_entity_rows, write_source_row
from .transform_engine import _apply_transform


def build_values_by_table(source_row: dict, mappings: list[dict]) -> tuple[dict, list[str]]:
    """Group transformed source values by their target table.

    Returns ({table: {target_column: value}}, errors). A transform failure is
    collected as an error rather than raised, mirroring transform_row so the
    caller can quarantine the event.
    """
    values_by_table: dict[str, dict] = {}
    errors: list[str] = []
    for m in mappings:
        target_table = m.get("target_table")
        target_col = m.get("target_column")
        source_col = m.get("source_column")
        if not target_table or not target_col:
            continue                      # rejected / ignored column
        if source_col not in source_row:
            continue
        value = source_row[source_col]
        try:
            value = _apply_transform(value, m.get("transform", "none"))
        except Exception as exc:
            errors.append(f"transform failed for {source_col} -> {target_table}.{target_col}: {exc}")
            continue
        values_by_table.setdefault(target_table, {})[target_col] = value
    return values_by_table, errors


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
                  target_system: str = "LRMIS", registry=None) -> dict:
    """Deliver one outbox event across the LRMIS tables. Returns a result dict.

    A 'deactivate' operation is not a delete: rows stay, and the delivery_audit
    envelope's `active` flag is set false, matching the project's rule that
    target rows are never destroyed.
    """
    registry = registry or get_registry()
    payload = event["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    values_by_table, errors = build_values_by_table(payload, mappings)
    if errors:
        return {"status": "error", "errors": errors}
    if not values_by_table:
        return {"status": "error", "errors": ["mapping produced no target values"]}

    active = event.get("operation") != "deactivate"
    target_ids = write_source_row(
        target_conn, central_conn,
        source_entity=entity_name,
        external_reference=str(event["external_reference"]),
        values_by_table=values_by_table,
        source_system=source_system, target_system=target_system,
        event_id=event.get("event_id"), registry=registry,
    )
    _record_delivery_audit(target_conn, event, active)
    return {"status": "delivered", "target_ids": target_ids, "active": active}


def refresh_entity(target_conn, central_conn, *, entity_name: str,
                   mappings: list[dict], source_rows: list[dict],
                   external_reference_of, source_system: str = "IRIMSV_REGION_V",
                   target_system: str = "LRMIS", registry=None) -> dict:
    """Rebuild an entity's LRMIS rows from the current source rows.

    Deletes only the rows this pipeline previously wrote (crosswalk-scoped,
    never TRUNCATE), then re-delivers every source row. `external_reference_of`
    maps a source row to its external_reference UUID (the caller owns the
    deterministic-UUID logic, as the backfill/trigger paths already do).
    """
    registry = registry or get_registry()
    deleted = delete_entity_rows(target_conn, central_conn,
                                 source_entity=entity_name, target_system=target_system,
                                 registry=registry)
    written, failed = 0, []
    for row in source_rows:
        ext_ref = external_reference_of(row)
        values_by_table, errors = build_values_by_table(row, mappings)
        if errors or not values_by_table:
            failed.append({"external_reference": str(ext_ref),
                           "errors": errors or ["no target values"]})
            continue
        write_source_row(
            target_conn, central_conn,
            source_entity=entity_name, external_reference=str(ext_ref),
            values_by_table=values_by_table,
            source_system=source_system, target_system=target_system, registry=registry,
        )
        written += 1
    return {"entity": entity_name, "deleted": deleted, "written": written,
            "failed": failed, "source_rows": len(source_rows)}


# ---------------------------------------------------------------------------
# Loading an entity's multi-table mappings from its approved proposal
# ---------------------------------------------------------------------------

def is_path_b_entity(central_conn, source_entity: str, target_system: str = "LRMIS") -> bool:
    """True when the entity has been onboarded to the LRMIS target (its
    lrmis_target_tables footprint is set). Legacy entities return False and
    stay on the single-table staging path."""
    with central_conn.cursor() as cur:
        cur.execute("""
            SELECT lrmis_target_tables IS NOT NULL
            FROM integration.onboarding_entity
            WHERE source_table = %s AND target_system = %s
            ORDER BY updated_at DESC LIMIT 1
        """, (source_entity, target_system))
        row = cur.fetchone()
    return bool(row and row[0])


def load_entity_mappings(central_conn, source_entity: str,
                         target_system: str = "LRMIS") -> list[dict]:
    """The accepted/resolved column mappings for a Path B entity, as
    {source_column, target_table, target_column, transform} dicts."""
    import psycopg2.extras
    with central_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT r.source_column, r.suggested_target_table,
                   r.suggested_target_column, r.resolved_target_column,
                   r.transform, r.resolved_transform
            FROM integration.onboarding_field_review r
            JOIN integration.onboarding_proposal p ON p.id = r.proposal_id
            JOIN integration.onboarding_entity e ON e.id = p.entity_id
            WHERE e.source_table = %s AND e.target_system = %s
              AND p.status IN ('approved', 'auto_approved')
              AND r.status IN ('accepted', 'resolved')
            ORDER BY p.id DESC
        """, (source_entity, target_system))
        rows = cur.fetchall()

    mappings = []
    for r in rows:
        target_col = r["resolved_target_column"] or r["suggested_target_column"]
        if not target_col or not r["suggested_target_table"]:
            continue
        mappings.append({
            "source_column": r["source_column"],
            "target_table": r["suggested_target_table"],
            "target_column": target_col,
            "transform": r["resolved_transform"] or r["transform"] or "none",
        })
    return mappings
