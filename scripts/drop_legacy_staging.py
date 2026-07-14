"""retire-legacy-staging §4: back up and DROP the legacy staging databases.

DESTRUCTIVE and final. This drops the `lrmis_staging` and `lrmis_staging_views`
DATABASES from the MySQL server. It does NOT touch the server itself or the
`lrmis_target` database that lives on the same server (the container is shared:
`for_target()` reaches `lrmis_target` via the same host/port/credentials).

Safety, in order:
  1. `--dry-run` (default behaviour prints the plan and changes nothing).
  2. A mysqldump backup of each database to ``backups/`` runs first; a failed
     backup aborts the drop unless ``--skip-backup`` is passed explicitly.
  3. The drop only proceeds with an EXACT typed confirmation:
        --confirm "lrmis_staging,lrmis_staging_views"
     Anything else refuses. There is no default that drops.

Connects as an admin account via LRMIS_ROOT_USER / LRMIS_ROOT_PASSWORD (the
compose container's root), matching scripts/init_lrmis_target.py.

Usage:
    python scripts/drop_legacy_staging.py --dry-run
    python scripts/drop_legacy_staging.py --confirm "lrmis_staging,lrmis_staging_views"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

STAGING_DATABASES = ["lrmis_staging", "lrmis_staging_views"]
CONFIRM_PHRASE = ",".join(STAGING_DATABASES)
BACKUP_DIR = os.environ.get("LRMIS_STAGING_BACKUP_DIR", "backups")


def _server() -> dict:
    return {
        "host": os.environ.get("LRMIS_STAGING_HOST", "localhost"),
        "port": os.environ.get("LRMIS_STAGING_PORT", "3307"),
        "user": os.environ.get("LRMIS_ROOT_USER", "root"),
        "password": os.environ.get("LRMIS_ROOT_PASSWORD", "root"),
    }


def _database_exists(cur, database: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (database,))
    return cur.fetchone() is not None


def backup_database(database: str, *, now: datetime) -> dict:
    """mysqldump one database to a timestamped file. Returns {path, ok, warning?}."""
    srv = _server()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    out_path = os.path.join(BACKUP_DIR, f"{database}-{stamp}.sql")
    cmd = (f'mysqldump -h {srv["host"]} -P {srv["port"]} -u {srv["user"]} '
           f'-p{srv["password"]} --single-transaction --databases "{database}"')
    plan = {"database": database, "path": out_path, "ok": False}
    try:
        with open(out_path, "w", encoding="utf8") as handle:
            completed = subprocess.run(cmd, shell=True, stdout=handle,
                                       stderr=subprocess.PIPE, text=True)
        if completed.returncode != 0:
            plan["warning"] = (completed.stderr or "")[-1000:]
        else:
            plan["ok"] = True
            plan["bytes"] = os.path.getsize(out_path)
    except Exception as exc:                       # mysqldump missing, disk, etc.
        plan["warning"] = str(exc)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up and DROP the legacy staging databases")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; change nothing (default when --confirm is absent)")
    parser.add_argument("--confirm", default="",
                        help=f'Exact confirmation to proceed: "{CONFIRM_PHRASE}"')
    parser.add_argument("--skip-backup", action="store_true",
                        help="Drop without a mysqldump backup first (discouraged)")
    args = parser.parse_args()

    print("=== retire-legacy-staging (section 4): drop legacy staging databases ===")
    print(f"Server     : {_server()['host']}:{_server()['port']} (as {_server()['user']})")
    print(f"Databases  : {', '.join(STAGING_DATABASES)}")
    print(f"Backup dir : {BACKUP_DIR}")

    if args.dry_run or not args.confirm:
        print("\nDRY RUN - nothing changed.")
        print(f'To proceed: python scripts/drop_legacy_staging.py --confirm "{CONFIRM_PHRASE}"')
        return 0

    if args.confirm != CONFIRM_PHRASE:
        print(f'\nREFUSING: --confirm must be exactly "{CONFIRM_PHRASE}" '
              f'(got "{args.confirm}").', file=sys.stderr)
        return 2

    import mysql.connector
    now = datetime.now(timezone.utc)

    if not args.skip_backup:
        print("\nBacking up...")
        for database in STAGING_DATABASES:
            result = backup_database(database, now=now)
            if result["ok"]:
                print(f"  {database}: {result['path']} ({result.get('bytes', 0)} bytes)")
            else:
                print(f"  {database}: BACKUP FAILED — {result.get('warning', 'unknown')}",
                      file=sys.stderr)
                print("Aborting drop (pass --skip-backup to override).", file=sys.stderr)
                return 3

    server = mysql.connector.connect(**{
        "host": _server()["host"], "port": int(_server()["port"]),
        "user": _server()["user"], "password": _server()["password"],
        "ssl_disabled": os.environ.get("LRMIS_STAGING_SSL_DISABLED", "true").lower() == "true",
    })
    server.autocommit = True
    cur = server.cursor()
    print("\nDropping...")
    for database in STAGING_DATABASES:
        if not _database_exists(cur, database):
            print(f"  {database}: already absent — skipped")
            continue
        cur.execute(f"DROP DATABASE `{database}`")
        print(f"  {database}: DROPPED")
    cur.close()
    server.close()
    print("\nDone. Next: remove the staging init from docker-compose.yml and the "
          "LRMIS_STAGING_DATABASE line from .env.example (section 4.3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
