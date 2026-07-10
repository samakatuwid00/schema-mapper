"""Durable PostgreSQL state transitions for delivery, replay, and audit."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import psycopg2.extras


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def checksum(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def claim_events(conn, target_system: str, limit: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT o.* FROM integration.outbox o
            JOIN integration.entity_control c
              ON c.source_entity = o.source_entity AND c.target_system = o.target_system
            WHERE o.target_system = %s
              AND o.status IN ('pending', 'retry')
              AND o.available_at <= now() AND c.enabled
            ORDER BY o.created_at LIMIT %s FOR UPDATE OF o SKIP LOCKED
        """, (target_system, limit))
        events = [dict(row) for row in cur.fetchall()]
        if events:
            cur.execute("""
                UPDATE integration.outbox SET status = 'processing'
                WHERE event_id = ANY(%s::uuid[])
            """, ([str(e["event_id"]) for e in events],))
        return events


def approved_mapping(conn, source_entity: str, target_system: str) -> dict | None:
    """Look for approved mapping in both mapping_version and onboarding_proposal tables."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # First, check the legacy mapping_version table
        cur.execute("""
            SELECT * FROM integration.mapping_version
            WHERE source_entity = %s AND target_system = %s AND status = 'approved'
        """, (source_entity, target_system))
        row = cur.fetchone()
        if row:
            return dict(row)

        # If not found, check the new onboarding_proposal table
        cur.execute("""
            SELECT p.*, e.source_table, e.staging_table, e.target_system, e.target_fingerprint
            FROM integration.onboarding_proposal p
            JOIN integration.onboarding_entity e ON p.entity_id = e.id
            WHERE e.source_table = %s AND e.target_system = %s
              AND p.status IN ('approved', 'auto_approved')
              AND e.status = 'deployed'
            ORDER BY p.created_at DESC
            LIMIT 1
        """, (source_entity, target_system))
        row = cur.fetchone()
        if row:
            # Convert onboarding_proposal to mapping_version format
            result = dict(row)
            result["id"] = result["id"]
            result["version"] = 1
            result["target_table"] = result.get("staging_table", f"irimsv_{source_entity}_staging")
            result["mappings"] = result.get("mappings", [])
            result["schema_fingerprint"] = result.get("target_fingerprint", "")
            return result

        return None


def save_projection(conn, event: dict, mapping: dict, payload: dict):
    payload_hash = checksum(payload)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.projection_record
                (event_id, external_reference, target_system, target_table,
                 mapping_version_id, payload, payload_checksum)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
        """, (event["event_id"], event["external_reference"], event["target_system"],
              mapping["target_table"], mapping["id"],
              psycopg2.extras.Json(json.loads(canonical_json(payload))), payload_hash))
        cur.execute("UPDATE integration.outbox SET mapping_version_id = %s WHERE event_id = %s",
                    (mapping["id"], event["event_id"]))


def quarantine(conn, event: dict, errors: list[str], mapping_id: int | None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO integration.quarantine
                (event_id, errors, payload_snapshot, mapping_version_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET errors = EXCLUDED.errors
        """, (event["event_id"], psycopg2.extras.Json(errors),
              psycopg2.extras.Json(event["payload"]), mapping_id))
        cur.execute("""
            UPDATE integration.outbox SET status = 'quarantined', processed_at = now(),
                last_error_code = 'VALIDATION', last_error_message = %s
            WHERE event_id = %s
        """, ("; ".join(errors), event["event_id"]))


def delivered(conn, event: dict, mapping: dict, target_id: str | None = None):
    attempt = event["attempts"] + 1
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE integration.outbox SET status = 'delivered', attempts = %s,
                processed_at = now(), last_error_code = NULL, last_error_message = NULL
            WHERE event_id = %s
        """, (attempt, event["event_id"]))
        cur.execute("UPDATE integration.projection_record SET delivered_at = now() WHERE event_id = %s",
                    (event["event_id"],))
        cur.execute("""
            INSERT INTO integration.delivery_audit (event_id, attempt, outcome, response_code)
            VALUES (%s, %s, 'accepted', 'UPSERTED')
        """, (event["event_id"], attempt))
        cur.execute("""
            INSERT INTO integration.id_crosswalk
                (source_system, source_entity, external_reference, target_system,
                 target_table, target_id, last_event_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_system, source_entity, external_reference, target_system, target_table)
            DO UPDATE SET target_id = COALESCE(EXCLUDED.target_id, integration.id_crosswalk.target_id),
                          last_event_id = EXCLUDED.last_event_id, updated_at = now()
        """, (event["source_system"], event["source_entity"], event["external_reference"],
              event["target_system"], mapping["target_table"], target_id, event["event_id"]))


def mark_event_delivered(conn, event: dict) -> None:
    """Mark an outbox event delivered without touching the crosswalk.

    The Path B multi-table writer records one crosswalk row per target table
    itself, so this only advances the outbox status and audit trail (unlike
    delivered(), which owns the single-table crosswalk write)."""
    attempt = event["attempts"] + 1
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE integration.outbox SET status = 'delivered', attempts = %s,
                processed_at = now(), last_error_code = NULL, last_error_message = NULL
            WHERE event_id = %s
        """, (attempt, event["event_id"]))
        cur.execute("""
            INSERT INTO integration.delivery_audit (event_id, attempt, outcome, response_code)
            VALUES (%s, %s, 'accepted', 'LRMIS_MULTI')
        """, (event["event_id"], attempt))


def retry_or_dead_letter(conn, event: dict, error: Exception, max_attempts: int):
    attempt = event["attempts"] + 1
    terminal = attempt >= max_attempts
    delay_seconds = min(3600, 2 ** min(attempt, 10) * 15)
    available_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE integration.outbox SET status = %s, attempts = %s, available_at = %s,
                processed_at = CASE WHEN %s THEN now() ELSE NULL END,
                last_error_code = 'TRANSPORT', last_error_message = %s
            WHERE event_id = %s
        """, ('dead_letter' if terminal else 'retry', attempt, available_at,
              terminal, str(error)[:2000], event["event_id"]))
        cur.execute("""
            INSERT INTO integration.delivery_audit
                (event_id, attempt, outcome, response_code, response_message)
            VALUES (%s, %s, 'transport_error', 'TRANSPORT', %s)
        """, (event["event_id"], attempt, str(error)[:2000]))


def replay(conn, event_id: str):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE integration.outbox SET status = 'pending', attempts = 0,
                available_at = now(), processed_at = NULL,
                last_error_code = NULL, last_error_message = NULL
            WHERE event_id = %s AND status IN ('quarantined', 'dead_letter', 'delivered')
        """, (event_id,))
        if cur.rowcount != 1:
            raise ValueError("event not found or is not replayable")
        cur.execute("DELETE FROM integration.quarantine WHERE event_id = %s", (event_id,))
