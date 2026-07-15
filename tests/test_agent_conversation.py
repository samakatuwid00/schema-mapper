"""Conversation loop tests (conversational-ai-assistant §3.8, §7.6, §0.3).

Covers: heuristic + LLM classification with failover, param extraction,
template fallback, dispatcher autonomy gating per tier, context capping,
persistence behavior, and the privacy fixture proving row values never reach
prompts or persisted messages. No live DB and no live LLM.
"""
import json

import pytest

import src.agent.conversation as conv
from src.agent.conversation import (
    AgentSession, ConversationManager, Intent, StreamEvent, ToolDispatcher,
    _build_classify_prompt, _cap_messages, classify, heuristic_classify,
    redact_row_values, render_result,
)
from src.agent.tool_defs import ToolDef
from src.services.common import NotFoundError, ValidationError


# --- heuristic classification (§3.2/3.4) --------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("what's the status of the queue?", "check_status"),
    ("summarize proposal 42 for me", "summarize_proposal"),
    ("why is schools blocked?", "explain_blocker"),
    ("show me the schema", "show_schema"),
    ("are we ready to deploy?", "deploy_guidance"),
    ("I have an unmapped column dilemma", "explain_dilemma"),
    ("onboard authors", "onboard_table"),
])
def test_heuristic_classifies_mvp_intents(message, expected):
    intent = heuristic_classify(message, {})
    assert intent.name == expected
    assert intent.confidence >= conv.CONFIDENCE_FLOOR


def test_heuristic_defers_only_recovery_requests():
    """§8.2/8.3: swap and drift now route to tools; recovery stays deferred."""
    assert heuristic_classify("restore a backup please", {}).name == "deferred"
    assert heuristic_classify("recover from backup", {}).name == "deferred"
    assert heuristic_classify("I need a schema swap", {}).name == \
        "swap_target_dry_run"


@pytest.mark.parametrize("message,expected", [
    ("swap the source please", "swap_source_schema"),
    ("the target schema changed", "swap_target_dry_run"),
    ("any drift lately?", "list_drift_reports"),
    ("resolve the drift now", "resolve_drift"),
    ("heal this delivery error", "heal_error"),
    ("refresh_all succeeded 2/3", "inspect_job"),
    ("author target db is paused status", "diagnose_entity_delivery"),
    ("Deploy to target failed - mapping cannot be deployed", "explain_deploy_error"),
    ("diagnose_entity_delivery divisions", "diagnose_entity_delivery"),
    ("how do I fix this refresh job 447f3eb8-6ff6-4dde-ab38-b8f56cdc2fb5", "plan_refresh_failure_repair"),
    ("division_libraries duplicate key value violates unique constraint", "diagnose_duplicate_key"),
    ("fix duplicate key for division_libraries", "repair_duplicate_key"),
    ("add mapping id to station_name.id", "add_mapping"),
    ("reject mapping review 99", "reject_mapping_review"),
    ("reopen mapping review 1217", "reopen_mapping_review"),
    ("reject mapping legislative_district", "reject_mapping"),
])
def test_heuristic_classifies_later_phase_intents(message, expected):
    assert heuristic_classify(message, {}).name == expected


def test_heal_error_param_is_the_message():
    intent = heuristic_classify("fix this error: invalid int cast", {})
    assert intent.name == "heal_error"
    assert intent.params["error"] == "fix this error: invalid int cast"


def test_heuristic_unknown_message_below_floor():
    intent = heuristic_classify("bake me a cake", {})
    assert intent.name is None and intent.confidence < conv.CONFIDENCE_FLOOR


def test_param_extraction_from_message_and_page_context():
    assert heuristic_classify("what's blocking proposal 42?", {}).params == \
        {"proposal_id": 42}
    assert heuristic_classify("why is this blocked?",
                              {"proposal_id": 7}).params == {"proposal_id": 7}
    assert heuristic_classify("onboard authors", {}).params == \
        {"source_table": "authors"}
    assert heuristic_classify("explain this type mismatch", {}).params == \
        {"kind": "type_mismatch"}
    assert heuristic_classify(
        "job #84ad54c8-399b-4a6d-8808-c74259ef6a5b refresh_all",
        {},
    ).params == {"job_id": "84ad54c8-399b-4a6d-8808-c74259ef6a5b"}
    assert heuristic_classify("author target db is paused status", {}).params == \
        {"entity": "author"}
    assert heuristic_classify("diagnose_entity_delivery divisions", {}).params == \
        {"entity": "divisions"}
    assert heuristic_classify(
        "Deploy to target failed - mapping cannot be deployed: "
        "- station_name.id is required but no source column maps to it",
        {"proposal_id": 581},
    ).params["proposal_id"] == 581
    assert heuristic_classify(
        "how do I fix this refresh job 447f3eb8-6ff6-4dde-ab38-b8f56cdc2fb5",
        {},
    ).params == {"job_id": "447f3eb8-6ff6-4dde-ab38-b8f56cdc2fb5"}
    assert heuristic_classify(
        "plan_refresh_failure_repair for job 8fb90391-339f-4ae3-b2d8-162e2b1c4585",
        {},
    ).params == {"job_id": "8fb90391-339f-4ae3-b2d8-162e2b1c4585"}
    duplicate_params = heuristic_classify(
        'fix duplicate key for division_libraries: duplicate key value violates '
        'unique constraint "profile_pkey" DETAIL: Key (id)=(abc) already exists.',
        {},
    ).params
    assert duplicate_params["entity"] == "division_libraries"
    assert duplicate_params["target_table"] == "profile"
    assert duplicate_params["target_column"] == "id"
    assert duplicate_params["target_id"] == "abc"
    assert heuristic_classify(
        "add mapping id to station_name.id proposal 581", {}
    ).params == {
        "proposal_id": 581, "source_column": "id",
        "target_table": "station_name", "target_column": "id"}
    assert heuristic_classify(
        "reject mapping legislative_district proposal 581", {}
    ).params == {"proposal_id": 581,
                 "source_column": "legislative_district"}
    assert heuristic_classify("reject mapping review 99", {}).params == \
        {"review_id": 99}
    assert heuristic_classify("send review 1217 back to review queue", {}).params == \
        {"review_id": 1217}


# --- LLM classification + failover (§3.3) --------------------------------------

class _FakeGeminiClient:
    def __init__(self, payload=None, error=None):
        self._payload, self._error = payload, error
        self.prompts = []

        outer = self

        class _Models:
            def generate_content(inner, **kwargs):
                outer.prompts.append(kwargs["contents"])
                if outer._error:
                    raise outer._error

                class _Response:
                    text = json.dumps(outer._payload)
                return _Response()

        self.models = _Models()


def test_classify_uses_gemini_when_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,heuristic")
    client = _FakeGeminiClient({"intent": "check_status", "params": {},
                                "confidence": 0.9})
    intent = classify("how are things?", {}, client=client)
    assert intent == Intent("check_status", {}, 0.9, "gemini")


def test_classify_falls_back_to_heuristic_on_provider_error(monkeypatch):
    """Quota exhaustion / timeout degrades gracefully (spec scenario)."""
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,heuristic")
    client = _FakeGeminiClient(error=RuntimeError("429 quota exceeded"))
    intent = classify("what's the queue status?", {}, client=client)
    assert intent.name == "check_status" and intent.source == "heuristic"


def test_classify_heuristic_only_never_calls_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "heuristic")
    client = _FakeGeminiClient({"intent": "check_status", "confidence": 1.0})
    classify("status?", {}, client=client)
    assert client.prompts == []


def test_classify_rejects_unregistered_intent_from_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,heuristic")
    client = _FakeGeminiClient({"intent": "rm_rf_slash", "confidence": 0.99})
    intent = classify("do something weird", {}, client=client)
    assert intent.name is None and intent.confidence == 0.0


def test_prompt_budget_enforced():
    huge = {"note": "x" * (conv.MAX_PROMPT_CHARS * 2)}
    prompt = _build_classify_prompt("hello", huge)
    assert len(prompt) <= conv.MAX_PROMPT_CHARS


# --- templates (§3.4) -----------------------------------------------------------

def test_templates_render_every_mvp_intent():
    assert "pending: 3" in render_result("check_status",
                                         {"outbox": {"pending": 3}})
    assert render_result("summarize_proposal", {"summary": "5 mapped."}) == "5 mapped."
    assert "review 1208: station_id -> station_address.station_id" in render_result(
        "summarize_proposal",
        {"summary": "5 mapped.",
         "accepted_mappings": [{
             "review_id": 1208,
             "source_column": "station_id",
             "suggested_target_table": "station_address",
             "suggested_target_column": "station_id",
         }]})
    assert render_result("explain_blocker",
                         {"deploy_ready": True}).startswith("Nothing is blocking")
    assert "Blocked:" in render_result("explain_blocker",
                                       {"deploy_ready": False,
                                        "blockers": ["not approved"]})
    schema_text = render_result("show_schema", {
        "source": {"tables": [{"name": "authors"}]},
        "target": {"tables": [{"name": "author"}]}})
    assert "1 tables" in schema_text and "authors" in schema_text
    assert "ready to deploy" in render_result("deploy_guidance",
                                              {"deploy_ready": True,
                                               "recommended_actions": ["deploy: go"]})
    assert "Recommended: cast" in render_result(
        "explain_dilemma", {"kind": "type_mismatch",
                            "options": [{"action": "cast", "label": "cast it"}],
                            "recommended": "cast"})
    assert "proposal 99" in render_result("onboard_table",
                                          {"proposal": {"proposal_id": 99},
                                           "note": "review it."})


def test_templates_survive_garbage_results():
    for name in conv.TEMPLATES:
        assert isinstance(render_result(name, {}), str)
    assert render_result("unknown_tool", {}) == "unknown_tool finished."


# --- dispatcher autonomy gate (§7.1-7.5, tested per §7.6) -----------------------

def _spy_registry():
    calls = []

    def make(name):
        def handler(params, **seams):
            calls.append((name, params))
            return {"ok": name, "rows": [{"secret": "ROWVALUE"}]}
        return handler

    registry = {
        "safe_tool": ToolDef("safe_tool", "read", {"type": "object",
                                                   "properties": {},
                                                   "required": []},
                             make("safe_tool"), autonomy="auto_safe"),
        "mut_tool": ToolDef("mut_tool", "mutate", {"type": "object",
                                                   "properties": {"n": {"type": "integer"}},
                                                   "required": ["n"]},
                            make("mut_tool"), autonomy="propose_only"),
        "destr_tool": ToolDef("destr_tool", "boom", {"type": "object",
                                                     "properties": {},
                                                     "required": []},
                              make("destr_tool"), autonomy="destructive"),
    }
    return registry, calls


def test_propose_only_tier_executes_reads_and_defers_mutations():
    registry, calls = _spy_registry()
    dispatcher = ToolDispatcher(registry)
    read = dispatcher.dispatch("safe_tool", {}, tier="propose_only", confidence=0.9)
    assert read.executed is True
    mutate = dispatcher.dispatch("mut_tool", {"n": 1}, tier="propose_only",
                                 confidence=0.99)
    assert mutate.executed is False and mutate.requires_confirmation is True
    assert calls == [("safe_tool", {})]


def test_auto_safe_tier_still_defers_non_allowlisted_and_destructive():
    registry, calls = _spy_registry()
    dispatcher = ToolDispatcher(registry)
    assert dispatcher.dispatch("mut_tool", {"n": 1}, tier="auto_safe",
                               confidence=0.99).requires_confirmation is True
    assert dispatcher.dispatch("destr_tool", {}, tier="auto_safe",
                               confidence=1.0).requires_confirmation is True
    assert calls == []


def test_auto_safe_defers_low_confidence_reads():
    registry, calls = _spy_registry()
    dispatcher = ToolDispatcher(registry)
    outcome = dispatcher.dispatch("safe_tool", {}, tier="auto_safe",
                                  confidence=conv.AUTO_SAFE_CONFIDENCE - 0.1)
    assert outcome.requires_confirmation is True and calls == []


def test_confirmed_call_executes_mutating_and_destructive():
    registry, calls = _spy_registry()
    audits = []
    dispatcher = ToolDispatcher(registry,
                                audit=lambda tool, details: audits.append((tool, details)))
    first = dispatcher.dispatch("mut_tool", {"n": 1}, tier="propose_only",
                                confirmed=True)
    second = dispatcher.dispatch("destr_tool", {}, tier="auto_safe",
                                 confirmed=True)
    assert first.executed and second.executed
    assert [c[0] for c in calls] == ["mut_tool", "destr_tool"]
    # §7.5 audit records tier + auto_executed=False for confirmed calls
    assert audits[0][1]["autonomy"] == "propose_only"
    assert audits[0][1]["auto_executed"] is False
    # results are redacted before anyone sees them
    assert "rows" not in first.result and first.result == {"ok": "mut_tool"}


def test_auto_executed_flag_true_for_unconfirmed_reads():
    registry, _ = _spy_registry()
    audits = []
    dispatcher = ToolDispatcher(registry,
                                audit=lambda tool, details: audits.append(details))
    dispatcher.dispatch("safe_tool", {}, tier="auto_safe", confidence=0.9)
    assert audits[0]["auto_executed"] is True and audits[0]["autonomy"] == "auto_safe"


def test_invalid_params_error_without_calling_handler():
    registry, calls = _spy_registry()
    outcome = ToolDispatcher(registry).dispatch("mut_tool", {}, tier="propose_only",
                                                confirmed=True)
    assert outcome.executed is False and "missing required param" in outcome.error
    assert calls == []


def test_auto_all_tier_rejected():
    registry, _ = _spy_registry()
    with pytest.raises(ValidationError, match="unsupported autonomy tier"):
        ToolDispatcher(registry).dispatch("safe_tool", {}, tier="auto_all")


# --- context cap (§3.6) ---------------------------------------------------------

def test_cap_messages_summarizes_older_schema_only():
    messages = [{"role": "user", "content": f"m{i}",
                 "tool_calls": [{"tool": "check_status"}]}
                for i in range(conv.MAX_MESSAGES + 10)]
    capped = _cap_messages(messages)
    assert len(capped) == conv.KEEP_RECENT_MESSAGES + 1
    summary = capped[0]
    assert summary["role"] == "system"
    assert "check_status" in summary["content"]
    assert "m0" not in summary["content"]        # content never copied


def test_redact_row_values_recursive():
    dirty = {"entity": "schools", "rows": [{"n": 1}],
             "nested": {"sample_values": ["x"], "keep": 1},
             "list": [{"payload": {"a": 1}, "ok": True}]}
    assert redact_row_values(dirty) == {
        "entity": "schools", "nested": {"keep": 1}, "list": [{"ok": True}]}


# --- session behavior over an in-memory manager (§3.7) --------------------------

class _MemManager:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._next = 0

    def create(self, user_id, autonomy_tier="propose_only"):
        if autonomy_tier not in conv.AUTONOMY_TIERS:
            raise ValidationError("unsupported autonomy tier")
        self._next += 1
        row = {"id": f"c{self._next}", "user_id": user_id, "title": "",
               "autonomy_tier": autonomy_tier, "messages": []}
        self.rows[row["id"]] = row
        return dict(row)

    def load(self, conversation_id, user_id):
        row = self.rows.get(str(conversation_id))
        if row is None or row["user_id"] != user_id:
            raise NotFoundError("conversation not found")
        return dict(row)

    def set_tier(self, conversation_id, user_id, tier):
        self.load(conversation_id, user_id)
        self.rows[str(conversation_id)]["autonomy_tier"] = tier

    def append_messages(self, conversation_id, user_id, new_messages,
                        title_seed=None):
        row = self.rows[str(conversation_id)]
        row["messages"] = _cap_messages(row["messages"] + list(new_messages))
        if not row["title"] and title_seed:
            row["title"] = title_seed.strip()[:conv.TITLE_MAX].capitalize()
        return row["messages"]


def _session(intent, manager=None, registry=None):
    registry = registry if registry is not None else _spy_registry()[0]
    return AgentSession(
        1, manager=manager or _MemManager(),
        dispatcher=ToolDispatcher(registry),
        classifier=lambda message, page_context: intent)


def _events_by_type(events):
    out = {}
    for e in events:
        out.setdefault(e.event, []).append(e)
    return out


def test_session_creates_conversation_and_streams_clarification():
    manager = _MemManager()
    session = _session(Intent(None, {}, 0.0, "heuristic"), manager)
    events = session.process_message("gibberish")
    by = _events_by_type(events)
    assert by["conversation"][0].data["conversation_id"] == "c1"
    assert conv.CLARIFICATION_TEXT.startswith(
        "".join(t.data["text"] for t in by["token"])[:20])
    assert by["done"][0].data["content"] == conv.CLARIFICATION_TEXT
    stored = manager.rows["c1"]["messages"]
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert manager.rows["c1"]["title"].lower().startswith("gibberish")


def test_session_help_lists_chat_commands():
    manager = _MemManager()
    session = _session(Intent(None, {}, 0.0, "heuristic"), manager)
    events = session.process_message("--help")
    by = _events_by_type(events)
    assert "Chat commands:" in by["done"][0].data["content"]
    assert "inspect job <job_id>" in by["done"][0].data["content"]
    assert "reopen mapping review <review_id>" in by["done"][0].data["content"]
    assert "tool_call" not in by


def _resolve_job_repair_registry(result):
    def handler(params, **seams):
        assert params == {"text": params["text"]}
        return result
    return {
        "resolve_deploy_job_repair": ToolDef(
            "resolve_deploy_job_repair", "resolve", {"type": "object",
                                                     "properties": {"text": {"type": "string"}},
                                                     "required": ["text"]},
            handler, autonomy="auto_safe"),
    }


def test_pasted_deploy_job_message_bypasses_classifier():
    manager = _MemManager()
    registry = _resolve_job_repair_registry({
        "job_id": "ff2c6da7-30e7-4711-b159-282e96c23704",
        "proposal_id": 582, "proposal_recovered": True,
        "missing_required": [{"target_table": "station_name", "target_column": "id"}],
        "draft_mappings": [{"source_column": "id", "target_table": "station_name",
                            "target_column": "id"}],
        "manual_required": [],
        "actions": [{"type": "open_proposal", "proposal_id": 582}],
        "summary": "1 required target column(s) are unmapped.",
    })

    def unreachable_classifier(message, page_context):
        raise AssertionError("classifier must not run for a pasted deploy job")

    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=unreachable_classifier)
    events = session.process_message(
        "#ff2c6da7-30e7-4711-b159-282e96c23704 deploy_lrmis failed\n"
        "mapping cannot be deployed:\n"
        "- station_name.id is required but no source column maps to it")
    by = _events_by_type(events)

    assert by["tool_call"][0].data["tool"] == "resolve_deploy_job_repair"
    assert by["tool_call"][0].data["executed"] is True
    content = by["done"][0].data["content"]
    assert "Proposal 582 recovered" in content
    assert "station_name.id" in content
    assert "Open the proposal: /mappings/582" in content


def test_pasted_deploy_job_without_resolvable_proposal_asks_for_one():
    manager = _MemManager()
    registry = _resolve_job_repair_registry({
        "job_id": "84ad54c8-399b-4a6d-8808-c74259ef6a5b",
        "proposal_id": None, "proposal_recovered": False,
        "missing_required": [{"target_table": "station_name", "target_column": "id"}],
        "draft_mappings": [], "manual_required": [],
        "actions": [], "summary": "1 required target column(s) are unmapped.",
    })
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=lambda m, c: Intent(None, {}, 0.0, "heuristic"))
    events = session.process_message(
        "#84ad54c8-399b-4a6d-8808-c74259ef6a5b deploy_lrmis failed\n"
        "mapping cannot be deployed:\n"
        "- station_name.id is required but no source column maps to it")
    content = _events_by_type(events)["done"][0].data["content"]

    assert "reply with a proposal id" in content
    assert "Open the proposal:" not in content


def test_pending_confirmation_still_wins_over_pasted_deploy_job_text():
    call = {"tool": "add_missing_mappings",
           "params": {"proposal_id": 582, "mappings": []}}
    registry = {
        **_resolve_job_repair_registry({}),
        "add_missing_mappings": ToolDef(
            "add_missing_mappings", "add", {"type": "object",
                                            "properties": {"proposal_id": {"type": "integer"},
                                                           "mappings": {"type": "array"}},
                                            "required": ["proposal_id", "mappings"]},
            lambda params, **seams: {"proposal_id": 582, "added": [], "skipped": []},
            autonomy="propose_only"),
    }
    manager = _MemManager()
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=lambda m, c: Intent(None, {}, 0.0, "heuristic"))
    events = session.process_message(
        "#ff2c6da7-30e7-4711-b159-282e96c23704 deploy_lrmis failed, please confirm",
        confirm=call)
    tool_call = _events_by_type(events)["tool_call"][0]
    assert tool_call.data["tool"] == "add_missing_mappings"


def test_session_deferred_intent_points_to_dashboard():
    session = _session(Intent("deferred", {}, 0.9, "heuristic"))
    events = session.process_message("swap the target schema")
    assert any("not available" in e.data.get("content", "")
               or "not available" in e.data.get("content", "")
               for e in events if e.event == "done")


def test_session_executes_safe_tool_and_attaches_result():
    manager = _MemManager()
    session = _session(Intent("safe_tool", {}, 0.9, "heuristic"), manager)
    events = session.process_message("run the safe thing")
    by = _events_by_type(events)
    call = by["tool_call"][0].data
    assert call["tool"] == "safe_tool" and call["params"] == {}
    assert call["requires_confirmation"] is False and call["executed"] is True
    assert by["tool_result"][0].data["result"] == {"ok": "safe_tool"}
    assistant = manager.rows["c1"]["messages"][-1]
    assert assistant["tool_results"] == [{"ok": "safe_tool"}]


def test_session_confirmation_roundtrip_for_mutating_tool():
    manager = _MemManager()
    intent = Intent("mut_tool", {"n": 1}, 0.9, "heuristic")
    registry, calls = _spy_registry()
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=lambda m, c: intent)
    first = session.process_message("mutate something")
    call_event = _events_by_type(first)["tool_call"][0]
    assert call_event.data["requires_confirmation"] is True
    assert calls == []

    second = session.process_message(
        "yes do it", conversation_id="c1",
        confirm={"tool": "mut_tool", "params": {"n": 1}})
    assert calls == [("mut_tool", {"n": 1})]
    assert _events_by_type(second)["tool_result"][0].data["result"] == {"ok": "mut_tool"}


def test_session_text_confirmation_reuses_pending_tool_params():
    manager = _MemManager()
    intent = Intent("mut_tool", {"n": 1}, 0.9, "heuristic")
    registry, calls = _spy_registry()
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=lambda m, c: intent)
    session.process_message("mutate something")

    second = session.process_message("done", conversation_id="c1")

    assert calls == [("mut_tool", {"n": 1})]
    assert _events_by_type(second)["tool_result"][0].data["result"] == {"ok": "mut_tool"}


def test_mapping_repair_request_is_confirmation_gated():
    calls = []
    registry = {
        "add_mapping": ToolDef(
            "add_mapping", "repair",
            {"type": "object",
             "properties": {"proposal_id": {"type": "integer"},
                            "source_column": {"type": "string"},
                            "target_table": {"type": "string"},
                            "target_column": {"type": "string"}},
             "required": ["proposal_id", "source_column",
                          "target_table", "target_column"]},
            lambda params, **s: calls.append(params) or {"ok": True},
            autonomy="propose_only"),
    }
    session = AgentSession(
        1, manager=_MemManager(), dispatcher=ToolDispatcher(registry),
        classifier=lambda m, c: Intent(
            "add_mapping",
            {"proposal_id": 581, "source_column": "id",
             "target_table": "station_name", "target_column": "id"},
            0.9, "h"))

    events = session.process_message("add mapping id to station_name.id")

    call = _events_by_type(events)["tool_call"][0].data
    assert call["requires_confirmation"] is True
    assert calls == []


def test_session_resumes_and_switches_tier():
    manager = _MemManager()
    session = _session(Intent(None, {}, 0.0, "h"), manager)
    session.process_message("hello")
    session.process_message("again", conversation_id="c1",
                            autonomy_tier="auto_safe")
    assert manager.rows["c1"]["autonomy_tier"] == "auto_safe"
    assert len(manager.rows["c1"]["messages"]) == 4


def test_session_rejects_foreign_conversation():
    manager = _MemManager()
    manager.create(2)                      # belongs to user 2
    session = _session(Intent(None, {}, 0.0, "h"), manager)
    with pytest.raises(NotFoundError):
        session.process_message("hi", conversation_id="c1")


# --- §6.4 workflow wiring --------------------------------------------------------

def test_onboard_execution_suggests_next_workflow_step():
    registry, _ = _spy_registry()
    registry["onboard_table"] = ToolDef(
        "onboard_table", "onboard", {"type": "object", "properties": {},
                                     "required": []},
        lambda params, **s: {"proposal": {"proposal_id": 9}, "note": "review."},
        autonomy="auto_safe")   # allowlisted here to exercise the executed path
    manager = _MemManager()
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=lambda m, c: Intent("onboard_table", {},
                                                          0.9, "h"))
    events = session.process_message("onboard authors")
    done = [e for e in events if e.event == "done"][0]
    assert "Next step: review" in done.data["content"]
    assert manager.rows["c1"]["messages"][-1]["workflow_state"][
        "current_step"] == "review"


def test_workflow_status_question_reads_persisted_state():
    manager = _MemManager()
    manager.create(1)
    manager.rows["c1"]["messages"] = [
        {"role": "assistant", "content": "…",
         "workflow_state": {"workflow_name": "onboard",
                            "current_step": "review",
                            "completed_steps": ["discover", "propose"],
                            "context": {}}}]
    session = _session(Intent("workflow_status", {}, 0.9, "h"), manager)
    events = session.process_message("where are we?", conversation_id="c1")
    done = [e for e in events if e.event == "done"][0]
    assert "current step: review" in done.data["content"]
    assert "Done: discover, propose" in done.data["content"]


def test_workflow_status_without_active_workflow():
    session = _session(Intent("workflow_status", {}, 0.9, "h"))
    events = session.process_message("where are we?")
    done = [e for e in events if e.event == "done"][0]
    assert "No workflow is in progress" in done.data["content"]


def test_heuristic_classifies_workflow_status():
    assert heuristic_classify("where are we in the onboarding?", {}).name == \
        "workflow_status"
    assert heuristic_classify("--help", {}).name == "chat_help"


def test_swap_dry_run_execution_suggests_next_swap_step():
    registry, _ = _spy_registry()
    registry["swap_target_dry_run"] = ToolDef(
        "swap_target_dry_run", "preview", {"type": "object", "properties": {},
                                           "required": []},
        lambda params, **s: {"would_remap": ["schools"]}, autonomy="auto_safe")
    manager = _MemManager()
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=lambda m, c: Intent("swap_target_dry_run",
                                                          {}, 0.9, "h"))
    events = session.process_message("schema swap preview")
    done = [e for e in events if e.event == "done"][0]
    assert "Next step: remap" in done.data["content"]


def test_later_phase_templates_render():
    assert "No drift reports" in render_result("list_drift_reports", {"count": 0})
    assert "resolve drift" in render_result(
        "list_drift_reports",
        {"count": 2, "reports": [{"target_system": "LRMIS",
                                  "impacted_entities": ["schools"]}]})
    assert "Proposed heal: cast" in render_result(
        "heal_error", {"action": "cast", "detail": {"transform": "str->int"},
                       "note": "heal proposal only"})
    assert "no deployed entities are affected" in render_result(
        "swap_target_dry_run", {"would_remap": []})
    assert "blocked on low-confidence" in render_result(
        "swap_target_apply", {"status": "blocked_on_review",
                              "blocked": ["schools"]})
    assert "dry-run complete" in render_result("resolve_drift",
                                               {"dry_run": True})
    assert "refresh_all" in render_result(
        "inspect_job",
        {"job_id": "j1", "job_type": "refresh_all", "status": "succeeded",
         "progress_current": 2, "progress_total": 3,
         "failures": [{"entity": "authors", "error": "boom"}]})
    inspect_text = render_result(
        "inspect_job",
        {"job_id": "j1", "job_type": "refresh_all", "status": "succeeded",
         "failures": [{"entity": "divisions", "error": "bad reference"}],
         "repair_plan": {
             "items": [{
                 "entity": "divisions",
                 "category": "reference_match_missing",
                 "diagnostic": {
                     "suspect_mapping_reviews": [{
                         "review_id": 1217,
                         "proposal_id": 582,
                         "source_column": "address",
                         "target_table": "station_address",
                         "target_column": "id",
                     }],
                 },
                 "gated_tools": [{
                     "tool": "reopen_mapping_review",
                     "params": {"review_id": 1217},
                 }],
             }],
         }})
    assert "proposal 582 review 1217" in inspect_text
    assert "`reopen mapping review 1217`" in inspect_text
    assert "authors is deployed" in render_result(
        "diagnose_entity_delivery",
        {"entity": "authors", "entity_status": "deployed",
         "target_counts": [{"table": "author", "rows": 0}],
         "recommendations": ["run refresh"]})
    assert "station_name.id" in render_result(
        "explain_deploy_error",
        {"missing_required": [{"target_table": "station_name",
                               "target_column": "id"}],
         "suggested_actions": [{"action": "add_mapping",
                                "source_column": "id",
                                "target_table": "station_name",
                                "target_column": "id"}]})
    assert "safe to prepare" in render_result(
        "diagnose_duplicate_key",
        {"entity": "division_libraries", "target_table": "profile",
         "target_column": "id", "target_id": "abc",
         "safe_to_repair": True})
    assert "division_libraries" in render_result(
        "plan_refresh_failure_repair",
        {"summary": "2 failed entity repair item(s)",
         "items": [{"entity": "division_libraries",
                    "category": "duplicate_key",
                    "diagnosis": "not safe",
                    "gated_tools": [{"tool": "reject_mapping_review",
                                     "params": {"review_id": 99}}],
                    "next_steps": ["review mapping"]}]})
    assert "Recorded duplicate-key crosswalk repair" in render_result(
        "repair_duplicate_key",
        {"entity": "division_libraries", "target_table": "profile",
         "target_column": "id", "target_id": "abc",
         "next_step": "retry refresh for division_libraries"})
    assert "Added mapping" in render_result(
        "add_mapping",
        {"proposal_id": 581, "source_column": "id",
         "target_table": "station_name", "target_column": "id",
         "proposal_status": "approved"})
    assert "Rejected" in render_result(
        "reject_mapping",
        {"proposal_id": 581, "source_column": "legislative_district",
         "proposal_status": "approved"})
    assert "review 99" in render_result(
        "reject_mapping_review",
        {"review_id": 99, "proposal_id": 583, "source_column": "librarian",
         "target_table": "profile", "target_column": "id",
         "proposal_status": "approved"})
    assert "Reopened review 1217" in render_result(
        "reopen_mapping_review",
        {"review_id": 1217, "proposal_id": 582, "source_column": "address",
         "target_table": "station_address", "target_column": "id",
         "proposal_status": "needs_review"})


# --- §0.3 privacy fixture: row values never reach prompts or storage ------------

def test_privacy_row_values_never_reach_prompt_or_persistence(monkeypatch):
    """The acceptance fixture from design D9: plant row values in BOTH the
    page context and the tool result; prove neither appears in the
    classification prompt nor in any persisted message."""
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,heuristic")
    captured_prompts = []

    def classifier(message, page_context):
        captured_prompts.append(
            _build_classify_prompt(message, page_context))
        return Intent("safe_tool", {}, 0.9, "heuristic")

    manager = _MemManager()
    registry, _ = _spy_registry()          # handler returns rows: ROWVALUE
    session = AgentSession(1, manager=manager,
                           dispatcher=ToolDispatcher(registry),
                           classifier=classifier)
    session.process_message(
        "check this", page_context={
            "proposal_id": 5,
            "rows": [{"name": "PII_SOURCE_ROW"}],
            "sample_values": ["PII_SAMPLE"]})

    blob = json.dumps(manager.rows["c1"]["messages"])
    for planted in ("PII_SOURCE_ROW", "PII_SAMPLE", "ROWVALUE"):
        assert planted not in blob
        assert all(planted not in prompt for prompt in captured_prompts)
    # the harmless schema-level context survived
    assert '"proposal_id": 5' in blob


# --- ConversationManager SQL behavior over a fake central (§3.6) ----------------

class _FakeCentral:
    """Interprets the manager's SQL statements against an in-memory store."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._next = 0

    def connection(self):
        central = self

        class _Cur:
            rowcount = 0

            def __enter__(cur):
                return cur

            def __exit__(cur, *a):
                return False

            def execute(cur, sql, params=None):
                cur._result, cur._all = None, []
                if "INSERT INTO integration.agent_conversation" in sql:
                    central._next += 1
                    row = {"id": f"u{central._next}", "user_id": params[0],
                           "title": "", "autonomy_tier": params[1],
                           "messages": [], "created_at": f"t{central._next}",
                           "updated_at": f"t{central._next}"}
                    central.rows[row["id"]] = row
                    cur._result = dict(row)
                elif "DELETE FROM integration.agent_conversation" in sql and "NOT IN" in sql:
                    user_id, _, limit = params
                    mine = sorted((r for r in central.rows.values()
                                   if r["user_id"] == user_id),
                                  key=lambda r: r["updated_at"], reverse=True)
                    for row in mine[limit:]:
                        central.rows.pop(row["id"])
                elif "DELETE FROM integration.agent_conversation" in sql:
                    cid, uid = params
                    row = central.rows.get(cid)
                    cur.rowcount = 1 if row and row["user_id"] == uid else 0
                    if cur.rowcount:
                        central.rows.pop(cid)
                elif "SET autonomy_tier" in sql:
                    tier, cid, uid = params
                    row = central.rows.get(cid)
                    if row and row["user_id"] == uid:
                        row["autonomy_tier"] = tier
                elif "SET messages" in sql:
                    messages, title, cid, uid = params
                    row = central.rows.get(cid)
                    if row and row["user_id"] == uid:
                        row["messages"] = json.loads(messages)
                        row["title"] = title
                elif "jsonb_array_length" in sql:
                    cur._all = [
                        {"id": r["id"], "title": r["title"],
                         "autonomy_tier": r["autonomy_tier"],
                         "created_at": r["created_at"],
                         "updated_at": r["updated_at"],
                         "message_count": len(r["messages"])}
                        for r in central.rows.values()
                        if r["user_id"] == params[0]]
                elif "SELECT * FROM integration.agent_conversation" in sql:
                    cid, uid = params
                    row = central.rows.get(str(cid))
                    cur._result = (dict(row) if row and row["user_id"] == uid
                                   else None)

            def fetchone(cur):
                return cur._result

            def fetchall(cur):
                return cur._all

        class _Conn:
            def __enter__(conn):
                return conn

            def __exit__(conn, *a):
                return False

            def cursor(conn, cursor_factory=None):
                return _Cur()

            def commit(conn):
                pass

        return _Conn()

    def close(self):
        pass


def test_manager_crud_round_trip():
    manager = ConversationManager(central=_FakeCentral())
    row = manager.create(1)
    assert row["autonomy_tier"] == "propose_only"
    manager.append_messages(row["id"], 1,
                            [{"role": "user", "content": "what's up?"}],
                            title_seed="what's up?")
    loaded = manager.load(row["id"], 1)
    assert loaded["messages"][0]["content"] == "what's up?"
    assert loaded["title"] == "What's up?"
    assert manager.list_for_user(1)[0]["message_count"] == 1
    manager.delete(row["id"], 1)
    with pytest.raises(NotFoundError):
        manager.load(row["id"], 1)


def test_manager_scopes_to_user():
    manager = ConversationManager(central=_FakeCentral())
    row = manager.create(1)
    with pytest.raises(NotFoundError):
        manager.load(row["id"], 2)
    with pytest.raises(NotFoundError):
        manager.delete(row["id"], 2)


def test_manager_rejects_auto_all():
    manager = ConversationManager(central=_FakeCentral())
    with pytest.raises(ValidationError):
        manager.create(1, "auto_all")
    row = manager.create(1)
    with pytest.raises(ValidationError):
        manager.set_tier(row["id"], 1, "auto_all")


def test_manager_title_truncated_to_120():
    manager = ConversationManager(central=_FakeCentral())
    row = manager.create(1)
    manager.append_messages(row["id"], 1, [{"role": "user", "content": "x"}],
                            title_seed="y" * 400)
    assert len(manager.load(row["id"], 1)["title"]) == conv.TITLE_MAX


def test_manager_prunes_oldest_beyond_retention(monkeypatch):
    monkeypatch.setattr(conv, "MAX_CONVERSATIONS_PER_USER", 3)
    manager = ConversationManager(central=_FakeCentral())
    ids = [manager.create(1)["id"] for _ in range(5)]
    remaining = {c["id"] for c in manager.list_for_user(1)}
    assert len(remaining) == 3
    assert ids[-1] in remaining and ids[0] not in remaining
