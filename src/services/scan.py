"""Schema scanning: fingerprint source + target, record drift, report paused entities.

Combines scripts/schema_monitor.py's target observation with the per-entity
drift check from src.pipeline cmd_monitor, returning one structured result.
"""
from __future__ import annotations

import os

import psycopg2.extras

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from ..schema_drift import diff_schemas, record_drift
from ..schema_ingest import from_information_schema, schema_fingerprint
from ..schema_models import Schema
from . import ops


def observe_target(approve_initial: bool = False, approved_by: str | None = None,
                   central: PostgresCentralConnector | None = None,
                   staging: MySQLStagingConnector | None = None) -> dict:
    """Fingerprint the MySQL staging contract and record drift against the last version."""
    target = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
    staging = staging or MySQLStagingConnector()
    owns = central is None
    central = central or PostgresCentralConnector()
    observed = from_information_schema(staging.information_schema(), target)
    fingerprint = schema_fingerprint(observed)
    result = {"target_system": target, "fingerprint": fingerprint,
              "differences": [], "impacted": [], "drift": False}
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM integration.schema_version WHERE target_system = %s
                    ORDER BY observed_at DESC LIMIT 1
                """, (target,))
                previous_row = cur.fetchone()
                previous = Schema.from_dict(previous_row["schema_document"]) if previous_row else None
                previous_fingerprint = previous_row["fingerprint"] if previous_row else None
                if previous:
                    result["differences"] = diff_schemas(previous, observed)
                cur.execute("""
                    INSERT INTO integration.schema_version
                        (target_system, fingerprint, schema_document, approved_at, approved_by)
                    VALUES (%s, %s, %s, CASE WHEN %s THEN now() END, %s)
                    ON CONFLICT (target_system, fingerprint) DO NOTHING
                """, (target, fingerprint, psycopg2.extras.Json(observed.to_dict()),
                      approve_initial and previous is None, approved_by))
                if previous_fingerprint != fingerprint and previous is not None:
                    result["drift"] = True
                    result["impacted"] = record_drift(
                        conn, target, previous_fingerprint, fingerprint, result["differences"])
            conn.commit()
        return result
    finally:
        if owns:
            central.close()


def scan(approve_initial: bool = False, by: str | None = None,
         central: PostgresCentralConnector | None = None,
         staging: MySQLStagingConnector | None = None) -> dict:
    """Full scan: observe the target contract, then drift-check every deployed entity."""
    target_result = observe_target(approve_initial, by, central=central, staging=staging)
    entity_result = ops.monitor(central=central)
    paused = sorted(set(target_result["impacted"]) | set(entity_result["paused_entities"]))
    return {
        "target": target_result,
        "entities": entity_result["entities"],
        "paused_entities": paused,
        "drift_detected": target_result["drift"] or bool(entity_result["paused_entities"]),
    }
