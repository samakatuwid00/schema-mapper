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
from ..services.operator_diagnostics import extract_job_id
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
_CHAT_HELP = "chat_help"
_INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (_CHAT_HELP, ("--help", "chat help", "help commands",
                  "what commands", "supported commands")),
    (_DEFERRED, ("restore a backup", "restore the target", "restore the source",
                 "recover from backup", "restore from backup")),
    (_WORKFLOW_STATUS, ("where are we", "workflow status", "current step",
                        "what step", "how far along")),
    ("reopen_mapping_review", ("reopen mapping review",
                               "send review back",
                               "back to review queue",
                               "return review",
                               "reopen review")),
    ("reject_mapping_review", ("reject field review", "reject review",
                               "reject mapping review")),
    ("reject_mapping", ("reject mapping", "remove mapping",
                        "reject and remove")),
    ("add_mapping", ("add mapping", "manual mapping", "map id to",
                     "map id ->", "maps to")),
    ("plan_refresh_failure_repair", ("plan_refresh_failure_repair",
                                     "plan refresh failure repair",
                                     "repair refresh failure",
                                     "fix refresh job", "fix this refresh",
                                     "how to fix this job",
                                     "how do i fix this refresh",
                                     "failed refresh")),
    ("repair_duplicate_key", ("repair duplicate key", "fix duplicate key",
                              "reconcile duplicate", "record the missing crosswalk")),
    ("diagnose_duplicate_key", ("duplicate key", "already exists",
                                "unique constraint")),
    ("explain_deploy_error", ("mapping cannot be deployed",
                              "required but no source column maps",
                              "deploy to target failed",
                              "deploying to target failed",
                              "no row in reference table")),
    ("diagnose_entity_delivery", ("diagnose_entity_delivery",
                                  "diagnose entity delivery",
                                  "diagnose target data",
                                  "no data in target", "has no data",
                                  "doesn't have data", "doesnt have data",
                                  "target db is empty", "paused status",
                                  "is paused", "deployed but no data")),
    ("inspect_job", ("inspect job", "job status", "worker job",
                     "worker & queues", "worker and queues", "refresh_all",
                     "failed job")),
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
_JOB_ID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
_ENTITY_RE = re.compile(
    r"\b(?:for|entity|table|refresh|deploy(?:ed|ing)?|onboard)\s+"
    r"([a-z_][a-z0-9_]*(?:\s+[a-z_][a-z0-9_]*)?)",
    re.I,
)
_LEADING_ENTITY_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*(?:\s+[a-z_][a-z0-9_]*)?)\s+"
    r"(?:is deployed|has no data|doesn't have|doesnt have|target db|is paused|duplicate key)",
    re.I,
)
_DIRECT_DIAGNOSE_ENTITY_RE = re.compile(
    r"\bdiagnose(?:_entity_delivery| entity delivery| target data)?\s+"
    r"([a-z_][a-z0-9_]*)\b",
    re.I,
)
_MAPPING_TARGET_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s*(?:->|to)\s*"
    r"([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b",
    re.I,
)
_REJECT_COLUMN_RE = re.compile(
    r"\breject(?:\s+mapping)?\s+([a-z_][a-z0-9_]*)\b", re.I)
_REVIEW_ID_RE = re.compile(r"\b(?:review|field review)\s*#?\s*(\d+)\b", re.I)


def _proposal_id(message: str, page_context: dict) -> int | None:
    match = _PROPOSAL_ID_RE.search(message)
    if match:
        return int(match.group(1))
    if page_context.get("proposal_id") is not None:
        return int(page_context["proposal_id"])
    return None


def _normalize_entity(value: str | None) -> str | None:
    if not value:
        return None
    entity = re.sub(r"\s+", "_", value.strip().lower())
    if entity in {"a", "the", "it", "this", "that", "target", "source",
                  "data", "status", "db", "fix", "repair", "diagnose"}:
        return None
    return entity


def _extract_entity(message: str, page_context: dict) -> str | None:
    if page_context.get("entity"):
        return str(page_context["entity"])
    for regex in (_DIRECT_DIAGNOSE_ENTITY_RE, _LEADING_ENTITY_RE, _ENTITY_RE):
        match = regex.search(message)
        entity = _normalize_entity(match.group(1) if match else None)
        if entity:
            return entity
    return None


def _extract_params(intent_name: str, message: str, page_context: dict) -> dict:
    params: dict = {}
    if intent_name in ("summarize_proposal", "explain_blocker", "deploy_guidance"):
        pid = _proposal_id(message, page_context)
        if pid is not None:
            params["proposal_id"] = pid
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
    if intent_name == "inspect_job":
        match = _JOB_ID_RE.search(message)
        if match:
            params["job_id"] = match.group(0)
        if page_context.get("target_system"):
            params["target_system"] = str(page_context["target_system"])
    if intent_name == "plan_refresh_failure_repair":
        match = _JOB_ID_RE.search(message)
        if match:
            params["job_id"] = match.group(0)
        if page_context.get("job_id"):
            params["job_id"] = str(page_context["job_id"])
        if page_context.get("target_system"):
            params["target_system"] = str(page_context["target_system"])
    if intent_name == "diagnose_entity_delivery":
        entity = _extract_entity(message, page_context)
        if entity:
            params["entity"] = entity
        if page_context.get("target_system"):
            params["target_system"] = str(page_context["target_system"])
    if intent_name == "explain_deploy_error":
        params["error"] = str(page_context.get("error") or message)
        entity = _extract_entity(message, page_context)
        if entity:
            params["entity"] = entity
        pid = _proposal_id(message, page_context)
        if pid is not None:
            params["proposal_id"] = pid
    if intent_name in ("diagnose_duplicate_key", "repair_duplicate_key"):
        params["error"] = str(page_context.get("error") or message)
        entity = _extract_entity(message, page_context)
        if entity:
            params["entity"] = entity
        try:
            from ..services.operator_diagnostics import parse_duplicate_key
            duplicate = parse_duplicate_key(params["error"]) or {}
        except Exception:
            duplicate = {}
        params.update({k: v for k, v in duplicate.items() if v})
        for key in ("target_table", "target_column", "target_id",
                    "target_system", "actor"):
            if page_context.get(key):
                params[key] = str(page_context[key])
    if intent_name == "add_mapping":
        pid = _proposal_id(message, page_context)
        if pid is not None:
            params["proposal_id"] = pid
        match = _MAPPING_TARGET_RE.search(message)
        if match:
            params["source_column"] = match.group(1)
            params["target_table"] = match.group(2)
            params["target_column"] = match.group(3)
        if page_context.get("actor"):
            params["actor"] = str(page_context["actor"])
    if intent_name in ("reject_mapping_review", "reopen_mapping_review"):
        review_match = _REVIEW_ID_RE.search(message)
        if review_match:
            params["review_id"] = int(review_match.group(1))
        if page_context.get("review_id"):
            params["review_id"] = int(page_context["review_id"])
        if page_context.get("actor"):
            params["actor"] = str(page_context["actor"])
    if intent_name == "reject_mapping":
        pid = _proposal_id(message, page_context)
        if pid is not None:
            params["proposal_id"] = pid
        match = _REJECT_COLUMN_RE.search(message)
        if match and match.group(1) not in {"mapping", "it", "this"}:
            params["source_column"] = match.group(1)
        elif page_context.get("source_column"):
            params["source_column"] = str(page_context["source_column"])
        if page_context.get("actor"):
            params["actor"] = str(page_context["actor"])
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
    lines = ["Integration status:"] + [f"- {p}" for p in parts]
    return "\n".join(lines)


def _fmt_summarize(result: dict) -> str:
    summary = result.get("summary") or "Proposal summary unavailable."
    mappings = result.get("accepted_mappings") or []
    if not mappings:
        return summary
    pieces = []
    for field in mappings[:20]:
        target_column = (
            field.get("resolved_target_column")
            or field.get("suggested_target_column")
            or "?"
        )
        target = f"{field.get('suggested_target_table')}.{target_column}"
        label = f"review {field.get('review_id')}" if field.get("review_id") else "review ?"
        pieces.append(f"{label}: {field.get('source_column')} -> {target}")
    lines = [summary, "Accepted mappings:"] + [f"- {p}" for p in pieces]
    if len(mappings) > 20:
        lines.append(f"(+{len(mappings) - 20} more)")
    return "\n".join(lines)


def _fmt_blocker(result: dict) -> str:
    if result.get("deploy_ready"):
        return "Nothing is blocking — this proposal is ready to deploy."
    blockers = result.get("blockers") or ["unknown blocker"]
    lines = ["Blocked:"] + [f"- {b}" for b in blockers]
    return "\n".join(lines)


def _fmt_schema(result: dict) -> str:
    def names(tree):
        tables = (tree or {}).get("tables") or []
        if isinstance(tables, dict):
            return sorted(tables)
        return [t.get("name", "?") if isinstance(t, dict) else str(t)
                for t in tables]
    source = names(result.get("source"))
    target = names(result.get("target"))
    lines = [f"Source: {len(source)} tables"]
    if source:
        lines.append("- " + ", ".join(source[:8]) + ("…" if len(source) > 8 else ""))
    lines.append(f"Target: {len(target)} tables")
    if target:
        lines.append("- " + ", ".join(target[:8]) + ("…" if len(target) > 8 else ""))
    return "\n".join(lines)


def _fmt_guidance(result: dict) -> str:
    actions = result.get("recommended_actions") or []
    status = "ready to deploy" if result.get("deploy_ready") else "not deploy-ready"
    lines = [f"This proposal is {status}."]
    if actions:
        lines.append("Next:")
        lines += [f"- {a}" for a in actions]
    return "\n".join(lines)


def _fmt_dilemma(result: dict) -> str:
    options = result.get("options") or []
    labels = [o.get("label", o.get("action", "?")) for o in options]
    lines = [f"Options for this {result.get('kind', 'dilemma')}:"]
    lines += [f"- {label}" for label in labels]
    lines.append(f"Recommended: {result.get('recommended', 'manual')}.")
    return "\n".join(lines)


def _fmt_onboard(result: dict) -> str:
    note = result.get("note") or ""
    proposal = result.get("proposal") or {}
    pid = proposal.get("proposal_id") or proposal.get("id")
    lead = (f"Created proposal {pid} for review." if pid
            else "Created a proposal for review.")
    lines = [lead]
    if note:
        lines.append(note)
    return "\n".join(lines)


def _fmt_heal(result: dict) -> str:
    lines = [f"Proposed heal: {result.get('action', '?')}",
             f"- detail: {json.dumps(result.get('detail') or {})}"]
    note = result.get("note")
    if note:
        lines.append(str(note))
    return "\n".join(lines)


def _fmt_drift_list(result: dict) -> str:
    count = result.get("count", 0)
    if not count:
        return "No drift reports recorded — schemas match their contracts."
    latest = (result.get("reports") or [{}])[0]
    impacted = latest.get("impacted_entities") or []
    lines = [
        f"{count} drift report(s).",
        f"- latest target: {latest.get('target_system', '?')}",
        f"- impacted entities: {', '.join(map(str, impacted)) or 'none'}",
        "Say \"resolve drift\" to re-map and re-deliver them "
        "(confirmation required).",
    ]
    return "\n".join(lines)


def _fmt_resolve_drift(result: dict) -> str:
    if result.get("dry_run"):
        return "Drift resolution dry-run complete — nothing was changed."
    return "Drift resolution ran. Full result attached."


def _fmt_swap_dry_run(result: dict) -> str:
    affected = result.get("would_remap") or []
    if not affected:
        return ("Swap preview: no deployed entities are affected by the "
                "target diff — an apply would only recreate and re-deliver.")
    lines = [
        f"Swap preview: {len(affected)} affected entities",
        "- " + ", ".join(map(str, affected)),
        "Applying will:",
        "- re-map affected entities (human-gated)",
        "- recreate the target",
        "- re-deliver",
        "Destructive — typed confirmation required.",
    ]
    return "\n".join(lines)


def _fmt_swap_apply(result: dict) -> str:
    status = result.get("status", "?")
    if status == "blocked_on_review":
        lines = ["Swap blocked on low-confidence re-mappings:"]
        lines += [f"- {b}" for b in (result.get("blocked") or [])]
        lines.append("Resolve them, or re-run with force.")
        return "\n".join(lines)
    return f"Target swap {status}."


def _fmt_inspect_job(result: dict) -> str:
    job_id = result.get("job_id", "?")
    job_type = result.get("job_type", "?")
    status = result.get("status", "?")
    lines = [f"Job {job_id} ({job_type}) is {status}."]
    if result.get("progress_total") is not None:
        lines.append(
            f"- progress: {result.get('progress_current', 0)}/{result.get('progress_total')}")
    error = result.get("error_message")
    if error:
        lines.append(f"- error: {error}")
    failures = result.get("failures") or []
    if failures:
        lines.append("Failures:")
        for f in failures[:3]:
            lines.append(f"- {f.get('entity') or '?'}: {f.get('error') or f.get('status')}")
    repair_text = _fmt_job_repair_handles(result.get("repair_plan") or {})
    if repair_text:
        lines.append(repair_text)
    return "\n".join(lines)


def _fmt_job_repair_handles(plan: dict) -> str:
    items = plan.get("items") or []
    if not items:
        return ""
    lines = ["Repair handles:"]
    for item in items[:3]:
        entity = item.get("entity") or "unknown"
        category = item.get("category") or "unknown"
        lines.append(f"- {entity} [{category}]")
        for review in _repair_reviews(item)[:3]:
            bits = []
            if review.get("proposal_id"):
                bits.append(f"proposal {review.get('proposal_id')}")
            if review.get("review_id"):
                bits.append(f"review {review.get('review_id')}")
            source = review.get("source_column") or "?"
            target = f"{review.get('target_table')}.{review.get('target_column')}"
            bits.append(f"{source} -> {target}")
            lines.append(f"  - {' '.join(bits)}")
        commands = [
            cmd for cmd in (
                _command_for_tool_call(call)
                for call in _repair_gated_calls(item)[:3]
            ) if cmd
        ]
        for cmd in commands:
            lines.append(f"  - command: {cmd}")
    return "\n".join(lines)


def _repair_reviews(item: dict) -> list[dict]:
    diagnostic = item.get("diagnostic") or {}
    reviews = []
    if diagnostic.get("mapping_review"):
        reviews.append(diagnostic["mapping_review"])
    reviews.extend(diagnostic.get("suspect_mapping_reviews") or [])
    duplicate_review = (item.get("diagnostic") or {}).get("mapping_review")
    if duplicate_review and duplicate_review not in reviews:
        reviews.append(duplicate_review)
    return [r for r in reviews if isinstance(r, dict)]


def _repair_gated_calls(item: dict) -> list[dict]:
    calls = item.get("gated_tools") or []
    if item.get("gated_tool"):
        calls = [item["gated_tool"], *calls]
    return [c for c in calls if isinstance(c, dict)]


def _command_for_tool_call(call: dict) -> str | None:
    tool = call.get("tool")
    params = call.get("params") or {}
    if tool == "reopen_mapping_review" and params.get("review_id"):
        return f"`reopen mapping review {params['review_id']}`"
    if tool == "reject_mapping_review" and params.get("review_id"):
        return f"`reject mapping review {params['review_id']}`"
    if tool == "repair_duplicate_key":
        return "`repair duplicate key ...`"
    return f"`{tool}`" if tool else None


def _fmt_diagnose_entity(result: dict) -> str:
    entity = result.get("entity", "?")
    status = result.get("entity_status", "?")
    counts = result.get("target_counts") or []
    if counts:
        count_text = ", ".join(
            f"{c.get('table')}: {c.get('rows') if 'rows' in c else c.get('error', '?')}"
            for c in counts
        )
    else:
        count_text = "no target tables recorded"
    lines = [f"{entity} is {status}.", f"- target counts: {count_text}"]
    latest_job = result.get("latest_job") or {}
    if latest_job:
        lines.append(f"- latest job: {latest_job.get('job_type')} {latest_job.get('status')}")
    recs = result.get("recommendations") or []
    if recs:
        lines.append("Next:")
        lines += [f"- {r}" for r in recs]
    else:
        lines.append("Next: no recommendation.")
    return "\n".join(lines)


def _fmt_deploy_error(result: dict) -> str:
    missing = result.get("missing_required") or []
    if missing:
        lines = ["Deploy is blocked — required target columns are unmapped:"]
        lines += [f"- {m.get('target_table')}.{m.get('target_column')}" for m in missing]
    else:
        lines = [result.get("summary") or "Deploy error parsed."]
    actions = result.get("suggested_actions") or []
    if actions:
        lines.append("Suggested:")
        for action in actions[:4]:
            if action.get("action") == "add_mapping":
                src = action.get("source_column") or "<source_column>"
                lines.append(
                    f"- add `{src} -> {action.get('target_table')}.{action.get('target_column')}`")
            else:
                lines.append(f"- {action.get('reason') or action.get('action')}")
    return "\n".join(lines)


def _fmt_resolve_deploy_job_repair(result: dict) -> str:
    job_id = result.get("job_id")
    proposal_id = result.get("proposal_id")
    lines: list[str] = []
    if job_id:
        lines.append(f"Job {job_id}.")
    if proposal_id:
        lines.append(f"Proposal {proposal_id} recovered from the job.")
    else:
        lines.append("Could not recover a proposal id from that job — reply "
                     "with a proposal id, or open the job in the job drawer.")
    missing = result.get("missing_required") or []
    if missing:
        lines.append("Missing required target columns:")
        lines += [f"- {m.get('target_table')}.{m.get('target_column')}"
                  for m in missing]
    drafts = result.get("draft_mappings") or []
    if drafts:
        lines.append(f"Draft repair ({len(drafts)} mapping(s), confirmation required):")
        lines += [f"- {d.get('source_column')} -> "
                  f"{d.get('target_table')}.{d.get('target_column')}"
                  for d in drafts]
    manual = result.get("manual_required") or []
    if manual:
        lines.append("Needs a manual source-column choice:")
        lines += [f"- {m.get('target_table')}.{m.get('target_column')}"
                  for m in manual]
    if proposal_id:
        lines.append(f"Open the proposal: /mappings/{proposal_id}")
        lines.append("Approved proposals can leave the review queue but "
                     "stay reachable by URL.")
    lines.append("After repair: review, approve, then redeploy — nothing "
                 "redeploys automatically.")
    return "\n".join(lines)


def _fmt_diagnose_duplicate(result: dict) -> str:
    target = f"{result.get('target_table')}.{result.get('target_column')}"
    if result.get("safe_to_repair"):
        lines = [
            f"Duplicate-key repair is safe to prepare for {result.get('entity')}.",
            f"- {target}={result.get('target_id')} exists, not claimed by another crosswalk row.",
            "Confirm `repair_duplicate_key` to record the missing crosswalk, "
            "then retry refresh for this entity.",
        ]
        return "\n".join(lines)
    reasons = result.get("reasons") or ["manual review required"]
    lines = [f"Duplicate-key repair is not safe to auto-prepare for {result.get('entity')}:"]
    lines += [f"- {r}" for r in reasons]
    return "\n".join(lines)


def _fmt_refresh_repair_plan(result: dict) -> str:
    items = result.get("items") or []
    if not items:
        return result.get("summary") or "No refresh failures to repair."
    lines = [result.get("summary") or f"{len(items)} repair item(s)."]
    for item in items[:5]:
        entity = item.get("entity") or "unknown"
        category = item.get("category") or "unknown"
        diagnosis = item.get("diagnosis") or "inspect manually"
        lines.append(f"- {entity} [{category}]: {diagnosis}")
        gated = item.get("gated_tools") or ([] if not item.get("gated_tool") else [item["gated_tool"]])
        if gated:
            for call in gated[:3]:
                params = call.get("params") or {}
                if params:
                    detail = ", ".join(f"{k}={v}" for k, v in params.items())
                    lines.append(f"  - gated action: `{call.get('tool')}` ({detail})")
                else:
                    lines.append(f"  - gated action: `{call.get('tool')}`")
        else:
            for step in (item.get("next_steps") or [])[:2]:
                lines.append(f"  - next: {step}")
    return "\n".join(lines)


def _fmt_repair_duplicate(result: dict) -> str:
    lines = [
        f"Recorded duplicate-key crosswalk repair for {result.get('entity')}.",
        f"- {result.get('target_table')}.{result.get('target_column')}={result.get('target_id')}",
        f"Next: {result.get('next_step', 'retry refresh')}.",
    ]
    return "\n".join(lines)


def _fmt_add_mapping(result: dict) -> str:
    lines = [
        f"Added mapping on proposal {result.get('proposal_id')}.",
        f"- {result.get('source_column')} -> "
        f"{result.get('target_table')}.{result.get('target_column')}",
        f"Proposal is {result.get('proposal_status', '?')}.",
    ]
    return "\n".join(lines)


def _fmt_add_missing_mappings(result: dict) -> str:
    added = result.get("added") or []
    skipped = result.get("skipped") or []
    lines = [f"Updated missing mappings on proposal {result.get('proposal_id')}."]
    if added:
        lines.append("Added:")
        lines += [
            f"- {m.get('source_column')} -> {m.get('target_table')}.{m.get('target_column')}"
            for m in added[:12]
        ]
    if skipped:
        lines.append("Skipped:")
        lines += [
            f"- {m.get('target_table')}.{m.get('target_column')}: {m.get('reason')}"
            for m in skipped[:8]
        ]
    lines.append(f"Proposal is {result.get('proposal_status', '?')}.")
    if result.get("next_step"):
        lines.append(f"Next: {result.get('next_step')}.")
    return "\n".join(lines)


def _fmt_reject_mapping(result: dict) -> str:
    lines = [
        f"Rejected `{result.get('source_column')}` on proposal {result.get('proposal_id')}.",
        f"Proposal is {result.get('proposal_status', '?')}.",
    ]
    return "\n".join(lines)


def _fmt_reject_mapping_review(result: dict) -> str:
    lines = [
        f"Rejected review {result.get('review_id')} on proposal {result.get('proposal_id')}.",
        f"- {result.get('source_column')} -> "
        f"{result.get('target_table')}.{result.get('target_column')}",
        f"Proposal is {result.get('proposal_status', '?')}.",
    ]
    return "\n".join(lines)


def _fmt_reopen_mapping_review(result: dict) -> str:
    lines = [
        f"Reopened review {result.get('review_id')} in the review queue.",
        f"- {result.get('source_column')} -> "
        f"{result.get('target_table')}.{result.get('target_column')}",
        f"On proposal {result.get('proposal_id')}, status: "
        f"{result.get('proposal_status', '?')}.",
    ]
    return "\n".join(lines)


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
    "inspect_job": _fmt_inspect_job,
    "diagnose_entity_delivery": _fmt_diagnose_entity,
    "explain_deploy_error": _fmt_deploy_error,
    "resolve_deploy_job_repair": _fmt_resolve_deploy_job_repair,
    "diagnose_duplicate_key": _fmt_diagnose_duplicate,
    "plan_refresh_failure_repair": _fmt_refresh_repair_plan,
    "repair_duplicate_key": _fmt_repair_duplicate,
    "add_mapping": _fmt_add_mapping,
    "add_missing_mappings": _fmt_add_missing_mappings,
    "reject_mapping": _fmt_reject_mapping,
    "reject_mapping_review": _fmt_reject_mapping_review,
    "reopen_mapping_review": _fmt_reopen_mapping_review,
}

CLARIFICATION_TEXT = "\n".join([
    "I did not catch a supported request. I can:",
    "- check integration status",
    "- summarize a proposal",
    "- explain what blocks a deploy",
    "- show the schemas",
    "- give deploy guidance",
    "- explain a mapping dilemma",
    "- onboard a table (propose-only)",
    "- inspect worker jobs",
    "- diagnose deployed target data",
    "- explain deploy errors",
    "- plan refresh failure repairs",
    "- diagnose or repair duplicate-key crosswalks",
    "- prepare mapping repairs",
    "- list or resolve schema drift",
    "- preview or apply a schema swap",
    "- propose an error heal",
    "",
    "Try e.g. \"what's blocking proposal 42?\"",
])

CHAT_HELP_TEXT = """Chat commands:
- `--help` — this list.
- `inspect job <job_id>` — worker status, failures, repair handles.
- `plan_refresh_failure_repair for job <job_id>` — build repair checklist.
- `summarize proposal <proposal_id>` — accepted mappings + review ids.
- `diagnose_entity_delivery <entity>` — deployed status + target row counts.
- `reopen mapping review <review_id>` — bad mapping back to Review Queue.
- `reject mapping review <review_id>` — reject one bad mapping row.
- `add mapping <source_column> to <target_table>.<target_column> proposal <proposal_id>` — manual mapping.
- `explain deploy error <error text>` — parse missing required mappings.
- `diagnose duplicate key for <entity>: <error text>` — safe to repair?
- `repair duplicate key ...` — gated crosswalk repair (if diagnostic says safe).
- `onboard <source_table>` — create proposal. Deploys nothing.
- `show schema`, `check status`, `list drift reports`, `schema swap` — read-only guidance.

Tip: type `/` in the box to pick a command."""

DEFERRED_TEXT = "\n".join([
    "Chat-guided backup recovery is not available. Restores stay on:",
    "- the Recovery page (Maintain group)",
    "- `scripts/recover.py`",
    "Both are behind the same typed-confirmation gates.",
])


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

    def list_for_user(self, user_id: int, query: str | None = None) -> list[dict]:
        """Title/redacted-message search (D4): searches persisted, already-
        redacted conversation content only — never raw source/target rows,
        which never reach persistence in the first place (D9)."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if query:
                    like = f"%{query}%"
                    cur.execute("""
                        SELECT id, title, autonomy_tier, created_at, updated_at,
                               jsonb_array_length(messages) AS message_count
                        FROM integration.agent_conversation
                        WHERE user_id = %s
                          AND (title ILIKE %s OR messages::text ILIKE %s)
                        ORDER BY updated_at DESC
                    """, (user_id, like, like))
                else:
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

    def bulk_delete(self, user_id: int, conversation_ids: list[str]) -> int:
        """Delete every id owned by `user_id`; ids that are foreign or do not
        exist are silently ignored so their existence is never revealed (D4)."""
        ids = [str(i) for i in conversation_ids]
        if not ids:
            return 0
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM integration.agent_conversation
                    WHERE user_id = %s AND id = ANY(%s::uuid[])
                """, (user_id, ids))
                deleted = cur.rowcount
            conn.commit()
        return deleted

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
_CONFIRM_WORD_RE = re.compile(
    r"\b(yes|yep|ok|okay|confirm|confirmed|approve|approved|done|proceed|"
    r"go ahead|run it|do it)\b",
    re.I,
)
_HELP_RE = re.compile(
    r"^\s*(--help|help|chat help|help commands|what commands.*)\s*$",
    re.I,
)
_DEPLOY_JOB_TEXT_RE = re.compile(
    r"deploy_lrmis|mapping cannot be deployed|"
    r"is required but no source column maps to it",
    re.I,
)


def _pending_confirmation(messages: list[dict]) -> dict | None:
    """Return the most recent still-gated tool call from assistant history."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        for call in reversed(message.get("tool_calls") or []):
            if call.get("requires_confirmation"):
                return {"tool": call.get("tool"), "params": call.get("params") or {}}
        if message.get("tool_results"):
            return None
    return None


def _looks_like_confirmation(message: str) -> bool:
    return bool(_CONFIRM_WORD_RE.search(message or ""))


def _looks_like_help(message: str) -> bool:
    return bool(_HELP_RE.search(message or ""))


def _looks_like_pasted_deploy_job(message: str) -> bool:
    """A pasted failed-job message (design D1): has a job UUID plus a deploy
    job-type or deploy-error marker, so it routes straight to
    `resolve_deploy_job_repair` instead of the substring intent classifier."""
    return bool(extract_job_id(message)
                and _DEPLOY_JOB_TEXT_RE.search(message or ""))


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

        if _looks_like_help(message):
            text = CHAT_HELP_TEXT
            assistant = {"role": "assistant", "content": text,
                         "created_at": _now_iso()}
            self.manager.append_messages(conversation_id, self.user_id,
                                         [user_message, assistant],
                                         title_seed=message)
            events.extend(StreamEvent("token", {"text": chunk})
                          for chunk in _chunks(text))
            events.append(StreamEvent("done", {"content": text,
                                               "conversation_id": str(conversation_id)}))
            return events

        # -- confirmation round-trip: execute the previously proposed call --
        # The UI normally sends an explicit confirm payload, but operators also
        # type "done" / "approve" naturally after a prompt. Reuse the saved
        # pending params so the table/proposal name is not lost.
        confirm = confirm or (
            _pending_confirmation(row["messages"])
            if _looks_like_confirmation(message)
            else None
        )
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

        if _looks_like_pasted_deploy_job(message):
            outcome = self.dispatcher.dispatch(
                "resolve_deploy_job_repair", {"text": message},
                tier=tier, confidence=1.0)
            events.extend(self._outcome_events(outcome))
            assistant = self._assistant_message(outcome)
            self.manager.append_messages(conversation_id, self.user_id,
                                         [user_message, assistant],
                                         title_seed=message)
            events.append(StreamEvent("done", {"content": assistant["content"],
                                               "conversation_id": str(conversation_id)}))
            return events

        intent = self._classify(message, page_context)

        if intent.name == _CHAT_HELP:
            text = CHAT_HELP_TEXT
            assistant = {"role": "assistant", "content": text,
                         "created_at": _now_iso()}
        elif intent.name == _DEFERRED:
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
