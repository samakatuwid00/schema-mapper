"""Migration agent CLI (generic engine, §8.5).

The `sync-engine agent` entry point. Runs the AI planner over a source table and
the discovered target schema and prints a graded plan (proposed mappings, risks,
whether it can auto-deploy). Guide/heal are library calls used interactively by
the deploy/worker paths (see src/agent/); this CLI covers the plan step, which is
the read-only, schema-only start of an agent session.

    python scripts/agent.py --plan --source-table schools \
        --target-engine postgres --target-tables school,station

Needs a live/restored target (for discovery) and central (for the source table).
Folds into `sync-engine agent` when the generic CLI lands (Sec 10).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.adapters import get_target_adapter
from src.agent import MigrationAgent
from src.connectors import PostgresCentralConnector
from src.services.schema_swap import build_target_schema, _default_discover_source


def _adapter_kwargs(args) -> dict:
    kwargs: dict = {}
    if args.target_engine.lower() in ("postgres", "postgresql", "pg"):
        if args.dsn:
            kwargs["dsn"] = args.dsn
        kwargs["schema"] = args.schema
    return kwargs


def main() -> int:
    parser = argparse.ArgumentParser(description="Migration agent — plan a source table")
    parser.add_argument("--plan", action="store_true", help="run the planner (default action)")
    parser.add_argument("--source-schema", default=os.environ.get("SOURCE_SCHEMA", "irimsv"))
    parser.add_argument("--source-table", required=True)
    parser.add_argument("--target-engine", default="postgres")
    parser.add_argument("--dsn", help="target DSN (or LRMIS_TARGET_PG_DSN)")
    parser.add_argument("--schema", default="public", help="target schema (postgres)")
    parser.add_argument("--target-tables", help="comma-separated target tables to consider "
                                                "(default: all discovered)")
    parser.add_argument("--threshold", type=float, default=0.7)
    args = parser.parse_args()

    adapter = get_target_adapter(args.target_engine, **_adapter_kwargs(args))
    registry = adapter.discover_registry()
    tables = ([t.strip() for t in args.target_tables.split(",") if t.strip()]
              if args.target_tables else registry.table_names)
    target_schema = build_target_schema(registry, tables)

    affected = [{"source_schema": args.source_schema, "source_table": args.source_table}]
    sources = _default_discover_source(PostgresCentralConnector(), affected)
    source_table = sources.get(args.source_table)
    if source_table is None:
        print(f"source table {args.source_schema}.{args.source_table} not found", file=sys.stderr)
        return 2

    plan = MigrationAgent(threshold=args.threshold).plan(source_table, target_schema)
    print(json.dumps({
        "source_table": plan.source_table,
        "auto_ok": plan.auto_ok,
        "risks": [{"kind": r.kind, "detail": r.detail} for r in plan.risks],
        "low_confidence": plan.low_confidence,
        "mappings": plan.mappings,
    }, indent=2, default=str))
    return 0 if plan.auto_ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
