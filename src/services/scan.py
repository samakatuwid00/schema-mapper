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
                    SELECT * FROM integration.schema_version
                    WHERE target_system = %s AND scope_kind = 'contract' AND scope_name = ''
                    ORDER BY observed_at DESC LIMIT 1
                """, (target,))
                previous_row = cur.fetchone()
                previous = Schema.from_dict(previous_row["schema_document"]) if previous_row else None
                previous_fingerprint = previous_row["fingerprint"] if previous_row else None
                if previous:
                    result["differences"] = diff_schemas(previous, observed)
                cur.execute("""
                    INSERT INTO integration.schema_version
                        (target_system, scope_kind, scope_name, fingerprint,
                         schema_document, approved_at, approved_by)
                    VALUES (%s, 'contract', '', %s, %s, CASE WHEN %s THEN now() END, %s)
                    ON CONFLICT (target_system, scope_kind, scope_name, fingerprint) DO NOTHING
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


def observe_staging_to_target(central: PostgresCentralConnector | None = None,
                              staging: MySQLStagingConnector | None = None,
                              target: MySQLStagingConnector | None = None) -> dict:
    """Fingerprint the Path B (lrmis_target) contract and diff it against the
    Path A (lrmis_staging) contract, recording ``staging->target`` drift.

    The Path B contract fingerprint is stored in the existing schema_version
    table under scope_name='target_b' (no new table)."""
    system = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
    staging = staging or MySQLStagingConnector()
    target = target or MySQLStagingConnector.for_target()
    owns = central is None
    central = central or PostgresCentralConnector()

    staging_contract = from_information_schema(staging.information_schema(), system)
    target_contract = from_information_schema(target.information_schema(), system)
    staging_fp = schema_fingerprint(staging_contract)
    target_fp = schema_fingerprint(target_contract)
    differences = diff_schemas(staging_contract, target_contract)
    result = {"target_system": system, "drift_pair": "staging->target",
              "fingerprint": target_fp, "staging_fingerprint": staging_fp,
              "differences": differences, "impacted": [], "drift": bool(differences)}
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO integration.schema_version
                        (target_system, scope_kind, scope_name, fingerprint, schema_document)
                    VALUES (%s, 'contract', 'target_b', %s, %s)
                    ON CONFLICT (target_system, scope_kind, scope_name, fingerprint) DO NOTHING
                """, (system, target_fp, psycopg2.extras.Json(target_contract.to_dict())))
                if differences:
                    result["impacted"] = record_drift(
                        conn, system, staging_fp, target_fp, differences,
                        drift_pair="staging->target")
            conn.commit()
        return result
    finally:
        if owns:
            central.close()


def scan(approve_initial: bool = False, by: str | None = None,
         mode: str = "source",
         central: PostgresCentralConnector | None = None,
         staging: MySQLStagingConnector | None = None,
         target: MySQLStagingConnector | None = None) -> dict:
    """Full scan of a drift pair.

    ``mode='source'`` (default) observes the IRIMSV->staging pair and drift-checks
    every deployed entity. ``mode='staging'`` observes the staging->target (Path B)
    pair. Both return the observed contract plus the drift pair label."""
    if mode == "staging":
        pair = observe_staging_to_target(central=central, staging=staging, target=target)
        return {
            "mode": "staging",
            "drift_pair": "staging->target",
            "target": pair,
            "entities": [],
            "paused_entities": sorted(set(pair["impacted"])),
            "drift_detected": pair["drift"],
        }

    target_result = observe_target(approve_initial, by, central=central, staging=staging)
    entity_result = ops.monitor(central=central, staging=staging)
    paused = sorted(set(target_result["impacted"]) | set(entity_result["paused_entities"]))
    return {
        "mode": "source",
        "drift_pair": "source->staging",
        "target": target_result,
        "entities": entity_result["entities"],
        "paused_entities": paused,
        "drift_detected": target_result["drift"] or bool(entity_result["paused_entities"]),
    }
