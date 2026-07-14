"""Onboarding workflow services: discover, propose, review, resolve, deploy, backfill.

Structured, print-free counterparts of the src.pipeline cmd_* commands.
Deploy adds the concurrency guards required by the admin-dashboard OpenSpec
change: advisory lock, in-lock status re-check, and a 'deploying' intermediate
status. Delivery is direct to the LRMIS target (no staging tables).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from ..mapping_engine import mapping_to_dicts, propose_mapping
from ..schema_ingest import schema_fingerprint, table_schema
from ..transform_engine import _ENVELOPE_FIELDS
from .common import ConflictError, NotFoundError, ValidationError


def _pipeline():
    from .. import pipeline
    return pipeline


def discover(source_schema: str, target_system: str,
             central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            src = p._discover_source_schema(conn, source_schema)
            tgt = p._discover_target_schema(conn, target_system)
            tables = []
            for table in src.tables:
                pk_cols = [c.name for c in table.columns if c.is_primary_key] or ["id"]
                updated_at_col = next(
                    (c.name for c in table.columns
                     if c.name.lower() in ("updated_at", "modified_at", "last_updated", "timestamp")),
                    None,
                )
                candidates = p._rank_target_tables(table, tgt) if tgt else []
                entity = p._get_or_create_entity(
                    conn, source_schema, table.name, target_system, pk_cols, updated_at_col,
                )
                tables.append({
                    "table": table.name,
                    "columns": len(table.columns),
                    "primary_key": pk_cols,
                    "updated_at_column": updated_at_col,
                    "target_candidates": candidates[:5],
                    "entity_id": entity["id"],
                })
        return {"source_schema": source_schema, "target_system": target_system, "tables": tables}
    finally:
        if owns:
            central.close()


def propose(source_schema: str, source_table: str, target_system: str,
            central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT * FROM integration.onboarding_entity
                WHERE source_schema = %s AND source_table = %s AND target_system = %s
            """, (source_schema, source_table, target_system))
            if not entities:
                raise NotFoundError("entity not found - run discover first")
            entity = entities[0]

            src = p._discover_source_schema(conn, source_schema)
            table = src.get_table(source_table)
            if not table:
                raise NotFoundError(f"table {source_table} not found in source schema")
            tgt = p._discover_target_schema(conn, target_system)
            if not tgt:
                raise ValidationError(f"no approved target schema for {target_system}")

            source_contract = table_schema(src, source_table)
            src_fp = schema_fingerprint(source_contract) if source_contract else ""
            tgt_fp = schema_fingerprint(tgt)
            p._execute(conn, """
                UPDATE integration.onboarding_entity
                SET source_fingerprint = %s, target_fingerprint = %s,
                    fingerprint_scope_version = 2, updated_at = now()
                WHERE id = %s
            """, (src_fp, tgt_fp, entity["id"]))

            gemini_error = None
            try:
                raw_mappings = propose_mapping(table, tgt)
                gemini_raw = {"mappings": mapping_to_dicts(raw_mappings)}
            except Exception as exc:  # Gemini unavailable -> manual resolution
                gemini_error = str(exc)
                raw_mappings = []
                gemini_raw = {"error": gemini_error}

            auto_approved, needs_review, rejected = [], [], []
            for m in raw_mappings:
                m_dict = m if isinstance(m, dict) else asdict(m)
                confidence = m_dict.get("confidence", 0.0)
                target_col = m_dict.get("target_column")
                if target_col is None or target_col == "?" or confidence == 0.0:
                    rejected.append(m_dict)
                elif confidence >= p.CONFIDENCE_THRESHOLD:
                    auto_approved.append(m_dict)
                else:
                    needs_review.append(m_dict)

            unmet_required = []
            for t_table in tgt.tables:
                for col in t_table.columns:
                    if not col.nullable and col.name not in _ENVELOPE_FIELDS:
                        mapped = any(m.get("target_column") == col.name
                                     for m in auto_approved + needs_review)
                        if not mapped and col.name not in ("active",):
                            unmet_required.append(f"{t_table.name}.{col.name}")

            proposal_id = p._create_proposal(
                conn, entity["id"], src_fp, tgt_fp,
                auto_approved + needs_review + rejected, [], unmet_required,
                gemini_raw, len(auto_approved), len(needs_review), len(rejected),
            )
        return {
            "proposal_id": proposal_id,
            "entity_id": entity["id"],
            "auto_approved": len(auto_approved),
            "needs_review": len(needs_review),
            "rejected": len(rejected),
            "unmet_required": unmet_required,
            "gemini_error": gemini_error,
            "status": "auto_approved" if not needs_review else "needs_review",
        }
    finally:
        if owns:
            central.close()


def get_review(proposal_id: int, central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            proposals = p._query(conn, """
                SELECT p.*, e.source_schema, e.source_table, e.target_system
                FROM integration.onboarding_proposal p
                JOIN integration.onboarding_entity e ON p.entity_id = e.id
                WHERE p.id = %s
            """, (proposal_id,))
            if not proposals:
                raise NotFoundError(f"proposal {proposal_id} not found")
            proposal = proposals[0]
            reviews = p._query(conn, """
                SELECT * FROM integration.onboarding_field_review
                WHERE proposal_id = %s ORDER BY confidence DESC
            """, (proposal_id,))
        unmet = proposal.get("unmet_required_columns") or []
        if isinstance(unmet, str):
            unmet = json.loads(unmet)
        return {
            "proposal": {
                "id": proposal["id"],
                "entity_id": proposal["entity_id"],
                "source_schema": proposal["source_schema"],
                "source_table": proposal["source_table"],
                "target_system": proposal["target_system"],
                "status": proposal["status"],
                "source_fingerprint": proposal["source_fingerprint"],
                "target_fingerprint": proposal["target_fingerprint"],
                "unmet_required_columns": unmet,
            },
            "fields": reviews,
        }
    finally:
        if owns:
            central.close()


def resolve(proposal_id: int, source_column: str, target_column: str,
            transform: str = "none", resolved_by: str = "admin",
            target_table: str | None = None,
            central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            if not p._query(conn, "SELECT 1 FROM integration.onboarding_proposal WHERE id = %s",
                            (proposal_id,)):
                raise NotFoundError(f"proposal {proposal_id} not found")
            if not p._query(conn, """
                SELECT 1 FROM integration.onboarding_field_review
                WHERE proposal_id = %s AND source_column = %s
            """, (proposal_id, source_column)):
                raise NotFoundError(f"field review for '{source_column}' not found")
            transform = transform or "none"
            if transform not in p.ALLOWED_TRANSFORMS:
                raise ValidationError(
                    f"transform '{transform}' not in allowlist: {sorted(p.ALLOWED_TRANSFORMS)}")

            if target_table:
                # Manual multi-table mapping: assign the target TABLE too (the AI
                # suggested none). The load paths read suggested_target_table, so
                # setting it here is what lets a source column be hand-mapped to
                # any LRMIS table.
                p._execute(conn, """
                    UPDATE integration.onboarding_field_review
                    SET status = 'resolved', suggested_target_table = %s,
                        resolved_target_column = %s, resolved_transform = %s,
                        resolved_by = %s, resolved_at = now()
                    WHERE proposal_id = %s AND source_column = %s
                """, (target_table, target_column, transform, resolved_by,
                      proposal_id, source_column))
            else:
                p._execute(conn, """
                    UPDATE integration.onboarding_field_review
                    SET status = 'resolved', resolved_target_column = %s,
                        resolved_transform = %s, resolved_by = %s, resolved_at = now()
                    WHERE proposal_id = %s AND source_column = %s
                """, (target_column, transform, resolved_by, proposal_id, source_column))

            pending = p._fetchval(conn, """
                SELECT COUNT(*) FROM integration.onboarding_field_review
                WHERE proposal_id = %s AND status = 'pending'
            """, (proposal_id,))
            new_status = "approved" if pending == 0 else "needs_review"
            p._execute(conn, """
                UPDATE integration.onboarding_proposal
                SET status = %s, reviewed_by = %s, reviewed_at = now(), updated_at = now()
                WHERE id = %s
            """, (new_status, resolved_by, proposal_id))
            conn.commit()
        return {
            "proposal_id": proposal_id,
            "source_column": source_column,
            "target_column": target_column,
            "transform": transform,
            "pending_remaining": pending,
            "proposal_status": new_status,
        }
    finally:
        if owns:
            central.close()


def onboard_bulk(source_schema: str, tables: list[str], target_system: str, actor: str,
                 central: PostgresCentralConnector | None = None,
                 progress=None) -> dict:
    """Onboard many tables in one guarded pass (bulk-onboarding spec).

    Conservative by design. A table is deployed only when its proposal is
    auto-approved with no unmet required columns; anything less certain is
    routed to `needs_review` with its proposal id rather than being deployed
    with columns nobody checked. This is the opposite of the CLI's --auto,
    which silently drops mid-confidence mappings.

    Direct delivery: composes `deploy_to_lrmis()` (validates the multi-table
    LRMIS mapping and sets the entity's target footprint — no staging tables) and
    `backfill()` (which only enqueues outbox events), so rows reach the target
    only through the audited delivery worker. A table whose LRMIS mapping fails
    the coverage gate is recorded as `failed`, never deployed half-mapped.

    Resilient: a failure on one table is recorded and the batch continues,
    unlike cmd_onboard which aborts on the first hard exception.
    """
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    target_system = target_system.upper()

    onboarded, needs_review, skipped, failed = [], [], [], []
    total = len(tables)
    try:
        for index, table in enumerate(tables):
            if progress:
                progress(index, total, f"onboarding {table}")
            try:
                with central.connection() as conn:
                    existing = p._query(conn, """
                        SELECT status FROM integration.onboarding_entity
                        WHERE source_schema = %s AND source_table = %s AND target_system = %s
                    """, (source_schema, table, target_system))
                if existing and existing[0]["status"] == "deployed":
                    skipped.append({"table": table, "reason": "already deployed"})
                    continue

                # Registers the entity if this table has never been seen.
                discover(source_schema, target_system, central=central)

                proposed = propose(source_schema, table, target_system, central=central)
                blocking = (proposed["status"] != "auto_approved"
                            or bool(proposed["unmet_required"]))
                if blocking:
                    needs_review.append({
                        "table": table,
                        "proposal_id": proposed["proposal_id"],
                        "needs_review_fields": proposed["needs_review"],
                        "rejected_fields": proposed["rejected"],
                        "unmet_required": proposed["unmet_required"],
                        "gemini_error": proposed.get("gemini_error"),
                    })
                    continue

                from .lrmis_onboarding import deploy_to_lrmis
                deployed = deploy_to_lrmis(proposed["proposal_id"], actor, central=central)
                filled = backfill(table, central=central)
                onboarded.append({
                    "table": table,
                    "proposal_id": proposed["proposal_id"],
                    "target_tables": deployed["target_tables"],
                    "mappings": deployed["mappings"],
                    "queued": filled["queued"],
                    "skipped_duplicates": filled["skipped"],
                })
            except Exception as exc:  # one bad table must not kill the batch
                failed.append({"table": table, "error": str(exc)})

        if progress:
            progress(total, total, "bulk onboard complete")

        return {
            "source_schema": source_schema,
            "target_system": target_system,
            "requested": total,
            "onboarded": onboarded,
            "needs_review": needs_review,
            "skipped_already_deployed": skipped,
            "failed": failed,
            "counts": {
                "onboarded": len(onboarded),
                "needs_review": len(needs_review),
                "skipped_already_deployed": len(skipped),
                "failed": len(failed),
            },
        }
    finally:
        if owns:
            central.close()


def backfill(entity_name: str, central: PostgresCentralConnector | None = None) -> dict:
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            entities = p._query(conn, """
                SELECT e.*, p.id as proposal_id
                FROM integration.onboarding_entity e
                LEFT JOIN integration.onboarding_proposal p
                    ON p.entity_id = e.id AND p.status IN ('approved', 'auto_approved')
                WHERE e.source_table = %s AND e.status = 'deployed'
                ORDER BY p.id DESC NULLS LAST
            """, (entity_name,))
            if not entities:
                raise NotFoundError(f"entity '{entity_name}' not found or not deployed")
            entity = entities[0]
            source_schema = entity["source_schema"]
            source_table = entity["source_table"]
            pk_columns = entity["primary_key_columns"]
            if isinstance(pk_columns, str):
                pk_columns = json.loads(pk_columns)

            mapping_version = p._fetchval(conn, """
                SELECT MAX(version) FROM integration.mapping_version
                WHERE source_entity = %s AND target_system = %s AND status = 'approved'
            """, (source_table, entity["target_system"]))

            source_rows = p._query(conn, f'SELECT * FROM {source_schema}.{source_table}')
            queued = skipped = 0
            for row in source_rows:
                pk_values = [row.get(col) for col in pk_columns]
                ext_ref = p.generate_external_reference(
                    entity.get("source_system") or "IRIMSV_REGION_V",
                    source_schema, source_table, pk_values,
                )
                if p._fetchval(conn, """
                    SELECT event_id FROM integration.outbox
                    WHERE external_reference = %s AND source_entity = %s
                """, (str(ext_ref), source_table)):
                    skipped += 1
                    continue
                payload = dict(row)
                payload["external_reference"] = str(ext_ref)
                p._execute(conn, """
                    INSERT INTO integration.outbox
                        (source_entity, external_reference, operation, payload, payload_checksum,
                         mapping_version_id, source_updated_at)
                    VALUES (%s, %s, 'backfill', %s, %s, %s, now())
                """, (source_table, str(ext_ref), json.dumps(payload, default=str),
                      hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
                      mapping_version))
                queued += 1
            conn.commit()
        return {"entity": entity_name, "queued": queued, "skipped": skipped,
                "total_source_rows": len(source_rows)}
    finally:
        if owns:
            central.close()
