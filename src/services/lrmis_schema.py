"""LRMIS target-schema versioning and drift (Path B, Phase 7).

Records the fingerprint of the canonical LRMIS DDL (sha256 of lrmis.sql) so a
change to the target structure itself is detectable, and plans a redeploy
(drop + recreate lrmis_target, then re-refresh deployed Path B entities). The
redeploy PLAN is always safe to run; the destructive apply is deliberately a
separate, admin-triggered action and never fired automatically.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from ..lrmis_registry import ddl_path

SCOPE_KIND = "target_ddl"
SCOPE_NAME = "lrmis_target"


def current_ddl_fingerprint(path: str | None = None) -> str:
    return hashlib.sha256(Path(path or ddl_path()).read_bytes()).hexdigest()


def stored_ddl_fingerprint(conn, target_system: str = "LRMIS") -> str | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fingerprint FROM integration.schema_version
            WHERE target_system = %s AND scope_kind = %s AND scope_name = %s
            ORDER BY observed_at DESC LIMIT 1
        """, (target_system, SCOPE_KIND, SCOPE_NAME))
        row = cur.fetchone()
    return row[0] if row else None


def record_ddl_fingerprint(by: str, target_system: str = "LRMIS",
                           central: PostgresCentralConnector | None = None,
                           path: str | None = None) -> dict:
    """Approve the current LRMIS DDL as the accepted target structure."""
    fingerprint = current_ddl_fingerprint(path)
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration.schema_version
                        (target_system, scope_kind, scope_name, fingerprint,
                         schema_document, approved_at, approved_by)
                    VALUES (%s, %s, %s, %s, %s, now(), %s)
                    ON CONFLICT (target_system, scope_kind, scope_name, fingerprint)
                    DO UPDATE SET approved_at = now(), approved_by = EXCLUDED.approved_by
                """, (target_system, SCOPE_KIND, SCOPE_NAME, fingerprint,
                      psycopg2.extras.Json({"source": str(path or ddl_path())}), by))
            conn.commit()
        return {"fingerprint": fingerprint, "approved_by": by}
    finally:
        if owns:
            central.close()


def check_ddl_drift(central: PostgresCentralConnector | None = None,
                    target_system: str = "LRMIS", path: str | None = None) -> dict:
    """Compare the current lrmis.sql DDL against the last approved fingerprint."""
    current = current_ddl_fingerprint(path)
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            stored = stored_ddl_fingerprint(conn, target_system)
        return {
            "current": current,
            "stored": stored,
            "drifted": stored is not None and stored != current,
            "unversioned": stored is None,
        }
    finally:
        if owns:
            central.close()


def redeploy_plan(central: PostgresCentralConnector | None = None,
                  target_system: str = "LRMIS", path: str | None = None) -> dict:
    """Describe what a target redeploy would do, without changing anything.

    A redeploy drops and recreates lrmis_target from the current DDL and then
    re-refreshes every deployed Path B entity from source. This returns the
    affected entities and the drift status so an admin can decide; applying it
    is a separate, explicit action (scripts/init_lrmis_target.py recreates the
    schema; lrmis_delivery.refresh_entity reloads each entity)."""
    p = _pipeline()
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            drift = {
                "current": current_ddl_fingerprint(path),
                "stored": stored_ddl_fingerprint(conn, target_system),
            }
            entities = p._query(conn, """
                SELECT source_schema, source_table, lrmis_target_tables
                FROM integration.onboarding_entity
                WHERE status = 'deployed' AND lrmis_target_tables IS NOT NULL
                  AND target_system = %s
                ORDER BY source_table
            """, (target_system,))
        drift["drifted"] = drift["stored"] is not None and drift["stored"] != drift["current"]
        return {
            "drift": drift,
            "path_b_entities": entities,
            "would_refresh": [e["source_table"] for e in entities],
            "note": "destructive apply not performed; run explicitly after review",
        }
    finally:
        if owns:
            central.close()


def _pipeline():
    from .. import pipeline
    return pipeline
