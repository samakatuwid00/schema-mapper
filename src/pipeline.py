"""
Generic AI-Assisted Onboarding Pipeline

Usage:
    python -m src.pipeline discover
    python -m src.pipeline propose --source-schema S --source-table T
    python -m src.pipeline review --proposal ID
    python -m src.pipeline resolve --proposal ID --source-column X --target-column Y [--transform Z]
    python -m src.pipeline deploy --proposal ID --by ADMIN
    python -m src.pipeline backfill --entity E
    python -m src.pipeline reconcile --entity E
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

from .connectors import PostgresCentralConnector, MySQLStagingConnector
from .schema_models import Schema, Table, Column
from .schema_ingest import parse_ddl, from_information_schema, schema_fingerprint
from .mapping_engine import propose_mapping, FieldMapping, mapping_to_dicts
from .transform_engine import transform_row, _ENVELOPE_FIELDS

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


def _detect_collation(staging: MySQLStagingConnector, database: str) -> str:
    """Query target MySQL for default collation."""
    try:
        with staging.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                    (database,),
                )
                row = cur.fetchone()
                return row[0] if row else "utf8mb4_unicode_ci"
    except Exception:
        return "utf8mb4_unicode_ci"


def _infer_column_type(target_schema: Schema, target_table: str, target_column: str) -> str:
    """Map target column type to MySQL DDL type."""
    TYPE_MAP = {
        "string": "VARCHAR(255)",
        "text": "TEXT",
        "integer": "INT",
        "float": "DECIMAL(18,2)",
        "boolean": "TINYINT(1)",
        "date": "DATE",
        "datetime": "DATETIME(6)",
        "uuid": "CHAR(36)",
        "json": "JSON",
    }
    table = target_schema.get_table(target_table)
    if table:
        col = table.get_column(target_column)
        if col:
            return TYPE_MAP.get(col.data_type, "VARCHAR(255)")
    return "VARCHAR(255)"


def _detect_cross_table_candidates(
    conn,
    source_schema: str,
    source_table: str,
    rejected_columns: list[str],
    target_system: str,
) -> list[dict]:
    """
    For each rejected source column, check if it appears in ANY other target table.
    Returns list of cross-table candidates with confidence scores.
    """
    if not rejected_columns:
        return []

    target_schema = _discover_target_schema(conn, target_system)
    if not target_schema:
        return []

    candidates = []
    for col_name in rejected_columns:
        for table in target_schema.tables:
            for tcol in table.columns:
                if tcol.name.lower() == col_name.lower():
                    candidates.append({
                        "source_schema": source_schema,
                        "source_table": source_table,
                        "source_column": col_name,
                        "target_system": target_system,
                        "target_table": table.name,
                        "target_column": tcol.name,
                        "confidence": 0.5,
                    })

    return candidates


def _run_worker_batch(
    central: PostgresCentralConnector,
    staging: MySQLStagingConnector,
    source_table: str,
    batch_size: int = 100,
) -> tuple[int, int, int, bool]:
    """
    Process ONE batch of outbox events.
    Returns: (delivered, failed, quarantined, has_more)
    """
    from .integration_store import (
        approved_mapping, claim_events, delivered as mark_delivered,
        quarantine, retry_or_dead_letter, save_projection,
    )

    TARGET_SYSTEM = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
    MAX_ATTEMPTS = int(os.environ.get("SYNC_MAX_ATTEMPTS", "8"))

    delivered_count = 0
    failed_count = 0
    quarantined_count = 0

    with central.connection() as conn:
        events = claim_events(conn, TARGET_SYSTEM, batch_size)
        has_more = len(events) == batch_size

        for event in events:
            mapping = approved_mapping(conn, event["source_entity"], event["target_system"])
            if not mapping:
                quarantine(conn, event, ["no approved mapping for entity"], None)
                quarantined_count += 1
                continue

            mappings = mapping["mappings"]
            if isinstance(mappings, str):
                mappings = json.loads(mappings)

            target_schema = _target_schema(conn, mapping["schema_fingerprint"])
            if not target_schema:
                quarantine(conn, event, ["mapping schema version is not approved"], mapping["id"])
                quarantined_count += 1
                continue

            transformed, errors = transform_row(
                event["payload"], {"mappings": mappings}, target_schema
            )
            if errors:
                quarantine(conn, event, errors, mapping["id"])
                quarantined_count += 1
                continue

            outbound = _outbound_row(event, mapping, transformed)
            save_projection(conn, event, mapping, outbound)
            try:
                staging.upsert(mapping["target_table"], outbound)
                mark_delivered(conn, event, mapping)
                delivered_count += 1
            except Exception as exc:
                retry_or_dead_letter(conn, event, exc, MAX_ATTEMPTS)
                failed_count += 1

        conn.commit()

    return delivered_count, failed_count, quarantined_count, has_more


def _target_schema(conn, fingerprint: str) -> Schema | None:
    """Get target schema from schema_version table."""
    row = _fetchval(conn, """
        SELECT schema_document FROM integration.schema_version
        WHERE target_system = %s AND fingerprint = %s AND approved_at IS NOT NULL
    """, (os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS"), fingerprint))
    return Schema.from_dict(row) if row else None


def _outbound_row(event: dict, mapping: dict, transformed: dict) -> dict:
    """Build outbound row for MySQL staging."""
    active = event["operation"] != "deactivate"
    transformed.setdefault("active", active)
    return {
        "event_id": str(event["event_id"]),
        "external_reference": str(event["external_reference"]),
        "source_system": event["source_system"],
        "operation": event["operation"],
        "source_updated_at": event["source_updated_at"].astimezone(timezone.utc).replace(tzinfo=None),
        "mapping_version": mapping["version"],
        "payload_checksum": event["payload_checksum"],
        **transformed,
    }


def _discover_target_schema(conn, target_system: str, target_schema_fingerprint: str = None) -> Schema | None:
    """Get target schema from schema_version table."""
    if target_schema_fingerprint:
        row = _fetchval(conn, """
            SELECT schema_document FROM integration.schema_version
            WHERE target_system = %s AND fingerprint = %s AND approved_at IS NOT NULL
        """, (target_system, target_schema_fingerprint))
    else:
        row = _fetchval(conn, """
            SELECT schema_document FROM integration.schema_version
            WHERE target_system = %s AND approved_at IS NOT NULL
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
    staging = MySQLStagingConnector()

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
        src_fp = schema_fingerprint(source_schema)
        tgt_fp = schema_fingerprint(target_schema)

        # Update entity fingerprints
        _execute(conn, """
            UPDATE integration.onboarding_entity
            SET source_fingerprint = %s, target_fingerprint = %s, updated_at = now()
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
    """Deploy a proposal: create staging table, trigger, and enable entity."""
    from .services.common import ServiceError
    from .services.onboarding import deploy as deploy_service

    try:
        result = deploy_service(args.proposal, args.by)
    except ServiceError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))
    print(f"\nDeployment complete!")
    print(f"  Staging table: {result['staging_table']}")
    print(f"  Trigger: integration.{result['trigger']}")
    if result.get("snapshot"):
        print(f"  Pre-drop snapshot: {result['snapshot']}")
    print(f"\nNext steps:")
    print(f"  python -m src.pipeline backfill --entity <source_table>")
    print(f"  python -m src.worker")


def _create_staging_table(staging: MySQLStagingConnector, table_name: str,
                          mappings: list[dict], pk_columns: list[str]):
    """Create the MySQL staging table with proper schema."""
    # Build column definitions from mappings
    columns = [
        "`event_id` CHAR(36) NOT NULL",
        "`external_reference` CHAR(36) NOT NULL",
        "`source_system` VARCHAR(40) NOT NULL",
        "`operation` VARCHAR(20) NOT NULL",
        "`source_updated_at` DATETIME(6) NOT NULL",
        "`mapping_version` INT NOT NULL",
        "`payload_checksum` CHAR(64) NOT NULL",
    ]

    # Track which columns we've already added (envelope fields)
    envelope_fields = {"event_id", "external_reference", "source_system", "operation",
                       "source_updated_at", "mapping_version", "payload_checksum", "active", "accepted_at"}

    # Add mapped business columns (skip envelope fields)
    for m in mappings:
        col_name = m["target_column"]
        if col_name in envelope_fields:
            continue  # Skip - already defined as envelope field
        # Default type - in production, derive from target contract
        col_type = "VARCHAR(255)"
        nullable = "NULL"
        columns.append(f"`{col_name}` {col_type} {nullable}")
        envelope_fields.add(col_name)  # Track to avoid duplicates

    columns.append("`active` TINYINT(1) NOT NULL DEFAULT 1")
    columns.append("`accepted_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)")

    # Drop table if exists (for redeployment)
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        conn.commit()

    create_sql = f"CREATE TABLE `{table_name}` (\n"
    create_sql += ",\n".join(f"  {c}" for c in columns)
    create_sql += f",\n  PRIMARY KEY (`event_id`)"
    create_sql += f",\n  UNIQUE KEY `uk_external_reference` (`external_reference`)"
    create_sql += "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"

    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(create_sql)
        conn.commit()
    print(f"  Created table: {table_name}")


def _create_source_trigger(conn, source_schema: str, source_table: str,
                           pk_columns: list[str], updated_at_column: str | None):
    """Create or replace a reusable PostgreSQL trigger function and trigger."""
    # Create the reusable trigger function (if not exists)
    trigger_func_sql = """
        CREATE OR REPLACE FUNCTION integration.enqueue_entity_change() RETURNS trigger AS $func$
        DECLARE
            record_json JSONB;
            record_ref UUID;
            op integration.event_operation;
            pk_values TEXT := '';
            src_schema TEXT;
            src_table TEXT;
        BEGIN
            src_schema := TG_ARGV[0];
            src_table := TG_ARGV[1];

            IF TG_OP = 'DELETE' THEN
                record_json := to_jsonb(OLD);
            ELSE
                record_json := to_jsonb(NEW);
            END IF;

            -- Build PK values from trigger arguments (skip first two which are schema/table)
            FOR i IN 2..array_length(TG_ARGV, 1) LOOP
                IF i > 2 THEN pk_values := pk_values || '|'; END IF;
                pk_values := pk_values || COALESCE(record_json ->> TG_ARGV[i], '');
            END LOOP;

            -- Generate deterministic external reference using uuid_generate_v5
            record_ref := uuid_generate_v5(
                '12345678-1234-5678-1234-567812345678'::uuid,
                src_schema || '|' || src_table || '|' || pk_values
            );

            op := CASE WHEN TG_OP = 'DELETE' THEN 'deactivate'::integration.event_operation
                       WHEN TG_OP = 'INSERT' THEN 'insert'::integration.event_operation
                       ELSE 'update'::integration.event_operation END;

            IF TG_OP = 'DELETE' THEN
                record_json := record_json || jsonb_build_object('active', false, 'deactivated_at', now());
            END IF;

            INSERT INTO integration.outbox
                (source_entity, external_reference, operation, payload, payload_checksum, source_updated_at)
            VALUES
                (src_table, record_ref, op, record_json,
                 encode(digest(record_json::text, 'sha256'), 'hex'), now());

            RETURN COALESCE(NEW, OLD);
        END;
        $func$ LANGUAGE plpgsql;
    """
    _execute(conn, trigger_func_sql)

    # Drop existing trigger if any
    _execute(conn, f"""
        DROP TRIGGER IF EXISTS trg_{source_schema}_{source_table}_outbox ON {source_schema}.{source_table}
    """)

    # Create trigger with arguments
    pk_args = ", ".join(f"'{col}'" for col in pk_columns)
    trigger_args = f"'{source_schema}', '{source_table}', {pk_args}"

    _execute(conn, f"""
        CREATE TRIGGER trg_{source_schema}_{source_table}_outbox
        AFTER INSERT OR UPDATE OR DELETE ON {source_schema}.{source_table}
        FOR EACH ROW EXECUTE FUNCTION integration.enqueue_entity_change({trigger_args})
    """)
    print(f"  Created trigger: trg_{source_schema}_{source_table}_outbox")


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


def cmd_reconcile(args):
    """Reconcile external references and payload checksums between central and staging."""
    central = PostgresCentralConnector()
    staging = MySQLStagingConnector()

    with central.connection() as conn:
        # Get entity
        entities = _query(conn, """
            SELECT * FROM integration.onboarding_entity
            WHERE source_table = %s AND status = 'deployed'
        """, (args.entity,))
        if not entities:
            print(f"Error: Entity '{args.entity}' not found or not deployed.")
            sys.exit(1)
        entity = entities[0]

        staging_table = entity["staging_table"]
        source_table = entity["source_table"]

        # Get delivered events from central
        delivered = _query(conn, """
            SELECT external_reference, payload_checksum
            FROM integration.outbox
            WHERE source_entity = %s AND status = 'delivered'
        """, (source_table,))

        # Get staging records
        with staging.connection() as sconn:
            with sconn.cursor(dictionary=True) as cur:
                cur.execute(f"SELECT external_reference, payload_checksum FROM `{staging_table}`")
                staging_rows = cur.fetchall()

        # Compare
        central_refs = {row["external_reference"]: row["payload_checksum"] for row in delivered}
        staging_refs = {row["external_reference"]: row["payload_checksum"] for row in staging_rows}

        missing_in_staging = set(central_refs.keys()) - set(staging_refs.keys())
        missing_in_central = set(staging_refs.keys()) - set(central_refs.keys())
        checksum_mismatches = {
            ref for ref in set(central_refs.keys()) & set(staging_refs.keys())
            if central_refs[ref] != staging_refs[ref]
        }

        print(f"\nReconciliation: {source_table} -> {staging_table}")
        print("=" * 60)
        print(f"Central delivered: {len(delivered)}")
        print(f"Staging rows: {len(staging_rows)}")
        print(f"Missing in staging: {len(missing_in_staging)}")
        print(f"Missing in central: {len(missing_in_central)}")
        print(f"Checksum mismatches: {len(checksum_mismatches)}")

        if missing_in_staging:
            print(f"\nMissing in staging (first 5):")
            for ref in list(missing_in_staging)[:5]:
                print(f"  - {ref}")

        if checksum_mismatches:
            print(f"\nChecksum mismatches (first 5):")
            for ref in list(checksum_mismatches)[:5]:
                print(f"  - {ref}")

        status = "OK" if not missing_in_staging and not checksum_mismatches else "MISMATCH"
        print(f"\nStatus: {status}")

    central.close()


def cmd_monitor(args):
    """Monitor source and target schema drift."""
    central = PostgresCentralConnector()
    staging = MySQLStagingConnector()

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
    """Refresh staging tables by dropping and recreating with fresh data."""
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
# Onboard command (end-to-end)
# ---------------------------------------------------------------------------

def _onboard_single_table(
    conn,
    central: PostgresCentralConnector,
    staging: MySQLStagingConnector,
    source_schema: str,
    source_table: str,
    target_system: str,
    auto: bool,
    batch_size: int,
    operator: str,
) -> dict:
    """Onboard a single table end-to-end. Returns result dict."""
    from .terminal_ui import (
        console, print_header, print_field_mapping_table,
        prompt_yes_no, prompt_text, print_progress,
        print_deployment_summary, print_cross_table_candidates,
    )

    result = {
        "source_schema": source_schema,
        "source_table": source_table,
        "status": "pending",
        "mappings_accepted": 0,
        "cross_table": 0,
        "source_count": 0,
        "backfill_count": 0,
        "worker_delivered": 0,
    }

    print_header(f"Onboarding: {source_schema}.{source_table}")

    # Step 1: Discover
    log.info("Step 1/6: Discovering source schema...")
    source_schema_obj = _discover_source_schema(conn, source_schema)
    source_table_obj = source_schema_obj.get_table(source_table)
    if not source_table_obj:
        log.error("Table %s.%s not found", source_schema, source_table)
        return result

    target_schema = _discover_target_schema(conn, target_system)
    pk_cols = [c.name for c in source_table_obj.columns if c.is_primary_key]
    if not pk_cols:
        pk_cols = ["id"]
    updated_at_col = None
    for c in source_table_obj.columns:
        if c.name.lower() in ("updated_at", "modified_at", "last_updated", "timestamp", "updated_at"):
            updated_at_col = c.name
            break

    candidates = _rank_target_tables(source_table_obj, target_schema) if target_schema else []
    log.info("Source: %s.%s (%d columns)", source_schema, source_table, len(source_table_obj.columns))
    if candidates:
        log.info("Best target match: %s (score: %.2f)", candidates[0]["table"], candidates[0]["score"])

    entity = _get_or_create_entity(conn, source_schema, source_table, target_system, pk_cols, updated_at_col)

    # Check if already deployed
    if entity.get("status") == "deployed":
        log.info("Table %s.%s is already deployed (staging: %s). Skipping.", source_schema, source_table, entity.get("staging_table"))
        return {
            "source_schema": source_schema,
            "source_table": source_table,
            "status": "already_deployed",
            "mappings_accepted": 0,
            "cross_table": 0,
            "source_count": 0,
            "backfill_count": 0,
            "worker_delivered": 0,
        }

    # Step 2: Propose
    log.info("Step 2/6: Calling Gemini AI for mapping proposal...")
    source_fp = schema_fingerprint(source_schema_obj)
    target_fp = schema_fingerprint(target_schema) if target_schema else ""

    try:
        mappings_raw = propose_mapping(source_table_obj, target_schema)
        gemini_response = {"mappings": mapping_to_dicts(mappings_raw)}
    except Exception as e:
        log.error("Gemini error: %s", e)
        mappings_raw = []
        gemini_response = None

    mappings = mapping_to_dicts(mappings_raw) if mappings_raw else []

    # Step 3: Interactive review
    log.info("Step 3/6: Reviewing field mappings...")
    accepted = []
    rejected = []
    pending = []

    for m in mappings:
        confidence = m.get("confidence", 0.0)
        target_col = m.get("target_column")
        if confidence >= CONFIDENCE_THRESHOLD:
            m["status"] = "accepted"
            accepted.append(m)
        elif confidence == 0.0 or target_col is None or target_col == "?":
            m["status"] = "rejected"
            rejected.append(m)
        else:
            m["status"] = "pending"
            pending.append(m)

    if not auto:
        print_field_mapping_table(mappings)

    if not auto:
        if prompt_yes_no("\nAccept all high-confidence (>=0.95) mappings?", default=True):
            for m in pending:
                m["status"] = "accepted"
                accepted.append(m)
            pending = []
        else:
            still_pending = []
            for i, m in enumerate(pending):
                if prompt_yes_no(
                    f"  {m['source_column']} -> {m.get('target_column', '?')} ({m['confidence']:.2f})?",
                    default=True,
                ):
                    m["status"] = "accepted"
                    accepted.append(m)
                else:
                    custom = prompt_text(
                        f"  Enter custom target column (or press Enter to skip):", default=""
                    )
                    if custom:
                        m["target_column"] = custom
                        m["status"] = "accepted"
                        accepted.append(m)
                    else:
                        still_pending.append(m)
            pending = still_pending

    # Save proposal
    proposal_id = _create_proposal(
        conn, entity["id"], source_fp, target_fp,
        mappings, [], [], gemini_response,
        len(accepted), len(pending), len(rejected),
    )

    # Update accepted/rejected statuses
    for m in accepted:
        _execute(conn, """
            UPDATE integration.onboarding_field_review
            SET status = 'accepted'
            WHERE proposal_id = %s AND source_column = %s
        """, (proposal_id, m["source_column"]))

    for m in rejected:
        _execute(conn, """
            UPDATE integration.onboarding_field_review
            SET status = 'rejected'
            WHERE proposal_id = %s AND source_column = %s
        """, (proposal_id, m["source_column"]))

    # Mark proposal as approved
    _execute(conn, """
        UPDATE integration.onboarding_proposal
        SET status = 'approved', reviewed_by = %s, reviewed_at = now(), updated_at = now()
        WHERE id = %s
    """, (operator, proposal_id))
    conn.commit()

    result["mappings_accepted"] = len(accepted)

    # Step 4: Deploy
    log.info("Step 4/6: Deploying staging table and trigger...")
    staging_table = f"irimsv_{source_table}_staging"

    # Build accepted mappings for deploy
    deploy_mappings = []
    for m in accepted:
        target_col = m.get("target_column")
        transform = m.get("transform", "none")
        deploy_mappings.append({
            "source_column": m["source_column"],
            "target_table": staging_table,
            "target_column": target_col,
            "confidence": m["confidence"],
            "transform": transform,
        })

    if not auto:
        collation = _detect_collation(staging, "lrmis_staging")
        col_types = []
        for dm in deploy_mappings:
            col_type = _infer_column_type(target_schema, staging_table, dm["target_column"])
            col_types.append({"name": dm["target_column"], "type": col_type})
        print_deployment_summary(
            f"{source_schema}.{source_table}", staging_table, col_types, collation
        )
        if not prompt_yes_no("Deploy staging table + trigger?", default=True):
            log.info("Deployment cancelled by user")
            return result

    # Create staging table
    _create_staging_table(staging, staging_table, deploy_mappings, pk_cols)

    # Create trigger
    _create_source_trigger(conn, source_schema, source_table, pk_cols, updated_at_col)

    # Save schema version
    staging_rows = staging.information_schema("lrmis_staging")
    staging_schema = from_information_schema(staging_rows, "LRMIS")
    staging_tables = [t for t in staging_schema.tables if t.name == staging_table]
    if staging_tables:
        staging_fp = schema_fingerprint(Schema(system_name="LRMIS", tables=staging_tables))
        staging_doc = Schema(system_name="LRMIS", tables=staging_tables).to_dict()
        _execute(conn, """
            INSERT INTO integration.schema_version (target_system, fingerprint, schema_document, approved_at, approved_by)
            VALUES (%s, %s, %s, now(), %s)
            ON CONFLICT (target_system, fingerprint) DO UPDATE SET schema_document = %s, approved_at = now()
        """, (target_system, staging_fp, json.dumps(staging_doc), operator, json.dumps(staging_doc)))

    # Update entity status
    _execute(conn, """
        UPDATE integration.onboarding_entity
        SET status = 'deployed', staging_table = %s, deployed_by = %s, deployed_at = now(), updated_at = now()
        WHERE id = %s
    """, (staging_table, operator, entity["id"]))

    # Create entity_control
    _execute(conn, """
        INSERT INTO integration.entity_control (source_entity, target_system, enabled)
        VALUES (%s, %s, true)
        ON CONFLICT (source_entity, target_system) DO UPDATE SET enabled = true, paused_reason = NULL
    """, (source_table, target_system))

    # Audit
    _execute(conn, """
        INSERT INTO integration.onboarding_audit (entity_id, proposal_id, action, details, performed_by)
        VALUES (%s, %s, 'deploy', %s, %s)
    """, (entity["id"], proposal_id, json.dumps({"staging_table": staging_table}), operator))

    conn.commit()
    log.info("Staging table created: %s", staging_table)

    # Step 5: Fast Backfill (drop + recreate + bulk load)
    log.info("Step 5/6: Fast backfill...")
    from .fast_refresh import generate_refresh_sql, fetch_and_bulk_insert, drop_staging_table

    # Drop and recreate staging table for fresh data
    drop_staging_table(staging, staging_table)
    _create_staging_table(staging, staging_table, deploy_mappings, pk_cols)

    # Generate SQL and bulk insert
    sql = generate_refresh_sql(
        source_schema, source_table, staging_table,
        mappings, operator.upper(), pk_cols,
        updated_at_column=updated_at_col,
    )
    log.debug("Generated SQL:\n%s", sql)

    # Build column list for MySQL insert (must match SELECT order from generate_refresh_sql)
    columns = [
        "event_id", "external_reference", "source_system", "operation",
        "source_updated_at", "mapping_version", "payload_checksum",
        "active", "accepted_at",
    ]
    envelope_fields = set(columns)
    for m in mappings:
        target_col = m.get("target_column")
        if target_col and target_col not in envelope_fields:
            columns.append(target_col)
            envelope_fields.add(target_col)

    count = fetch_and_bulk_insert(conn, staging, sql, staging_table, columns, batch_size)
    result["source_count"] = count
    result["backfill_count"] = count
    log.info("Loaded %d rows into %s", count, staging_table)

    if not auto:
        log.info("Backfill complete: %d rows loaded", count)

    # Cross-table candidates
    rejected_columns = [m["source_column"] for m in rejected]
    cross_candidates = _detect_cross_table_candidates(
        conn, source_schema, source_table, rejected_columns, target_system
    )

    if cross_candidates and not auto:
        print_cross_table_candidates(cross_candidates)
        if prompt_yes_no("Save these as cross-table candidates?", default=True):
            for c in cross_candidates:
                _execute(conn, """
                    INSERT INTO integration.onboarding_field_review
                        (proposal_id, source_column, suggested_target_table, suggested_target_column,
                         confidence, transform, reasoning, status, cross_table_candidate)
                    VALUES (%s, %s, %s, %s, %s, 'none', 'cross-table candidate', 'pending', true)
                """, (proposal_id, c["source_column"], c["target_table"], c["target_column"], c["confidence"]))
            conn.commit()
            result["cross_table"] = len(cross_candidates)

    result["status"] = "deployed"
    return result


def cmd_onboard(args):
    """Onboard one or more tables end-to-end."""
    from .terminal_ui import print_final_summary, console

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    central = PostgresCentralConnector()
    staging = MySQLStagingConnector()

    tables = [t.strip() for t in args.source_table.split(",")]
    results = []

    for table in tables:
        with central.connection() as conn:
            result = _onboard_single_table(
                conn, central, staging, args.source_schema, table,
                args.target_system, args.auto, args.batch_size, args.by,
            )
            results.append(result)

    print_final_summary(results)
    central.close()


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

    # reconcile
    p_reconcile = subparsers.add_parser("reconcile", help="Reconcile central vs staging")
    p_reconcile.add_argument("--entity", required=True, help="Entity/table name")

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="Monitor schema drift")

    # status
    p_status = subparsers.add_parser("status", help="Show onboarding status for all entities")

    # refresh
    p_refresh = subparsers.add_parser("refresh", help="Refresh staging tables (drop + recreate + load)")
    p_refresh.add_argument("--source-schema", required=True, help="Source schema name")
    p_refresh.add_argument("--source-table", required=True, help="Source table(s), comma-separated")
    p_refresh.add_argument("--target-system", required=True, help="Target system name")
    p_refresh.add_argument("--source-system", default="IRIMSV_REGION_V", help="Source system name")
    p_refresh.add_argument("--batch-size", type=int, default=1000, help="MySQL batch size")
    p_refresh.add_argument("--schedule", default=None, help="Schedule (e.g., daily, weekly, monthly)")

    # onboard
    p_onboard = subparsers.add_parser("onboard", help="Onboard one or more tables end-to-end")
    p_onboard.add_argument("--source-schema", required=True, help="Source schema name")
    p_onboard.add_argument("--source-table", required=True, help="Source table(s), comma-separated")
    p_onboard.add_argument("--target-system", required=True, help="Target system name")
    p_onboard.add_argument("--auto", action="store_true", help="Non-interactive mode")
    p_onboard.add_argument("--batch-size", type=int, default=100, help="Worker batch size")
    p_onboard.add_argument("--by", default="pipeline", help="Operator name")

    args = parser.parse_args()

    commands = {
        "discover": cmd_discover,
        "propose": cmd_propose,
        "review": cmd_review,
        "resolve": cmd_resolve,
        "deploy": cmd_deploy,
        "backfill": cmd_backfill,
        "reconcile": cmd_reconcile,
        "monitor": cmd_monitor,
        "onboard": cmd_onboard,
        "status": cmd_status,
        "refresh": cmd_refresh,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
