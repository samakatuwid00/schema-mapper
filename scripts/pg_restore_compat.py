"""Restore a PostgreSQL custom dump while filtering known version-skew SETs.

PostgreSQL 17+ dumps include `SET transaction_timeout = 0;`, which PostgreSQL
16 does not recognize. This helper streams pg_restore SQL through a tiny filter
before feeding it to psql, keeping all other restore errors fatal.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_BIN_DIR = Path(r"C:\Program Files\PostgreSQL\18\bin")
DROP_LINES = {b"SET transaction_timeout = 0;"}
SAFE_SCHEMA_RE = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _tool(name: str, env_name: str) -> str:
    configured = os.environ.get(env_name)
    if configured:
        return configured
    candidate = DEFAULT_BIN_DIR / f"{name}.exe"
    return str(candidate) if candidate.exists() else name


def _validate_schema_name(schema: str) -> None:
    if not re.match(SAFE_SCHEMA_RE, schema):
        raise ValueError("schema names must be bare PostgreSQL identifiers")


def _pre_restore_sql(schema_from: str, schema_to: str) -> str:
    """SQL run before a renamed restore: drop both staging schemas, recreate the source one.

    `pg_restore --schema=X` emits X's objects but *not* `CREATE SCHEMA X`, so the
    schema we just dropped has to be recreated here or every object fails with
    "schema does not exist".
    """
    seen: set[str] = set()
    statements: list[str] = []
    for schema in (schema_to, schema_from):
        if not schema or schema in seen:
            continue
        _validate_schema_name(schema)
        seen.add(schema)
        statements.append(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE;')
    if schema_from:
        statements.append(f'CREATE SCHEMA "{schema_from}";')
    return "\n".join(statements)


def _rename_schema_sql(source_schema: str, target_schema: str) -> str:
    """SQL that renames a restored schema only when the source schema exists."""
    _validate_schema_name(source_schema)
    _validate_schema_name(target_schema)
    if source_schema == target_schema:
        return ""
    return f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.schemata
        WHERE schema_name = '{source_schema}'
    ) THEN
        EXECUTE 'ALTER SCHEMA "{source_schema}" RENAME TO "{target_schema}"';
    END IF;
END $$;
"""


def _restore_cmd(pg_restore: str, dump: str, toc_file: Path | None) -> list[str]:
    """The pg_restore command for `dump`.

    Without a rename the dump is replayed whole, as before. With one, `toc_file`
    (from `_restore_toc`) selects what may be replayed: a source `pg_dump` is a
    whole-database dump, so it also carries schemas central owns itself
    (`integration`, `lrmis_projection`) -- replaying those fails on `CREATE SCHEMA`
    ("schema already exists") and would overwrite central's own metadata.
    """
    cmd = [pg_restore, "--no-owner", "--no-privileges"]
    if toc_file is not None:
        cmd.append(f"--use-list={toc_file}")
    else:
        cmd.extend(["--clean", "--if-exists"])
    cmd.extend(["--file=-", dump])
    return cmd


EXTENSION_TOC_RE = re.compile(rb"^\d+;\s+\d+\s+\d+\s+EXTENSION\s")
TRIGGER_TOC_RE = re.compile(rb"^\d+;\s+\d+\s+\d+\s+TRIGGER\s")


def _list_toc(pg_restore: str, dump: str, *extra_args: str) -> list[bytes]:
    listing = subprocess.run([pg_restore, "-l", *extra_args, dump],
                             stdout=subprocess.PIPE, stderr=sys.stderr)
    if listing.returncode != 0:
        raise RuntimeError(f"pg_restore -l failed (exit {listing.returncode})")
    return listing.stdout.splitlines()


def _restore_toc(pg_restore: str, dump: str, schema_from: str) -> bytes:
    """The table-of-contents entries a renamed restore may replay, for `-L`.

    Two things `--schema=X` alone gets wrong:

    * it drops the extensions X's objects depend on (`pgcrypto`), which the
      destination may not have -- so the EXTENSION entries are added back. pg_dump
      writes them as `CREATE EXTENSION IF NOT EXISTS`, so a destination that
      already has them is unaffected.
    * it keeps X's triggers, and in the LRMIS source those are CDC triggers
      calling `integration.enqueue_*()` -- functions that live in the *source's*
      integration schema, not central's, so creating them fails with "function
      integration.enqueue_author_change() does not exist". The restored schema is
      a read-only mirror that gets its own delivery triggers at deploy time, so
      the source's triggers are dropped rather than repaired.
    """
    _validate_schema_name(schema_from)
    extensions = [ln for ln in _list_toc(pg_restore, dump)
                  if EXTENSION_TOC_RE.match(ln)]
    schema_entries = [ln for ln in _list_toc(pg_restore, dump, f"--schema={schema_from}")
                      if not TRIGGER_TOC_RE.match(ln)]
    return b"\n".join(extensions + schema_entries) + b"\n"


def _run_psql_sql(psql: str, dsn: str, sql: str) -> int:
    if not sql.strip():
        return 0
    completed = subprocess.run(
        [psql, dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", sql])
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="pg_restore -> filter incompatible SETs -> psql")
    parser.add_argument("--dump", required=True, help="PostgreSQL custom-format dump")
    parser.add_argument("--dsn", required=True, help="destination PostgreSQL DSN")
    parser.add_argument("--schema-from", default=os.environ.get("LRMIS_SOURCE_RESTORE_SCHEMA_FROM", ""),
                        help="rename this restored schema after restore, if it exists")
    parser.add_argument("--schema-to", default=os.environ.get("LRMIS_SOURCE_RESTORE_SCHEMA_TO", ""),
                        help="schema name to use after --schema-from is restored")
    parser.add_argument("--pg-restore", default=_tool("pg_restore", "PG_RESTORE_EXE"))
    parser.add_argument("--psql", default=_tool("psql", "PSQL_EXE"))
    args = parser.parse_args()

    rename_requested = bool(
        args.schema_from and args.schema_to and args.schema_from != args.schema_to
    )

    toc_file: Path | None = None
    if rename_requested:
        pre_rc = _run_psql_sql(
            args.psql,
            args.dsn,
            _pre_restore_sql(args.schema_from, args.schema_to),
        )
        if pre_rc != 0:
            return pre_rc

        toc_file = Path(args.dump).with_suffix(".restore.toc")
        toc_file.write_bytes(_restore_toc(args.pg_restore, args.dump, args.schema_from))

    restore_cmd = _restore_cmd(args.pg_restore, args.dump, toc_file)

    psql_cmd = [
        args.psql,
        args.dsn,
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
    ]

    restore = subprocess.Popen(restore_cmd, stdout=subprocess.PIPE, stderr=sys.stderr)
    psql = subprocess.Popen(psql_cmd, stdin=subprocess.PIPE, stderr=sys.stderr)

    assert restore.stdout is not None
    assert psql.stdin is not None
    pipe_failed = False
    try:
        for line in restore.stdout:
            if line.strip() in DROP_LINES:
                continue
            try:
                psql.stdin.write(line)
            except (BrokenPipeError, OSError):
                pipe_failed = True
                break
    except (BrokenPipeError, OSError):
        pipe_failed = True
    finally:
        restore.stdout.close()
        try:
            psql.stdin.close()
        except (BrokenPipeError, OSError):
            pipe_failed = True

    restore_rc = restore.wait()
    psql_rc = psql.wait()
    if toc_file is not None:
        toc_file.unlink(missing_ok=True)
    if restore_rc != 0:
        return restore_rc
    if psql_rc != 0:
        return psql_rc
    if pipe_failed:
        return 1

    if rename_requested:
        rename_sql = _rename_schema_sql(args.schema_from, args.schema_to)
        return _run_psql_sql(args.psql, args.dsn, rename_sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
