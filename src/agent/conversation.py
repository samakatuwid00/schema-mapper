"""Conversation loop (conversational-ai-assistant §3, §7; design D1, D4, D8, D9).

Intent-classified, tool-dispatched chat over the existing services — never
free-form function calling. The LLM (existing provider-failover contract:
``LLM_PROVIDER_ORDER`` + ``<NAME>_API_KEY``, terminal ``heuristic``) only
classifies the message; typed tools from `tools.py` do the work; templates
render every response so the assistant works with no LLM at all.

Free-tier budget limits (§0.2, env-overridable):
* ``AGENT_MAX_PROMPT_CHARS``   — hard cap on any classification prompt.
* ``AGENT_MAX_RESPONSE_TOKENS``— max_output_tokens for LLM calls.
* ``AGENT_LLM_TIMEOUT_SECONDS``— per-provider timeout; on timeout/error the
  next provider in the order (or the heuristic) takes over.
* ``AGENT_MAX_MESSAGES``       — context cap; older messages collapse into a
  schema-only summary entry.
* ``AGENT_MAX_CONVERSATIONS``  — per-user retention (oldest pruned).

Privacy (D9): prompts and persisted messages carry schema metadata, IDs,
statuses, and action summaries only. `redact_row_values` strips row-shaped
keys from every tool result before it is rendered or stored, and the prompt
builder never receives tool results at all.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import psycopg2.extras

from ..connectors import PostgresCentralConnector
from ..services.common import NotFoundError, ValidationError
from . import workflows
from .tool_defs import ToolDef, validate_params
from .tools import REGISTRY, list_tools

# --- §0.2 free-tier budgets ---------------------------------------------------
MAX_PROMPT_CHARS = int(os.environ.get("AGENT_MAX_PROMPT_CHARS", "6000"))
MAX_RESPONSE_TOKENS = int(os.environ.get("AGENT_MAX_RESPONSE_TOKENS", "512"))
LLM_TIMEOUT_SECONDS = float(os.environ.get("AGENT_LLM_TIMEOUT_SECONDS", "20"))
MAX_MESSAGES = int(os.environ.get("AGENT_MAX_MESSAGES", "40"))
KEEP_RECENT_MESSAGES = 20
MAX_CONVERSATIONS_PER_USER = int(os.environ.get("AGENT_MAX_CONVERSATIONS", "100"))

CONFIDENCE_FLOOR = 0.5          # below this: ask for clarification
AUTO_SAFE_CONFIDENCE = float(os.environ.get("AGENT_AUTO_SAFE_CONFIDENCE", "0.7"))
AUTONOMY_TIERS = ("propose_only", "auto_safe")   # auto_all is NOT a tier (D8)

TITLE_MAX = 120

# Row-shaped keys stripped from every tool result before render/persist (D9).
ROW_VALUE_KEYS = frozenset({
    "rows", "row", "records", "record", "values", "sample_values",
    "payload", "payload_snapshot", "source_row", "target_row", "data_rows",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_row_values(obj):
    """Recursively drop row-shaped keys so row values can never reach a
    prompt, a rendered reply, or the persisted conversation."""
    if isinstance(obj, dict):
        return {k: redact_row_values(v) for k, v in obj.items()
                if k not in ROW_VALUE_KEYS}
    if isinstance(obj, list):
        return [redact_row_values(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# §3.1 conversation context
# ---------------------------------------------------------------------------

@dataclass
class ConversationContext:
    conversation_id: str | None = None
    messages: list[dict] = field(default_factory=list)
    page_context: dict = field(default_factory=dict)
    workflow_state: workflows.WorkflowState | None = None
    autonomy_tier: str = "propose_only"


@dataclass
class Intent:
    name: str | None          # a registered tool name, "deferred", or None
    params: dict
    confidence: float
    source: str               # "heuristic" | "llm" | provider name


@dataclass
class StreamEvent:
    event: str                # conversation|token|tool_call|tool_result|error|done
    data: dict


# ---------------------------------------------------------------------------
# §3.2/3.3 intent classification (LLM failover order + heuristic terminal)
# ---------------------------------------------------------------------------

# Ordered: more specific phrasings first. Every pattern maps to a registered
# tool; only backup-recovery phrasings still map to the safe deferral
# (recovery keeps its typed-confirmation home on the Recovery page/CLI);
# "where are we" reads the persisted workflow state without any tool call.
_DEFERRED = "deferred"
_WORKFLOW_STATUS = "workflow_status"
_INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (_DEFERRED, ("restore a backup", "restore the target", "restore the source",
                 "recover from backup", "restore from backup")),
    (_WORKFLOW_STATUS, ("where are we", "workflow status", "current step",
                        "what step", "how far along")),
    ("swap_source_schema", ("swap the source", "source schema changed",
                            "restructured source", "new source schema",
                            "source swap")),
    ("swap_target_dry_run", ("schema swap", "swap the target", "swap target",
                             "target schema changed", "new target schema",
                             "swap the schema")),
    ("resolve_drift", ("resolve drift", "resolve the drift", "fix the drift",
                       "apply drift")),
    ("list_drift_reports", ("drift",)),
    ("heal_error", ("heal", "fix this error", "fix the error",
                    "delivery error", "quarantined event")),
    ("onboard_table", ("onboard", "add a table", "add table", "new table",
                       "map a table", "start mapping")),
    ("summarize_proposal", ("summarize", "summary of", "proposal look",
                            "review proposal", "how good is the proposal",
                            "proposal risk")),
    ("explain_blocker", ("block", "stuck", "why can't", "why cant",
                         "not deploying", "won't deploy", "wont deploy")),
    ("deploy_guidance", ("deploy", "ready to ship", "go live")),
    ("explain_dilemma", ("dilemma", "unmapped column", "type mismatch",
                         "fk violation", "which option", "how do i map")),
    ("show_schema", ("schema", "structure", "what tables", "which tables",
                     "columns of", "show tables")),
    ("check_status", ("status", "health", "queue", "pending", "outbox",
                      "how are things", "everything ok", "anything wrong")),
]

_PROPOSAL_ID_RE = re.compile(r"(?:proposal\s*#?|#)(\d+)", re.I)
_TABLE_RE = re.compile(r"\b(?:onboard|table)\s+([a-z_][a-z0-9_]*)", re.I)


def _extract_params(intent_name: str, message: str, page_context: dict) -> dict:
    params: dict = {}
    if intent_name in ("summarize_proposal", "explain_blocker", "deploy_guidance"):
        match = _PROPOSAL_ID_RE.search(message)
        if match:
            params["proposal_id"] = int(match.group(1))
        elif page_context.get("proposal_id") is not None:
            params["proposal_id"] = int(page_context["proposal_id"])
    if intent_name == "onboard_table":
        match = _TABLE_RE.search(message)
        if match and match.group(1) not in ("a", "the", "new"):
            params["source_table"] = match.group(1)
        elif page_context.get("entity"):
            params["source_table"] = str(page_context["entity"])
    if intent_name == "explain_dilemma":
        lowered = message.lower()
        for kind in ("unmapped_column", "type_mismatch", "fk_violation"):
            if kind.replace("_", " ") in lowered or kind in lowered:
                params["kind"] = kind
        params.setdefault("kind", "unknown")
    if intent_name == "heal_error":
        # the message IS the error description unless the page provided one
        params["error"] = str(page_context.get("error") or message)
    if intent_name == "resolve_drift" and page_context.get("entity"):
        params["entities"] = [str(page_context["entity"])]
    return params


def heuristic_classify(message: str, page_context: dict) -> Intent:
    """Deterministic keyword classifier — the terminal, no-API provider."""
    lowered = message.lower()
    for name, needles in _INTENT_PATTERNS:
        if any(needle in lowered for needle in needles):
            return Intent(name=name,
                          params=_extract_params(name, message, page_context),
                          confidence=0.75, source="heuristic")
    return Intent(name=None, params={}, confidence=0.0, source="heuristic")


_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "params": {"type": "object"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["intent", "confidence"],
    "additionalProperties": False,
}

_CLASSIFY_PROMPT = """You classify a database-migration operator's message into one intent.

Available intents (respond with exactly one name, or "none"):
{tools}

Page context (route + IDs only): {page}

Message: {message}

Respond as JSON: {{"intent": "<name or none>", "params": {{...extracted ids/names...}}, "confidence": 0.0-1.0}}"""


def _build_classify_prompt(message: str, page_context: dict) -> str:
    tools = "\n".join(f"- {t['name']}: {t['description']}" for t in list_tools())
    prompt = _CLASSIFY_PROMPT.format(tools=tools, page=json.dumps(page_context),
                                     message=message)
    # §0.2/D9: hard prompt budget — drop from the front (tool docs) not the
    # message if something pathological slips through.
    return prompt[-MAX_PROMPT_CHARS:] if len(prompt) > MAX_PROMPT_CHARS else prompt


def _classify_gemini(prompt: str, client=None) -> dict:
    from ..mapping_engine import genai
    if genai is None:
        raise RuntimeError("google-genai not installed")
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key and client is None:
        raise RuntimeError("no Gemini API key")
    if client is None:
        client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": _CLASSIFY_SCHEMA,
            "temperature": 0.0,
            "max_output_tokens": MAX_RESPONSE_TOKENS,
            "http_options": {"timeout": int(LLM_TIMEOUT_SECONDS * 1000)},
        },
    )
    if not response.text:
        raise RuntimeError("Gemini returned no content")
    return json.loads(response.text)


def classify(message: str, page_context: dict | None = None, *,
             client=None, prompt_sink: list | None = None) -> Intent:
    """Classify via the LLM provider order, falling back to the heuristic on
    any failure (quota, timeout, no key, bad output). ``prompt_sink`` lets
    tests capture exactly what would be sent to a provider (privacy §0.3)."""
    page_context = page_context or {}
    order = [n.strip().lower() for n in
             os.environ.get("LLM_PROVIDER_ORDER", "gemini,fallback").split(",")
             if n.strip()]
    for name in order:
        if name == "heuristic":
            break
        if name != "gemini":
            # OpenAI-compatible classification is a later phase; those
            # providers are skipped for classification (mapping keeps using
            # them) and the heuristic terminal answers instead.
            continue
        prompt = _build_classify_prompt(message, page_context)
        if prompt_sink is not None:
            prompt_sink.append(prompt)
        try:
            raw = _classify_gemini(prompt, client)
            name_out = str(raw.get("intent") or "none").strip()
            confidence = float(raw.get("confidence") or 0.0)
            if name_out in REGISTRY:
                params = {**_extract_params(name_out, message, page_context),
                          **(raw.get("params") or {})}
                return Intent(name=name_out, params=params,
                              confidence=confidence, source="gemini")
            if name_out == _DEFERRED:
                return Intent(name=_DEFERRED, params={}, confidence=confidence,
                              source="gemini")
            return Intent(name=None, params={}, confidence=0.0, source="gemini")
        except Exception:
            continue                     # failover: next provider / heuristic
    return heuristic_classify(message, page_context)


# ---------------------------------------------------------------------------
# §3.4 deterministic templates (offline mode + every MVP intent)
# ---------------------------------------------------------------------------

def _fmt_check_status(result: dict) -> str:
    outbox = result.get("outbox") or {}
    parts = [f"{k}: {v}" for k, v in outbox.items()] or ["no outbox data"]
    quarantine = result.get("quarantine_unresolved")
    if quarantine is not None:
        parts.append(f"unresolved quarantine: {quarantine}")
    return "Integration status — " + ", ".join(str(p) for p in parts) + "."


def _fmt_summarize(result: dict) -> str:
    return result.get("summary") or "Proposal summary unavailable."


def _fmt_blocker(result: dict) -> str:
    if result.get("deploy_ready"):
        return "Nothing is blocking — this proposal is ready to deploy."
    blockers = result.get("blockers") or ["unknown blocker"]
    return "Blocked: " + " | ".join(blockers)


def _fmt_schema(result: dict) -> str:
    def names(tree):
        tables = (tree or {}).get("tables") or []
        if isinstance(tables, dict):
            return sorted(tables)
        return [t.get("name", "?") if isinstance(t, dict) else str(t)
                for t in tables]
    source = names(result.get("source"))
    target = names(result.get("target"))
    return (f"Source has {len(source)} tables ({', '.join(source[:8])}"
            f"{'…' if len(source) > 8 else ''}); target has {len(target)} "
            f"tables ({', '.join(target[:8])}{'…' if len(target) > 8 else ''}).")


def _fmt_guidance(result: dict) -> str:
    actions = result.get("recommended_actions") or []
    status = "ready to deploy" if result.get("deploy_ready") else "not deploy-ready"
    return (f"This proposal is {status}. " +
            (" Next: " + "; ".join(actions) if actions else ""))


def _fmt_dilemma(result: dict) -> str:
    options = result.get("options") or []
    labels = [o.get("label", o.get("action", "?")) for o in options]
    return (f"Options for this {result.get('kind', 'dilemma')}: "
            + "; ".join(labels)
            + f". Recommended: {result.get('recommended', 'manual')}.")


def _fmt_onboard(result: dict) -> str:
    note = result.get("note") or ""
    proposal = result.get("proposal") or {}
    pid = proposal.get("proposal_id") or proposal.get("id")
    lead = (f"Created proposal {pid} for review. " if pid
            else "Created a proposal for review. ")
    return lead + note


def _fmt_heal(result: dict) -> str:
    return (f"Proposed heal: {result.get('action', '?')} "
            f"({json.dumps(result.get('detail') or {})}). "
            + str(result.get("note") or ""))


def _fmt_drift_list(result: dict) -> str:
    count = result.get("count", 0)
    if not count:
        return "No drift reports recorded — schemas match their contracts."
    latest = (result.get("reports") or [{}])[0]
    impacted = latest.get("impacted_entities") or []
    return (f"{count} drift report(s). Latest: {latest.get('target_system', '?')}"
            f", impacted entities: {', '.join(map(str, impacted)) or 'none'}. "
            "Say \"resolve drift\" to re-map and re-deliver them "
            "(confirmation required).")


def _fmt_resolve_drift(result: dict) -> str:
    if result.get("dry_run"):
        return "Drift resolution dry-run complete — nothing was changed."
    return "Drift resolution ran. Full result attached."


def _fmt_swap_dry_run(result: dict) -> str:
    affected = result.get("would_remap") or []
    if not affected:
        return ("Swap preview: no deployed entities are affected by the "
                "target diff — an apply would only recreate and re-deliver.")
    return (f"Swap preview: {len(affected)} affected entities "
            f"({', '.join(map(str, affected))}). Applying re-maps them "
            "(human-gated), recreates the target, and re-delivers — "
            "destructive, typed confirmation required.")


def _fmt_swap_apply(result: dict) -> str:
    status = result.get("status", "?")
    if status == "blocked_on_review":
        return ("Swap blocked on low-confidence re-mappings: "
                f"{', '.join(map(str, result.get('blocked') or []))}. "
                "Resolve them or re-run with force.")
    return f"Target swap {status}."


TEMPLATES = {
    "check_status": _fmt_check_status,
    "summarize_proposal": _fmt_summarize,
    "explain_blocker": _fmt_blocker,
    "show_schema": _fmt_schema,
    "deploy_guidance": _fmt_guidance,
    "explain_dilemma": _fmt_dilemma,
    "onboard_table": _fmt_onboard,
    "heal_error": _fmt_heal,
    "list_drift_reports": _fmt_drift_list,
    "resolve_drift": _fmt_resolve_drift,
    "swap_target_dry_run": _fmt_swap_dry_run,
    "swap_target_apply": _fmt_swap_apply,
    "swap_source_schema": _fmt_swap_dry_run,
}

CLARIFICATION_TEXT = (
    "I did not catch a supported request. I can: check integration status, "
    "summarize a proposal, explain what blocks a deploy, show the schemas, "
    "give deploy guidance, explain a mapping dilemma, onboard a table "
    "(propose-only), list or resolve schema drift, preview or apply a "
    "schema swap, or propose an error heal. Try e.g. \"what's blocking "
    "proposal 42?\".")

DEFERRED_TEXT = (
    "Chat-guided backup recovery is not available — restores stay on the "
    "Recovery page (Maintain group) or `scripts/recover.py`, both behind "
    "the same typed-confirmation gates.")


def render_result(tool_name: str, result: dict) -> str:
    formatter = TEMPLATES.get(tool_name)
    if formatter is None:
        return f"{tool_name} finished."
    try:
        return formatter(result)
    except Exception:
        return f"{tool_name} finished (result attached)."


# ---------------------------------------------------------------------------
# §3.5 + §7 tool dispatch with the autonomy gate
# ---------------------------------------------------------------------------

@dataclass
class DispatchOutcome:
    executed: bool
    requires_confirmation: bool
    tool_name: str
    params: dict
    result: dict | None
    error: str | None
    autonomy: str            # the tool's declared level
    tier: str                # the conversation tier at dispatch time
    auto_executed: bool
    destructive: bool = False


class ToolDispatcher:
    """Validates params, applies the autonomy gate (§7), calls the handler,
    audits the execution. The gate (D8):

    * ``destructive`` tools ALWAYS require confirmation, any tier — and the
      wrapped services enforce their own typed confirmation besides.
    * ``auto_safe``-marked tools (the read-only allowlist) execute in any
      tier when classification confidence >= the auto-safe threshold.
    * ``propose_only`` tools require confirmation unless the user already
      confirmed this exact call.
    """

    def __init__(self, registry: dict[str, ToolDef] | None = None, audit=None):
        self._registry = registry or REGISTRY
        self._audit = audit      # callable(tool_name, details) — router wires it

    def dispatch(self, tool_name: str, params: dict, *, tier: str,
                 confidence: float = 1.0, confirmed: bool = False,
                 ) -> DispatchOutcome:
        if tier not in AUTONOMY_TIERS:
            raise ValidationError(
                f"unsupported autonomy tier {tier!r}; supported: {AUTONOMY_TIERS}")
        tool = self._registry.get(tool_name)
        if tool is None:
            raise NotFoundError(f"unknown tool {tool_name!r}")

        try:
            validate_params(tool, params)
        except ValidationError as exc:
            return DispatchOutcome(False, False, tool_name, params, None,
                                   str(exc), tool.autonomy, tier, False,
                                   tool.destructive)

        if tool.destructive and not confirmed:
            needs_confirmation = True
        elif confirmed:
            needs_confirmation = False
        elif tool.autonomy == "auto_safe":
            needs_confirmation = confidence < AUTO_SAFE_CONFIDENCE
        else:                                   # propose_only tool
            needs_confirmation = True

        if needs_confirmation:
            return DispatchOutcome(False, True, tool_name, params, None, None,
                                   tool.autonomy, tier, False, tool.destructive)

        auto_executed = not confirmed
        try:
            result = redact_row_values(tool.handler(params))
            error = None
        except (ValidationError, NotFoundError) as exc:
            result, error = None, str(exc)
        if self._audit:
            self._audit(tool_name, {"autonomy": tier,
                                    "tool_autonomy": tool.autonomy,
                                    "auto_executed": auto_executed,
                                    "error": error})
        return DispatchOutcome(error is None, False, tool_name, params,
                               result, error, tool.autonomy, tier,
                               auto_executed, tool.destructive)


# ---------------------------------------------------------------------------
# §3.6 conversation persistence (DB-backed, injectable central)
# ---------------------------------------------------------------------------

class ConversationManager:
    """Load/save conversations; cap context; prune per-user retention."""

    def __init__(self, central: PostgresCentralConnector | None = None):
        self._central = central

    def _conn(self):
        return (self._central or PostgresCentralConnector()).connection()

    # -- CRUD ---------------------------------------------------------------
    def create(self, user_id: int, autonomy_tier: str = "propose_only") -> dict:
        if autonomy_tier not in AUTONOMY_TIERS:
            raise ValidationError(
                f"unsupported autonomy tier {autonomy_tier!r}; "
                f"supported: {AUTONOMY_TIERS}")
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO integration.agent_conversation
                        (user_id, autonomy_tier) VALUES (%s, %s) RETURNING *
                """, (user_id, autonomy_tier))
                row = dict(cur.fetchone())
                # retention (D6): prune oldest beyond the per-user cap
                cur.execute("""
                    DELETE FROM integration.agent_conversation
                    WHERE user_id = %s AND id NOT IN (
                        SELECT id FROM integration.agent_conversation
                        WHERE user_id = %s
                        ORDER BY updated_at DESC LIMIT %s)
                """, (user_id, user_id, MAX_CONVERSATIONS_PER_USER))
            conn.commit()
        return row

    def load(self, conversation_id: str, user_id: int) -> dict:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM integration.agent_conversation
                    WHERE id = %s AND user_id = %s
                """, (str(conversation_id), user_id))
                row = cur.fetchone()
        if row is None:                     # other users' conversations: 404
            raise NotFoundError(f"conversation {conversation_id} not found")
        row = dict(row)
        if isinstance(row.get("messages"), str):
            row["messages"] = json.loads(row["messages"])
        return row

    def list_for_user(self, user_id: int) -> list[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, title, autonomy_tier, created_at, updated_at,
                           jsonb_array_length(messages) AS message_count
                    FROM integration.agent_conversation
                    WHERE user_id = %s ORDER BY updated_at DESC
                """, (user_id,))
                return [dict(r) for r in cur.fetchall()]

    def delete(self, conversation_id: str, user_id: int) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM integration.agent_conversation
                    WHERE id = %s AND user_id = %s
                """, (str(conversation_id), user_id))
                deleted = cur.rowcount
            conn.commit()
        if not deleted:
            raise NotFoundError(f"conversation {conversation_id} not found")

    def set_tier(self, conversation_id: str, user_id: int, tier: str) -> None:
        if tier not in AUTONOMY_TIERS:
            raise ValidationError(
                f"unsupported autonomy tier {tier!r}; supported: {AUTONOMY_TIERS}")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integration.agent_conversation
                    SET autonomy_tier = %s, updated_at = now()
                    WHERE id = %s AND user_id = %s
                """, (tier, str(conversation_id), user_id))
            conn.commit()

    # -- messages -----------------------------------------------------------
    def append_messages(self, conversation_id: str, user_id: int,
                        new_messages: list[dict], title_seed: str | None = None,
                        ) -> list[dict]:
        row = self.load(conversation_id, user_id)
        messages = list(row["messages"]) + [redact_row_values(m)
                                            for m in new_messages]
        messages = _cap_messages(messages)
        title = row["title"]
        if not title and title_seed:
            title = title_seed.strip()[:TITLE_MAX]
            if title:
                title = title[0].upper() + title[1:]
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE integration.agent_conversation
                    SET messages = %s::jsonb, title = %s, updated_at = now()
                    WHERE id = %s AND user_id = %s
                """, (json.dumps(messages, default=str), title,
                      str(conversation_id), user_id))
            conn.commit()
        return messages


def _cap_messages(messages: list[dict]) -> list[dict]:
    """Context-window cap (§3.6): collapse everything but the most recent
    KEEP_RECENT_MESSAGES into one schema-only summary entry (intent names and
    counts — never content, never row values)."""
    if len(messages) <= MAX_MESSAGES:
        return messages
    old, recent = messages[:-KEEP_RECENT_MESSAGES], messages[-KEEP_RECENT_MESSAGES:]
    intents = []
    for message in old:
        for call in message.get("tool_calls") or []:
            name = call.get("tool")
            if name and name not in intents:
                intents.append(name)
    summary = {
        "role": "system",
        "content": (f"[summary] {len(old)} earlier messages condensed. "
                    f"Tools discussed: {', '.join(intents) or 'none'}."),
        "created_at": _now_iso(),
    }
    return [summary, *recent]


# ---------------------------------------------------------------------------
# §3.7 the session: process one message -> typed stream events
# ---------------------------------------------------------------------------

TOKEN_CHUNK = 80


class AgentSession:
    """One authenticated user's chat session over the shared managers.

    `process_message` is the synchronous, fully-testable core; `converse`
    wraps it as the AsyncGenerator the SSE endpoint streams (blocking work
    runs in a worker thread so the event loop never stalls)."""

    def __init__(self, user_id: int, *, manager: ConversationManager | None = None,
                 dispatcher: ToolDispatcher | None = None, classifier=None,
                 client=None):
        self.user_id = user_id
        self.manager = manager or ConversationManager()
        self.dispatcher = dispatcher or ToolDispatcher()
        self._classify = classifier or (
            lambda message, page_context: classify(message, page_context,
                                                   client=client))

    # -- sync core ------------------------------------------------------------
    def process_message(self, message: str, *, conversation_id: str | None = None,
                        page_context: dict | None = None,
                        autonomy_tier: str | None = None,
                        confirm: dict | None = None) -> list[StreamEvent]:
        page_context = redact_row_values(page_context or {})
        events: list[StreamEvent] = []

        if conversation_id:
            row = self.manager.load(conversation_id, self.user_id)
            if autonomy_tier and autonomy_tier != row["autonomy_tier"]:
                self.manager.set_tier(conversation_id, self.user_id, autonomy_tier)
                row["autonomy_tier"] = autonomy_tier
        else:
            row = self.manager.create(self.user_id,
                                      autonomy_tier or "propose_only")
            conversation_id = str(row["id"])
        tier = row["autonomy_tier"]
        events.append(StreamEvent("conversation", {
            "conversation_id": str(conversation_id), "autonomy_tier": tier,
            "title": row.get("title") or ""}))

        user_message = {"role": "user", "content": message,
                        "created_at": _now_iso()}
        if page_context:
            user_message["page_context"] = page_context

        # -- confirmation round-trip: execute the previously proposed call --
        if confirm:
            outcome = self.dispatcher.dispatch(
                str(confirm.get("tool")), dict(confirm.get("params") or {}),
                tier=tier, confidence=1.0, confirmed=True)
            events.extend(self._outcome_events(outcome))
            assistant = self._assistant_message(outcome)
            self.manager.append_messages(conversation_id, self.user_id,
                                         [user_message, assistant],
                                         title_seed=message)
            events.append(StreamEvent("done", {"content": assistant["content"]}))
            return events

        intent = self._classify(message, page_context)

        if intent.name == _DEFERRED:
            text = DEFERRED_TEXT
            assistant = {"role": "assistant", "content": text,
                         "created_at": _now_iso()}
        elif intent.name == _WORKFLOW_STATUS:
            state = _last_workflow_state(row["messages"])
            text = (workflows.describe(state) if state
                    else "No workflow is in progress. Say e.g. \"onboard "
                         "<table>\" to start the onboarding walkthrough.")
            assistant = {"role": "assistant", "content": text,
                         "created_at": _now_iso()}
        elif intent.name is None or intent.confidence < CONFIDENCE_FLOOR:
            text = CLARIFICATION_TEXT
            assistant = {"role": "assistant", "content": text,
                         "created_at": _now_iso()}
        else:
            outcome = self.dispatcher.dispatch(
                intent.name, intent.params, tier=tier,
                confidence=intent.confidence)
            events.extend(self._outcome_events(outcome))
            assistant = self._assistant_message(outcome)
            _attach_workflow(assistant, outcome)
            text = assistant["content"]

        self.manager.append_messages(conversation_id, self.user_id,
                                     [user_message, assistant],
                                     title_seed=message)
        events.extend(StreamEvent("token", {"text": chunk})
                      for chunk in _chunks(text))
        events.append(StreamEvent("done", {"content": text,
                                           "conversation_id": str(conversation_id)}))
        return events

    def _outcome_events(self, outcome: DispatchOutcome) -> list[StreamEvent]:
        events = [StreamEvent("tool_call", {
            "tool": outcome.tool_name, "params": outcome.params,
            "requires_confirmation": outcome.requires_confirmation,
            "executed": outcome.executed, "autonomy": outcome.tier,
            "auto_executed": outcome.auto_executed})]
        if outcome.executed:
            events.append(StreamEvent("tool_result", {
                "tool": outcome.tool_name, "result": outcome.result}))
        elif outcome.error:
            events.append(StreamEvent("error", {"tool": outcome.tool_name,
                                                "detail": outcome.error}))
        return events

    def _assistant_message(self, outcome: DispatchOutcome) -> dict:
        tool_call = {"tool": outcome.tool_name, "params": outcome.params,
                     "requires_confirmation": outcome.requires_confirmation,
                     "autonomy": outcome.tier,
                     "auto_executed": outcome.auto_executed}
        if outcome.requires_confirmation:
            content = (f"I can run `{outcome.tool_name}` with "
                       f"{json.dumps(outcome.params)} — confirm to proceed."
                       + (" This action is destructive and always requires "
                          "confirmation." if outcome.destructive else ""))
            return {"role": "assistant", "content": content,
                    "tool_calls": [tool_call], "created_at": _now_iso()}
        if outcome.error:
            return {"role": "assistant",
                    "content": f"That did not work: {outcome.error}",
                    "tool_calls": [tool_call], "created_at": _now_iso()}
        return {"role": "assistant",
                "content": render_result(outcome.tool_name, outcome.result or {}),
                "tool_calls": [tool_call],
                "tool_results": [redact_row_values(outcome.result or {})],
                "created_at": _now_iso()}

    # -- async wrapper (§3.7) --------------------------------------------------
    async def converse(self, message: str, **kwargs):
        import asyncio
        events = await asyncio.to_thread(self.process_message, message, **kwargs)
        for event in events:
            yield event


def _chunks(text: str, size: int = TOKEN_CHUNK) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


# ---------------------------------------------------------------------------
# §6.4 workflow wiring: suggest the next step after a workflow-entry tool runs
# ---------------------------------------------------------------------------

def _attach_workflow(assistant: dict, outcome: DispatchOutcome) -> None:
    """After a successful workflow-entry tool execution, record the workflow
    state on the message and append the next-step suggestion to the reply."""
    if not outcome.executed:
        return
    if outcome.tool_name == "onboard_table":
        # onboard_table discovers + proposes in one call
        state = workflows.advance(workflows.advance(
            workflows.start("onboard"), "discover"), "propose")
    elif outcome.tool_name == "deploy_guidance":
        state = workflows.advance(workflows.start("deploy"), "check_coverage")
        if (outcome.result or {}).get("deploy_ready"):
            state = workflows.advance(state, "resolve_dilemmas")
    elif outcome.tool_name == "list_drift_reports":
        state = workflows.advance(workflows.start("drift"), "list_reports")
    elif outcome.tool_name == "swap_target_dry_run":
        state = workflows.advance(workflows.start("swap"), "dry_run")
        if not (outcome.result or {}).get("would_remap"):
            state = workflows.advance(state, "remap")   # nothing to re-map
    else:
        return
    assistant["workflow_state"] = state.to_dict()
    assistant["content"] = (assistant["content"].rstrip() + " " +
                            workflows.next_step_suggestion(state))


def _last_workflow_state(messages: list[dict]) -> workflows.WorkflowState | None:
    for message in reversed(messages):
        if message.get("workflow_state"):
            return workflows.WorkflowState.from_dict(message["workflow_state"])
    return None
