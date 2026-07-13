"""Path B, Phase 1: create and seed the `lrmis_target` database.

Builds a parallel target database that holds the canonical 51 LRMIS tables (the
real schema, not the VARCHAR staging shape), seeds only the lookup tables the
sync pipeline's own inserts depend on, and adds a delivery_audit envelope
table. It never touches `lrmis_staging`, so the 75 entities delivering there
today are undisturbed.

Idempotent: safe to re-run. The database and tables use IF NOT EXISTS; the
seed lookup tables are truncated and reloaded from the canonical dump each run.

Requires MySQL privileges to CREATE DATABASE and load data — the pipeline's
least-privilege `irimsv_writer` cannot do this, so connect as an admin account
via LRMIS_ROOT_USER / LRMIS_ROOT_PASSWORD (default root/root, the compose
container's root).

Usage:
    python scripts/init_lrmis_target.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.lrmis_registry import ddl_path, get_registry
from src.adapters.lrmis_plugin import DELIVERY_AUDIT_DDL

TARGET_DB = os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")

# The tables to seed are computed from the registry as the FK-closure of the
# pipeline's write set (see seed_tables() / station_write_set()) - i.e. only
# the lookups the inserts actually point at. This is derived, not hardcoded,
# so it can't drift from the schema. On the real schema it resolves to 9
# tables (psgc, profile, geo_level, station_type, school_type, contact_type,
# circular_class, user_status, user_type) and correctly excludes the
# multi-million-row library catalog.
#   NOTE: `profile` is ~15k person records rather than a pure lookup; it is
#   seeded because station_head FKs to it, but IRIMSV could own profiles
#   instead - drop it by narrowing the write set if so.

_FK_LINE_RE = re.compile(
    r"CONSTRAINT `[^`]+` FOREIGN KEY \(`([^`]+)`\) REFERENCES `([^`]+)` \(`([^`]+)`\)")


def sanitize_ddl(ddl: str) -> str:
    """Drop degenerate self-referential FKs (a column referencing itself).

    e.g. `CONSTRAINT psgc_psgc_FK FOREIGN KEY (id) REFERENCES psgc (id)` — this
    enforces nothing (a PK column trivially satisfies it) and MySQL 8.4 rejects
    it outright ("missing unique key"). Real self-references like
    `station.parent_station -> station.id` (different columns) are kept.
    """
    lines = ddl.splitlines()
    head = re.match(r"CREATE TABLE(?: IF NOT EXISTS)? `([^`]+)`", lines[0])
    table = head.group(1) if head else None

    kept = []
    dropped = False
    for line in lines:
        m = _FK_LINE_RE.search(line)
        if m and m.group(2) == table and m.group(1) == m.group(3):
            dropped = True
            continue
        kept.append(line)

    if dropped:
        # The closing ")" line's predecessor must not end with a dangling comma.
        for i in range(len(kept) - 1, -1, -1):
            if kept[i].lstrip().startswith(")"):
                kept[i - 1] = kept[i - 1].rstrip().rstrip(",")
                break
    return "\n".join(kept)


def _root_config() -> dict:
    return {
        "host": os.environ.get("LRMIS_STAGING_HOST", "localhost"),
        "port": int(os.environ.get("LRMIS_STAGING_PORT", "3307")),
        "user": os.environ.get("LRMIS_ROOT_USER", "root"),
        "password": os.environ.get("LRMIS_ROOT_PASSWORD", "root"),
        "ssl_disabled": os.environ.get("LRMIS_STAGING_SSL_DISABLED", "true").lower() == "true",
    }


def iter_seed_statements(path: str, wanted: set[str]):
    """Yield complete `INSERT INTO <wanted>` statements from a mysqldump.

    Streams the file so the multi-hundred-MB dump never lands in memory. A
    statement runs from its `INSERT INTO` line to the first line ending in `;`.
    """
    insert_re = re.compile(r"^INSERT INTO `([^`]+)`")
    buf: list[str] = []
    capturing_for: str | None = None
    with open(path, "r", encoding="utf8", errors="replace") as handle:
        for line in handle:
            if capturing_for is None:
                m = insert_re.match(line)
                if m and m.group(1) in wanted:
                    capturing_for = m.group(1)
                    buf = [line]
                    if line.rstrip().endswith(";"):
                        yield capturing_for, "".join(buf)
                        capturing_for = None
                continue
            buf.append(line)
            if line.rstrip().endswith(";"):
                yield capturing_for, "".join(buf)
                capturing_for = None


def seed_tables(registry=None) -> list[str]:
    """The lookup tables to seed, derived from the schema's FK graph."""
    registry = registry or get_registry()
    return registry.seed_tables(registry.station_write_set())


def recreate_target_database(dry_run: bool = False) -> dict:
    """Drop + recreate the Path B target database from canonical DDL.

    This is the programmatic equivalent of running
    ``python scripts/init_lrmis_target.py``, returning a dict with table
    counts and seed rows so callers can report progress without parsing stdout.
    """
    registry = get_registry()
    tables_in_order = registry.topological_order()
    seeds = seed_tables(registry)
    seed_set = set(seeds)

    result = {
        "target_database": TARGET_DB,
        "schema_source": ddl_path(),
        "tables_to_create": len(tables_in_order),
        "lookups_to_seed": len(seeds),
        "seed_tables": seeds,
        "created": 0,
        "table_total": 0,
        "seed_rows": {},
    }
    if dry_run:
        return result

    import mysql.connector

    server = mysql.connector.connect(**_root_config())
    server.autocommit = True
    cur = server.cursor()

    cur.execute(f"DROP DATABASE IF EXISTS `{TARGET_DB}`")
    cur.execute(f"CREATE DATABASE `{TARGET_DB}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    cur.execute(f"USE `{TARGET_DB}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")

    created = 0
    for table in tables_in_order:
        ddl = registry.get_create_sql(table)
        ddl = ddl.replace("CREATE TABLE `", "CREATE TABLE IF NOT EXISTS `", 1)
        ddl = sanitize_ddl(ddl)
        ddl = ddl.rstrip().rstrip(";")
        cur.execute(ddl)
        created += 1
    result["created"] = created

    cur.execute(DELIVERY_AUDIT_DDL)

    for table in seeds:
        cur.execute(f"TRUNCATE TABLE `{table}`")
    statements = 0
    for _table, statement in iter_seed_statements(ddl_path(), seed_set):
        cur.execute(statement.rstrip().rstrip(";"))
        statements += 1
    result["seed_statements"] = statements

    cur.execute("SET FOREIGN_KEY_CHECKS = 1")

    seed_rows = {}
    for table in seeds:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        seed_rows[table] = cur.fetchone()[0]
    result["seed_rows"] = seed_rows

    writer_user = os.environ.get("LRMIS_STAGING_USER", "irimsv_writer")
    try:
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{TARGET_DB}`.* TO %s@'%%'",
            (writer_user,))
        result["grant_result"] = f"granted to {writer_user}"
    except Exception as exc:
        result["grant_result"] = f"warning: could not grant: {exc}"

    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (TARGET_DB,))
    result["table_total"] = cur.fetchone()[0]

    cur.close()
    server.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and seed lrmis_target")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the plan without changing anything")
    args = parser.parse_args()

    result = recreate_target_database(args.dry_run)
    print(f"Target database : {result['target_database']}")
    print(f"Schema source   : {result['schema_source']}")
    print(f"Tables to create: {result['tables_to_create']}")
    print(f"Lookups to seed : {result['lookups_to_seed']} -> {result.get('seed_tables', [])}")
    if not args.dry_run:
        print(f"Created         : {result.get('created', 0)} tables")
        print(f"Seed statements : {result.get('seed_statements', 0)}")
        print(f"Table total     : {result.get('table_total', 0)}")
        if result.get("grant_result"):
            print(result["grant_result"])
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
