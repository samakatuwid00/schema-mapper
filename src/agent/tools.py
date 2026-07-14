"""MVP tool registry (conversational-ai-assistant §2, design D3).

Seven read/propose tools the conversation loop can dispatch, each a thin
wrapper over an existing service — no new business logic lives here. Every
handler is schema-only by construction: none of the wrapped services return
row values, and `summarize_proposal` re-shapes field reviews down to an
explicit allowlist of schema-level keys.

Also registers the two tools `source-schema-swap-and-disaster-recovery`
defined ahead of time in `tool_defs.py` (`swap_source_schema`,
`recover_from_backup`) — closing that change's deferred task 6.3.

Autonomy tiers (task 0.4): only `propose_only` and `auto_safe` are dispatch
tiers in the MVP; `destructive` tools always require confirmation regardless
of tier (enforced again inside the services themselves — typed-confirmation
checks run before any DB or shell access).
"""
from __future__ import annotations

import os

from ..services import onboarding as onboarding_service
from ..services import ops as ops_service
from .tool_defs import (RECOVERY_TOOLS, ToolDef, validate_params)

__all__ = ["REGISTRY", "MVP_TOOLS", "ToolDef", "get_tool", "list_tools",
           "validate_params"]


# ---------------------------------------------------------------------------
# Handlers (thin wrappers; services are module attributes so tests patch them)
# ---------------------------------------------------------------------------

def _check_status(params: dict, **seams) -> dict:
    return ops_service.get_status()


def _show_schema(params: dict, **seams) -> dict:
    kwargs = {}
    if params.get("source_schema"):
        kwargs["source_schema"] = params["source_schema"]
    return ops_service.get_schema_trees(**kwargs)


_FIELD_KEYS = ("source_column", "suggested_target_table",
               "suggested_target_column", "resolved_target_column",
               "confidence", "status", "transform")


def _field_view(field: dict) -> dict:
    """Schema-level allowlist projection of a field review row (redaction by
    construction — sample values or payloads never pass through)."""
    return {k: field.get(k) for k in _FIELD_KEYS}


def _summarize_proposal(params: dict, **seams) -> dict:
    review = onboarding_service.get_review(int(params["proposal_id"]))
    fields = review["fields"]
    threshold = float(params.get("threshold", 0.7))
    by_status: dict[str, int] = {}
    for field in fields:
        key = str(field.get("status") or "unknown")
        by_status[key] = by_status.get(key, 0) + 1
    low = [f for f in fields
           if not f.get("suggested_target_column")
           or (f.get("confidence") or 0.0) < threshold]
    unmet = review["proposal"].get("unmet_required_columns") or []
    return {
        "proposal": review["proposal"],
        "field_count": len(fields),
        "by_status": by_status,
        "low_confidence": [_field_view(f) for f in low],
        "unmet_required_columns": unmet,
        "risk": "high" if (low or unmet) else "low",
        "summary": (f"{len(fields)} mapped columns, {len(low)} below "
                    f"{threshold:.2f} or unmapped, "
                    f"{len(unmet)} required target columns unmet, "
                    f"status {review['proposal']['status']!r}."),
    }


def _explain_blocker(params: dict, **seams) -> dict:
    review = onboarding_service.get_review(int(params["proposal_id"]))
    proposal = review["proposal"]
    fields = review["fields"]
    accepted = [f for f in fields
                if f.get("status") in ("accepted", "resolved")
                and f.get("suggested_target_table")]
    unmet = proposal.get("unmet_required_columns") or []

    blockers: list[str] = []
    if proposal["status"] not in ("approved", "auto_approved"):
        blockers.append(f"proposal status is {proposal['status']!r}; it must "
                        "be approved before deploy")
    if not accepted:
        blockers.append("no accepted target-column mappings yet — review and "
                        "accept (or resolve) the proposed fields")
    for column in unmet:
        blockers.append(f"required target column {column!r} has no mapping "
                        "and the writer does not fill it")
    return {
        "proposal_id": proposal["id"],
        "status": proposal["status"],
        "accepted_fields": len(accepted),
        "unmet_required_columns": unmet,
        "blockers": blockers,
        "deploy_ready": not blockers,
    }


def _deploy_guidance(params: dict, **seams) -> dict:
    """Readiness + recommended next actions. Never executes a deploy."""
    explained = _explain_blocker(params, **seams)
    actions: list[str] = []
    if explained["deploy_ready"]:
        actions.append("deploy: run the deploy job (typed confirmation) — "
                       "coverage and approval gates are green")
    else:
        for blocker in explained["blockers"]:
            if "must be approved" in blocker:
                actions.append("approve the proposal (Mapping Review page or "
                               "approve-mapping action)")
            elif "no accepted" in blocker:
                actions.append("accept or resolve the proposed field mappings")
            else:
                actions.append(f"resolve: {blocker}")
    return {**explained, "recommended_actions": actions, "executed": False}


def _explain_dilemma(params: dict, **seams) -> dict:
    from .agent import Dilemma, MigrationAgent
    agent = seams.get("agent") or MigrationAgent()
    dilemma = Dilemma(kind=params.get("kind", "unknown"),
                      table=params.get("table", ""),
                      column=params.get("column", ""),
                      context=params.get("context") or {})
    guidance = agent.guide(dilemma)
    return {"kind": dilemma.kind, "table": dilemma.table,
            "column": dilemma.column, "options": guidance.options,
            "recommended": guidance.recommended}


def _onboard_table(params: dict, **seams) -> dict:
    """Propose-only: creates a proposal for human review; deploys nothing."""
    source_schema = params.get("source_schema") or os.environ.get(
        "SOURCE_SCHEMA", "irimsv")
    result = onboarding_service.propose(
        source_schema, params["source_table"],
        params.get("target_system", "LRMIS"))
    return {"proposal": result,
            "note": ("proposal created for human review; nothing was "
                     "deployed — approval and deploy stay gated.")}


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

_PROPOSAL_PARAMS = {
    "type": "object",
    "properties": {"proposal_id": {"type": "integer"},
                   "threshold": {"type": "number"}},
    "required": ["proposal_id"],
}

MVP_TOOLS: list[ToolDef] = [
    ToolDef(
        name="check_status",
        description="Integration health: outbox counts, oldest pending age, "
                    "entity states, quarantine and drift summary.",
        params_schema={"type": "object", "properties": {}, "required": []},
        handler=_check_status, autonomy="auto_safe"),
    ToolDef(
        name="summarize_proposal",
        description="Summarize a mapping proposal: field counts by status, "
                    "low-confidence/unmapped columns, unmet required target "
                    "columns, overall risk.",
        params_schema=_PROPOSAL_PARAMS,
        handler=_summarize_proposal, autonomy="auto_safe"),
    ToolDef(
        name="explain_blocker",
        description="Why a proposal cannot deploy yet: approval status, "
                    "accepted-mapping coverage, unmet required columns.",
        params_schema=_PROPOSAL_PARAMS,
        handler=_explain_blocker, autonomy="auto_safe"),
    ToolDef(
        name="show_schema",
        description="The source and target schema trees (structure only).",
        params_schema={"type": "object",
                       "properties": {"source_schema": {"type": "string"}},
                       "required": []},
        handler=_show_schema, autonomy="auto_safe"),
    ToolDef(
        name="deploy_guidance",
        description="Deploy readiness for a proposal plus recommended next "
                    "actions. Never executes the deploy.",
        params_schema=_PROPOSAL_PARAMS,
        handler=_deploy_guidance, autonomy="auto_safe"),
    ToolDef(
        name="explain_dilemma",
        description="Resolution options for a blocked mapping decision "
                    "(unmapped column, type mismatch, FK violation) with a "
                    "recommendation. Proposes only; the human picks.",
        params_schema={"type": "object",
                       "properties": {"kind": {"type": "string"},
                                      "table": {"type": "string"},
                                      "column": {"type": "string"},
                                      "context": {}},
                       "required": ["kind"]},
        handler=_explain_dilemma, autonomy="auto_safe"),
    ToolDef(
        name="onboard_table",
        description="Discover a source table and create an AI mapping "
                    "proposal for human review. Propose-only: nothing is "
                    "approved or deployed by this tool.",
        params_schema={"type": "object",
                       "properties": {"source_table": {"type": "string"},
                                      "source_schema": {"type": "string"},
                                      "target_system": {"type": "string"}},
                       "required": ["source_table"]},
        handler=_onboard_table, autonomy="propose_only"),
]

# MVP tools + the source-swap/recovery tools defined by
# source-schema-swap-and-disaster-recovery (its deferred task 6.3: register
# here once this registry exists).
REGISTRY: dict[str, ToolDef] = {t.name: t for t in [*MVP_TOOLS, *RECOVERY_TOOLS]}


def get_tool(name: str) -> ToolDef:
    from ..services.common import NotFoundError
    tool = REGISTRY.get(name)
    if tool is None:
        raise NotFoundError(f"unknown tool {name!r}")
    return tool


def list_tools() -> list[dict]:
    """Name/description/autonomy/destructive for the NL classifier prompt."""
    return [{"name": t.name, "description": t.description,
             "autonomy": t.autonomy, "destructive": t.destructive}
            for t in REGISTRY.values()]
