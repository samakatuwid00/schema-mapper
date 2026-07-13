"""Staging-table snapshots taken before destructive drop/recreate operations."""
from __future__ import annotations

from datetime import datetime, timezone

from ..connectors import MySQLStagingConnector, VIEWS_DATABASE

SNAPSHOT_KEEP = 2
_SNAPSHOT_MARKER = "_b_"


def _views_schema(table: str) -> str | None:
    return VIEWS_DATABASE if "_for_lrmis" in table else None


def _table_exists(staging: MySQLStagingConnector, table: str) -> bool:
    schema = _views_schema(table)
    cond = f"table_schema = '{schema}'" if schema else "table_schema = DATABASE()"
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM information_schema.tables "
                f"WHERE {cond} AND table_name = %s",
                (table,),
            )
            return cur.fetchone()[0] > 0


def _qt(table: str) -> str:
    db = _views_schema(table)
    return f"`{db}`.`{table}`" if db else f"`{table}`"


def list_snapshots(staging: MySQLStagingConnector, table: str) -> list[str]:
    schema = _views_schema(table)
    schema_cond = f"table_schema = '{schema}'" if schema else "table_schema = DATABASE()"
    prefix = f"{table}{_SNAPSHOT_MARKER}"
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT table_name FROM information_schema.tables "
                f"WHERE {schema_cond} AND table_name LIKE %s "
                f"ORDER BY table_name",
                (prefix + "%",),
            )
            return [row[0] for row in cur.fetchall()]


def snapshot_staging_table(staging: MySQLStagingConnector, table: str) -> str | None:
    """Rename the current table aside before a drop; returns the snapshot name."""
    if not _table_exists(staging, table):
        return None
    ts = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    snapshot = f"{table}{_SNAPSHOT_MARKER}{ts}"
    qt = _qt(table)
    qs = _qt(snapshot)
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"RENAME TABLE {qt} TO {qs}")
        conn.commit()
    prune_snapshots(staging, table)
    return snapshot


def prune_snapshots(staging: MySQLStagingConnector, table: str, keep: int = SNAPSHOT_KEEP) -> list[str]:
    snapshots = list_snapshots(staging, table)
    stale = snapshots[:-keep] if keep else snapshots
    with staging.connection() as conn:
        with conn.cursor() as cur:
            for name in stale:
                qs = _qt(name)
                cur.execute(f"DROP TABLE IF EXISTS {qs}")
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
    qt = _qt(table)
    qs = _qt(chosen)
    with staging.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {qt}")
            cur.execute(f"RENAME TABLE {qs} TO {qt}")
        conn.commit()
    return chosen
