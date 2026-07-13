"""Operational services: status/health reads, reconcile, monitor, refresh,
replay, kill switches, approvals, and schema trees."""
from __future__ import annotations

import json

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from ..integration_store import replay as _replay
from ..mapping_repository import approve as _approve_mapping
from .common import ConflictError, NotFoundError, ValidationError
from .snapshots import list_snapshots, restore_snapshot, snapshot_staging_table


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
                     staging: MySQLStagingConnector | None = None,
                     source_schema: str = "irimsv",
                     target: MySQLStagingConnector | None = None) -> dict:
    """Three trees: source (IRIMSV), staging (Path A lrmis_staging), and
    target_b (Path B lrmis_target canonical contract)."""
    from ..schema_ingest import from_information_schema, schema_fingerprint
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    target = target or MySQLStagingConnector.for_target()
    try:
        with central.connection() as conn:
            src = p._discover_source_schema(conn, source_schema)
        stg = from_information_schema(staging.information_schema(), "LRMIS")
        tgt_b = from_information_schema(target.information_schema(), "LRMIS_B")
        return {
            "source": {"fingerprint": schema_fingerprint(src), **src.to_dict()},
            "staging": {"fingerprint": schema_fingerprint(stg), **stg.to_dict()},
            "target_b": {"fingerprint": schema_fingerprint(tgt_b), **tgt_b.to_dict()},
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


def reconcile(entity_name: str,
              central: PostgresCentralConnector | None = None,
              staging: MySQLStagingConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity
                WHERE source_table = %s AND status = 'deployed'
            """, (entity_name,))
            if not entities:
                raise NotFoundError(f"entity '{entity_name}' not found or not deployed")
            entity = entities[0]
            staging_table = entity["staging_table"]
            delivered = p._query(conn, """
                SELECT external_reference, payload_checksum FROM integration.outbox
                WHERE source_entity = %s AND status = 'delivered'
            """, (entity_name,))
        with staging.connection() as sconn:
            with sconn.cursor(dictionary=True) as cur:
                cur.execute(f"SELECT external_reference, payload_checksum FROM `{staging_table}`")
                staging_rows = cur.fetchall()

        central_refs = {r["external_reference"]: r["payload_checksum"] for r in delivered}
        staging_refs = {r["external_reference"]: r["payload_checksum"] for r in staging_rows}
        missing_in_staging = sorted(set(central_refs) - set(staging_refs))
        missing_in_central = sorted(set(staging_refs) - set(central_refs))
        mismatches = sorted(ref for ref in set(central_refs) & set(staging_refs)
                            if central_refs[ref] != staging_refs[ref])
        return {
            "entity": entity_name,
            "staging_table": staging_table,
            "central_delivered": len(delivered),
            "staging_rows": len(staging_rows),
            "missing_in_staging": missing_in_staging[:50],
            "missing_in_staging_count": len(missing_in_staging),
            "missing_in_central": missing_in_central[:50],
            "missing_in_central_count": len(missing_in_central),
            "checksum_mismatches": mismatches[:50],
            "checksum_mismatch_count": len(mismatches),
            "status": "OK" if not missing_in_staging and not mismatches else "MISMATCH",
        }
    finally:
        if owns:
            central.close()


def _entity_fingerprints(conn, staging: MySQLStagingConnector, entity: dict) -> tuple[str | None, str | None]:
    """Fingerprint only this entity's source object and exact staging table."""
    from ..connectors import VIEWS_DATABASE
    from ..schema_ingest import from_information_schema, schema_fingerprint, table_schema

    p = _pipeline()
    current_source = p._discover_source_schema(conn, entity["source_schema"])
    source_contract = table_schema(current_source, entity["source_table"])
    source_fp = schema_fingerprint(source_contract) if source_contract else None

    staging_table = entity.get("staging_table")
    target_fp = None
    if staging_table:
        database = VIEWS_DATABASE if staging.is_views_table(staging_table) else None
        target = from_information_schema(staging.information_schema(database), "LRMIS")
        target_contract = table_schema(target, staging_table)
        target_fp = schema_fingerprint(target_contract) if target_contract else None
    return source_fp, target_fp


def monitor(central: PostgresCentralConnector | None = None,
            staging: MySQLStagingConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity WHERE status = 'deployed'
            """)
            results, paused = [], []
            for entity in entities:
                new_source_fp, new_target_fp = _entity_fingerprints(conn, staging, entity)
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
                                   staging: MySQLStagingConnector | None = None) -> dict:
    """Convert legacy whole-database fingerprints to isolated entity contracts.

    Only entities paused by the old schema-drift monitor are eligible for
    automatic re-enable. Missing source or target objects are reported and
    left untouched. Call with ``apply=False`` for a read-only preview.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    converted, skipped = [], []
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity
                WHERE fingerprint_scope_version < 2
                  AND status IN ('deployed', 'paused')
                ORDER BY source_schema, source_table
            """)
            for entity in entities:
                source_fp, target_fp = _entity_fingerprints(conn, staging, entity)
                if not source_fp or not target_fp:
                    skipped.append({
                        "entity": entity["source_table"],
                        "source_schema": entity["source_schema"],
                        "reason": "source object missing" if not source_fp else "staging table missing",
                    })
                    continue
                was_drift_pause = (
                    entity["status"] == "paused"
                    and str(entity.get("paused_reason") or "").startswith("Schema drift detected:")
                )
                converted.append({
                    "entity": entity["source_table"],
                    "source_schema": entity["source_schema"],
                    "staging_table": entity["staging_table"],
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
                    "staging_table": entity["staging_table"],
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
            staging: MySQLStagingConnector | None = None,
            progress=None) -> dict:
    """Drop + recreate + reload staging tables, snapshotting each before the drop."""
    from ..fast_refresh import fetch_and_bulk_insert, generate_refresh_sql
    from ..integration_store import approved_mapping
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    results = []
    try:
        for i, table in enumerate(source_tables):
            if progress:
                progress(i, len(source_tables), f"refreshing {table}")
            with central.connection() as conn:
                mapping = approved_mapping(conn, table, target_system)
                if not mapping:
                    results.append({"table": table, "status": "skipped",
                                    "error": "no approved mapping - run onboard first"})
                    continue
                mappings = mapping["mappings"]
                if isinstance(mappings, str):
                    mappings = json.loads(mappings)
                target_table = mapping.get("target_table", f"irimsv_{table}_staging")

                source_schema_obj = p._discover_source_schema(conn, source_schema)
                source_table_obj = source_schema_obj.get_table(table)
                if not source_table_obj:
                    results.append({"table": table, "status": "skipped",
                                    "error": f"source table {source_schema}.{table} not found"})
                    continue

                # Use stored primary_key_columns from the entity (set at deploy time)
                # rather than re-detecting via information_schema, which can fail when
                # PK constraints are not visible to the querying role.
                # If a stored PK column no longer exists in the source table (e.g. the
                # entity was deployed with a fallback ["id"]), fall back to detecting
                # actual PK columns from the live source schema.
                entity_row = p._fetchval(conn, """
                    SELECT primary_key_columns FROM integration.onboarding_entity
                    WHERE source_schema = %s AND source_table = %s AND target_system = %s
                """, (source_schema, table, target_system))
                pk_cols = json.loads(entity_row) if isinstance(entity_row, str) else (entity_row or [])
                source_col_names = {c.name for c in source_table_obj.columns}
                if not pk_cols or not all(c in source_col_names for c in pk_cols):
                    live_pk = [c.name for c in source_table_obj.columns if c.is_primary_key]
                    if live_pk:
                        pk_cols = live_pk
                    elif "id" in source_col_names:
                        pk_cols = ["id"]
                    else:
                        # No declared PK and no id column: let generate_refresh_sql
                        # key each row off a content hash instead of a column that
                        # does not exist.
                        pk_cols = []

                updated_at_col = next(
                    (c.name for c in source_table_obj.columns
                     if c.name.lower() in ("updated_at", "modified_at", "last_updated", "timestamp")),
                    None,
                )

                snapshot = snapshot_staging_table(staging, target_table)

                target_schema = p._discover_target_schema(conn, target_system)
                deploy_mappings = []
                for m in mappings:
                    target_col = m.get("target_column")
                    if not target_col:
                        continue
                    deploy_mappings.append({
                        "source_column": m["source_column"],
                        "target_table": target_table,
                        "target_column": target_col,
                        "confidence": m.get("confidence", 0.0),
                        "transform": m.get("transform", "none"),
                        "col_type": p._infer_column_type(target_schema, target_table, target_col),
                    })
                p._create_staging_table(staging, target_table, deploy_mappings, pk_cols)

                sql = generate_refresh_sql(source_schema, table, target_table, mappings,
                                           source_system, pk_cols, updated_at_column=updated_at_col)
                columns = ["event_id", "external_reference", "source_system", "operation",
                           "source_updated_at", "mapping_version", "payload_checksum",
                           "active", "accepted_at"]
                for m in mappings:
                    tc = m.get("target_column")
                    if tc and tc not in columns:
                        columns.append(tc)
                count = fetch_and_bulk_insert(conn, staging, sql, target_table, columns, batch_size)

                p._execute(conn, """
                    UPDATE integration.onboarding_entity
                    SET status = 'deployed', staging_table = %s, deployed_by = %s,
                        deployed_at = now(), updated_at = now()
                    WHERE source_schema = %s AND source_table = %s AND target_system = %s
                """, (target_table, source_system, source_schema, table, target_system))
                conn.commit()
                if schedule:
                    p._execute(conn, """
                        INSERT INTO integration.onboarding_audit (entity_id, action, details, performed_by)
                        VALUES ((SELECT id FROM integration.onboarding_entity
                                 WHERE source_schema = %s AND source_table = %s AND target_system = %s),
                                'schedule', %s, %s)
                    """, (source_schema, table, target_system,
                          json.dumps({"schedule": schedule}), source_system))
                    conn.commit()
                results.append({"table": table, "status": "refreshed", "target_table": target_table,
                                "rows_loaded": count, "snapshot": snapshot})
        return {"results": results}
    finally:
        if owns:
            central.close()


def restore_staging_snapshot(table: str, snapshot: str | None = None,
                             staging: MySQLStagingConnector | None = None) -> dict:
    staging = staging or MySQLStagingConnector()
    try:
        restored = restore_snapshot(staging, table, snapshot)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    return {"table": table, "restored_from": restored}


def staging_snapshots(table: str, staging: MySQLStagingConnector | None = None) -> dict:
    staging = staging or MySQLStagingConnector()
    return {"table": table, "snapshots": list_snapshots(staging, table)}
