"""MVP tool registry tests (conversational-ai-assistant §2.9).

Every tool: schema validation, handler dispatch (services patched at the
module seam), error handling, and redaction-by-allowlist. Also pins the
registration of the source-swap/recovery tools — the deferred task 6.3 of
the archived source-schema-swap-and-disaster-recovery change.
"""
import pytest

import src.agent.tools as tools
from src.agent.tools import REGISTRY, get_tool, list_tools, validate_params
from src.services.common import NotFoundError, ValidationError

EXPECTED_TOOLS = {
    "check_status", "summarize_proposal", "explain_blocker", "show_schema",
    "deploy_guidance", "explain_dilemma", "onboard_table",
    # registered from tool_defs (archived change's 6.3)
    "swap_source_schema", "recover_from_backup",
}


def _review(status="needs_review", fields=None, unmet=None):
    return {
        "proposal": {"id": 7, "entity_id": 1, "source_schema": "irimsv",
                     "source_table": "schools", "target_system": "LRMIS",
                     "status": status, "source_fingerprint": "sf",
                     "target_fingerprint": "tf",
                     "unmet_required_columns": unmet or []},
        "fields": fields if fields is not None else [],
    }


def _field(col, table="school", target="name", confidence=0.9,
           status="accepted", **extra):
    return {"source_column": col, "suggested_target_table": table,
            "suggested_target_column": target, "resolved_target_column": None,
            "confidence": confidence, "status": status, "transform": "none",
            "reasoning": "because", **extra}


# --- registry shape -----------------------------------------------------------

def test_registry_contains_mvp_and_recovery_tools():
    assert set(REGISTRY) == EXPECTED_TOOLS


def test_recovery_tools_keep_their_gates_when_registered():
    assert REGISTRY["recover_from_backup"].autonomy == "destructive"
    assert REGISTRY["recover_from_backup"].destructive is True
    assert REGISTRY["swap_source_schema"].autonomy == "propose_only"


def test_read_tools_are_auto_safe_and_not_destructive():
    for name in ("check_status", "summarize_proposal", "explain_blocker",
                 "show_schema", "deploy_guidance", "explain_dilemma"):
        assert REGISTRY[name].autonomy == "auto_safe", name
        assert REGISTRY[name].destructive is False, name


def test_onboard_table_is_propose_only():
    assert REGISTRY["onboard_table"].autonomy == "propose_only"


def test_get_tool_unknown_raises():
    with pytest.raises(NotFoundError):
        get_tool("format_disk")


def test_list_tools_shape_for_classifier_prompt():
    listed = {t["name"]: t for t in list_tools()}
    assert set(listed) == EXPECTED_TOOLS
    assert listed["recover_from_backup"]["destructive"] is True
    assert all(t["description"] for t in listed.values())


# --- schema validation ----------------------------------------------------------

def test_proposal_tools_require_proposal_id():
    for name in ("summarize_proposal", "explain_blocker", "deploy_guidance"):
        with pytest.raises(ValidationError, match="proposal_id"):
            validate_params(REGISTRY[name], {})


def test_param_type_mismatch_rejected():
    with pytest.raises(ValidationError, match="must be integer"):
        validate_params(REGISTRY["summarize_proposal"], {"proposal_id": "7"})
    with pytest.raises(ValidationError, match="must be string"):
        validate_params(REGISTRY["onboard_table"], {"source_table": 5})


# --- dispatch through the service seams ----------------------------------------

def test_check_status_wraps_ops_get_status(monkeypatch):
    monkeypatch.setattr(tools.ops_service, "get_status",
                        lambda: {"outbox": {"pending": 3}})
    assert REGISTRY["check_status"].handler({}) == {"outbox": {"pending": 3}}


def test_show_schema_passes_source_schema_through(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.ops_service, "get_schema_trees",
                        lambda **kw: calls.append(kw) or {"source": {}, "target": {}})
    REGISTRY["show_schema"].handler({})
    REGISTRY["show_schema"].handler({"source_schema": "other"})
    assert calls == [{}, {"source_schema": "other"}]


def test_summarize_proposal_flags_low_confidence_and_redacts(monkeypatch):
    fields = [
        _field("id", confidence=0.95),
        _field("name", confidence=0.4, sample_values=["SECRET ROW VALUE"]),
        _field("orphan", target=None, confidence=0.9),
    ]
    monkeypatch.setattr(tools.onboarding_service, "get_review",
                        lambda pid: _review(fields=fields))
    result = REGISTRY["summarize_proposal"].handler({"proposal_id": 7})
    assert result["field_count"] == 3
    assert result["risk"] == "high"
    low_cols = {f["source_column"] for f in result["low_confidence"]}
    assert low_cols == {"name", "orphan"}
    # redaction by allowlist: no reasoning, no sample values, nothing extra
    for f in result["low_confidence"]:
        assert set(f) == set(tools._FIELD_KEYS)
    assert "SECRET ROW VALUE" not in str(result)


def test_summarize_low_risk_when_all_confident(monkeypatch):
    monkeypatch.setattr(tools.onboarding_service, "get_review",
                        lambda pid: _review(status="approved",
                                            fields=[_field("id")]))
    result = REGISTRY["summarize_proposal"].handler({"proposal_id": 7})
    assert result["risk"] == "low" and result["low_confidence"] == []


def test_explain_blocker_reports_status_and_unmet(monkeypatch):
    monkeypatch.setattr(
        tools.onboarding_service, "get_review",
        lambda pid: _review(status="needs_review", fields=[_field("id")],
                            unmet=["station_id"]))
    result = REGISTRY["explain_blocker"].handler({"proposal_id": 7})
    assert result["deploy_ready"] is False
    assert any("must be approved" in b for b in result["blockers"])
    assert any("station_id" in b for b in result["blockers"])


def test_explain_blocker_ready_when_approved_covered(monkeypatch):
    monkeypatch.setattr(tools.onboarding_service, "get_review",
                        lambda pid: _review(status="approved",
                                            fields=[_field("id")]))
    result = REGISTRY["explain_blocker"].handler({"proposal_id": 7})
    assert result["deploy_ready"] is True and result["blockers"] == []


def test_deploy_guidance_recommends_without_executing(monkeypatch):
    monkeypatch.setattr(
        tools.onboarding_service, "get_review",
        lambda pid: _review(status="needs_review", fields=[_field("id")]))
    result = REGISTRY["deploy_guidance"].handler({"proposal_id": 7})
    assert result["executed"] is False
    assert any("approve the proposal" in a for a in result["recommended_actions"])

    monkeypatch.setattr(tools.onboarding_service, "get_review",
                        lambda pid: _review(status="approved",
                                            fields=[_field("id")]))
    ready = REGISTRY["deploy_guidance"].handler({"proposal_id": 7})
    assert ready["deploy_ready"] is True
    assert any(a.startswith("deploy:") for a in ready["recommended_actions"])
    assert ready["executed"] is False


def test_explain_dilemma_uses_real_agent_guidance():
    result = REGISTRY["explain_dilemma"].handler({
        "kind": "unmapped_column", "table": "school", "column": "schoolName",
        "context": {"candidates": ["school_name", "station_id"]}})
    assert result["recommended"] == "auto_suggest"
    assert any(o["action"] == "auto_suggest" and o["value"] == "school_name"
               for o in result["options"])


def test_onboard_table_proposes_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools.onboarding_service, "propose",
        lambda schema, table, system: calls.append((schema, table, system))
        or {"proposal_id": 99, "status": "needs_review"})
    result = REGISTRY["onboard_table"].handler(
        {"source_table": "authors", "source_schema": "irimsv"})
    assert calls == [("irimsv", "authors", "LRMIS")]
    assert result["proposal"]["proposal_id"] == 99
    assert "nothing was" in result["note"]


def test_handler_errors_propagate(monkeypatch):
    def boom(pid):
        raise NotFoundError("proposal 404 not found")
    monkeypatch.setattr(tools.onboarding_service, "get_review", boom)
    with pytest.raises(NotFoundError):
        REGISTRY["summarize_proposal"].handler({"proposal_id": 404})
