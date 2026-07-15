"""MVP tool registry (conversational-ai-assistant §2, design D3).

Seven read/propose tools the conversation loop can dispatch, each a thin
wrapper over an existing service — no new business logic lives here. Every
handler is schema-only by construction: none of the wrapped services return
row values, and `summarize_proposal` re-shapes field reviews down to an
explicit allowlist of schema-level keys.

Also registers the two tools `source-schema-swap-and-disaster-recovery`
defined ahead of time in `tool_defs.py` (`swap_source_schema`,
`recover_from_backup`) — closing that change's deferred task 6.3 — and the
§8 later-phase tools (`heal_error`, drift resolution, target schema-swap)
defined below in `LATER_PHASE_TOOLS`.

Autonomy tiers (task 0.4): only `propose_only` and `auto_safe` are dispatch
tiers in the MVP; `destructive` tools always require confirmation regardless
of tier (enforced again inside the services themselves — typed-confirmation
checks run before any DB or shell access).
"""
from __future__ import annotations

import os

from ..services import drift_resolution as drift_service
from ..services import onboarding as onboarding_service
from ..services import ops as ops_service
from ..services import operator_diagnostics as diagnostics_service
from ..services import schema_swap as schema_swap_service
from .tool_defs import (RECOVERY_TOOLS, ToolDef, ValidationError,
                        validate_params)

__all__ = ["REGISTRY", "MVP_TOOLS", "LATER_PHASE_TOOLS", "ToolDef",
           "get_tool", "list_tools", "validate_params"]


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


_FIELD_KEYS = ("review_id", "source_column", "suggested_target_table",
               "suggested_target_column", "resolved_target_column",
               "confidence", "status", "transform")


def _field_view(field: dict) -> dict:
    """Schema-level allowlist projection of a field review row (redaction by
    construction — sample values or payloads never pass through)."""
    return {
        "review_id": field.get("review_id") or field.get("id"),
        "source_column": field.get("source_column"),
        "suggested_target_table": field.get("suggested_target_table"),
        "suggested_target_column": field.get("suggested_target_column"),
        "resolved_target_column": field.get("resolved_target_column"),
        "confidence": field.get("confidence"),
        "status": field.get("status"),
        "transform": field.get("transform"),
    }


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
    accepted = [
        f for f in fields
        if f.get("status") in ("accepted", "resolved")
        and f.get("suggested_target_table")
        and (f.get("resolved_target_column") or f.get("suggested_target_column"))
    ]
    unmet = review["proposal"].get("unmet_required_columns") or []
    return {
        "proposal": review["proposal"],
        "field_count": len(fields),
        "by_status": by_status,
        "low_confidence": [_field_view(f) for f in low],
        "accepted_mappings": [_field_view(f) for f in accepted],
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


def _inspect_job(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    result = service.inspect_job(params.get("job_id"))
    if result.get("failures"):
        try:
            result["repair_plan"] = service.plan_refresh_failure_repair(
                result.get("job_id") or params.get("job_id"),
                target_system=params.get("target_system", "LRMIS"))
        except Exception as exc:
            result["repair_plan_error"] = str(exc)
    return result


def _diagnose_entity_delivery(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    return service.diagnose_entity_delivery(
        params["entity"], params.get("target_system", "LRMIS"))


def _explain_deploy_error(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    return service.explain_deploy_error(
        params["error"],
        entity=params.get("entity"),
        proposal_id=params.get("proposal_id"))


def _diagnose_duplicate_key(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    return service.diagnose_duplicate_key(
        params["entity"],
        error=params.get("error"),
        target_table=params.get("target_table"),
        target_column=params.get("target_column"),
        target_id=params.get("target_id"),
        target_system=params.get("target_system", "LRMIS"))


def _plan_refresh_failure_repair(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    return service.plan_refresh_failure_repair(
        params.get("job_id"),
        target_system=params.get("target_system", "LRMIS"))


def _resolve_deploy_job_repair(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    return service.resolve_deploy_job_repair(
        params["text"], target_system=params.get("target_system", "LRMIS"))


def _repair_duplicate_key(params: dict, **seams) -> dict:
    service = seams.get("diagnostics_service") or diagnostics_service
    return service.repair_duplicate_key(
        params["entity"],
        error=params.get("error"),
        target_table=params.get("target_table"),
        target_column=params.get("target_column"),
        target_id=params.get("target_id"),
        target_system=params.get("target_system", "LRMIS"),
        actor=params.get("actor", "agent"))


def _add_mapping(params: dict, **seams) -> dict:
    return onboarding_service.add_mapping(
        int(params["proposal_id"]),
        params["source_column"],
        params["target_table"],
        params["target_column"],
        transform=params.get("transform", "none"),
        resolved_by=params.get("actor", "agent"))


def _add_missing_mappings(params: dict, **seams) -> dict:
    return onboarding_service.add_missing_mappings(
        int(params["proposal_id"]),
        list(params["mappings"]),
        resolved_by=params.get("actor", "agent"))


def _reject_mapping(params: dict, **seams) -> dict:
    return onboarding_service.reject(
        int(params["proposal_id"]),
        params["source_column"],
        rejected_by=params.get("actor", "agent"))


def _reject_mapping_review(params: dict, **seams) -> dict:
    return onboarding_service.reject_field_review(
        int(params["review_id"]),
        rejected_by=params.get("actor", "agent"))


def _reopen_mapping_review(params: dict, **seams) -> dict:
    return onboarding_service.reopen_field_review(
        int(params["review_id"]),
        reopened_by=params.get("actor", "agent"))


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

# ---------------------------------------------------------------------------
# §8 later-phase tools: heal, drift resolution, target schema-swap
# ---------------------------------------------------------------------------

def _heal_error(params: dict, **seams) -> dict:
    """§8.1 — propose-only unless safe auto-heal is explicitly allowlisted
    via AGENT_AUTONOMOUS_HEAL (and even then MigrationAgent only auto-applies
    the safe `cast` action)."""
    from dataclasses import asdict

    from .agent import MigrationAgent
    allowlisted = os.environ.get("AGENT_AUTONOMOUS_HEAL", "").strip().lower() \
        in ("1", "true", "yes")
    agent = seams.get("agent") or MigrationAgent(autonomous_heal=allowlisted)
    proposal = agent.heal(str(params["error"]),
                          dict(params.get("context") or {}))
    return {**asdict(proposal),
            "note": ("safe cast auto-applied (AGENT_AUTONOMOUS_HEAL "
                     "allowlist)." if proposal.apply else
                     "heal proposal only — nothing was changed; apply it "
                     "through the existing resolution flow.")}


def _list_drift_reports(params: dict, **seams) -> dict:
    reports = ops_service.list_drift_reports(
        limit=int(params.get("limit", 20)))
    return {"count": len(reports), "reports": reports}


def _resolve_drift(params: dict, **seams) -> dict:
    """§8.2 apply step — destructive (live drop+recreate on the target side),
    so the registry marks it destructive and chat always confirms it."""
    entities = params.get("entities")
    return drift_service.resolve_drift(
        resolve_source=bool(params.get("resolve_source", True)),
        resolve_target=bool(params.get("resolve_target", True)),
        entities=list(entities) if entities else None,
        actor=str(params.get("actor", "agent:resolve_drift")),
        dry_run=bool(params.get("dry_run", False)))


def _target_engine(params: dict) -> str:
    return str(params.get("target_engine")
               or os.environ.get("LRMIS_TARGET_ENGINE", "mysql")).lower()


def _expected_target_confirm(engine: str) -> str:
    """The typed token for a destructive target swap — the target database
    name, exactly the CLI's guard."""
    if engine in ("postgres", "postgresql", "pg"):
        dsn = os.environ.get("LRMIS_TARGET_PG_DSN", "")
        if dsn:
            db = dsn.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
            if db:
                return db
    return os.environ.get("LRMIS_TARGET_DATABASE", "lrmis_target")


def _swap_target_dry_run(params: dict, **seams) -> dict:
    from ..adapters import get_target_adapter
    adapter = seams.get("target_adapter") or get_target_adapter(
        _target_engine(params))
    return schema_swap_service.dry_run(target_adapter=adapter)


def _swap_target_apply(params: dict, **seams) -> dict:
    from ..adapters import get_target_adapter
    engine = _target_engine(params)
    expected = _expected_target_confirm(engine)
    if params.get("confirm") != expected:
        raise ValidationError(
            f"swap_target_apply requires confirm={expected!r} — the typed "
            "confirmation token the CLI uses; chat approval alone cannot "
            "run a destructive target recreate")
    adapter = seams.get("target_adapter") or get_target_adapter(engine)
    return schema_swap_service.apply(
        target_adapter=adapter,
        actor=str(params.get("actor", "agent:swap_target")),
        threshold=float(params.get("threshold", 0.7)),
        force=bool(params.get("force", False)),
        backup_path=params.get("backup_path"), dsn=params.get("dsn"))


LATER_PHASE_TOOLS: list[ToolDef] = [
    ToolDef(
        name="heal_error",
        description="Propose a fix for a delivery error (safe cast or "
                    "quarantine). Propose-only unless safe auto-heal is "
                    "explicitly allowlisted via AGENT_AUTONOMOUS_HEAL.",
        params_schema={"type": "object",
                       "properties": {"error": {"type": "string"},
                                      "context": {"type": "object"}},
                       "required": ["error"]},
        handler=_heal_error, autonomy="propose_only"),
    ToolDef(
        name="list_drift_reports",
        description="List recorded schema-drift reports (fingerprints, "
                    "differences, impacted entities) — the first step of "
                    "drift resolution.",
        params_schema={"type": "object",
                       "properties": {"limit": {"type": "integer"}},
                       "required": []},
        handler=_list_drift_reports, autonomy="auto_safe"),
    ToolDef(
        name="resolve_drift",
        description="Re-map and re-deliver drifted entities (live "
                    "drop+recreate on the target side). Destructive: always "
                    "requires confirmation. Supports dry_run and an entities "
                    "filter.",
        params_schema={"type": "object",
                       "properties": {"entities": {"type": "object"},
                                      "resolve_source": {"type": "boolean"},
                                      "resolve_target": {"type": "boolean"},
                                      "dry_run": {"type": "boolean"}},
                       "required": []},
        handler=_resolve_drift, autonomy="destructive"),
    ToolDef(
        name="swap_target_dry_run",
        description="Preview a target schema swap: discover the (new) target, "
                    "diff against the approved registry, report affected "
                    "entities and proposed re-maps. Read-only.",
        params_schema={"type": "object",
                       "properties": {"target_engine": {"type": "string"}},
                       "required": []},
        handler=_swap_target_dry_run, autonomy="auto_safe"),
    ToolDef(
        name="swap_target_apply",
        description="Confirmed target schema swap: re-map affected entities "
                    "(human-gated on low confidence), recreate the target, "
                    "re-deliver. Destructive; requires the typed target-db "
                    "confirmation token in addition to chat approval.",
        params_schema={"type": "object",
                       "properties": {"target_engine": {"type": "string"},
                                      "confirm": {"type": "string"},
                                      "threshold": {"type": "number"},
                                      "force": {"type": "boolean"},
                                      "backup_path": {"type": "string"},
                                      "dsn": {"type": "string"}},
                       "required": []},
        handler=_swap_target_apply, autonomy="destructive"),
    ToolDef(
        name="inspect_job",
        description="Inspect an admin worker job by id, or the latest job: "
                    "status, progress, error, recent events, and per-entity "
                    "failures where available. Read-only.",
        params_schema={"type": "object",
                       "properties": {"job_id": {"type": "string"},
                                      "target_system": {"type": "string"}},
                       "required": []},
        handler=_inspect_job, autonomy="auto_safe"),
    ToolDef(
        name="diagnose_entity_delivery",
        description="Diagnose a deployed entity that has no target data: "
                    "entity status, target tables, target row counts, "
                    "crosswalk counts, latest related refresh job, and next "
                    "safe action. Read-only.",
        params_schema={"type": "object",
                       "properties": {"entity": {"type": "string"},
                                      "target_system": {"type": "string"}},
                       "required": ["entity"]},
        handler=_diagnose_entity_delivery, autonomy="auto_safe"),
    ToolDef(
        name="explain_deploy_error",
        description="Parse a deploy validation error into missing target "
                    "columns and concrete repair suggestions. Read-only.",
        params_schema={"type": "object",
                       "properties": {"error": {"type": "string"},
                                      "entity": {"type": "string"},
                                      "proposal_id": {"type": "integer"}},
                       "required": ["error"]},
        handler=_explain_deploy_error, autonomy="auto_safe"),
    ToolDef(
        name="diagnose_duplicate_key",
        description="Diagnose a target duplicate-primary-key failure and "
                    "determine whether a missing crosswalk can be safely "
                    "recorded. Read-only.",
        params_schema={"type": "object",
                       "properties": {"entity": {"type": "string"},
                                      "error": {"type": "string"},
                                      "target_table": {"type": "string"},
                                      "target_column": {"type": "string"},
                                      "target_id": {"type": "string"},
                                      "target_system": {"type": "string"}},
                       "required": ["entity"]},
        handler=_diagnose_duplicate_key, autonomy="auto_safe"),
    ToolDef(
        name="plan_refresh_failure_repair",
        description="Inspect a refresh job and produce a read-only, per-entity "
                    "repair checklist. Includes gated tool suggestions only "
                    "when a repair is proven safe.",
        params_schema={"type": "object",
                       "properties": {"job_id": {"type": "string"},
                                      "target_system": {"type": "string"}},
                       "required": []},
        handler=_plan_refresh_failure_repair, autonomy="auto_safe"),
    ToolDef(
        name="repair_duplicate_key",
        description="Confirmed duplicate-key repair: records a missing "
                    "central crosswalk for an existing target row after the "
                    "diagnostic proves ownership is safe. Confirmation-gated.",
        params_schema={"type": "object",
                       "properties": {"entity": {"type": "string"},
                                      "error": {"type": "string"},
                                      "target_table": {"type": "string"},
                                      "target_column": {"type": "string"},
                                      "target_id": {"type": "string"},
                                      "target_system": {"type": "string"},
                                      "actor": {"type": "string"}},
                       "required": ["entity"]},
        handler=_repair_duplicate_key, autonomy="propose_only"),
    ToolDef(
        name="resolve_deploy_job_repair",
        description="Resolve a pasted failed deploy_lrmis job message into a "
                    "repair context: recovers the job's stored proposal id, "
                    "parses missing required target columns, and drafts "
                    "confirmation-gated *.id fan-out mappings. Read-only.",
        params_schema={"type": "object",
                       "properties": {"text": {"type": "string"},
                                      "target_system": {"type": "string"}},
                       "required": ["text"]},
        handler=_resolve_deploy_job_repair, autonomy="auto_safe"),
    ToolDef(
        name="add_mapping",
        description="Add a manual source-column to target table/column "
                    "mapping row on a proposal. Confirmation-gated.",
        params_schema={"type": "object",
                       "properties": {"proposal_id": {"type": "integer"},
                                      "source_column": {"type": "string"},
                                      "target_table": {"type": "string"},
                                      "target_column": {"type": "string"},
                                      "transform": {"type": "string"},
                                      "actor": {"type": "string"}},
                       "required": ["proposal_id", "source_column",
                                    "target_table", "target_column"]},
        handler=_add_mapping, autonomy="propose_only"),
    ToolDef(
        name="add_missing_mappings",
        description="Add a confirmation-gated batch of missing required "
                    "target mappings to a proposal, skipping target columns "
                    "that already have accepted/resolved mappings.",
        params_schema={"type": "object",
                       "properties": {"proposal_id": {"type": "integer"},
                                      "mappings": {"type": "array"},
                                      "actor": {"type": "string"}},
                       "required": ["proposal_id", "mappings"]},
        handler=_add_missing_mappings, autonomy="propose_only"),
    ToolDef(
        name="reject_mapping",
        description="Reject one source-column mapping on a proposal so deploy "
                    "ignores it. Confirmation-gated.",
        params_schema={"type": "object",
                       "properties": {"proposal_id": {"type": "integer"},
                                      "source_column": {"type": "string"},
                                      "actor": {"type": "string"}},
                       "required": ["proposal_id", "source_column"]},
        handler=_reject_mapping, autonomy="propose_only"),
    ToolDef(
        name="reject_mapping_review",
        description="Reject one exact field-review mapping row by review id. "
                    "Safer for fan-out mappings where a source column feeds "
                    "multiple targets. Confirmation-gated.",
        params_schema={"type": "object",
                       "properties": {"review_id": {"type": "integer"},
                                      "actor": {"type": "string"}},
                       "required": ["review_id"]},
        handler=_reject_mapping_review, autonomy="propose_only"),
    ToolDef(
        name="reopen_mapping_review",
        description="Reopen one exact field-review row in the normal review "
                    "queue by review id. Preserves the suspect mapping so an "
                    "operator can correct it in the UI. Confirmation-gated.",
        params_schema={"type": "object",
                       "properties": {"review_id": {"type": "integer"},
                                      "actor": {"type": "string"}} ,
                       "required": ["review_id"]},
        handler=_reopen_mapping_review, autonomy="propose_only"),
]

# MVP tools + the source-swap/recovery tools defined by
# source-schema-swap-and-disaster-recovery (its deferred task 6.3: register
# here once this registry exists) + the §8 later-phase tools.
REGISTRY: dict[str, ToolDef] = {
    t.name: t for t in [*MVP_TOOLS, *RECOVERY_TOOLS, *LATER_PHASE_TOOLS]}


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
