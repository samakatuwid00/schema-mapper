"""sync-engine — unified CLI for the generic AI DB migration engine (§10).

Supersedes the per-task `pipeline.py` commands (§10.6). Subcommands:

    sync-engine init [path]          create an engine config template (§10.1)
    sync-engine plan   <args>        AI planner over a source table (§10.2)  -> agent.py --plan
    sync-engine schema-swap <args>   dry-run / apply a schema swap, --side source|target (§10.4)
    sync-engine agent  <args>        agent session (§10.5)
    sync-engine run    <args>        full migration (backfill + CDC) (§10.3, not yet implemented)

Pass-through subcommands delegate to the focused scripts so there is a single
entry point without duplicated logic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(__file__)

_CONFIG_TEMPLATE = {
    "source": {"engine": "postgres",
               "dsn": "postgresql://postgres:postgres@localhost:5433/central",
               "schema": "irimsv"},
    "target": {"engine": "postgres",
               "dsn": "postgresql://postgres:postgres@localhost:5433/oldlrmis",
               "schema": "lrmis"},
    "cdc": {"strategy": "trigger", "options": {}},
    "llm": {"LLM_PROVIDER_ORDER": "gemini,fallback,heuristic"},
    "plugin": "OLD_LRMIS",
}


def _delegate(script: str, rest: list[str]) -> int:
    return subprocess.call([sys.executable, os.path.join(_HERE, script)] + rest)


def cmd_init(rest: list[str]) -> int:
    path = rest[0] if rest else "sync-engine.config.json"
    if os.path.exists(path):
        print(f"{path} already exists; not overwriting", file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf8") as fh:
        json.dump(_CONFIG_TEMPLATE, fh, indent=2)
    print(f"wrote engine config template: {path}")
    return 0


def cmd_run(rest: list[str]) -> int:
    print("sync-engine run (full backfill + CDC) is not implemented yet (§10.3). "
          "Use `schema-swap` to adopt a target schema and `agent`/`plan` to map.",
          file=sys.stderr)
    return 2


_COMMANDS = {
    "init": cmd_init,
    "plan": lambda rest: _delegate("agent.py", ["--plan"] + rest),
    "schema-swap": lambda rest: _delegate("schema_swap.py", rest),
    "agent": lambda rest: _delegate("agent.py", rest),
    "run": cmd_run,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f"unknown command: {cmd!r}. Try: {', '.join(_COMMANDS)}", file=sys.stderr)
        return 2
    return fn(rest)


if __name__ == "__main__":
    raise SystemExit(main())
