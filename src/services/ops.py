"""Operational services: status/health reads, monitor, refresh,
replay, kill switches, approvals, and schema trees."""
from __future__ import annotations

import json

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from ..integration_store import replay as _replay
from ..mapping_repository import approve as _approve_mapping
from .common import ConflictError, NotFoundError, ValidationError


def _pipeline():
    from .. import pipeline
    return pipeline


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_status(central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity
                WHERE status IN ('deployed', 'proposed', 'reviewed', 'paused')
                ORDER BY created_at
            """)
            outbox_stats = p._query(conn, """
                SELECT source_entity, status, COUNT(*) as events, MIN(created_at) as oldest
                FROM integration.outbox
                GROUP BY source_entity, status
                ORDER BY source_entity, status
            """)
            queues = p._query(conn, """
                SELECT status, count(*) AS events, min(created_at) AS oldest
                FROM integration.outbox GROUP BY status ORDER BY status
            """)
            controls = p._query(conn, """
                SELECT source_entity, target_system, enabled, paused_reason, updated_at
                FROM integration.entity_control ORDER BY source_entity
            """)
            unresolved_quarantine = p._fetchval(conn, """
                SELECT count(*) FROM integration.quarantine WHERE resolved_at IS NULL
            """)
            unresolved_drift = p._fetchval(conn, """
                SELECT count(*) FROM integration.schema_drift_report WHERE resolved_at IS NULL
            """)
        return {
            "entities": entities,
            "outbox_stats": outbox_stats,
            "queues": queues,
            "entity_controls": controls,
            "unresolved_quarantine": unresolved_quarantine,
            "unresolved_drift": unresolved_drift,
        }
    finally:
        if owns:
            central.close()


def list_quarantine(central: PostgresCentralConnector | None = None,
                    include_resolved: bool = False, limit: int = 200) -> list[dict]:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            where = "" if include_resolved else "WHERE q.resolved_at IS NULL"
            return p._query(conn, f"""
                SELECT q.*, o.source_entity, o.status AS outbox_status, o.attempts,
                       o.operation, o.external_reference
                FROM integration.quarantine q
                JOIN integration.outbox o ON o.event_id = q.event_id
                {where}
                ORDER BY q.created_at DESC LIMIT %s
            """, (limit,))
    finally:
        if owns:
            central.close()


def list_dead_letter(central: PostgresCentralConnector | None = None, limit: int = 200) -> list[dict]:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            return p._query(conn, """
                SELECT event_id, source_entity, external_reference, operation, attempts,
                       last_error_code, last_error_message, created_at, processed_at
                FROM integration.outbox WHERE status = 'dead_letter'
                ORDER BY processed_at DESC NULLS LAST LIMIT %s
            """, (limit,))
    finally:
        if owns:
            central.close()


def list_drift_reports(central: PostgresCentralConnector | None = None, limit: int = 100) -> list[dict]:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            return p._query(conn, """
                SELECT * FROM integration.schema_drift_report
                ORDER BY created_at DESC LIMIT %s
            """, (limit,))
    finally:
        if owns:
            central.close()


def list_proposals(central: PostgresCentralConnector | None = None,
                   status: str | None = None, limit: int = 200) -> list[dict]:
    """Proposals joined to their entity, so the UI never asks for a typed id."""
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            where, params = "", []
            if status:
                where = "WHERE p.status = %s"
                params.append(status)
            params.append(limit)
            return p._query(conn, f"""
                SELECT p.id AS proposal_id, p.status, p.auto_approved_count,
                       p.needs_review_count, p.rejected_count, p.unmet_required_columns,
                       p.created_at, p.updated_at, p.reviewed_by,
                       e.id AS entity_id, e.source_schema, e.source_table,
                       e.target_system, e.status AS entity_status,
                       (e.lrmis_target_tables IS NOT NULL) AS on_target,
                       EXISTS (
                         SELECT 1 FROM integration.onboarding_field_review lr
                         WHERE lr.proposal_id = p.id
                           AND lr.status IN ('accepted', 'resolved')
                           AND lr.suggested_target_table IS NOT NULL
                           AND lr.suggested_target_table NOT LIKE 'irimsv_%%_staging'
                       ) AS has_lrmis_mapping,
                       (SELECT COUNT(*) FROM integration.onboarding_field_review r
                        WHERE r.proposal_id = p.id AND r.status = 'pending') AS pending_fields
                FROM integration.onboarding_proposal p
                JOIN integration.onboarding_entity e ON e.id = p.entity_id
                {where}
                ORDER BY p.created_at DESC LIMIT %s
            """, tuple(params))
    finally:
        if owns:
            central.close()


def get_schema_trees(central: PostgresCentralConnector | None = None,
                     source_schema: str = "irimsv",
                     target: MySQLStagingConnector | None = None) -> dict:
    """Two trees: source (IRIMSV) and target (the real lrmis_target contract)."""
    from ..schema_ingest import from_information_schema, schema_fingerprint
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    try:
        with central.connection() as conn:
            src = p._discover_source_schema(conn, source_schema)
        tgt = from_information_schema(target.information_schema(), "LRMIS")
        return {
            "source": {"fingerprint": schema_fingerprint(src), **src.to_dict()},
            "target": {"fingerprint": schema_fingerprint(tgt), **tgt.to_dict()},
        }
    finally:
        if owns:
            central.close()


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def replay_event(event_id: str, central: PostgresCentralConnector | None = None) -> dict:
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            try:
                _replay(conn, event_id)
            except ValueError as exc:
                raise ConflictError(str(exc)) from exc
            conn.commit()
        return {"event_id": event_id, "status": "pending"}
    finally:
        if owns:
            central.close()


def set_entity_enabled(entity: str, target_system: str, enabled: bool,
                       reason: str | None,
                       central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            p._execute(conn, """
                INSERT INTO integration.entity_control (source_entity, target_system, enabled, paused_reason)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_entity, target_system)
                DO UPDATE SET enabled = EXCLUDED.enabled,
                              paused_reason = EXCLUDED.paused_reason, updated_at = now()
            """, (entity, target_system, enabled,
                  None if enabled else (reason or "manually paused")))
            conn.commit()
        return {"entity": entity, "target_system": target_system, "enabled": enabled,
                "reason": None if enabled else (reason or "manually paused")}
    finally:
        if owns:
            central.close()


def cancel_queue(entity: str,
                 central: PostgresCentralConnector | None = None) -> dict:
    """Quarantine all pending outbox events for an entity."""
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            count = p._execute(conn, """
                UPDATE integration.outbox
                SET status = 'quarantined',
                    updated_at = now(),
                    error_log = COALESCE(error_log || chr(10), '') || 'cancelled by admin'
                WHERE source_entity = %s AND status = 'pending'
            """, (entity,))
            conn.commit()
        return {"entity": entity, "cancelled": count or 0}
    finally:
        if owns:
            central.close()


def approve_mapping(mapping_id: int, by: str,
                    central: PostgresCentralConnector | None = None) -> dict:
    """Approve a legacy mapping_version or the newer onboarding proposal.

    The admin UI reviews onboarding proposals, while the older CLI approval path
    still targets integration.mapping_version. Keep one API action compatible
    with both records so admins do not see a 500 when approving from Mapping
    Review.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            try:
                _approve_mapping(conn, mapping_id, by)
            except ValueError as exc:
                conn.rollback()
                if str(exc) != "mapping version not found":
                    raise ValidationError(str(exc)) from exc

                proposals = p._query(conn, """
                    SELECT p.id, p.entity_id, p.status, e.source_table, e.status AS entity_status
                    FROM integration.onboarding_proposal p
                    JOIN integration.onboarding_entity e ON e.id = p.entity_id
                    WHERE p.id = %s
                    FOR UPDATE OF p
                """, (mapping_id,))
                if not proposals:
                    raise NotFoundError(f"mapping version or onboarding proposal {mapping_id} not found")
                proposal = proposals[0]
                if proposal["status"] in ("rejected", "deploying"):
                    raise ValidationError(
                        f"proposal status is '{proposal['status']}' and cannot be approved")
                pending = p._fetchval(conn, """
                    SELECT COUNT(*) FROM integration.onboarding_field_review
                    WHERE proposal_id = %s AND status = 'pending'
                """, (mapping_id,))
                if pending:
                    raise ValidationError(
                        f"proposal has {pending} pending field review(s); resolve them before approval")
                accepted = p._fetchval(conn, """
                    SELECT COUNT(*) FROM integration.onboarding_field_review
                    WHERE proposal_id = %s AND status IN ('accepted', 'resolved')
                """, (mapping_id,))
                if not accepted:
                    raise ValidationError("proposal has no accepted or resolved field mappings")

                p._execute(conn, """
                    UPDATE integration.onboarding_proposal
                    SET status = 'approved', reviewed_by = %s,
                        reviewed_at = now(), updated_at = now()
                    WHERE id = %s
                """, (by, mapping_id))
                p._execute(conn, """
                    UPDATE integration.onboarding_entity
                    SET status = CASE WHEN status = 'deployed' THEN status ELSE 'reviewed' END,
                        updated_at = now()
                    WHERE id = %s
                """, (proposal["entity_id"],))
                conn.commit()
                return {"proposal_id": mapping_id, "approved_by": by, "kind": "onboarding_proposal"}
            conn.commit()
        return {"mapping_id": mapping_id, "approved_by": by, "kind": "mapping_version"}
    finally:
        if owns:
            central.close()


def approve_schema(fingerprint: str, target_system: str, by: str,
                   central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            count = p._execute(conn, """
                UPDATE integration.schema_version SET approved_at = now(), approved_by = %s
                WHERE target_system = %s AND scope_kind = 'contract' AND scope_name = ''
                  AND fingerprint = %s
            """, (by, target_system, fingerprint))
            if count != 1:
                raise NotFoundError("schema fingerprint not found")
            conn.commit()
        return {"fingerprint": fingerprint, "target_system": target_system, "approved_by": by}
    finally:
        if owns:
            central.close()


def _entity_fingerprints(conn, target: MySQLStagingConnector, entity: dict) -> tuple[str | None, str | None]:
    """Fingerprint this entity's source object and its real LRMIS target footprint.

    The target side covers exactly the entity's ``lrmis_target_tables`` (read from
    the real ``lrmis_target`` database via ``target``), never the whole schema —
    unrelated tables would otherwise pause every entity."""
    from ..schema_ingest import (entity_target_contract, schema_fingerprint,
                                  table_schema)

    p = _pipeline()
    current_source = p._discover_source_schema(conn, entity["source_schema"])
    source_contract = table_schema(current_source, entity["source_table"])
    source_fp = schema_fingerprint(source_contract) if source_contract else None

    target_tables = entity.get("lrmis_target_tables")
    if isinstance(target_tables, str):
        target_tables = json.loads(target_tables)
    target_fp = None
    if target_tables:
        # Shared construction with deploy_to_lrmis — see entity_target_contract.
        _, target_fp = entity_target_contract(target.information_schema(),
                                              target_tables)
    return source_fp, target_fp


def monitor(central: PostgresCentralConnector | None = None,
            target: MySQLStagingConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity WHERE status = 'deployed'
            """)
            results, paused = [], []
            for entity in entities:
                new_source_fp, new_target_fp = _entity_fingerprints(conn, target, entity)
                legacy = int(entity.get("fingerprint_scope_version") or 1) < 2
                source_drift = bool(not legacy and entity.get("source_fingerprint")
                                    and entity["source_fingerprint"] != new_source_fp)
                target_drift = bool(not legacy and entity.get("target_fingerprint")
                                    and entity["target_fingerprint"] != new_target_fp)
                if source_drift or target_drift:
                    reason = f"Schema drift detected: source={source_drift}, target={target_drift}"
                    p._execute(conn, """
                        UPDATE integration.onboarding_entity
                        SET status = 'paused', paused_reason = %s, updated_at = now()
                        WHERE id = %s
                    """, (reason, entity["id"]))
                    p._execute(conn, """
                        UPDATE integration.entity_control
                        SET enabled = false, paused_reason = %s, updated_at = now()
                        WHERE source_entity = %s AND target_system = %s
                    """, (reason, entity["source_table"], entity["target_system"]))
                    p._execute(conn, """
                        INSERT INTO integration.schema_drift_report
                            (target_system, previous_fingerprint, observed_fingerprint,
                             differences, impacted_entities, breaking)
                        VALUES (%s, %s, %s, %s, %s, true)
                    """, (entity["target_system"],
                          entity.get("target_fingerprint") or entity.get("source_fingerprint"),
                          new_target_fp or new_source_fp or "",
                          json.dumps({"source_drift": source_drift, "target_drift": target_drift}),
                          [entity["source_table"]]))
                    paused.append(entity["source_table"])
                results.append({
                    "entity": entity["source_table"],
                    "source_drift": source_drift,
                    "target_drift": target_drift,
                    "paused": source_drift or target_drift,
                    "rebaseline_required": legacy,
                })
            conn.commit()
        return {"entities": results, "paused_entities": paused}
    finally:
        if owns:
            central.close()


def rebaseline_entity_fingerprints(actor: str, apply: bool = False,
                                   central: PostgresCentralConnector | None = None,
                                   target: MySQLStagingConnector | None = None) -> dict:
    """Re-baseline entity fingerprints to the live monitor construction.

    Covers two populations:
    * legacy whole-database fingerprints (``fingerprint_scope_version < 2``);
    * entities the drift monitor paused whose STORED fingerprints came from a
      different construction than the monitor recomputes — e.g.
      ``deploy_to_lrmis`` stores a registry-document target fingerprint while
      ``monitor`` fingerprints the live information_schema discovery, so the
      first scan after a deploy flags spurious target drift (found live
      2026-07-14: one scan paused all 44 delivering entities).

    Only entities paused by the schema-drift monitor are eligible for
    automatic re-enable; run this when the flagged drift is known-spurious or
    accepted — it baselines whatever the live schemas are NOW. Missing source
    or target objects are reported and left untouched. Call with
    ``apply=False`` for a read-only preview.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    converted, skipped = [], []
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity
                WHERE (fingerprint_scope_version < 2
                       OR (status = 'paused'
                           AND strpos(paused_reason, 'Schema drift detected:') = 1))
                  AND status IN ('deployed', 'paused')
                ORDER BY source_schema, source_table
            """)
            for entity in entities:
                source_fp, target_fp = _entity_fingerprints(conn, target, entity)
                if not source_fp or not target_fp:
                    skipped.append({
                        "entity": entity["source_table"],
                        "source_schema": entity["source_schema"],
                        "reason": "source object missing" if not source_fp else "target footprint missing",
                    })
                    continue
                was_drift_pause = (
                    entity["status"] == "paused"
                    and str(entity.get("paused_reason") or "").startswith("Schema drift detected:")
                )
                converted.append({
                    "entity": entity["source_table"],
                    "source_schema": entity["source_schema"],
                    "target_tables": entity.get("lrmis_target_tables"),
                    "will_reenable": was_drift_pause,
                })
                if not apply:
                    continue
                p._execute(conn, """
                    UPDATE integration.onboarding_entity
                    SET source_fingerprint = %s, target_fingerprint = %s,
                        fingerprint_scope_version = 2,
                        status = CASE WHEN %s THEN 'deployed' ELSE status END,
                        paused_reason = CASE WHEN %s THEN NULL ELSE paused_reason END,
                        updated_at = now()
                    WHERE id = %s
                """, (source_fp, target_fp, was_drift_pause, was_drift_pause, entity["id"]))
                if was_drift_pause:
                    p._execute(conn, """
                        UPDATE integration.entity_control
                        SET enabled = true, paused_reason = NULL, updated_at = now()
                        WHERE source_entity = %s AND target_system = %s
                    """, (entity["source_table"], entity["target_system"]))
                p._execute(conn, """
                    INSERT INTO integration.onboarding_audit
                        (entity_id, action, details, performed_by)
                    VALUES (%s, 'fingerprint_rebaseline', %s, %s)
                """, (entity["id"], json.dumps({
                    "source_schema": entity["source_schema"],
                    "source_table": entity["source_table"],
                    "target_tables": entity.get("lrmis_target_tables"),
                    "reenabled": was_drift_pause,
                }), actor))
            if apply:
                conn.commit()
            else:
                conn.rollback()
        return {"apply": apply, "converted": converted, "skipped": skipped,
                "converted_count": len(converted), "skipped_count": len(skipped)}
    finally:
        if owns:
            central.close()


def refresh(source_schema: str, source_tables: list[str], target_system: str,
            source_system: str = "IRIMSV_REGION_V", batch_size: int = 1000,
            schedule: str | None = None,
            central: PostgresCentralConnector | None = None,
            progress=None) -> dict:
    """Re-deliver each entity's current source rows into the real LRMIS target.

    Replaces the legacy staging drop+recreate+reload: instead of rebuilding an
    ``irimsv_*_staging`` table, this delegates to the direct-delivery primitive
    (:func:`nightly_refresh.redeliver_all` -> ``lrmis_delivery.refresh_entity``),
    which does a crosswalk-scoped delete + rewrite straight into the target. Only
    entities already deployed to the target (``lrmis_target_tables`` set) can
    refresh; anything else is reported as skipped. ``batch_size`` is retained for
    signature compatibility (redelivery streams per entity).
    """
    from .nightly_refresh import redeliver_all
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT id, source_schema, source_table, primary_key_columns,
                       source_system, lrmis_target_tables
                FROM integration.onboarding_entity
                WHERE source_schema = %s AND source_table = ANY(%s)
                  AND target_system = %s
                ORDER BY source_table
            """, (source_schema, list(source_tables), target_system))

        found = {e["source_table"] for e in entities}
        on_target = [e for e in entities if e.get("lrmis_target_tables") is not None]
        on_target_names = {e["source_table"] for e in on_target}
        for e in on_target:
            if not e.get("source_system"):
                e["source_system"] = source_system

        delivered = redeliver_all(on_target, target_system, progress) if on_target else []

        results = []
        for r in delivered:
            # Normalise to the shape drift_resolution / the CLI consume.
            r.setdefault("table", r.get("entity"))
            r["rows_loaded"] = r.get("written", 0)
            results.append(r)
        for table in source_tables:
            if table not in on_target_names:
                reason = ("entity is not deployed to the LRMIS target"
                          if table in found else
                          "no LRMIS-target entity - run onboard/deploy first")
                results.append({"table": table, "status": "skipped", "error": reason})

        if schedule and on_target:
            with central.connection() as conn:
                for e in on_target:
                    p._execute(conn, """
                        INSERT INTO integration.onboarding_audit
                            (entity_id, action, details, performed_by)
                        VALUES (%s, 'schedule', %s, %s)
                    """, (e["id"], json.dumps({"schedule": schedule}), source_system))
                conn.commit()
        return {"results": results}
    finally:
        if owns:
            central.close()


