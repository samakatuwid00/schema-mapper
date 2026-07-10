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
from .lrmis_mapping import store_target_tables, target_tables_for, validate_deployment


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
        if not target_col or not r["suggested_target_table"]:
            continue
        mappings.append({
            "source_column": r["source_column"],
            "target_table": r["suggested_target_table"],
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
                    registry=None) -> dict:
    """Validate and deploy a proposal against the LRMIS target schema."""
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
            report = validate_deployment(mappings, registry)

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


def _pipeline():
    from .. import pipeline
    return pipeline
