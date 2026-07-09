"""Staging-table snapshots taken before destructive drop/recreate operations."""
from __future__ import annotations

from datetime import datetime, timezone

from ..connectors import MySQLStagingConnector

SNAPSHOT_KEEP = 2
_SNAPSHOT_MARKER = "_bak_"


def _table_exists(staging: MySQLStagingConnector, table: str) -> bool:
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
            )
            return cur.fetchone()[0] > 0


def list_snapshots(staging: MySQLStagingConnector, table: str) -> list[str]:
    prefix = f"{table}{_SNAPSHOT_MARKER}"
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name LIKE %s "
                "ORDER BY table_name",
                (prefix + "%",),
            )
            return [row[0] for row in cur.fetchall()]


def snapshot_staging_table(staging: MySQLStagingConnector, table: str) -> str | None:
    """Rename the current table aside before a drop; returns the snapshot name."""
    if not _table_exists(staging, table):
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    snapshot = f"{table}{_SNAPSHOT_MARKER}{ts}"
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"RENAME TABLE `{table}` TO `{snapshot}`")
        conn.commit()
    prune_snapshots(staging, table)
    return snapshot


def prune_snapshots(staging: MySQLStagingConnector, table: str, keep: int = SNAPSHOT_KEEP) -> list[str]:
    snapshots = list_snapshots(staging, table)
    stale = snapshots[:-keep] if keep else snapshots
    with staging.connection() as conn:
        with conn.cursor() as cur:
            for name in stale:
                cur.execute(f"DROP TABLE IF EXISTS `{name}`")
        conn.commit()
    return stale


def restore_snapshot(staging: MySQLStagingConnector, table: str, snapshot: str | None = None) -> str:
    """Replace the current table with a snapshot (latest by default)."""
    snapshots = list_snapshots(staging, table)
    if not snapshots:
        raise ValueError(f"no snapshots exist for {table}")
    chosen = snapshot or snapshots[-1]
    if chosen not in snapshots:
        raise ValueError(f"snapshot {chosen} not found for {table}")
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            cur.execute(f"RENAME TABLE `{chosen}` TO `{table}`")
        conn.commit()
    return chosen
