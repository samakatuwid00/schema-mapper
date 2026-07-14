"""Workflow state-machine tests (conversational-ai-assistant §6.5)."""
import pytest

from src.agent.workflows import (WorkflowState, advance, describe, is_complete,
                                 next_step_suggestion, start)
from src.services.common import ValidationError


def test_start_begins_at_first_step():
    state = start("onboard")
    assert state.current_step == "discover" and state.completed_steps == []


def test_unknown_workflow_rejected():
    with pytest.raises(ValidationError, match="unknown workflow"):
        start("world_domination")


def test_valid_transitions_walk_the_graph_to_completion():
    state = start("onboard")
    for step in ("discover", "propose", "review", "deploy", "backfill"):
        assert not is_complete(state)
        state = advance(state, step)
    assert is_complete(state)
    assert state.completed_steps == ["discover", "propose", "review",
                                     "deploy", "backfill"]
    assert "complete" in describe(state)


def test_invalid_move_rejected_and_state_unchanged():
    state = start("deploy")
    with pytest.raises(ValidationError, match="current step is 'check_coverage'"):
        advance(state, "deploy")
    assert state.current_step == "check_coverage"
    assert state.completed_steps == []


def test_describe_lists_current_done_remaining():
    state = advance(start("onboard"), "discover")
    text = describe(state)
    assert "current step: propose" in text
    assert "Done: discover" in text
    assert "review" in text and "backfill" in text


def test_next_step_suggestion():
    state = start("deploy")
    assert next_step_suggestion(state).startswith("Next step: check_coverage")
    for step in ("check_coverage", "resolve_dilemmas", "confirm", "deploy"):
        state = advance(state, step)
    assert "complete" in next_step_suggestion(state)


def test_state_round_trips_through_dict():
    state = advance(start("onboard"), "discover")
    state.context["entity"] = "schools"
    clone = WorkflowState.from_dict(state.to_dict())
    assert clone == state
