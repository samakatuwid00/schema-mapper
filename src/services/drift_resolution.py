"""Automated resolution of schema drift.

When ``ops.monitor`` detects that an entity's source or target schema no longer
matches its stored fingerprint, it pauses the entity and records a drift report.
The only resolution path until now was manual: an admin refreshed each entity,
then approved the new fingerprint. This module chains those steps into a single
coordinated workflow — re-scan, refresh the entity into the target, update the
stored fingerprint, clear the pause, re-enable delivery, and mark the report
resolved.

It reuses ``ops.refresh`` (real-target redelivery) and
``ops._entity_fingerprints`` (per-entity source/target fingerprints) rather than
reimplementing them.
"""
from __future__ import annotations

import json

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from . import ops as ops_service
from .ops import _entity_fingerprints


def _pipeline():
    from .. import pipeline
    return pipeline


# Which onboarding_entity column each direction owns, and the substring that
# ops.monitor writes into paused_reason for that direction. The reason format is
# ``Schema drift detected: source=<bool>, target=<bool>`` (ops.monitor).
_FP_COLUMN = {"source": "source_fingerprint", "target": "target_fingerprint"}
_MARKER = {"source": "source=True", "target": "target=True"}


def _drifted_entities(p, conn, kind: str, entities: list[str] | None) -> list[dict]:
    """Paused entities whose pause reason marks this direction as drifted."""
    sql = [
        "SELECT * FROM integration.onboarding_entity",
        "WHERE status = 'paused'",
        "  AND paused_reason LIKE 'Schema drift detected:%%'",
        "  AND paused_reason LIKE %s",
    ]
    params: list = [f"%{_MARKER[kind]}%"]
    if entities:
        sql.append("  AND source_table = ANY(%s)")
        params.append(list(entities))
    sql.append("ORDER BY source_schema, source_table")
    return p._query(conn, "\n".join(sql), tuple(params))


def _mark_reports_resolved(p, conn, actor: str) -> int:
    """Stamp resolved_at on drift reports whose impacted entities are all cleared."""
    return p._execute(conn, """
        UPDATE integration.schema_drift_report r
        SET resolved_at = now(), resolved_by = %s
        WHERE r.resolved_at IS NULL
          AND cardinality(r.impacted_entities) > 0
          AND NOT EXISTS (
              SELECT 1 FROM integration.onboarding_entity e
              WHERE e.source_table = ANY(r.impacted_entities)
                AND e.status = 'paused'
          )
    """, (actor,))


def _resolve(kind: str, entities: list[str] | None, actor: str, dry_run: bool,
             source_system: str, batch_size: int,
             central: PostgresCentralConnector | None,
             target: MySQLStagingConnector | None, progress) -> dict:
    """Resolve drift for one direction (``source`` or ``target``).

    Live mode, per entity: refresh the entity into the target (crosswalk-scoped
    delete + rewrite), recompute the fingerprint from the post-refresh state,
    store it, clear the pause on both ``onboarding_entity`` and
    ``entity_control``, and audit. Fingerprints are written only after the
    target is refreshed and committed, so a stored fingerprint never outruns the
    data it describes.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    fp_col = _FP_COLUMN[kind]
    resolved, skipped, plan = [], [], []
    try:
        with central.connection() as conn:
            targets = _drifted_entities(p, conn, kind, entities)
        total = len(targets)
        for i, entity in enumerate(targets):
            table = entity["source_table"]
            if progress:
                progress(i, total, f"resolving {kind} drift: {table}")
            old_fp = entity.get(fp_col)

            if dry_run:
                with central.connection() as conn:
                    new_source_fp, new_target_fp = _entity_fingerprints(conn, target, entity)
                new_fp = new_source_fp if kind == "source" else new_target_fp
                plan.append({
                    "entity": table,
                    "kind": kind,
                    "current_fingerprint": old_fp,
                    "new_fingerprint": new_fp,
                    "changed": bool(old_fp and new_fp and old_fp != new_fp),
                    "action": ("refresh target, update fingerprint, re-enable"
                               if new_fp else "skip - source/target object missing"),
                })
                continue

            refresh_result = ops_service.refresh(
                entity["source_schema"], [table], entity["target_system"],
                source_system=source_system, batch_size=batch_size,
                central=central)
            row = (refresh_result.get("results") or [{}])[0]
            if row.get("status") != "refreshed":
                skipped.append({"entity": table,
                                "reason": row.get("error", "refresh did not run")})
                continue

            with central.connection() as conn:
                new_source_fp, new_target_fp = _entity_fingerprints(conn, target, entity)
                new_fp = new_source_fp if kind == "source" else new_target_fp
                p._execute(conn, f"""
                    UPDATE integration.onboarding_entity
                    SET {fp_col} = %s, status = 'deployed',
                        paused_reason = NULL, updated_at = now()
                    WHERE id = %s
                """, (new_fp, entity["id"]))
                p._execute(conn, """
                    UPDATE integration.entity_control
                    SET enabled = true, paused_reason = NULL, updated_at = now()
                    WHERE source_entity = %s AND target_system = %s
                """, (table, entity["target_system"]))
                p._execute(conn, """
                    INSERT INTO integration.onboarding_audit
                        (entity_id, action, details, performed_by)
                    VALUES (%s, 'drift_resolved', %s, %s)
                """, (entity["id"], json.dumps({
                    "kind": kind,
                    "old_fingerprint": old_fp,
                    "new_fingerprint": new_fp,
                    "target_tables": entity.get("lrmis_target_tables"),
                    "rows_loaded": row.get("rows_loaded"),
                }), actor))
                conn.commit()
            resolved.append({"entity": table, "kind": kind, "old_fingerprint": old_fp,
                             "new_fingerprint": new_fp, "rows_loaded": row.get("rows_loaded")})

        reports_marked = 0
        if not dry_run:
            with central.connection() as conn:
                reports_marked = _mark_reports_resolved(p, conn, actor) or 0
                conn.commit()
            if progress:
                progress(total, total, f"{kind} drift resolution complete")

        return {"kind": kind, "dry_run": dry_run, "resolved": resolved,
                "skipped": skipped, "plan": plan,
                "resolved_count": len(resolved), "skipped_count": len(skipped),
                "reports_marked_resolved": reports_marked}
    finally:
        if owns:
            central.close()


def resolve_source_drift(entities: list[str] | None = None, *,
                         actor: str = "integration-admin", dry_run: bool = False,
                         source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
                         central: PostgresCentralConnector | None = None,
                         target: MySQLStagingConnector | None = None,
                         progress=None) -> dict:
    """Resolve source-side drift for the given entities (or all source-drifted)."""
    return _resolve("source", entities, actor, dry_run, source_system, batch_size,
                    central, target, progress)


def resolve_target_drift(entities: list[str] | None = None, *,
                         actor: str = "integration-admin", dry_run: bool = False,
                         source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
                         central: PostgresCentralConnector | None = None,
                         target: MySQLStagingConnector | None = None,
                         progress=None) -> dict:
    """Resolve target-side drift for the given entities (or all target-drifted)."""
    return _resolve("target", entities, actor, dry_run, source_system, batch_size,
                    central, target, progress)


def resolve_all(entities: list[str] | None = None, *,
                actor: str = "integration-admin", dry_run: bool = False,
                source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
                central: PostgresCentralConnector | None = None,
                target: MySQLStagingConnector | None = None,
                progress=None) -> dict:
    """Resolve source-side drift first, then target-side, in one invocation."""
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    try:
        source = resolve_source_drift(
            entities, actor=actor, dry_run=dry_run, source_system=source_system,
            batch_size=batch_size, central=central, target=target, progress=progress)
        target = resolve_target_drift(
            entities, actor=actor, dry_run=dry_run, source_system=source_system,
            batch_size=batch_size, central=central, target=target, progress=progress)
        return {
            "dry_run": dry_run,
            "source": source,
            "target": target,
            "resolved_count": source["resolved_count"] + target["resolved_count"],
            "skipped_count": source["skipped_count"] + target["skipped_count"],
        }
    finally:
        if owns:
            central.close()


def resolve_drift(resolve_source: bool = True, resolve_target: bool = True,
                  entities: list[str] | None = None, *,
                  actor: str = "integration-admin", dry_run: bool = False,
                  source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
                  central: PostgresCentralConnector | None = None,
                  target: MySQLStagingConnector | None = None,
                  progress=None) -> dict:
    """Entry point used by the job handler.

    ``resolve_source``/``resolve_target`` select which directions run; when both
    are set this is equivalent to ``resolve_all``.
    """
    if resolve_source and resolve_target:
        return resolve_all(entities, actor=actor, dry_run=dry_run,
                           source_system=source_system, batch_size=batch_size,
                           central=central, target=target, progress=progress)
    if resolve_source:
        return resolve_source_drift(entities, actor=actor, dry_run=dry_run,
                                    source_system=source_system, batch_size=batch_size,
                                    central=central, target=target, progress=progress)
    if resolve_target:
        return resolve_target_drift(entities, actor=actor, dry_run=dry_run,
                                    source_system=source_system, batch_size=batch_size,
                                    central=central, target=target, progress=progress)
    return {"dry_run": dry_run, "resolved_count": 0, "skipped_count": 0,
            "resolved": [], "skipped": [], "plan": []}


# ---------------------------------------------------------------------------
# Manual "Drop & Restore" — admin-initiated full reset, not tied to drift
# ---------------------------------------------------------------------------

def _deployed_entities(p, conn, entities: list[str] | None = None) -> list[dict]:
    """All deployed entities, optionally filtered by name."""
    sql = ["SELECT * FROM integration.onboarding_entity WHERE status = 'deployed'"]
    params: list = []
    if entities:
        sql.append("  AND source_table = ANY(%s)")
        params.append(list(entities))
    sql.append("ORDER BY source_schema, source_table")
    return p._query(conn, "\n".join(sql), tuple(params) if params else None)


def reset_source(entities: list[str] | None = None, *,
                 actor: str = "integration-admin", dry_run: bool = False,
                 central: PostgresCentralConnector | None = None,
                 target: MySQLStagingConnector | None = None,
                 progress=None) -> dict:
    """Drop stored source fingerprints and re-scan every deployed entity from source.

    Leaves integration data (mappings, proposals, audit) untouched. Re-scans
    the source ``information_schema`` for each entity, computes a fresh
    ``source_fingerprint``, and stores it. Used when the IRIMSV source schema
    has changed and you want to accept the new shape without waiting for drift.
    """
    from ..schema_ingest import schema_fingerprint, table_schema

    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    resolved, plan = [], []
    try:
        with central.connection() as conn:
            targets = _deployed_entities(p, conn, entities)
        total = len(targets)
        for i, entity in enumerate(targets):
            table = entity["source_table"]
            if progress:
                progress(i, total, f"resetting source: {table}")
            old_fp = entity.get("source_fingerprint")

            with central.connection() as conn:
                source_obj = p._discover_source_schema(conn, entity["source_schema"])
            source_contract = table_schema(source_obj, table)
            new_fp = schema_fingerprint(source_contract) if source_contract else None

            if dry_run:
                plan.append({
                    "entity": table, "kind": "source",
                    "current_fingerprint": old_fp,
                    "new_fingerprint": new_fp,
                    "changed": bool(old_fp != new_fp),
                    "action": "update source_fingerprint" if new_fp else "skip - source object missing",
                })
                continue

            if not new_fp:
                resolved.append({"entity": table, "kind": "source",
                                 "status": "skipped", "reason": "source object not found"})
                continue

            with central.connection() as conn:
                p._execute(conn, """
                    UPDATE integration.onboarding_entity
                    SET source_fingerprint = %s, updated_at = now()
                    WHERE id = %s AND status = 'deployed'
                """, (new_fp, entity["id"]))
                p._execute(conn, """
                    INSERT INTO integration.onboarding_audit
                        (entity_id, action, details, performed_by)
                    VALUES (%s, 'source_reset', %s, %s)
                """, (entity["id"], json.dumps({
                    "old_fingerprint": old_fp,
                    "new_fingerprint": new_fp,
                }), actor))
                conn.commit()
            resolved.append({"entity": table, "kind": "source",
                             "old_fingerprint": old_fp,
                             "new_fingerprint": new_fp,
                             "status": "updated"})

        if progress:
            progress(total, total, "source reset complete")
        return {"kind": "source", "dry_run": dry_run,
                "resolved": resolved, "plan": plan,
                "total_count": len(resolved), "skipped_count": total - len(resolved)}
    finally:
        if owns:
            central.close()


def reset_target(entities: list[str] | None = None, *,
                 actor: str = "integration-admin", dry_run: bool = False,
                 source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
                 central: PostgresCentralConnector | None = None,
                 target: MySQLStagingConnector | None = None,
                 progress=None) -> dict:
    """Re-deliver every deployed entity into the LRMIS target.

    Each entity's target rows are rewritten (crosswalk-scoped delete + rewrite)
    from the current source and mapping. The ``target_fingerprint`` is recomputed
    from the post-refresh state and stored. Use this to give the target side a
    clean slate when the LRMIS schema has changed or data has gone stale.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    resolved, plan = [], []
    try:
        with central.connection() as conn:
            targets = _deployed_entities(p, conn, entities)
        total = len(targets)
        for i, entity in enumerate(targets):
            table = entity["source_table"]
            if progress:
                progress(i, total, f"resetting target: {table}")
            old_fp = entity.get("target_fingerprint")

            if dry_run:
                with central.connection() as conn:
                    new_source_fp, new_target_fp = _entity_fingerprints(conn, target, entity)
                plan.append({
                    "entity": table, "kind": "target",
                    "current_fingerprint": old_fp,
                    "new_fingerprint": new_target_fp,
                    "changed": bool(old_fp and new_target_fp and old_fp != new_target_fp),
                    "action": ("refresh target, update target_fingerprint"
                               if new_target_fp else "skip - target table missing"),
                })
                continue

            refresh_result = ops_service.refresh(
                entity["source_schema"], [table], entity["target_system"],
                source_system=source_system, batch_size=batch_size,
                central=central)
            row = (refresh_result.get("results") or [{}])[0]
            if row.get("status") != "refreshed":
                resolved.append({"entity": table, "kind": "target",
                                 "status": "skipped",
                                 "reason": row.get("error", "refresh did not run")})
                continue

            with central.connection() as conn:
                new_source_fp, new_target_fp = _entity_fingerprints(conn, target, entity)
                p._execute(conn, """
                    UPDATE integration.onboarding_entity
                    SET target_fingerprint = %s, updated_at = now()
                    WHERE id = %s AND status = 'deployed'
                """, (new_target_fp, entity["id"]))
                p._execute(conn, """
                    INSERT INTO integration.onboarding_audit
                        (entity_id, action, details, performed_by)
                    VALUES (%s, 'target_reset', %s, %s)
                """, (entity["id"], json.dumps({
                    "old_fingerprint": old_fp,
                    "new_fingerprint": new_target_fp,
                    "target_tables": entity.get("lrmis_target_tables"),
                    "rows_loaded": row.get("rows_loaded"),
                }), actor))
                conn.commit()
            resolved.append({"entity": table, "kind": "target",
                             "old_fingerprint": old_fp,
                             "new_fingerprint": new_target_fp,
                             "rows_loaded": row.get("rows_loaded"),
                             "status": "refreshed"})

        if progress:
            progress(total, total, "target reset complete")
        return {"kind": "target", "dry_run": dry_run,
                "resolved": resolved, "plan": plan,
                "total_count": len(resolved), "skipped_count": total - len(resolved)}
    finally:
        if owns:
            central.close()


def reset_all(reset_source_flag: bool = True, reset_target_flag: bool = True,
              entities: list[str] | None = None, *,
              actor: str = "integration-admin", dry_run: bool = False,
              source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
              central: PostgresCentralConnector | None = None,
              target: MySQLStagingConnector | None = None,
              progress=None) -> dict:
    """Reset source fingerprints, re-deliver target footprints, or both."""
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    try:
        result = {"dry_run": dry_run}
        total_resolved = 0
        total_skipped = 0

        if reset_source_flag:
            src = reset_source(entities, actor=actor, dry_run=dry_run,
                               central=central, target=target, progress=progress)
            result["source"] = src
            total_resolved += src["total_count"]
            total_skipped += src["skipped_count"]

        if reset_target_flag:
            tgt = reset_target(entities, actor=actor, dry_run=dry_run,
                               source_system=source_system, batch_size=batch_size,
                               central=central, target=target, progress=progress)
            result["target"] = tgt
            total_resolved += tgt["total_count"]
            total_skipped += tgt["skipped_count"]

        result["resolved_count"] = total_resolved
        result["skipped_count"] = total_skipped
        return result
    finally:
        if owns:
            central.close()


def reset_path_b(dry_run: bool = False) -> dict:
    """Drop + recreate the Path B target database (51 canonical LRMIS tables).

    Connects to MySQL as root (``LRMIS_ROOT_USER`` / ``LRMIS_ROOT_PASSWORD``)
    via :func:`scripts.init_lrmis_target.recreate_target_database`, which
    drops and recreates the database from the canonical DDL, seeds lookup
    tables from the dump, and grants DML to the pipeline writer user.
    """
    from scripts.init_lrmis_target import recreate_target_database as _recreate
    return _recreate(dry_run=dry_run)
