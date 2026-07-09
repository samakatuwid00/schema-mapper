"""Observe LRMIS staging metadata, record drift, and selectively pause mappings."""
from __future__ import annotations

import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2.extras

from src.connectors import MySQLStagingConnector, PostgresCentralConnector
from src.schema_drift import diff_schemas, record_drift
from src.schema_ingest import from_information_schema, schema_fingerprint
from src.schema_models import Schema


def observe(approve_initial: bool = False, approved_by: str | None = None) -> dict:
    target = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
    mysql = MySQLStagingConnector()
    central = PostgresCentralConnector()
    observed = from_information_schema(mysql.information_schema(), target)
    fingerprint = schema_fingerprint(observed)
    result = {"target_system": target, "fingerprint": fingerprint, "differences": [], "impacted": []}
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
                    result["impacted"] = record_drift(
                        conn, target, previous_fingerprint, fingerprint, result["differences"]
                    )
            conn.commit()
        return result
    finally:
        central.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-initial", action="store_true",
                        help="Approve only the first observed contract; later changes still require review")
    parser.add_argument("--by", default="integration-admin")
    args = parser.parse_args()
    print(json.dumps(observe(args.approve_initial, args.by), indent=2, default=str))


if __name__ == "__main__":
    main()
