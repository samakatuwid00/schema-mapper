"""Agent chat API tests (conversational-ai-assistant §4.9, §9.3, §9.4).

Drives the REAL routes, REAL conversation loop, and REAL tool registry in
heuristic/offline mode (`LLM_PROVIDER_ORDER=heuristic`) — substituting only
the conversation store (in-memory manager), the wrapped ops service, and the
audit writer. This is the end-to-end path: chat → intent classify → tool
dispatch → SSE events → persisted messages.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ADMIN_SESSION_SECRET", "test-secret-for-unit-tests")

from fastapi.testclient import TestClient

import src.admin_api.agent_chat as agent_chat
import src.agent.tools as tools
from src.admin_api.app import create_app
from src.admin_api.auth import AdminUser, current_user
from src.agent import conversation as conv
from src.services.common import NotFoundError, ValidationError


class _MemManager:
    """In-memory ConversationManager double shared across requests."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._next = 0

    def create(self, user_id, autonomy_tier="propose_only"):
        if autonomy_tier not in conv.AUTONOMY_TIERS:
            raise ValidationError("unsupported autonomy tier")
        self._next += 1
        row = {"id": f"c{self._next}", "user_id": user_id, "title": "",
               "autonomy_tier": autonomy_tier, "messages": [],
               "created_at": "t", "updated_at": "t"}
        self.rows[row["id"]] = row
        return dict(row)

    def load(self, conversation_id, user_id):
        row = self.rows.get(str(conversation_id))
        if row is None or row["user_id"] != user_id:
            raise NotFoundError(f"conversation {conversation_id} not found")
        return dict(row)

    def set_tier(self, conversation_id, user_id, tier):
        if tier not in conv.AUTONOMY_TIERS:
            raise ValidationError("unsupported autonomy tier")
        self.load(conversation_id, user_id)
        self.rows[str(conversation_id)]["autonomy_tier"] = tier

    def append_messages(self, conversation_id, user_id, new_messages,
                        title_seed=None):
        row = self.rows[str(conversation_id)]
        row["messages"] = row["messages"] + list(new_messages)
        if not row["title"] and title_seed:
            row["title"] = title_seed.strip()[:conv.TITLE_MAX]
        return row["messages"]

    def list_for_user(self, user_id):
        return [{"id": r["id"], "title": r["title"],
                 "autonomy_tier": r["autonomy_tier"],
                 "created_at": r["created_at"], "updated_at": r["updated_at"],
                 "message_count": len(r["messages"])}
                for r in self.rows.values() if r["user_id"] == user_id]

    def delete(self, conversation_id, user_id):
        row = self.rows.get(str(conversation_id))
        if row is None or row["user_id"] != user_id:
            raise NotFoundError(f"conversation {conversation_id} not found")
        self.rows.pop(str(conversation_id))


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    current_event = None
    for line in text.splitlines():
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current_event:
            events.append((current_event,
                           json.loads(line.split(":", 1)[1].strip())))
            current_event = None
    return events


@pytest.fixture()
def harness(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "heuristic")   # offline mode (§9.4)
    manager = _MemManager()
    audits: list[dict] = []
    monkeypatch.setattr(agent_chat, "_manager", lambda: manager)
    monkeypatch.setattr(
        agent_chat, "write_audit",
        lambda actor, action, **kw: audits.append({"actor": actor,
                                                   "action": action, **kw}))
    monkeypatch.setattr(tools.ops_service, "get_status",
                        lambda: {"outbox": {"pending": 2, "delivered": 40}})
    app = create_app()
    app.dependency_overrides[current_user] = lambda: AdminUser(1, "tester", "operator")
    client = TestClient(app)
    yield {"client": client, "manager": manager, "audits": audits, "app": app}
    app.dependency_overrides.clear()


def _chat(client, payload):
    with client.stream("POST", "/api/agent/chat", json=payload) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return _parse_sse(response.read().decode())


# --- §9.3 end-to-end: chat → classify → dispatch → SSE → persistence ------------

def test_full_chat_flow_streams_typed_events(harness):
    events = _chat(harness["client"], {"message": "what's the status?"})
    kinds = [k for k, _ in events]
    assert kinds[0] == "conversation" and kinds[-1] == "done"
    assert "tool_call" in kinds and "tool_result" in kinds and "token" in kinds

    conversation = dict(events)["conversation"]
    assert conversation["conversation_id"] == "c1"
    result = [d for k, d in events if k == "tool_result"][0]
    assert result["result"]["outbox"] == {"pending": 2, "delivered": 40}
    done = [d for k, d in events if k == "done"][0]
    assert "pending: 2" in done["content"]

    stored = harness["manager"].rows["c1"]["messages"]
    assert [m["role"] for m in stored] == ["user", "assistant"]


def test_new_conversation_id_in_first_event_and_resume(harness):
    first = _chat(harness["client"], {"message": "status?"})
    cid = dict(first)["conversation"]["conversation_id"]
    _chat(harness["client"], {"message": "status again?",
                              "conversation_id": cid})
    assert len(harness["manager"].rows[cid]["messages"]) == 4


def test_page_context_disambiguates(harness, monkeypatch):
    review = {"proposal": {"id": 7, "entity_id": 1, "source_schema": "irimsv",
                           "source_table": "schools", "target_system": "LRMIS",
                           "status": "approved", "source_fingerprint": "sf",
                           "target_fingerprint": "tf",
                           "unmet_required_columns": []},
              "fields": [{"source_column": "id", "suggested_target_table": "school",
                          "suggested_target_column": "id",
                          "resolved_target_column": None, "confidence": 0.9,
                          "status": "accepted", "transform": "none"}]}
    monkeypatch.setattr(tools.onboarding_service, "get_review", lambda pid: review)
    events = _chat(harness["client"], {
        "message": "why is this blocked?", "context": {"proposal_id": 7}})
    call = [d for k, d in events if k == "tool_call"][0]
    assert call["tool"] == "explain_blocker" and call["params"] == {"proposal_id": 7}
    done = [d for k, d in events if k == "done"][0]
    assert "Nothing is blocking" in done["content"]


def test_mutating_intent_returns_confirmation_then_confirm_executes(harness,
                                                                    monkeypatch):
    proposed = []
    monkeypatch.setattr(tools.onboarding_service, "propose",
                        lambda schema, table, system: proposed.append(table) or
                        {"proposal_id": 31, "status": "needs_review"})
    first = _chat(harness["client"], {"message": "onboard authors"})
    call = [d for k, d in first if k == "tool_call"][0]
    assert call["requires_confirmation"] is True and proposed == []

    second = _chat(harness["client"], {
        "message": "yes, go ahead",
        "conversation_id": dict(first)["conversation"]["conversation_id"],
        "confirm": {"tool": "onboard_table",
                    "params": {"source_table": "authors"}}})
    assert proposed == ["authors"]
    result = [d for k, d in second if k == "tool_result"][0]
    assert result["result"]["proposal"]["proposal_id"] == 31


def test_unknown_message_gets_clarification(harness):
    events = _chat(harness["client"], {"message": "bake me a cake"})
    done = [d for k, d in events if k == "done"][0]
    assert "I can:" in done["content"]
    assert all(k != "tool_call" for k, _ in events)


def test_swap_preview_flow_through_chat(harness, monkeypatch):
    """§8.3: a swap request now runs the read-only dry-run tool and the
    workflow suggests the next step; the destructive apply stays gated."""
    import src.adapters as adapters
    monkeypatch.setattr(adapters, "get_target_adapter",
                        lambda engine, **kw: object())
    monkeypatch.setattr(tools.schema_swap_service, "dry_run",
                        lambda target_adapter: {"would_remap": ["schools"],
                                                "affected_entities": []})
    events = _chat(harness["client"], {"message": "let's do a schema swap"})
    call = [d for k, d in events if k == "tool_call"][0]
    assert call["tool"] == "swap_target_dry_run" and call["executed"] is True
    done = [d for k, d in events if k == "done"][0]
    assert "1 affected entities" in done["content"]
    assert "Next step: remap" in done["content"]


def test_swap_apply_via_chat_is_double_gated(harness, monkeypatch):
    """Destructive tier: chat approval is required, AND the handler still
    demands the typed token — confirming without it fails safely."""
    monkeypatch.setenv("LRMIS_TARGET_ENGINE", "mysql")
    monkeypatch.setenv("LRMIS_TARGET_DATABASE", "lrmis_target")
    monkeypatch.delenv("LRMIS_TARGET_PG_DSN", raising=False)
    events = _chat(harness["client"], {
        "message": "apply it",
        "confirm": {"tool": "swap_target_apply", "params": {}}})
    error = [d for k, d in events if k == "error"][0]
    assert "requires confirm='lrmis_target'" in error["detail"]


def test_deferred_recovery_request_points_to_recovery_page(harness):
    events = _chat(harness["client"], {"message": "restore a backup"})
    done = [d for k, d in events if k == "done"][0]
    assert "Recovery page" in done["content"]


# --- audit (§4.7) ----------------------------------------------------------------

def test_executed_tool_is_audited_with_autonomy_details(harness):
    _chat(harness["client"], {"message": "check status please"})
    audit = harness["audits"][0]
    assert audit["action"] == "agent:check_status"
    assert audit["target_type"] == "agent_conversation"
    assert audit["target_id"] == "c1"
    assert audit["details"]["autonomy"] == "propose_only"
    assert audit["details"]["auto_executed"] is True


def test_confirmation_prompt_is_not_audited_as_execution(harness):
    _chat(harness["client"], {"message": "onboard authors"})
    assert harness["audits"] == []


# --- tiers (§7 via API) ------------------------------------------------------------

def test_auto_all_tier_rejected_with_422(harness):
    response = harness["client"].post(
        "/api/agent/chat", json={"message": "hi", "autonomy_tier": "auto_all"})
    assert response.status_code == 422
    assert "unsupported autonomy tier" in response.json()["detail"]


def test_tier_change_persists_on_conversation(harness):
    first = _chat(harness["client"], {"message": "status?"})
    cid = dict(first)["conversation"]["conversation_id"]
    _chat(harness["client"], {"message": "status?", "conversation_id": cid,
                              "autonomy_tier": "auto_safe"})
    assert harness["manager"].rows[cid]["autonomy_tier"] == "auto_safe"


# --- CRUD (§4.4-4.6) ---------------------------------------------------------------

def test_conversation_crud_scoped_to_user(harness):
    client = harness["client"]
    _chat(client, {"message": "status?"})
    listed = client.get("/api/agent/conversations").json()
    assert len(listed) == 1 and listed[0]["message_count"] == 2
    assert listed[0]["title"].lower().startswith("status")

    detail = client.get(f"/api/agent/conversations/{listed[0]['id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    # another user cannot see or delete it (404, not 403)
    harness["app"].dependency_overrides[current_user] = \
        lambda: AdminUser(2, "other", "operator")
    assert client.get(f"/api/agent/conversations/{listed[0]['id']}").status_code == 404
    assert client.delete(f"/api/agent/conversations/{listed[0]['id']}").status_code == 404

    harness["app"].dependency_overrides[current_user] = \
        lambda: AdminUser(1, "tester", "operator")
    assert client.delete(f"/api/agent/conversations/{listed[0]['id']}").json() == {"ok": True}
    assert client.get("/api/agent/conversations").json() == []


def test_stream_recovery_reload_returns_persisted_messages(harness):
    """Reconnect semantics (MVP): reload the conversation by id after a drop."""
    first = _chat(harness["client"], {"message": "status?"})
    cid = dict(first)["conversation"]["conversation_id"]
    detail = harness["client"].get(f"/api/agent/conversations/{cid}").json()
    assert detail["messages"][-1]["role"] == "assistant"


def test_chat_error_event_on_unknown_conversation(harness):
    events = _chat(harness["client"], {"message": "hi",
                                       "conversation_id": "nope"})
    assert events[0][0] == "error" and "not found" in events[0][1]["detail"]
    assert events[-1][0] == "done"


def test_agent_routes_require_auth(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "heuristic")
    app = create_app()
    with TestClient(app) as client:
        assert client.post("/api/agent/chat",
                           json={"message": "hi"}).status_code == 401
        assert client.get("/api/agent/conversations").status_code == 401
        assert client.delete("/api/agent/conversations/x").status_code == 401
