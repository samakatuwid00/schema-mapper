"""Schema-swap CLI (generic engine, §0.4-0.5; side-agnostic).

Preview, or apply, adopting a new schema on either side of the pipeline.

    # dry-run against a restored Postgres target (needs a live/restored DB)
    python scripts/schema_swap.py --target-engine postgres --dry-run

    # show the guarded restore plan for the .backup that seeds that target
    python scripts/schema_swap.py --target-engine postgres --dry-run \
        --backup "C:/.../old-lrmis.backup"

    # confirmed apply (destructive): re-map affected entities, recreate, re-deliver
    python scripts/schema_swap.py --target-engine postgres \
        --confirm lrmis_target --backup "C:/.../old-lrmis.backup"

    # SOURCE side: diff a restructured/replacement IRIMSV source and preview
    # the re-maps (never issues DDL/DML against the source)
    python scripts/schema_swap.py --side source --dry-run

    # confirmed source apply: persist approved re-maps, resume delivery
    python scripts/schema_swap.py --side source --confirm irimsv

The apply re-maps affected entities with the AI (schema-only) and BLOCKS on any
low-confidence mapping unless --force is given. A non-MySQL target is re-mapped
and recreated, but row delivery is reported pending the generic writer (Sec 4/7).

`sync-engine schema-swap` delegates here, so --side works there identically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.adapters import get_source_adapter, get_target_adapter
from src.services import schema_swap
from src.services.pg_restore import restore_pg_backup


def _adapter_kwargs(args) -> dict:
    kwargs: dict = {}
    if args.target_engine.lower() in ("postgres", "postgresql", "pg"):
        if args.dsn:
            kwargs["dsn"] = args.dsn
        kwargs["schema"] = args.schema
    return kwargs


def _expected_confirm(args) -> str:
    """The typed token the operator must supply to run the destructive apply —
    the target database name, mirroring the nightly rebuild's guard."""
    if args.target_engine.lower() in ("postgres", "postgresql", "pg"):
        dsn = args.dsn or os.environ.get("LRMIS_TARGET_PG_DSN", "")
        if dsn:
            db = dsn.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
            if db:
                return db
    return os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply a schema swap (source or target side)")
    parser.add_argument("--side", choices=("source", "target"), default="target",
                        help="which side of the pipeline changed (default: target)")
    parser.add_argument("--target-engine", default="postgres",
                        help="engine of the new target (postgres|mysql)")
    parser.add_argument("--source-engine", default="postgres",
                        help="engine of the new source (postgres)")
    parser.add_argument("--source-schema",
                        default=os.environ.get("SOURCE_SCHEMA", "irimsv"),
                        help="schema of the (new) source database (side=source)")
    parser.add_argument("--dsn", help="target DSN (or LRMIS_TARGET_PG_DSN for postgres)")
    parser.add_argument("--schema", default="public", help="target schema name (postgres)")
    parser.add_argument("--backup", help="path to the target .backup (restore plan / apply recreate)")
    parser.add_argument("--target-system", default="LRMIS")
    parser.add_argument("--confirm", metavar="TOKEN",
                        help="type the target database name (side=target) or the "
                             "source schema name (side=source) to APPLY the swap")
    parser.add_argument("--force", action="store_true",
                        help="apply even when re-mappings are below the confidence threshold")
    parser.add_argument("--threshold", type=float, default=0.7,
                        help="confidence threshold below which a re-mapping needs review")
    parser.add_argument("--actor", default="cli:schema_swap")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the plan without changing anything")
    args = parser.parse_args()

    # --- source side: discovery + re-mapping only, never source DDL/DML ---
    if args.side == "source":
        adapter = get_source_adapter(args.source_engine, schema=args.source_schema)
        if args.confirm is not None and not args.dry_run:
            expected = args.source_schema
            if args.confirm != expected:
                print(f"refusing to apply: pass --confirm {expected} to approve "
                      f"the source-side re-map (or use --dry-run).", file=sys.stderr)
                return 2
            result = schema_swap.apply(
                side="source", source_adapter=adapter, actor=args.actor,
                force=args.force, threshold=args.threshold,
                target_system=args.target_system)
            print(json.dumps(result, default=str, indent=2))
            return 0 if result.get("status") != "blocked_on_review" else 3
        result = schema_swap.dry_run(
            side="source", source_adapter=adapter, target_system=args.target_system)
        print(json.dumps({"swap": result}, default=str, indent=2))
        return 0

    adapter = get_target_adapter(args.target_engine, **_adapter_kwargs(args))

    # Confirmed, destructive apply.
    if args.confirm is not None and not args.dry_run:
        expected = _expected_confirm(args)
        if args.confirm != expected:
            print(f"refusing to apply: pass --confirm {expected} to confirm the "
                  f"destructive swap of the target (or use --dry-run).", file=sys.stderr)
            return 2
        result = schema_swap.apply(
            target_adapter=adapter, actor=args.actor, force=args.force,
            threshold=args.threshold, backup_path=args.backup, dsn=args.dsn,
            target_system=args.target_system)
        print(json.dumps(result, default=str, indent=2))
        return 0 if result.get("status") != "blocked_on_review" else 3

    # Dry-run preview (default).
    out: dict = {}
    if args.backup:
        out["restore_plan"] = restore_pg_backup(
            backup_path=args.backup, dsn=args.dsn, dry_run=True)
    out["swap"] = schema_swap.dry_run(
        target_adapter=adapter, target_system=args.target_system)
    print(json.dumps(out, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
