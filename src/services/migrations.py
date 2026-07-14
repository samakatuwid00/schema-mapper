"""Tracked, checksummed, advisory-lock-guarded SQL migration runner (central Postgres).

This runner only manages the ordered central-database migration files below;
the LRMIS target schema is provisioned separately (scripts/init_lrmis_target).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..connectors import PostgresCentralConnector
from .common import ConflictError, NotFoundError, ValidationError

# Ordered list of central-database migration files, repo-root relative.
MIGRATION_FILES = [
    "sql/central_db_init.sql",
    "sql/001_integration_foundation.sql",
    "sql/002_onboarding_metadata.sql",
    "sql/003_admin_ui.sql",
    "sql/004_schema_scope_isolation.sql",
    "sql/005_crosswalk_target_table.sql",
    "sql/006_station_id_sequence.sql",
    "sql/007_onboarding_target_tables.sql",
    "sql/008_target_ddl_scope.sql",
    "sql/009_drift_resolution.sql",
    "sql/010_three_way_drift.sql",
]

_MIGRATION_LOCK_KEY = "schema_mapper:migrations"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_tracker(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS integration.schema_migrations (
                filename TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                applied_by TEXT NOT NULL,
                success BOOLEAN NOT NULL DEFAULT true
            )
        """)
    conn.commit()


def list_migrations(central: PostgresCentralConnector | None = None) -> list[dict]:
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            _ensure_tracker(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT filename, checksum, applied_at, applied_by, success "
                            "FROM integration.schema_migrations")
                applied = {row[0]: {"checksum": row[1], "applied_at": row[2],
                                    "applied_by": row[3], "success": row[4]}
                           for row in cur.fetchall()}
        out = []
        for filename in MIGRATION_FILES:
            path = REPO_ROOT / filename
            current = _checksum(path) if path.exists() else None
            record = applied.get(filename)
            out.append({
                "filename": filename,
                "exists_on_disk": path.exists(),
                "current_checksum": current,
                "applied": record is not None,
                "applied_at": record["applied_at"] if record else None,
                "applied_by": record["applied_by"] if record else None,
                "checksum_mismatch": bool(record and current and record["checksum"] != current),
            })
        return out
    finally:
        if owns:
            central.close()


def read_migration_sql(filename: str) -> str:
    if filename not in MIGRATION_FILES:
        raise NotFoundError(f"{filename} is not a managed migration")
    path = REPO_ROOT / filename
    if not path.exists():
        raise NotFoundError(f"{filename} missing on disk")
    return path.read_text(encoding="utf-8")


def apply_migration(filename: str, applied_by: str,
                    central: PostgresCentralConnector | None = None) -> dict:
    if filename not in MIGRATION_FILES:
        raise NotFoundError(f"{filename} is not a managed migration")
    path = REPO_ROOT / filename
    if not path.exists():
        raise NotFoundError(f"{filename} missing on disk")
    sql_text = path.read_text(encoding="utf-8")
    checksum = _checksum(path)

    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            _ensure_tracker(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (_MIGRATION_LOCK_KEY,))
                if not cur.fetchone()[0]:
                    raise ConflictError("another migration apply is in progress")
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT checksum FROM integration.schema_migrations "
                                "WHERE filename = %s", (filename,))
                    row = cur.fetchone()
                    if row and row[0] != checksum:
                        raise ValidationError(
                            f"{filename} was edited after being applied "
                            f"(recorded {row[0][:12]}, current {checksum[:12]}) - refusing to run")
                    if row and row[0] == checksum:
                        return {"filename": filename, "status": "already_applied",
                                "checksum": checksum}
                    # One transaction: DDL rolls back atomically on failure.
                    cur.execute(sql_text)
                    cur.execute("""
                        INSERT INTO integration.schema_migrations (filename, checksum, applied_by)
                        VALUES (%s, %s, %s)
                    """, (filename, checksum, applied_by))
                conn.commit()
                return {"filename": filename, "status": "applied", "checksum": checksum,
                        "applied_by": applied_by}
            except Exception:
                conn.rollback()
                raise
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_MIGRATION_LOCK_KEY,))
                conn.commit()
    finally:
        if owns:
            central.close()


def mark_applied(filename: str, applied_by: str,
                 central: PostgresCentralConnector | None = None) -> dict:
    """Record a file as applied without executing it (Docker-initialized databases)."""
    if filename not in MIGRATION_FILES:
        raise NotFoundError(f"{filename} is not a managed migration")
    path = REPO_ROOT / filename
    if not path.exists():
        raise NotFoundError(f"{filename} missing on disk")
    checksum = _checksum(path)
    owns = central is None
    central = central or PostgresCentralConnector()
    try:
        with central.connection() as conn:
            _ensure_tracker(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration.schema_migrations (filename, checksum, applied_by)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (filename) DO UPDATE
                        SET checksum = EXCLUDED.checksum, applied_by = EXCLUDED.applied_by,
                            applied_at = now()
                """, (filename, checksum, applied_by))
            conn.commit()
        return {"filename": filename, "status": "marked_applied", "checksum": checksum}
    finally:
        if owns:
            central.close()
