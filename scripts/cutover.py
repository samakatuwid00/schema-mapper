"""Legacy-staging retirement cutover (retire-legacy-staging).

Phase 0 — the migration gate. `--precheck` (read-only) reports whether every
deployed entity has been migrated off legacy staging; it exits non-zero while any
legacy-only entity remains, so the destructive steps (and CI) can gate on it.

    python scripts/cutover.py --precheck

The destructive steps (collapse worker, delete staging code, drop databases) are
NOT run here — they are separate, confirmed operations once the gate passes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.cutover import precheck


def main() -> int:
    parser = argparse.ArgumentParser(description="Legacy-staging retirement cutover")
    parser.add_argument("--precheck", action="store_true",
                        help="read-only migration gate (exit 3 if not ready)")
    parser.add_argument("--target-system", default=os.environ.get("LRMIS_TARGET_SYSTEM", "LRMIS"))
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    if not args.precheck:
        print("nothing to do: pass --precheck for the read-only migration gate. "
              "Destructive steps are separate, confirmed operations.", file=sys.stderr)
        return 2

    result = precheck(target_system=args.target_system)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("=== retire-legacy-staging: migration gate ===")
        print(f"entities total    : {result['total']}")
        print(f"  on direct target: {result['on_target']}")
        print(f"  legacy-only     : {result['legacy_only_total']} by status "
              f"{result['legacy_only_by_status']}")
        print(f"  BLOCKING (legacy-only, delivering/resumable): {result['blocking_count']}")
        for name in result["blocking"]:
            print(f"       - {name}")
        verdict = ("PASS - safe to proceed" if result["ready"]
                   else "BLOCKED - migrate or disable the entities above first")
        print(f"\nGATE: {verdict}")
    return 0 if result["ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
