"""Legacy-staging retirement — the migration gate (retire-legacy-staging §0).

Read-only precheck: the destructive cutover must not proceed while any
`status='deployed'` entity still lacks a direct-delivery target footprint
(`lrmis_target_tables IS NULL`) — i.e. is still legacy-staging-only. This module
is the programmatic gate the cutover and its CI check consult.
"""
from __future__ import annotations

import psycopg2.extras

from ..connectors import PostgresCentralConnector

TARGET_SYSTEM = "LRMIS"

# States in which a legacy-only entity is (or is intended to be) delivering, and
# so would be STRANDED if staging were deleted before it is migrated. `disabled`
# (retired) and `discovered` (never onboarded, no staging table) are terminal and
# do not block the cutover.
DELIVERING_STATES = frozenset({"deployed", "paused", "reviewed"})


def all_entities(conn, target_system: str = TARGET_SYSTEM) -> list[dict]:
    """Every entity + its status and whether it has a direct-delivery footprint."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, source_table, status, lrmis_target_tables
            FROM integration.onboarding_entity
            WHERE target_system = %s
            ORDER BY status, source_table
        """, (target_system,))
        return [dict(r) for r in cur.fetchall()]


def summarize(rows: list[dict]) -> dict:
    """Pure gate computation. `ready` is True only when NO legacy-only entity is
    in a delivering/resumable state (deployed/paused/reviewed) — those would be
    stranded by staging deletion and must be migrated or disabled first."""
    legacy_only = [r for r in rows if r.get("lrmis_target_tables") is None]
    on_target = [r for r in rows if r.get("lrmis_target_tables") is not None]
    blocking = [r["source_table"] for r in legacy_only
                if r.get("status") in DELIVERING_STATES]
    by_status: dict[str, int] = {}
    for r in legacy_only:
        by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
    return {
        "total": len(rows),
        "on_target": len(on_target),
        "legacy_only_total": len(legacy_only),
        "legacy_only_by_status": by_status,
        "blocking": blocking,               # must migrate/disable before cutover
        "blocking_count": len(blocking),
        "ready": len(blocking) == 0,
    }


def precheck(central: PostgresCentralConnector | None = None,
             target_system: str = TARGET_SYSTEM) -> dict:
    """Run the migration gate. Changes nothing."""
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            rows = all_entities(conn, target_system)
    finally:
        if owns:
            central.close()
    result = summarize(rows)
    result["target_system"] = target_system
    return result
