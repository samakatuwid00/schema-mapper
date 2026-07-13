"""Guarded restore of a PostgreSQL target dump (generic engine, §0.3).

This is how a `pg_dump`/`.backup` target (e.g. old-lrmis.backup) is made
discoverable: restore it into a live Postgres database, then point
`PostgresTargetAdapter` at that database. The binary archive is never parsed —
`pg_restore` loads it, and the schema is read back from `information_schema`.

Like the nightly source restore, the command is **configured, never guessed**:
a real restore is destructive, so if nothing is configured this raises rather
than run a destructive command with assumed parameters. `dry_run=True` returns
the plan and changes nothing.
"""
from __future__ import annotations

import os
import subprocess

from .common import ValidationError

# PostgreSQL custom-format archives (the `.backup`/`-Fc` format pg_restore reads)
# start with this magic. A plain-SQL dump does not, and needs psql, not pg_restore.
PG_DUMP_MAGIC = b"PGDMP"


def is_pg_custom_dump(path: str) -> bool:
    """True when `path` is a PostgreSQL custom-format archive (pg_restore input)."""
    try:
        with open(path, "rb") as handle:
            return handle.read(len(PG_DUMP_MAGIC)) == PG_DUMP_MAGIC
    except OSError:
        return False


def restore_pg_backup(*, backup_path: str | None = None, dsn: str | None = None,
                      restore_cmd: str | None = None, dry_run: bool = True) -> dict:
    """Restore a Postgres target backup. Destructive when `dry_run` is False.

    The command is taken, in order, from: `restore_cmd`, the
    `LRMIS_TARGET_RESTORE_CMD` env var (a full command; `{backup}` and `{dsn}`
    are substituted), or -- only if a target `dsn` is available -- a default
    `pg_restore` of a custom-format archive into that database.
    """
    backup_path = backup_path or os.environ.get("LRMIS_TARGET_BACKUP_PATH")
    if not backup_path:
        raise ValidationError(
            "no backup path: pass backup_path or set LRMIS_TARGET_BACKUP_PATH")
    if not os.path.exists(backup_path):
        raise ValidationError(f"backup file not found: {backup_path}")

    fmt_ok = is_pg_custom_dump(backup_path)
    dsn = dsn or os.environ.get("LRMIS_TARGET_PG_DSN")
    cmd = restore_cmd or os.environ.get("LRMIS_TARGET_RESTORE_CMD")
    if cmd:
        cmd = cmd.replace("{backup}", backup_path).replace("{dsn}", dsn or "")
    elif dsn:
        if not fmt_ok:
            raise ValidationError(
                f"{backup_path} is not a PostgreSQL custom-format archive "
                "(no PGDMP magic). Restore a plain-SQL dump with psql, or set "
                "LRMIS_TARGET_RESTORE_CMD for a custom command.")
        cmd = (f'pg_restore --no-owner --no-privileges --clean --if-exists '
               f'--dbname="{dsn}" "{backup_path}"')
    else:
        raise ValidationError(
            "target restore is not configured: set LRMIS_TARGET_PG_DSN (the "
            "target database to restore into) or LRMIS_TARGET_RESTORE_CMD (a "
            "full command; {backup}/{dsn} are substituted). Refusing to guess "
            "a destructive command.")

    plan = {
        "backup_path": backup_path,
        "is_pg_custom_dump": fmt_ok,
        "command": cmd,
        "executed": False,
    }
    if dry_run:
        return plan

    completed = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    plan["executed"] = True
    plan["returncode"] = completed.returncode
    if completed.returncode != 0:
        raise RuntimeError(
            f"pg_restore failed (exit {completed.returncode}): "
            f"{(completed.stderr or '')[-2000:]}")
    return plan
