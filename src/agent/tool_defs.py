"""Agent tool definitions for source-swap and disaster recovery (§6).

`conversational-ai-assistant` (not yet landed) builds the tool registry in
``src/agent/tools.py`` with this exact ToolDef shape (its design, Decision 3).
These two tools are defined HERE so that change can register them with one
import (``from .tool_defs import RECOVERY_TOOLS``) — and so their gating is
testable now, standalone:

* ``swap_source_schema`` — propose-only by default: without ``apply`` it runs
  the read-only source-swap dry-run. The apply path demands the same typed
  confirmation token the CLI does AND stays behind the service's confidence
  gate, so no autonomy tier can skip either.
* ``recover_from_backup`` — hard-coded ``destructive`` (design D4): a restore
  discards current data, so it requires explicit confirmation regardless of
  the conversation's autonomy tier. The typed-confirmation check lives in
  `backup_recovery` itself (before any DB or shell access), so this holds for
  every caller, not just the agent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from ..services.common import ValidationError

AUTONOMY_LEVELS = ("propose_only", "auto_safe", "destructive")


@dataclass
class ToolDef:
    """The registry entry shape from `conversational-ai-assistant` design D3
    (+ the explicit `destructive` flag its task 2.1 adds). A tool whose
    autonomy is "destructive" is always destructive; the separate flag lets a
    propose_only tool that CAN mutate on confirmation also declare itself."""
    name: str
    description: str
    params_schema: dict
    handler: Callable
    autonomy: str = "propose_only"
    destructive: bool = False

    def __post_init__(self):
        if self.autonomy not in AUTONOMY_LEVELS:
            raise ValidationError(
                f"tool {self.name!r}: autonomy must be one of {AUTONOMY_LEVELS}")
        if self.autonomy == "destructive":
            self.destructive = True


def validate_params(tool: ToolDef, params: dict) -> dict:
    """Minimal schema check: required keys present, primitive types match.
    (The registry change may swap this for a full JSON-schema validator; the
    tests pin the behavioural contract, not the implementation.)"""
    schema = tool.params_schema
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in params:
            raise ValidationError(f"tool {tool.name!r}: missing required param {key!r}")
    type_map = {"string": str, "boolean": bool, "number": (int, float),
                "integer": int}
    for key, value in params.items():
        expected = props.get(key, {}).get("type")
        if expected and not isinstance(value, type_map.get(expected, object)):
            raise ValidationError(
                f"tool {tool.name!r}: param {key!r} must be {expected}")
    return params


# ---------------------------------------------------------------------------
# swap_source_schema
# ---------------------------------------------------------------------------

def _swap_source_schema(params: dict, **seams) -> dict:
    """Dry-run by default; apply only with the typed source-schema token.
    ``seams`` pass through to `schema_swap` for tests (adapters, fetchers)."""
    from ..adapters import get_source_adapter
    from ..services import schema_swap

    source_schema = params.get("source_schema") or os.environ.get(
        "SOURCE_SCHEMA", "irimsv")
    adapter = seams.pop("source_adapter", None) or get_source_adapter(
        params.get("source_engine", "postgres"), schema=source_schema)

    if not params.get("apply"):
        return schema_swap.dry_run(side="source", source_adapter=adapter, **seams)

    if params.get("confirm") != source_schema:
        raise ValidationError(
            f"swap_source_schema apply requires confirm={source_schema!r} "
            "(typed confirmation; the agent cannot skip it at any autonomy tier)")
    return schema_swap.apply(
        side="source", source_adapter=adapter,
        actor=params.get("actor", "agent:swap_source_schema"),
        threshold=float(params.get("threshold", 0.7)),
        force=bool(params.get("force", False)), **seams)


SWAP_SOURCE_SCHEMA = ToolDef(
    name="swap_source_schema",
    description=(
        "Diff a restructured/replacement IRIMSV source against the approved "
        "source contracts and propose AI re-maps for the affected entities. "
        "Read-only unless apply=true, which additionally requires the typed "
        "source-schema confirmation and passes the same low-confidence human "
        "gate as the CLI. Never issues DDL/DML against the source."),
    params_schema={
        "type": "object",
        "properties": {
            "source_engine": {"type": "string"},
            "source_schema": {"type": "string"},
            "apply": {"type": "boolean"},
            "force": {"type": "boolean"},
            "threshold": {"type": "number"},
            "confirm": {"type": "string"},
            "actor": {"type": "string"},
        },
        "required": [],
    },
    handler=_swap_source_schema,
    autonomy="propose_only",
)


# ---------------------------------------------------------------------------
# recover_from_backup
# ---------------------------------------------------------------------------

def _recover_from_backup(params: dict, **seams) -> dict:
    from ..services import backup_recovery

    action = params.get("action")
    confirm = params.get("confirm") or ""
    by = params.get("actor", "agent:recover_from_backup")
    if action == "restore_target":
        return backup_recovery.restore_target(
            str(params["backup_id"]), confirm=confirm, by=by,
            dry_run=bool(params.get("dry_run", False)), **seams)
    if action == "restore_source":
        return backup_recovery.restore_source(
            int(params["upload_id"]), confirm=confirm, by=by,
            dry_run=bool(params.get("dry_run", False)), **seams)
    raise ValidationError(
        "recover_from_backup: action must be 'restore_target' or 'restore_source'")


RECOVER_FROM_BACKUP = ToolDef(
    name="recover_from_backup",
    description=(
        "Restore the target from a listed backup / validated upload, or the "
        "source from a validated uploaded dump. ALWAYS destructive: requires "
        "the typed confirmation token no matter the autonomy tier (design D4) "
        "- the service enforces it before any database or shell access."),
    params_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "backup_id": {"type": "string"},
            "upload_id": {"type": "integer"},
            "confirm": {"type": "string"},
            "reason": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "actor": {"type": "string"},
        },
        "required": ["action"],
    },
    handler=_recover_from_backup,
    # Hard-coded, never tierable (D4). ToolDef validates the value; the
    # registry must take this entry as-is.
    autonomy="destructive",
)

RECOVERY_TOOLS: list[ToolDef] = [SWAP_SOURCE_SCHEMA, RECOVER_FROM_BACKUP]
