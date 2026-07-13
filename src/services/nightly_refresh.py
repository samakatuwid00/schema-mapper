"""One-command nightly rebuild: restore fresh source -> reset target -> re-deliver.

This is the operator's midnight flow, composed from three primitives that already
exist and are individually tested, so this module is orchestration only:

  1. restore  - load the fresh `lrmis` source dump into central (`irimsv`).
                DESTRUCTIVE and environment-specific: the exact command depends on
                how the dump is produced, so it is configured, never guessed
                (see `restore_source_dump`). Off by default; callers opt in.
  2. reset    - `scripts.init_lrmis_target.recreate_target_database()` drops and
                recreates the target with ONLY the FK-closure lookup tables
                seeded. This is exactly the operator's "full truncate, reseed only
                the lookups" choice: every data table starts empty, `psgc` and the
                other ~8 lookups the source does not carry keep their seed rows so
                foreign keys resolve.
  3. redeliver- for every deployed LRMIS-target entity, read its current source
                rows and re-deliver them through `lrmis_delivery.refresh_entity`
                (crosswalk-scoped delete then rewrite). After a reset the target is
                empty, so this simply fills it from the fresh source.

The whole run is idempotent: source is authoritative, so re-running reproduces the
same target. A failure in step 1 aborts before the target is touched.

Design: openspec/changes/simplify-source-to-target-delivery/design.md (D2, D3).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

import psycopg2.extras

from ..connectors import MySQLStagingConnector, PostgresCentralConnector
from ..lrmis_delivery import load_entity_mappings, refresh_entity
from .common import ValidationError
# Imported at module level so tests can monkeypatch them by name.
from scripts.init_lrmis_target import recreate_target_database

TARGET_SYSTEM = os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS")
DEFAULT_SOURCE_SYSTEM = os.environ.get("LRMIS_SOURCE_SYSTEM", "IRIMSV_REGION_V")
BACKUP_DIR = os.environ.get("LRMIS_TARGET_BACKUP_DIR", "backups")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Entity enumeration + counts
# ---------------------------------------------------------------------------

def deployed_target_entities(conn, target_system: str = TARGET_SYSTEM) -> list[dict]:
    """Every entity that delivers into the LRMIS target and is live.

    Filtered to `lrmis_target_tables IS NOT NULL` so the rebuild is safe to run
    even while legacy-staging entities still exist (they are simply not touched).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT source_schema, source_table, primary_key_columns, source_system
            FROM integration.onboarding_entity
            WHERE target_system = %s AND status = 'deployed'
              AND lrmis_target_tables IS NOT NULL
            ORDER BY source_table
        """, (target_system,))
        return [dict(r) for r in cur.fetchall()]


def _pk_columns(entity: dict) -> list[str]:
    pk = entity.get("primary_key_columns")
    if isinstance(pk, str):
        pk = json.loads(pk)
    return pk or []


def _source_count(conn, source_schema: str, source_table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {source_schema}.{source_table}")
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Step 1: guarded source restore (DESTRUCTIVE, environment-specific)
# ---------------------------------------------------------------------------

def restore_source_dump(*, dump_path: str | None = None, restore_cmd: str | None = None,
                        dry_run: bool = False) -> dict:
    """Restore the fresh source dump into central. Destructive.

    The command is taken from, in order: the `restore_cmd` argument, the
    `LRMIS_SOURCE_RESTORE_CMD` env var (a full shell command; `{dump}` is
    substituted with the dump path), or -- only if `CENTRAL_DSN` is set -- a
    default `psql` restore of a plain-SQL dump. It is configured rather than
    guessed because the dump format is deployment-specific (design open
    question). If nothing is configured, this raises instead of running a
    destructive command with assumed parameters.
    """
    dump_path = dump_path or os.environ.get("LRMIS_SOURCE_DUMP_PATH")
    if not dump_path:
        raise ValidationError(
            "no source dump path: pass dump_path or set LRMIS_SOURCE_DUMP_PATH")

    cmd = restore_cmd or os.environ.get("LRMIS_SOURCE_RESTORE_CMD")
    if cmd:
        cmd = cmd.replace("{dump}", dump_path)
    elif os.environ.get("CENTRAL_DSN"):
        cmd = f'psql "{os.environ["CENTRAL_DSN"]}" -v ON_ERROR_STOP=1 -f "{dump_path}"'
    else:
        raise ValidationError(
            "source restore is not configured: set LRMIS_SOURCE_RESTORE_CMD "
            "(a full command; {dump} is substituted) or CENTRAL_DSN for the "
            "default psql restore. Refusing to guess a destructive command.")

    plan = {"dump_path": dump_path, "command": cmd, "executed": False}
    if dry_run:
        return plan

    completed = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    plan["executed"] = True
    plan["returncode"] = completed.returncode
    if completed.returncode != 0:
        raise RuntimeError(
            f"source restore failed (exit {completed.returncode}): "
            f"{(completed.stderr or '')[-2000:]}")
    return plan


# ---------------------------------------------------------------------------
# Step 1.5: pre-reset target backup
# ---------------------------------------------------------------------------

def backup_target(*, dry_run: bool = False, now: datetime | None = None) -> dict:
    """Dump the target to a timestamped file before the destructive reset.

    Uses `mysqldump` via the same root credentials init_lrmis_target uses. A
    missing `mysqldump` is recorded as a warning rather than aborting the run --
    the source remains authoritative, so a failed backup does not risk data.
    """
    now = now or _now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    target_db = os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")
    out_path = os.path.join(BACKUP_DIR, f"{target_db}-{stamp}.sql")
    plan = {"path": out_path, "database": target_db, "executed": False}
    if dry_run:
        return plan

    os.makedirs(BACKUP_DIR, exist_ok=True)
    host = os.environ.get("LRMIS_STAGING_HOST", "localhost")
    port = os.environ.get("LRMIS_STAGING_PORT", "3307")
    user = os.environ.get("LRMIS_ROOT_USER", "root")
    password = os.environ.get("LRMIS_ROOT_PASSWORD", "root")
    cmd = (f'mysqldump -h {host} -P {port} -u {user} -p{password} '
           f'--single-transaction "{target_db}"')
    try:
        with open(out_path, "w", encoding="utf8") as handle:
            completed = subprocess.run(cmd, shell=True, stdout=handle,
                                       stderr=subprocess.PIPE, text=True)
        if completed.returncode != 0:
            plan["warning"] = (completed.stderr or "")[-1000:]
        else:
            plan["executed"] = True
    except Exception as exc:                       # mysqldump missing, etc.
        plan["warning"] = str(exc)
    return plan


# ---------------------------------------------------------------------------
# Step 3: re-deliver every deployed target entity from the fresh source
# ---------------------------------------------------------------------------

def _redeliver_entity(cconn, tconn, entity: dict, target_system: str,
                      writer=None, registry=None) -> dict:
    """Read one entity's current source rows and refresh them into the target."""
    source_schema = entity["source_schema"]
    source_table = entity["source_table"]
    source_system = entity.get("source_system") or DEFAULT_SOURCE_SYSTEM
    pk_columns = _pk_columns(entity)

    mappings = load_entity_mappings(cconn, source_table, target_system)
    if not mappings:
        return {"entity": source_table, "status": "skipped",
                "error": "no approved LRMIS-target mapping"}

    from .ops import _pipeline
    p = _pipeline()
    source_rows = p._query(cconn, f"SELECT * FROM {source_schema}.{source_table}")

    def external_reference_of(row: dict) -> str:
        pk_values = [row.get(col) for col in pk_columns]
        return str(p.generate_external_reference(
            source_system, source_schema, source_table, pk_values))

    out = refresh_entity(
        tconn, cconn, entity_name=source_table, mappings=mappings,
        source_rows=source_rows, external_reference_of=external_reference_of,
        source_system=source_system, target_system=target_system,
        writer=writer, registry=registry)
    tconn.commit()
    cconn.commit()
    out["status"] = "refreshed"
    return out


def redeliver_all(entities: list[dict], target_system: str = TARGET_SYSTEM,
                  progress=None, target=None, writer=None, registry=None) -> list[dict]:
    """Refresh every entity into the (freshly reset) target, one open pair of
    connections for the whole batch.

    `target`/`writer` default to the MySQL target + legacy writer. A caller with
    a different target engine (e.g. schema-swap to Postgres) passes a target
    connector exposing `connection()` plus a `delivery.GenericWriter`."""
    if not entities:
        return []
    central = PostgresCentralConnector()
    target = target or MySQLStagingConnector.for_target()
    results: list[dict] = []
    try:
        with central.connection() as cconn, target.connection() as tconn:
            for i, entity in enumerate(entities):
                if progress:
                    progress(i, len(entities), f"delivering {entity['source_table']}")
                try:
                    results.append(_redeliver_entity(cconn, tconn, entity, target_system,
                                                     writer, registry))
                except Exception as exc:
                    tconn.rollback()
                    cconn.rollback()
                    results.append({"entity": entity["source_table"],
                                    "status": "failed", "error": str(exc)})
    finally:
        central.close()
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_nightly_refresh(*, actor: str, restore: bool = False,
                        dump_path: str | None = None, restore_cmd: str | None = None,
                        dry_run: bool = False, target_system: str = TARGET_SYSTEM,
                        progress=None) -> dict:
    """Restore the fresh source, reset the target, and re-deliver every entity.

    `restore` is opt-in: the destructive source restore only runs when the caller
    asks for it and a dump is configured. A restore failure aborts before the
    target is reset. Returns a structured, per-step result.
    """
    started = _now()
    result: dict = {
        "actor": actor, "dry_run": dry_run, "target_system": target_system,
        "started_at": started.isoformat(), "steps": {},
    }

    with PostgresCentralConnector().connection() as conn:
        entities = deployed_target_entities(conn, target_system)
        result["entities"] = [e["source_table"] for e in entities]
        if dry_run:
            result["steps"]["source_counts"] = {
                e["source_table"]: _source_count(conn, e["source_schema"], e["source_table"])
                for e in entities}

    # Step 1: restore (destructive, opt-in) -- abort everything if it fails.
    if restore:
        if progress:
            progress(0, 3, "restoring source")
        result["steps"]["restore"] = restore_source_dump(
            dump_path=dump_path, restore_cmd=restore_cmd, dry_run=dry_run)

    # Step 1.5 + 2: back up the target, then reset it (fresh + seeded lookups).
    if progress:
        progress(1, 3, "resetting target")
    result["steps"]["backup"] = backup_target(dry_run=dry_run, now=started)
    result["steps"]["reset"] = recreate_target_database(dry_run=dry_run)

    # Step 3: re-deliver from the fresh source.
    if progress:
        progress(2, 3, "re-delivering entities")
    if dry_run:
        result["steps"]["redeliver"] = {"skipped": "dry_run"}
    else:
        result["steps"]["redeliver"] = redeliver_all(entities, target_system, progress)

    finished = _now()
    result["finished_at"] = finished.isoformat()
    result["duration_seconds"] = (finished - started).total_seconds()
    return result
