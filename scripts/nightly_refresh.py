"""One-command nightly rebuild (source -> target), runnable on demand or by a scheduler.

Restores the fresh `lrmis` source dump into central, resets the target to a
fresh database seeded with only the FK-closure lookups, then re-delivers every
deployed LRMIS-target entity from the fresh source.

The reset and re-delivery are destructive, so a real (non-dry-run) execution
requires typed confirmation of the target database name:

    python scripts/nightly_refresh.py --dry-run
    python scripts/nightly_refresh.py --confirm lrmis_target --restore
    python scripts/nightly_refresh.py --confirm lrmis_target          # reset+redeliver, no source restore

Schedule it at midnight with the OS scheduler (cron / Windows Task Scheduler)
invoking this script, or run the `nightly_refresh` job from the admin UI.

See: openspec/changes/simplify-source-to-target-delivery/design.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.nightly_refresh import run_nightly_refresh

TARGET_DB = os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly source->target rebuild")
    parser.add_argument("--confirm", metavar="TARGET_DB",
                        help=f"type the target database name ({TARGET_DB}) to run destructively")
    parser.add_argument("--restore", action="store_true",
                        help="also restore the fresh source dump into central first")
    parser.add_argument("--dump-path", help="path to the source dump (or set LRMIS_SOURCE_DUMP_PATH)")
    parser.add_argument("--actor", default="cli:nightly_refresh",
                        help="who is running this (recorded in the result)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the plan and counts without changing anything")
    args = parser.parse_args()

    if not args.dry_run and args.confirm != TARGET_DB:
        print(f"refusing to run: pass --confirm {TARGET_DB} to confirm the destructive "
              f"reset of the target (or use --dry-run).", file=sys.stderr)
        return 2

    result = run_nightly_refresh(
        actor=args.actor, restore=args.restore, dump_path=args.dump_path,
        dry_run=args.dry_run,
        progress=lambda i, n, msg: print(f"[{i}/{n}] {msg}", file=sys.stderr),
    )
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
