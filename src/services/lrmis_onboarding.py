"""Deploy an entity to the LRMIS target (Path B, Phase 6).

The legacy deploy() creates a single VARCHAR staging table and points the entity
at lrmis_staging. This deploys against the real 51-table LRMIS schema instead:
it validates the multi-table mapping against the canonical schema, records the
entity's target-table footprint (which flips the worker to the Path B delivery
path), and marks it deployed. It never creates tables - Phase 1 already built
lrmis_target - and never touches the legacy staging path.
"""
from __future__ import annotations

import json

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from ..lrmis_registry import get_registry
from ..schema_ingest import schema_fingerprint
from ..schema_models import Column, Schema, Table
from .common import NotFoundError, ValidationError
from .lrmis_mapping import (
    coverage_report, store_target_tables, target_tables_for, validate_deployment)


def deploy_guidance(report, registry, agent) -> list[dict]:
    """Turn a failing deployment coverage report into interactive agent guidance
    — one entry per blocking dilemma (unmapped required column, unknown target
    column, unknown table, or an unsatisfiable FK) with the agent's recommended
    resolution. Used when a guided deploy is blocked (§8.6)."""
    from ..agent import Dilemma

    def _ser(guidance) -> dict:
        return {"kind": guidance.dilemma.kind, "table": guidance.dilemma.table,
                "column": guidance.dilemma.column, "recommended": guidance.recommended,
                "options": guidance.options}

    out: list[dict] = []
    for tc in report.tables:
        if not tc.exists:
            out.append(_ser(agent.guide(Dilemma(kind="unknown_table", table=tc.table, column=""))))
            continue
        candidates = [c.name for c in registry.get_table(tc.table).columns]
        for col in tc.required_missing:
            out.append(_ser(agent.guide(Dilemma(
                kind="unmapped_column", table=tc.table, column=col,
                context={"candidates": candidates}))))
        for col in tc.unknown_target_columns:
            out.append(_ser(agent.guide(Dilemma(
                kind="type_mismatch", table=tc.table, column=col))))
    for fk in report.fk_unsatisfiable:
        out.append(_ser(agent.guide(Dilemma(
            kind="fk_violation", table=fk["table"], column=fk["column"], context=fk))))
    return out


def _is_staging_table(table: str | None) -> bool:
    """True for legacy single-table staging targets (`irimsv_*_staging`), which
    are not part of the LRMIS schema and must be ignored on the target path."""
    return bool(table) and table.startswith("irimsv_") and table.endswith("_staging")


def _load_proposal_mappings(conn, proposal_id: int) -> tuple[dict, list[dict]]:
    """Return (proposal_row, accepted mappings) for a proposal id."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT p.id, p.status, p.entity_id,
                   e.source_schema, e.source_table, e.target_system, e.status AS entity_status
            FROM integration.onboarding_proposal p
            JOIN integration.onboarding_entity e ON e.id = p.entity_id
            WHERE p.id = %s
        """, (proposal_id,))
        proposal = cur.fetchone()
        if not proposal:
            raise NotFoundError(f"proposal {proposal_id} not found")
        cur.execute("""
            SELECT source_column, suggested_target_table, suggested_target_column,
                   resolved_target_column, transform, resolved_transform
            FROM integration.onboarding_field_review
            WHERE proposal_id = %s AND status IN ('accepted', 'resolved')
        """, (proposal_id,))
        reviews = cur.fetchall()

    mappings = []
    for r in reviews:
        target_col = r["resolved_target_column"] or r["suggested_target_column"]
        table = r["suggested_target_table"]
        if not target_col or not table:
            continue
        if _is_staging_table(table):
            continue  # legacy staging leftover — not a valid LRMIS target, ignore
        mappings.append({
            "source_column": r["source_column"],
            "target_table": table,
            "target_column": target_col,
            "transform": r["resolved_transform"] or r["transform"] or "none",
        })
    return dict(proposal), mappings


def _lrmis_schema_document(tables: list[str], registry) -> dict:
    """A schema_models document for the entity's LRMIS footprint, so drift on
    those specific tables can be fingerprinted like any other target schema."""
    model = Schema(system_name="LRMIS", tables=[
        Table(name=t, columns=[
            Column(name=c.name, data_type=c.base_type, nullable=c.nullable,
                   is_primary_key=c.is_primary_key)
            for c in registry.get_table(t).columns
        ])
        for t in tables
    ])
    return model.to_dict(), schema_fingerprint(model)


def deploy_to_lrmis(proposal_id: int, by: str,
                    central: PostgresCentralConnector | None = None,
                    registry=None, agent=None) -> dict:
    """Validate and deploy a proposal against the LRMIS target schema.

    When an `agent` is supplied and validation fails, the deploy does not raise —
    it returns `{"status": "needs_guidance", ...}` with the agent's interactive
    resolution options (§8.6). Without an agent, behaviour is unchanged (raises
    ValidationError)."""
    registry = registry or get_registry()
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            proposal, mappings = _load_proposal_mappings(conn, proposal_id)
            if proposal["status"] not in ("approved", "auto_approved"):
                raise ValidationError(
                    f"proposal status is {proposal['status']!r}, must be approved")
            if not mappings:
                raise ValidationError("proposal has no accepted target-column mappings")

            # Gate: every target table/column must exist and every required
            # column (that the writer does not fill) must be mapped.
            report = coverage_report(mappings, registry)
            if not report.ok:
                if agent is not None:
                    return {"status": "needs_guidance", "blocking": report.blocking,
                            "guidance": deploy_guidance(report, registry, agent)}
                raise ValidationError("mapping cannot be deployed:\n  - "
                                      + "\n  - ".join(report.blocking))

            entity_id = proposal["entity_id"]
            tables = target_tables_for(mappings)
            store_target_tables(conn, entity_id, mappings)

            doc, fingerprint = _lrmis_schema_document(tables, registry)
            p._execute(conn, """
                INSERT INTO integration.schema_version
                    (target_system, scope_kind, scope_name, fingerprint,
                     schema_document, approved_at, approved_by)
                VALUES (%s, 'entity_staging', %s, %s, %s, now(), %s)
                ON CONFLICT (target_system, scope_kind, scope_name, fingerprint)
                DO UPDATE SET schema_document = EXCLUDED.schema_document, approved_at = now()
            """, (proposal["target_system"], proposal["source_table"], fingerprint,
                  json.dumps(doc), by))

            p._execute(conn, """
                UPDATE integration.onboarding_entity
                SET status = 'deployed', deployed_by = %s, deployed_at = now(),
                    target_fingerprint = %s, updated_at = now()
                WHERE id = %s
            """, (by, fingerprint, entity_id))
            p._execute(conn, """
                UPDATE integration.onboarding_proposal
                SET status = 'approved', reviewed_by = %s, reviewed_at = now(), updated_at = now()
                WHERE id = %s
            """, (by, proposal_id))
            p._execute(conn, """
                INSERT INTO integration.entity_control (source_entity, target_system, enabled)
                VALUES (%s, %s, true)
                ON CONFLICT (source_entity, target_system)
                DO UPDATE SET enabled = true, paused_reason = NULL
            """, (proposal["source_table"], proposal["target_system"]))
            p._execute(conn, """
                INSERT INTO integration.onboarding_audit
                    (entity_id, proposal_id, action, details, performed_by)
                VALUES (%s, %s, 'deploy_lrmis', %s, %s)
            """, (entity_id, proposal_id,
                  json.dumps({"target_tables": tables, "fingerprint": fingerprint}), by))
            conn.commit()

        return {
            "proposal_id": proposal_id,
            "entity_id": entity_id,
            "source_table": proposal["source_table"],
            "target_tables": tables,
            "fingerprint": fingerprint,
            "mappings": len(mappings),
            "coverage_ok": report.ok,
        }
    finally:
        if owns:
            central.close()


def _deployable_proposals(conn, target_system: str) -> list[dict]:
    """The latest approved proposal per entity that is not yet on the LRMIS
    target — i.e. exactly what a bulk migration should deploy."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT ON (e.id) p.id AS proposal_id, e.source_table
            FROM integration.onboarding_proposal p
            JOIN integration.onboarding_entity e ON e.id = p.entity_id
            WHERE p.status IN ('approved', 'auto_approved')
              AND e.lrmis_target_tables IS NULL
              AND e.target_system = %s
              AND EXISTS (
                SELECT 1 FROM integration.onboarding_field_review r
                WHERE r.proposal_id = p.id
                  AND r.status IN ('accepted', 'resolved')
                  AND r.suggested_target_table IS NOT NULL
                  AND r.suggested_target_table NOT LIKE 'irimsv_%%_staging'
              )
            ORDER BY e.id, p.created_at DESC
        """, (target_system,))
        return [dict(r) for r in cur.fetchall()]


def bulk_deploy_to_lrmis(by: str, target_system: str = "LRMIS",
                         central: PostgresCentralConnector | None = None,
                         registry=None, progress=None) -> dict:
    """Deploy every approved, not-yet-migrated entity to the LRMIS target.

    Conservative and non-destructive: composes `deploy_to_lrmis` per entity and
    continues past a failure — an entity whose mapping fails the coverage gate is
    reported as `failed` rather than aborting the batch. Returns per-entity
    outcomes so the UI can show what deployed and what needs attention."""
    registry = registry or get_registry()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            candidates = _deployable_proposals(conn, target_system)
        results = []
        for i, c in enumerate(candidates):
            if progress:
                progress(i, len(candidates), c["source_table"])
            try:
                out = deploy_to_lrmis(c["proposal_id"], by, central=central, registry=registry)
                results.append({"source_table": c["source_table"], "status": "deployed",
                                "target_tables": out["target_tables"]})
            except Exception as exc:
                results.append({"source_table": c["source_table"], "status": "failed",
                                "error": str(exc)})
        deployed = sum(1 for r in results if r["status"] == "deployed")
        return {"total": len(candidates), "deployed": deployed,
                "failed": len(candidates) - deployed, "results": results}
    finally:
        if owns:
            central.close()


def _tables_to_repropose(conn, source_schema: str, target_system: str) -> list[str]:
    """Deployed entities that are still on staging (not yet on the LRMIS target)
    — the ones that need a fresh LRMIS-target proposal."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT source_table
            FROM integration.onboarding_entity
            WHERE status = 'deployed' AND lrmis_target_tables IS NULL
              AND target_system = %s AND source_schema = %s
            ORDER BY source_table
        """, (target_system, source_schema))
        return [r["source_table"] for r in cur.fetchall()]


def bulk_propose_lrmis(by: str, source_schema: str = "irimsv",
                       target_system: str = "LRMIS",
                       central: PostgresCentralConnector | None = None,
                       progress=None) -> dict:
    """Generate a fresh LRMIS-target proposal (one Gemini call each) for every
    deployed entity not yet on the target. This replaces the legacy staging
    mappings with real multi-table LRMIS mappings, ready for review/deploy.

    Continues past a per-table failure and reports it. Expensive: it makes one
    AI call per table, so it is a single-flight, reason-gated job."""
    from .onboarding import propose
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            tables = _tables_to_repropose(conn, source_schema, target_system)
        results = []
        for i, t in enumerate(tables):
            if progress:
                progress(i, len(tables), t)
            try:
                out = propose(source_schema, t, target_system, central=central)
                if out.get("gemini_error"):
                    # A provider error (rate limit etc.) — the call layer already
                    # backed off and retried; mark it and keep going, since later
                    # tables can still succeed once the per-minute window resets.
                    results.append({"source_table": t, "status": "failed",
                                    "error": str(out["gemini_error"])[:120]})
                else:
                    results.append({"source_table": t, "status": "proposed",
                                    "proposal_id": out.get("proposal_id"),
                                    "auto_approved": out.get("auto_approved"),
                                    "needs_review": out.get("needs_review")})
            except Exception as exc:
                results.append({"source_table": t, "status": "failed", "error": str(exc)})
        proposed = sum(1 for r in results if r["status"] == "proposed")
        return {"total": len(tables), "proposed": proposed,
                "failed": len(tables) - proposed, "results": results}
    finally:
        if owns:
            central.close()


def _pipeline():
    from .. import pipeline
    return pipeline
