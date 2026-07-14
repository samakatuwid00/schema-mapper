"""Disaster-recovery CLI (source-schema-swap-and-disaster-recovery §4.5).

Mirrors the /api/recovery endpoints 1:1 — both call the exact same
`src.services.backup_recovery` functions; neither bypasses the other.

    # list restorable target backups + recorded uploads
    python scripts/recover.py --list-backups

    # stage + validate a file without restoring anything
    python scripts/recover.py --stage path/to/dump.sql --kind source_dump

    # restore the target from a listed backup file or a validated upload id
    python scripts/recover.py --restore-target lrmis_target-20260714-010203.sql \
        --confirm lrmis_target --reason "rebuild failed partway"

    # restore the source from a staged file (stages + validates, then restores)
    python scripts/recover.py --restore-source path/to/replacement_dump.sql \
        --confirm irimsv --reason "nightly dump unreadable (UTF-16)"

Restores ALWAYS require the typed --confirm token (the target database name,
or the source schema name) — enforced in the service, not here (design D4).
`--dry-run` prints the restore plan without executing it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services import backup_recovery
from src.services.common import NotFoundError, ValidationError


def _print(obj) -> None:
    print(json.dumps(obj, default=str, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List backups, stage validated uploads, and run confirmed restores")
    parser.add_argument("--list-backups", action="store_true",
                        help="list target backups and recorded uploads")
    parser.add_argument("--stage", metavar="FILE",
                        help="stage + validate a file into the quarantined upload area")
    parser.add_argument("--kind", choices=backup_recovery.VALID_KINDS,
                        default="source_dump",
                        help="what --stage/--restore-source is uploading (default: source_dump)")
    parser.add_argument("--restore-target", metavar="ID",
                        help="backup filename (from --list-backups) or validated upload id")
    parser.add_argument("--restore-source", metavar="FILE_OR_ID",
                        help="a dump file to stage+restore, or an already-staged upload id")
    parser.add_argument("--confirm", help="typed confirmation token (target db name / source schema)")
    parser.add_argument("--reason", default="", help="why this restore is being run (audited)")
    parser.add_argument("--actor", default="cli:recover")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the restore plan without executing it")
    args = parser.parse_args()

    try:
        if args.list_backups:
            _print({"target_backups": backup_recovery.list_target_backups(),
                    "uploads": backup_recovery.list_uploads()})
            return 0

        if args.stage:
            with open(args.stage, "rb") as handle:
                row = backup_recovery.stage_upload(
                    handle, os.path.basename(args.stage), args.kind, args.actor)
            _print(row)
            return 0 if row["valid"] else 4

        if args.restore_target:
            _print(backup_recovery.restore_target(
                args.restore_target, confirm=args.confirm or "",
                by=args.actor, dry_run=args.dry_run))
            return 0

        if args.restore_source:
            ref = args.restore_source
            if str(ref).isdigit():
                upload_id = int(ref)
            else:
                # A file path: stage + validate it first, then restore from
                # the recorded upload — same two steps the UI performs.
                with open(ref, "rb") as handle:
                    row = backup_recovery.stage_upload(
                        handle, os.path.basename(ref), "source_dump", args.actor)
                if not row["valid"]:
                    _print(row)
                    print(f"refusing to restore: upload failed validation "
                          f"({row['invalid_reason']})", file=sys.stderr)
                    return 4
                upload_id = row["id"]
            _print(backup_recovery.restore_source(
                upload_id, confirm=args.confirm or "", by=args.actor,
                dry_run=args.dry_run))
            return 0

        parser.print_help()
        return 2
    except (ValidationError, NotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
