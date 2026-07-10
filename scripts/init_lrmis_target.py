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

TARGET_DB = os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")

# The FK-closure of the pipeline's write set: the only lookups its inserts
# point at. Computed from the registry (see the Phase 1 analysis); everything
# else in LRMIS (the multi-million-row library catalog) stays empty.
#   NOTE: `profile` is ~15k person records rather than a pure lookup; it is
#   seeded because station_head FKs to it, but it can be dropped from this list
#   if IRIMSV should own profiles instead.
SEED_TABLES = [
    "psgc",           # ~44k geographic codes (national PSGC registry)
    "profile",        # ~15k person profiles (needed by station_head)
    "geo_level",
    "station_type",
    "school_type",
    "contact_type",
    "circular_class",
    "user_status",
    "user_type",
]

DELIVERY_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS `delivery_audit` (
    `event_id` CHAR(36) NOT NULL PRIMARY KEY,
    `external_reference` CHAR(36) NOT NULL,
    `source_system` VARCHAR(40),
    `operation` VARCHAR(20),
    `source_updated_at` DATETIME(6),
    `mapping_version` INT,
    `payload_checksum` CHAR(64),
    `active` TINYINT(1) DEFAULT 1,
    `accepted_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    INDEX `idx_ext_ref` (`external_reference`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""".strip()


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and seed lrmis_target")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the plan without changing anything")
    args = parser.parse_args()

    registry = get_registry()
    tables_in_order = registry.topological_order()   # parents before children
    seed_set = set(SEED_TABLES)
    missing = seed_set - set(registry.table_names)
    if missing:
        print(f"ERROR: seed tables not in the schema: {sorted(missing)}", file=sys.stderr)
        return 1

    print(f"Target database : {TARGET_DB}")
    print(f"Schema source   : {ddl_path()}")
    print(f"Tables to create: {len(tables_in_order)} (all, so every FK resolves)")
    print(f"Lookups to seed : {len(SEED_TABLES)} -> {SEED_TABLES}")
    print(f"Left empty      : {len(tables_in_order) - len(SEED_TABLES)} "
          f"(entity tables the pipeline fills + unused catalog)")
    if args.dry_run:
        print("\n--dry-run: no changes made.")
        return 0

    import mysql.connector

    server = mysql.connector.connect(**_root_config())
    server.autocommit = True
    cur = server.cursor()

    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{TARGET_DB}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    cur.execute(f"USE `{TARGET_DB}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")

    created = 0
    for table in tables_in_order:
        ddl = registry.get_create_sql(table)
        # registry stores the exact mysqldump CREATE TABLE block; make it
        # re-runnable and drop only the degenerate self-FKs 8.4 rejects.
        ddl = ddl.replace("CREATE TABLE `", "CREATE TABLE IF NOT EXISTS `", 1)
        ddl = sanitize_ddl(ddl)
        ddl = ddl.rstrip().rstrip(";")
        cur.execute(ddl)
        created += 1
    print(f"\nCreated/verified {created} tables.")

    cur.execute(DELIVERY_AUDIT_DDL)
    print("Created/verified delivery_audit.")

    # Seed: rebuild each lookup from the canonical dump for idempotency.
    for table in SEED_TABLES:
        cur.execute(f"TRUNCATE TABLE `{table}`")
    statements = 0
    for _table, statement in iter_seed_statements(ddl_path(), seed_set):
        cur.execute(statement.rstrip().rstrip(";"))
        statements += 1
    print(f"\nExecuted {statements} seed INSERT statement(s).")

    cur.execute("SET FOREIGN_KEY_CHECKS = 1")

    print("\nSeed row counts:")
    for table in SEED_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        print(f"  {table:16} {cur.fetchone()[0]:>8}")

    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (TARGET_DB,))
    total = cur.fetchone()[0]
    print(f"\n{TARGET_DB}: {total} tables total (51 schema + delivery_audit expected).")

    cur.close()
    server.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
