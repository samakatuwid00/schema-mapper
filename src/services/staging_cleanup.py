"""Staging-table lifecycle cleanup.

Two operations stop the unbounded growth of ``lrmis_staging``:

* ``retire_entity`` - explicitly drop one entity's staging table (snapshotting it
  aside first) and mark the entity ``disabled`` so it no longer delivers.
* ``sweep_orphans`` - drop every ``irimsv_*_staging`` table that is not referenced
  by a currently *deployed* entity. These are leftovers from retired/removed
  entities that otherwise accumulate forever.

Both snapshot each table aside before dropping (see ``snapshots.py``) so a mistaken
drop is restorable, and both support a dry-run preview that mutates nothing.
"""
from __future__ import annotations

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from ..fast_refresh import drop_staging_table
from ..services import NotFoundError, ValidationError
from ..services.snapshots import snapshot_staging_table


def _active_staging_tables(central: PostgresCentralConnector) -> set[str]:
    """Staging tables owned by entities still marked deployed."""
    with central.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT staging_table FROM integration.onboarding_entity
                WHERE status = 'deployed' AND staging_table IS NOT NULL
                """
            )
            return {row[0] for row in cur.fetchall()}


def _is_staging_table(name: str) -> bool:
    # Path A staging tables are irimsv_<entity>_staging. View-generated tables
    # end in _for_lrmis (a different database) and are deliberately excluded.
    return name.endswith("_staging")


def sweep_orphans(
    dry_run: bool = True,
    central: PostgresCentralConnector | None = None,
    staging: MySQLStagingConnector | None = None,
) -> dict:
    """Find and optionally drop staging tables with no deployed entity."""
    owns_central = central is None
    owns_staging = staging is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    try:
        live = _active_staging_tables(central)
        # One row per table (information_schema.tables), not per column.
        all_names = set(staging.table_names())
        orphans = sorted(n for n in all_names if _is_staging_table(n) and n not in live)

        dropped: list[str] = []
        snapshots: list[str] = []
        if not dry_run:
            for name in orphans:
                snap = snapshot_staging_table(staging, name)
                if snap:
                    snapshots.append(snap)
                drop_staging_table(staging, name)
                dropped.append(name)
        return {
            "orphans_found": orphans,
            "dropped": dropped,
            "snapshots": snapshots,
            "dry_run": dry_run,
        }
    finally:
        if owns_central:
            central.close()
        if owns_staging:
            staging.close()


def retire_entity(
    entity_id: int,
    dry_run: bool = False,
    central: PostgresCentralConnector | None = None,
    staging: MySQLStagingConnector | None = None,
) -> dict:
    """Drop an entity's staging table (snapshot first) and disable the entity."""
    owns_central = central is None
    owns_staging = staging is None
    central = central or PostgresCentralConnector()
    staging = staging or MySQLStagingConnector()
    try:
        with central.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, source_table, staging_table, status
                    FROM integration.onboarding_entity WHERE id = %s
                    """,
                    (entity_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise NotFoundError(f"entity {entity_id} not found")
                _, source_table, staging_table, status = row

        snapshot = None
        if staging_table and not dry_run:
            snapshot = snapshot_staging_table(staging, staging_table)
            drop_staging_table(staging, staging_table)
            with central.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE integration.onboarding_entity
                        SET status = 'disabled', staging_table = NULL
                        WHERE id = %s
                        """,
                        (entity_id,),
                    )
                    conn.commit()

        return {
            "entity_id": entity_id,
            "source_table": source_table,
            "staging_table": staging_table,
            "status": status,
            "snapshot": snapshot,
            "dropped": bool(staging_table and not dry_run),
            "dry_run": dry_run,
        }
    finally:
        if owns_central:
            central.close()
        if owns_staging:
            staging.close()
