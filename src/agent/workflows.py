"""MVP workflow guidance state machines (conversational-ai-assistant §6, D7).

The LLM explains and classifies; these machines decide the allowed transition
graph. MVP ships onboarding and deploy guidance; drift resolution and
schema-swap flows are later phases — the conversation layer answers those
requests with a safe deferral pointing at the existing dashboard/CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..services.common import ValidationError

WORKFLOWS: dict[str, dict] = {
    "onboard": {
        "steps": ["discover", "propose", "review", "deploy", "backfill"],
        "entry_tool": "onboard_table",
        "descriptions": {
            "discover": "discover the source table's structure",
            "propose": "create the AI mapping proposal",
            "review": "review and accept/resolve the proposed field mappings",
            "deploy": "deploy the approved proposal (typed confirmation)",
            "backfill": "backfill existing source rows into the target",
        },
    },
    "deploy": {
        "steps": ["check_coverage", "resolve_dilemmas", "confirm", "deploy"],
        "entry_tool": "deploy_guidance",
        "descriptions": {
            "check_coverage": "check approval status and required-column coverage",
            "resolve_dilemmas": "resolve any low-confidence or unmapped columns",
            "confirm": "confirm the deploy action",
            "deploy": "run the deploy job",
        },
    },
}


@dataclass
class WorkflowState:
    workflow_name: str
    current_step: str
    completed_steps: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"workflow_name": self.workflow_name,
                "current_step": self.current_step,
                "completed_steps": list(self.completed_steps),
                "context": dict(self.context)}

    @staticmethod
    def from_dict(data: dict) -> "WorkflowState":
        return WorkflowState(
            workflow_name=data["workflow_name"],
            current_step=data["current_step"],
            completed_steps=list(data.get("completed_steps") or []),
            context=dict(data.get("context") or {}))


def start(workflow_name: str, context: dict | None = None) -> WorkflowState:
    spec = WORKFLOWS.get(workflow_name)
    if spec is None:
        raise ValidationError(
            f"unknown workflow {workflow_name!r}; supported: {sorted(WORKFLOWS)}")
    return WorkflowState(workflow_name=workflow_name,
                         current_step=spec["steps"][0],
                         context=context or {})


def advance(state: WorkflowState, completed_step: str) -> WorkflowState:
    """Mark `completed_step` done and move to the next step. Only the current
    step may be completed — the transition graph is enforced here, never by
    the LLM."""
    spec = WORKFLOWS[state.workflow_name]
    steps = spec["steps"]
    if completed_step != state.current_step:
        raise ValidationError(
            f"workflow {state.workflow_name!r}: cannot complete "
            f"{completed_step!r} — the current step is {state.current_step!r}")
    state.completed_steps.append(completed_step)
    index = steps.index(completed_step)
    state.current_step = steps[index + 1] if index + 1 < len(steps) else ""
    return state


def is_complete(state: WorkflowState) -> bool:
    return not state.current_step


def describe(state: WorkflowState) -> str:
    """Current step / done / remaining, in plain operator language."""
    spec = WORKFLOWS[state.workflow_name]
    if is_complete(state):
        return (f"The {state.workflow_name} workflow is complete "
                f"({' -> '.join(spec['steps'])}).")
    remaining = [s for s in spec["steps"]
                 if s not in state.completed_steps and s != state.current_step]
    parts = [f"{state.workflow_name} workflow — current step: "
             f"{state.current_step} ({spec['descriptions'][state.current_step]})."]
    if state.completed_steps:
        parts.append(f"Done: {', '.join(state.completed_steps)}.")
    if remaining:
        parts.append(f"Remaining: {', '.join(remaining)}.")
    return " ".join(parts)


def next_step_suggestion(state: WorkflowState) -> str:
    """One-line 'what to do next' the agent appends after a workflow step."""
    if is_complete(state):
        return f"The {state.workflow_name} workflow is complete."
    spec = WORKFLOWS[state.workflow_name]
    return (f"Next step: {state.current_step} — "
            f"{spec['descriptions'][state.current_step]}.")
