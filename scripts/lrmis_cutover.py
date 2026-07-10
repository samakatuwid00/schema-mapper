"""Read-only Path B cutover status (Phase 9 planning).

Reports where each entity sits (legacy staging vs LRMIS target), the target
DDL drift status, and what remains to migrate. Changes nothing: the actual
cutover (re-onboarding each legacy entity to lrmis_target, then dropping the
old staging tables and decommissioning lrmis_staging) is destructive and left
as explicit, reviewed steps - see the printed checklist.

Usage: python scripts/lrmis_cutover.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2.extras

from src.connectors import MySQLStagingConnector, PostgresCentralConnector
from src.services import lrmis_schema


def main() -> int:
    target_system = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
    central = PostgresCentralConnector()
    try:
        with central.connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT source_table, status,
                           lrmis_target_tables IS NOT NULL AS path_b,
                           staging_table
                    FROM integration.onboarding_entity
                    WHERE target_system = %s
                    ORDER BY source_table
                """, (target_system,))
                entities = [dict(r) for r in cur.fetchall()]
            drift = lrmis_schema.check_ddl_drift(central=central, target_system=target_system)

        path_b = [e for e in entities if e["path_b"]]
        legacy = [e for e in entities if not e["path_b"]]
        deployed_legacy = [e for e in legacy if e["status"] == "deployed"]

        print("=== Path B cutover status ===\n")
        print(f"entities total        : {len(entities)}")
        print(f"  on LRMIS target (Path B): {len(path_b)}")
        print(f"  on legacy staging       : {len(legacy)}  "
              f"({len(deployed_legacy)} deployed)")
        print()
        print("target DDL fingerprint:")
        print(f"  current : {drift['current'][:16]}...")
        print(f"  approved: {(drift['stored'] or '(none - unversioned)')[:16]}"
              + ("..." if drift["stored"] else ""))
        print(f"  drifted : {drift['drifted']}")

        # target readiness
        try:
            staging = MySQLStagingConnector.for_target()
            with staging.connection() as c:
                with c.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                                "WHERE table_schema = DATABASE()")
                    tables = cur.fetchone()[0]
            print(f"\nlrmis_target: reachable, {tables} tables present")
        except Exception as exc:
            print(f"\nlrmis_target: NOT reachable -> {exc}")

        if path_b:
            print("\nPath B entities:")
            for e in path_b:
                print(f"  - {e['source_table']:24} {e['status']:10} "
                      f"tables={e['lrmis_target_tables'] if 'lrmis_target_tables' in e else '?'}")

        print("\n=== remaining cutover steps (destructive - run explicitly) ===")
        print("  1. Per legacy entity: re-propose against the LRMIS target schema,")
        print("     review, then deploy_to_lrmis(); backfill/refresh from source.")
        print("  2. Verify target row counts vs source, then retire the entity's")
        print("     old irimsv_*_staging table.")
        print("  3. Only once every entity is migrated: remove the views/staging")
        print("     code paths (still used by 9 modules + view_proposer today) and")
        print("     drop lrmis_staging / lrmis_staging_views.")
        print("\n(nothing was changed by this report.)")
        return 0
    finally:
        central.close()


if __name__ == "__main__":
    raise SystemExit(main())
