"""Agent chat API (conversational-ai-assistant §4).

`POST /api/agent/chat` streams typed SSE events (design D4:
conversation | token | tool_call | tool_result | error | done) from one
turn of the conversation loop; conversation CRUD is scoped to the
authenticated user (another user's conversation is a 404, never a 403 —
existence is not leaked). Every EXECUTED tool is audited through the same
`admin_action_audit` writer as the rest of the admin API, with the autonomy
tier and whether it auto-executed (§7.5).
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..agent.conversation import (AUTONOMY_TIERS, AgentSession,
                                  ConversationManager, ToolDispatcher)
from ..services.common import NotFoundError, ValidationError
from .audit import write_audit
from .auth import AdminUser, current_user, require_operator

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])


def _manager() -> ConversationManager:
    """Per-request manager; module-level factory so tests substitute it."""
    return ConversationManager()


class ChatBody(BaseModel):
    message: str
    conversation_id: str | None = None
    context: dict | None = None
    autonomy_tier: str | None = None
    # Confirmation round-trip for a previously proposed tool call:
    # {"tool": name, "params": {...}} — dispatched with confirmed=True.
    confirm: dict | None = None


@agent_router.post("/chat")
async def chat(body: ChatBody, user: AdminUser = Depends(require_operator)):
    if body.autonomy_tier and body.autonomy_tier not in AUTONOMY_TIERS:
        raise ValidationError(
            f"unsupported autonomy tier {body.autonomy_tier!r}; "
            f"supported: {AUTONOMY_TIERS}")   # auto_all et al: rejected (D8)
    session = AgentSession(user.id, manager=_manager(),
                           dispatcher=ToolDispatcher())

    async def stream():
        try:
            events = await asyncio.to_thread(
                session.process_message, body.message,
                conversation_id=body.conversation_id,
                page_context=body.context or {},
                autonomy_tier=body.autonomy_tier,
                confirm=body.confirm)
        except (ValidationError, NotFoundError) as exc:
            yield {"event": "error", "data": json.dumps({"detail": str(exc)})}
            yield {"event": "done", "data": json.dumps({"content": ""})}
            return

        conversation_id = events[0].data.get("conversation_id") if events else None
        for event in events:
            if event.event == "tool_call" and event.data.get("executed"):
                write_audit(user.username, f"agent:{event.data['tool']}",
                            target_type="agent_conversation",
                            target_id=str(conversation_id),
                            details={"autonomy": event.data.get("autonomy"),
                                     "auto_executed": event.data.get("auto_executed"),
                                     "params": event.data.get("params")})
            yield {"event": event.event,
                   "data": json.dumps(event.data, default=str)}

    return EventSourceResponse(stream())


@agent_router.get("/conversations")
def list_conversations(user: AdminUser = Depends(current_user)):
    return _manager().list_for_user(user.id)


@agent_router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str,
                     user: AdminUser = Depends(current_user)):
    return _manager().load(conversation_id, user.id)


@agent_router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str,
                        user: AdminUser = Depends(require_operator)):
    _manager().delete(conversation_id, user.id)
    return {"ok": True}
