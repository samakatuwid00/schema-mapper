"""
Generic AI-Assisted Onboarding Pipeline

Usage:
    python -m src.pipeline discover
    python -m src.pipeline propose --source-schema S --source-table T
    python -m src.pipeline review --proposal ID
    python -m src.pipeline resolve --proposal ID --source-column X --target-column Y [--transform Z]
    python -m src.pipeline deploy --proposal ID --by ADMIN
    python -m src.pipeline backfill --entity E
    python -m src.pipeline monitor
    python -m src.pipeline onboard --source-schema S --source-table T --target-system T
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from .connectors import PostgresCentralConnector
from .schema_models import Schema, Table, Column
from .schema_ingest import parse_ddl, schema_fingerprint, table_schema
from .mapping_engine import propose_mapping, FieldMapping, mapping_to_dicts
from .transform_engine import _ENVELOPE_FIELDS

log = logging.getLogger(__name__)

# UUIDv5 namespace for deterministic external references
# This MUST remain stable across environments
UUID5_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")

CONFIDENCE_THRESHOLD = 0.95

# Allowlisted transforms
ALLOWED_TRANSFORMS = {
    "none",
    "cast:date->datetime",
    "cast:str->int",
    "cast:int->str",
}


# ---------------------------------------------------------------------------
# UUIDv5 deterministic external reference generation
# ---------------------------------------------------------------------------

def generate_external_reference(
    source_system: str,
    source_schema: str,
    source_table: str,
    pk_values: list,
) -> uuid.UUID:
    """
    Generate a deterministic UUIDv5 from source system + schema + table + canonical PK values.
    Supports integer, UUID, text, and composite primary keys.
    """
    canonical = f"{source_system}|{source_schema}|{source_table}|{'|'.join(str(v) for v in pk_values)}"
    return uuid.uuid5(UUID5_NAMESPACE, canonical)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(conn) -> psycopg2.extensions.connection:
    """Extract raw connection from connector context manager."""
    return conn


def _query(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def _execute(conn, sql: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _fetchval(conn, sql: str, params: tuple = ()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# Schema discovery helpers
# ---------------------------------------------------------------------------

def _discover_source_schema(conn, source_schema: str) -> Schema:
    """Ingest source schema from PostgreSQL information_schema."""
    sql = """
        SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
               CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN 'PRI' ELSE '' END as column_key,
               col_description((quote_ident(c.table_schema)||'.'||quote_ident(c.table_name))::regclass, c.ordinal_position) as description
        FROM information_schema.columns c
        LEFT JOIN information_schema.key_column_usage kcu
            ON c.table_schema = kcu.table_schema AND c.table_name = kcu.table_name
            AND c.column_name = kcu.column_name
        LEFT JOIN information_schema.table_constraints tc
            ON kcu.constraint_name = tc.constraint_name AND tc.constraint_type = 'PRIMARY KEY'
        WHERE c.table_schema = %s
        ORDER BY c.table_name, c.ordinal_position
    """
    rows = _query(conn, sql, (source_schema,))
    grouped = {}
    for row in rows:
        r = {k.lower(): v for k, v in row.items()}
        grouped.setdefault(r["table_name"], []).append(Column(
            name=r["column_name"],
            data_type=r["data_type"],
            nullable=r["is_nullable"] == "YES",
            is_primary_key=r.get("column_key") == "PRI",
            description=r.get("description") or "",
        ))
    return Schema(
        system_name=source_schema,
        tables=[Table(name=n, columns=cols) for n, cols in sorted(grouped.items())],
    )


def _discover_target_schema(conn, target_system: str, target_schema_fingerprint: str = None) -> Schema | None:
    """Get target schema from schema_version table."""
    if target_schema_fingerprint:
        row = _fetchval(conn, """
            SELECT schema_document FROM integration.schema_version
            WHERE target_system = %s AND scope_kind = 'contract' AND scope_name = ''
              AND fingerprint = %s AND approved_at IS NOT NULL
        """, (target_system, target_schema_fingerprint))
    else:
        row = _fetchval(conn, """
            SELECT schema_document FROM integration.schema_version
            WHERE target_system = %s AND scope_kind = 'contract' AND scope_name = ''
              AND approved_at IS NOT NULL
            ORDER BY observed_at DESC LIMIT 1
        """, (target_system,))
    return Schema.from_dict(row) if row else None


def _rank_target_tables(source_table: Table, target_schema: Schema) -> list[dict]:
    """Rank target tables by name similarity to source table."""
    candidates = []
    source_name = source_table.name.lower()
    for t in target_schema.tables:
        target_name = t.name.lower()
        # Simple scoring: exact match > contains > partial match
        score = 0.0
        if source_name == target_name:
            score = 1.0
        elif source_name in target_name or target_name in source_name:
            score = 0.7
        elif any(part in target_name for part in source_name.split("_")):
            score = 0.4
        candidates.append({
            "table": t.name,
            "score": score,
            "columns": len(t.columns),
        })
    return sorted(candidates, key=lambda x: -x["score"])


# ---------------------------------------------------------------------------
# Onboarding metadata operations
# ---------------------------------------------------------------------------

def _get_or_create_entity(
    conn,
    source_schema: str,
    source_table: str,
    target_system: str,
    pk_columns: list[str],
    updated_at_column: str | None,
) -> dict:
    """Get existing or create new onboarding entity."""
    existing = _query(conn, """
        SELECT * FROM integration.onboarding_entity
        WHERE source_schema = %s AND source_table = %s AND target_system = %s
    """, (source_schema, source_table, target_system))
    if existing:
        return existing[0]

    entity_id = _fetchval(conn, """
        INSERT INTO integration.onboarding_entity
            (source_schema, source_table, target_system, primary_key_columns, updated_at_column)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (source_schema, source_table, target_system, json.dumps(pk_columns), updated_at_column))
    conn.commit()
    return {"id": entity_id, "source_schema": source_schema, "source_table": source_table,
            "target_system": target_system, "primary_key_columns": pk_columns,
            "updated_at_column": updated_at_column}


def _create_proposal(
    conn,
    entity_id: int,
    source_fingerprint: str,
    target_fingerprint: str,
    mappings: list[dict],
    ignored_columns: list[str],
    unmet_required: list[str],
    gemini_response: dict | None,
    auto_approved: int,
    needs_review: int,
    rejected: int,
) -> int:
    """Create a new proposal and its field reviews."""
    proposal_id = _fetchval(conn, """
        INSERT INTO integration.onboarding_proposal
            (entity_id, source_fingerprint, target_fingerprint, mappings,
             ignored_source_columns, unmet_required_columns, gemini_raw_response,
             auto_approved_count, needs_review_count, rejected_count, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (entity_id, source_fingerprint, target_fingerprint, json.dumps(mappings),
          json.dumps(ignored_columns), json.dumps(unmet_required),
          json.dumps(gemini_response) if gemini_response else None,
          auto_approved, needs_review, rejected,
          "auto_approved" if needs_review == 0 else "needs_review"))

    # Create field reviews for each mapping
    for m in mappings:
        # Determine status based on confidence and target column
        confidence = m.get("confidence", 0.0)
        target_col = m.get("target_column")
        if confidence >= CONFIDENCE_THRESHOLD:
            field_status = "accepted"
        elif confidence == 0.0 or target_col is None or target_col == "?":
            field_status = "rejected"
        else:
            field_status = "pending"

        _execute(conn, """
            INSERT INTO integration.onboarding_field_review
                (proposal_id, source_column, suggested_target_table, suggested_target_column,
                 confidence, transform, reasoning, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (proposal_id, m["source_column"], m.get("target_table"), target_col,
              confidence, m.get("transform", "none"), m.get("reasoning", ""),
              field_status))

    conn.commit()
    return proposal_id


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_discover(args):
    """Discover source tables and suggest target candidates."""
    central = PostgresCentralConnector()
    with central.connection() as conn:
        source_schema = _discover_source_schema(conn, args.source_schema)
        target_schema = _discover_target_schema(conn, args.target_system)

        print(f"\nSource Schema: {args.source_schema}")
        print(f"Target System: {args.target_system}")
        print("=" * 60)

        for table in source_schema.tables:
            # Get PK columns
            pk_cols = [c.name for c in table.columns if c.is_primary_key]
            if not pk_cols:
                pk_cols = ["id"]  # fallback

            # Get updated_at candidate
            updated_at_col = None
            for c in table.columns:
                if c.name.lower() in ("updated_at", "modified_at", "last_updated", "timestamp"):
                    updated_at_col = c.name
                    break

            # Rank target tables
            candidates = _rank_target_tables(table, target_schema) if target_schema else []

            print(f"\n  Table: {args.source_schema}.{table.name}")
            print(f"  Columns: {len(table.columns)}")
            print(f"  Primary Key: {pk_cols}")
            print(f"  Updated At: {updated_at_col or 'none detected'}")
            if candidates:
                print(f"  Target Candidates:")
                for c in candidates[:5]:
                    print(f"    - {c['table']} (score: {c['score']:.2f}, {c['columns']} cols)")

            # Upsert entity
            entity = _get_or_create_entity(
                conn, args.source_schema, table.name, args.target_system,
                pk_cols, updated_at_col,
            )
            print(f"  Entity ID: {entity['id']}")

    central.close()


def cmd_propose(args):
    """Propose mappings using Gemini AI."""
    central = PostgresCentralConnector()

    with central.connection() as conn:
        # Get entity
        entities = _query(conn, """
            SELECT * FROM integration.onboarding_entity
            WHERE source_schema = %s AND source_table = %s AND target_system = %s
        """, (args.source_schema, args.source_table, args.target_system))
        if not entities:
            print(f"Error: Entity not found. Run 'discover' first.")
            sys.exit(1)
        entity = entities[0]

        # Get schemas
        source_schema = _discover_source_schema(conn, args.source_schema)
        source_table = source_schema.get_table(args.source_table)
        if not source_table:
            print(f"Error: Table {args.source_table} not found in source schema.")
            sys.exit(1)

        target_schema = _discover_target_schema(conn, args.target_system)
        if not target_schema:
            print(f"Error: No approved target schema found for {args.target_system}.")
            sys.exit(1)

        # Compute fingerprints
        source_contract = table_schema(source_schema, args.source_table)
        src_fp = schema_fingerprint(source_contract) if source_contract else ""
        tgt_fp = schema_fingerprint(target_schema)

        # Update entity fingerprints
        _execute(conn, """
            UPDATE integration.onboarding_entity
            SET source_fingerprint = %s, target_fingerprint = %s,
                fingerprint_scope_version = 2, updated_at = now()
            WHERE id = %s
        """, (src_fp, tgt_fp, entity["id"]))

        # Call Gemini for mapping proposals
        print(f"\nCalling Gemini AI for mapping proposals...")
        print(f"Source: {args.source_schema}.{args.source_table}")
        print(f"Target: {args.target_system}")
        print(f"Threshold: {CONFIDENCE_THRESHOLD}")
        print("-" * 60)

        try:
            mappings = propose_mapping(source_table, target_schema)
            gemini_raw = {"mappings": mapping_to_dicts(mappings)}
        except Exception as e:
            print(f"Gemini error: {e}")
            print("Using empty mappings - manual resolution required.")
            mappings = []
            gemini_raw = {"error": str(e)}

        # Classify mappings
        auto_approved = []
        needs_review = []
        rejected = []
        ignored = []
        unmet_required = []

        for m in mappings:
            m_dict = m if isinstance(m, dict) else asdict(m)
            confidence = m_dict.get("confidence", 0.0)
            target_col = m_dict.get("target_column")

            # Reject if: no target column, target is "?", or confidence is 0%
            if target_col is None or target_col == "?" or confidence == 0.0:
                rejected.append(m_dict)
            elif confidence >= CONFIDENCE_THRESHOLD:
                auto_approved.append(m_dict)
            else:
                needs_review.append(m_dict)

        # Check for unmet required target columns
        for t_table in target_schema.tables:
            for col in t_table.columns:
                if not col.nullable and col.name not in _ENVELOPE_FIELDS:
                    mapped = any(m.get("target_column") == col.name for m in auto_approved + needs_review)
                    has_default = col.name in ("active",)  # known defaults
                    if not mapped and not has_default:
                        unmet_required.append(f"{t_table.name}.{col.name}")

        all_mappings = auto_approved + needs_review + rejected
        proposal_id = _create_proposal(
            conn, entity["id"], src_fp, tgt_fp,
            all_mappings, ignored, unmet_required,
            gemini_raw, len(auto_approved), len(needs_review), len(rejected),
        )

        print(f"\nProposal ID: {proposal_id}")
        print(f"Auto-approved (>{CONFIDENCE_THRESHOLD}): {len(auto_approved)}")
        print(f"Needs review: {len(needs_review)}")
        print(f"Rejected: {len(rejected)}")
        print(f"Unmet required: {len(unmet_required)}")

        if needs_review or rejected:
            print(f"\nNext: python -m src.pipeline review --proposal {proposal_id}")
        elif not unmet_required:
            print(f"\nAll mappings auto-approved! Ready to deploy:")
            print(f"  python -m src.pipeline deploy --proposal {proposal_id} --by ADMIN")
        else:
            print(f"\nUnmet required columns: {unmet_required}")

    central.close()


def cmd_review(args):
    """Review a proposal's mappings."""
    central = PostgresCentralConnector()
    with central.connection() as conn:
        # Get proposal
        proposals = _query(conn, """
            SELECT p.*, e.source_schema, e.source_table, e.target_system
            FROM integration.onboarding_proposal p
            JOIN integration.onboarding_entity e ON p.entity_id = e.id
            WHERE p.id = %s
        """, (args.proposal,))
        if not proposals:
            print(f"Error: Proposal {args.proposal} not found.")
            sys.exit(1)
        proposal = proposals[0]

        # Get field reviews
        reviews = _query(conn, """
            SELECT * FROM integration.onboarding_field_review
            WHERE proposal_id = %s ORDER BY confidence DESC
        """, (args.proposal,))

        print(f"\nProposal #{proposal['id']}")
        print(f"Entity: {proposal['source_schema']}.{proposal['source_table']} -> {proposal['target_system']}")
        print(f"Status: {proposal['status']}")
        print(f"Source Fingerprint: {proposal['source_fingerprint'][:16]}...")
        print(f"Target Fingerprint: {proposal['target_fingerprint'][:16]}...")
        print("=" * 70)

        # Group by status
        accepted = [r for r in reviews if r["status"] == "accepted"]
        pending = [r for r in reviews if r["status"] == "pending"]
        resolved = [r for r in reviews if r["status"] == "resolved"]
        rejected = [r for r in reviews if r["status"] == "rejected"]

        if accepted:
            print(f"\nACCEPTED ({len(accepted)}):")
            for r in accepted:
                print(f"  {r['source_column']:20} -> {r['suggested_target_column']:20} "
                      f"({r['confidence']:.0%}) [{r['transform']}]")

        if resolved:
            print(f"\nRESOLVED ({len(resolved)}):")
            for r in resolved:
                target = r.get("resolved_target_column") or r.get("suggested_target_column") or "?"
                transform = r.get("resolved_transform") or r["transform"]
                print(f"  {r['source_column']:20} -> {target:20} [{transform}]")

        if pending:
            print(f"\nNEEDS REVIEW ({len(pending)}):")
            for r in pending:
                target = r.get('suggested_target_column') or '?'
                print(f"  {r['source_column']:20} -> {target:20} "
                      f"({r['confidence']:.0%}) [{r['transform']}]")
                print(f"    Reason: {r.get('reasoning', 'N/A')}")

        if rejected:
            print(f"\nIGNORED ({len(rejected)}):")
            for r in rejected:
                target = r.get('suggested_target_column') or 'no match'
                print(f"  {r['source_column']:20} -> {target:20} "
                      f"({r['confidence']:.0%})")

        # Show unmet required
        unmet = proposal.get("unmet_required_columns", [])
        if isinstance(unmet, str):
            unmet = json.loads(unmet)
        if unmet:
            print(f"\nUNMET REQUIRED COLUMNS:")
            for col in unmet:
                print(f"  - {col}")

        print(f"\nTo resolve: python -m src.pipeline resolve --proposal {args.proposal} "
              "--source-column X --target-column Y")

    central.close()


def cmd_resolve(args):
    """Manually resolve a field mapping."""
    central = PostgresCentralConnector()
    with central.connection() as conn:
        # Get proposal
        proposals = _query(conn, """
            SELECT * FROM integration.onboarding_proposal WHERE id = %s
        """, (args.proposal,))
        if not proposals:
            print(f"Error: Proposal {args.proposal} not found.")
            sys.exit(1)

        # Get field review
        reviews = _query(conn, """
            SELECT * FROM integration.onboarding_field_review
            WHERE proposal_id = %s AND source_column = %s
        """, (args.proposal, args.source_column))
        if not reviews:
            print(f"Error: Field review for '{args.source_column}' not found.")
            sys.exit(1)

        # Update resolution
        transform = args.transform or "none"
        if transform not in ALLOWED_TRANSFORMS:
            print(f"Error: Transform '{transform}' not in allowlist: {ALLOWED_TRANSFORMS}")
            sys.exit(1)

        _execute(conn, """
            UPDATE integration.onboarding_field_review
            SET status = 'resolved',
                resolved_target_column = %s,
                resolved_transform = %s,
                resolved_by = %s,
                resolved_at = now()
            WHERE proposal_id = %s AND source_column = %s
        """, (args.target_column, transform, args.resolved_by or "admin",
              args.proposal, args.source_column))

        # Update proposal status
        pending_count = _fetchval(conn, """
            SELECT COUNT(*) FROM integration.onboarding_field_review
            WHERE proposal_id = %s AND status = 'pending'
        """, (args.proposal,))

        new_status = "approved" if pending_count == 0 else "needs_review"
        _execute(conn, """
            UPDATE integration.onboarding_proposal
            SET status = %s, reviewed_by = %s, reviewed_at = now(), updated_at = now()
            WHERE id = %s
        """, (new_status, args.resolved_by or "admin", args.proposal))

        conn.commit()
        print(f"Resolved: {args.source_column} -> {args.target_column} [{transform}]")
        print(f"Proposal status: {new_status}")

        if pending_count == 0:
            print(f"\nAll fields resolved! Ready to deploy:")
            print(f"  python -m src.pipeline deploy --proposal {args.proposal} --by ADMIN")

    central.close()


def cmd_deploy(args):
    """Deploy a proposal to the real LRMIS target (direct, multi-table).

    The legacy single-table staging deploy is retired (retire-legacy-staging §2.3);
    this validates the multi-table LRMIS mapping and sets the entity's target
    footprint — no staging table."""
    from .services.common import ServiceError
    from .services.lrmis_onboarding import deploy_to_lrmis

    try:
        result = deploy_to_lrmis(args.proposal, args.by)
    except ServiceError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))
    print(f"\nDeployment complete!")
    print(f"  Target tables: {result['target_tables']}")
    print(f"\nNext steps:")
    print(f"  python -m src.pipeline backfill --entity <source_table>")
    print(f"  python -m src.worker")


def cmd_backfill(args):
    """Backfill existing records into the outbox."""
    central = PostgresCentralConnector()
    with central.connection() as conn:
        # Get entity (prefer one with approved proposal)
        entities = _query(conn, """
            SELECT e.*, p.id as proposal_id
            FROM integration.onboarding_entity e
            LEFT JOIN integration.onboarding_proposal p ON p.entity_id = e.id AND p.status IN ('approved', 'auto_approved')
            WHERE e.source_table = %s AND e.status = 'deployed'
            ORDER BY p.id DESC NULLS LAST
        """, (args.entity,))
        if not entities:
            print(f"Error: Entity '{args.entity}' not found or not deployed.")
            sys.exit(1)
        entity = entities[0]

        source_schema = entity["source_schema"]
        source_table = entity["source_table"]
        pk_columns = json.loads(entity["primary_key_columns"]) if isinstance(entity["primary_key_columns"], str) else entity["primary_key_columns"]
        updated_at_column = entity.get("updated_at_column")
        target_system = entity["target_system"]

        # Get approved mapping version
        mapping_version = _fetchval(conn, """
            SELECT MAX(version) FROM integration.mapping_version
            WHERE source_entity = %s AND target_system = %s AND status = 'approved'
        """, (source_table, target_system))

        if not mapping_version:
            # Use proposal mappings
            proposal = _query(conn, """
                SELECT mappings FROM integration.onboarding_proposal WHERE id = %s
            """, (entity["proposal_id"],))
            raw = proposal[0]["mappings"] if proposal else []
            mappings = json.loads(raw) if isinstance(raw, str) else raw
        else:
            mapping_row = _query(conn, """
                SELECT mappings FROM integration.mapping_version
                WHERE source_entity = %s AND target_system = %s AND version = %s
            """, (source_table, target_system, mapping_version))
            raw = mapping_row[0]["mappings"] if mapping_row else []
            mappings = json.loads(raw) if isinstance(raw, str) else raw

        # Fetch all source records
        pk_cols_sql = ", ".join(f'"{c}"' for c in pk_columns)
        source_rows = _query(conn, f'SELECT * FROM {source_schema}.{source_table}')

        queued = 0
        skipped = 0

        for row in source_rows:
            # Generate deterministic external reference
            pk_values = [row.get(col) for col in pk_columns]
            ext_ref = generate_external_reference(
                entity["source_system"] if "source_system" in entity else "IRIMSV_REGION_V",
                source_schema, source_table, pk_values,
            )

            # Check if already in outbox
            existing = _fetchval(conn, """
                SELECT event_id FROM integration.outbox
                WHERE external_reference = %s AND source_entity = %s
            """, (str(ext_ref), source_table))
            if existing:
                skipped += 1
                continue

            # Build payload
            payload = dict(row)
            payload["external_reference"] = str(ext_ref)

            # Create outbox event
            _execute(conn, """
                INSERT INTO integration.outbox
                    (source_entity, external_reference, operation, payload, payload_checksum,
                     mapping_version_id, source_updated_at)
                VALUES (%s, %s, 'backfill', %s, %s, %s, now())
            """, (source_table, str(ext_ref), json.dumps(payload, default=str),
                  hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
                  mapping_version))
            queued += 1

        conn.commit()
        print(f"Backfill complete for {source_schema}.{source_table}:")
        print(f"  Queued: {queued}")
        print(f"  Skipped (duplicates): {skipped}")

    central.close()


def cmd_monitor(args):
    """Monitor source and target schema drift."""
    central = PostgresCentralConnector()

    with central.connection() as conn:
        # Get all deployed entities
        entities = _query(conn, """
            SELECT * FROM integration.onboarding_entity WHERE status = 'deployed'
        """)

        print(f"\nSchema Drift Monitor")
        print("=" * 60)

        for entity in entities:
            source_schema_name = entity["source_schema"]
            source_table_name = entity["source_table"]
            target_system = entity["target_system"]
            old_source_fp = entity.get("source_fingerprint")
            old_target_fp = entity.get("target_fingerprint")

            # Check source drift
            current_source = _discover_source_schema(conn, source_schema_name)
            current_source_table = current_source.get_table(source_table_name)
            new_source_fp = schema_fingerprint(current_source) if current_source_table else None

            # Check target drift
            target_schema = _discover_target_schema(conn, target_system)
            new_target_fp = schema_fingerprint(target_schema) if target_schema else None

            source_drift = old_source_fp and new_source_fp and old_source_fp != new_source_fp
            target_drift = old_target_fp and new_target_fp and old_target_fp != new_target_fp

            print(f"\n  Entity: {source_table_name}")
            print(f"  Source drift: {'YES' if source_drift else 'No'}")
            print(f"  Target drift: {'YES' if target_drift else 'No'}")

            if source_drift or target_drift:
                # Pause entity
                _execute(conn, """
                    UPDATE integration.onboarding_entity
                    SET status = 'paused', paused_reason = %s, updated_at = now()
                    WHERE id = %s
                """, (f"Schema drift detected: source={source_drift}, target={target_drift}", entity["id"]))

                # Create drift report
                _execute(conn, """
                    INSERT INTO integration.schema_drift_report
                        (target_system, previous_fingerprint, observed_fingerprint, differences, impacted_entities, breaking)
                    VALUES (%s, %s, %s, %s, %s, true)
                """, (target_system, old_target_fp or old_source_fp, new_target_fp or new_source_fp,
                      json.dumps({"source_drift": source_drift, "target_drift": target_drift}),
                      [source_table_name]))

                print(f"  Action: PAUSED - manual review required")
                print(f"  Create new proposal: python -m src.pipeline propose "
                      f"--source-schema {source_schema_name} --source-table {source_table_name}")
            else:
                print(f"  Action: None - schemas stable")

    central.close()


def cmd_status(args):
    """Show onboarding status for all entities."""
    from .terminal_ui import print_onboarding_status

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    central = PostgresCentralConnector()

    with central.connection() as conn:
        # Get only deployed entities and entities with proposals
        entities = _query(conn, """
            SELECT * FROM integration.onboarding_entity
            WHERE status IN ('deployed', 'proposed', 'reviewed', 'paused')
            ORDER BY created_at
        """)

        # Get outbox status per entity
        outbox_stats = _query(conn, """
            SELECT source_entity, status, COUNT(*) as events, MIN(created_at) as oldest
            FROM integration.outbox
            GROUP BY source_entity, status
            ORDER BY source_entity, status
        """)

    print_onboarding_status(entities, outbox_stats)
    central.close()


def cmd_refresh(args):
    """Re-deliver entities into the LRMIS target from the current source rows."""
    from .services.ops import get_status, refresh as refresh_service
    from .terminal_ui import print_header, print_onboarding_status

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    tables = [t.strip() for t in args.source_table.split(",")]
    print_header(f"Refreshing: {args.source_schema}.{args.source_table}")
    result = refresh_service(
        args.source_schema, tables, args.target_system,
        source_system=args.source_system, batch_size=args.batch_size,
        schedule=args.schedule,
    )
    print(json.dumps(result, indent=2, default=str))

    status = get_status()
    print_onboarding_status(status["entities"], status["outbox_stats"])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generic AI-Assisted Onboarding Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover source tables and target candidates")
    p_discover.add_argument("--source-schema", default="irimsv", help="Source schema name")
    p_discover.add_argument("--target-system", default="LRMIS", help="Target system name")

    # propose
    p_propose = subparsers.add_parser("propose", help="Propose mappings using Gemini AI")
    p_propose.add_argument("--source-schema", default="irimsv", help="Source schema name")
    p_propose.add_argument("--source-table", required=True, help="Source table name")
    p_propose.add_argument("--target-system", default="LRMIS", help="Target system name")

    # review
    p_review = subparsers.add_parser("review", help="Review a proposal's mappings")
    p_review.add_argument("--proposal", type=int, required=True, help="Proposal ID")

    # resolve
    p_resolve = subparsers.add_parser("resolve", help="Manually resolve a field mapping")
    p_resolve.add_argument("--proposal", type=int, required=True, help="Proposal ID")
    p_resolve.add_argument("--source-column", required=True, help="Source column name")
    p_resolve.add_argument("--target-column", required=True, help="Target column name")
    p_resolve.add_argument("--transform", default="none", help="Transform type")
    p_resolve.add_argument("--resolved-by", default="admin", help="Resolver name")

    # deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy a proposal")
    p_deploy.add_argument("--proposal", type=int, required=True, help="Proposal ID")
    p_deploy.add_argument("--by", required=True, help="Admin name")

    # backfill
    p_backfill = subparsers.add_parser("backfill", help="Backfill existing records")
    p_backfill.add_argument("--entity", required=True, help="Entity/table name")

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="Monitor schema drift")

    # status
    p_status = subparsers.add_parser("status", help="Show onboarding status for all entities")

    # refresh
    p_refresh = subparsers.add_parser("refresh", help="Re-deliver entities into the LRMIS target from source")
    p_refresh.add_argument("--source-schema", required=True, help="Source schema name")
    p_refresh.add_argument("--source-table", required=True, help="Source table(s), comma-separated")
    p_refresh.add_argument("--target-system", required=True, help="Target system name")
    p_refresh.add_argument("--source-system", default="IRIMSV_REGION_V", help="Source system name")
    p_refresh.add_argument("--batch-size", type=int, default=1000, help="MySQL batch size")
    p_refresh.add_argument("--schedule", default=None, help="Schedule (e.g., daily, weekly, monthly)")

    args = parser.parse_args()

    commands = {
        "discover": cmd_discover,
        "propose": cmd_propose,
        "review": cmd_review,
        "resolve": cmd_resolve,
        "deploy": cmd_deploy,
        "backfill": cmd_backfill,
        "monitor": cmd_monitor,
        "status": cmd_status,
        "refresh": cmd_refresh,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
