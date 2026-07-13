"""
Calls Google Gemini to propose a field-to-field mapping between one of your
central tables and the closest matching table(s) in the target schema.

This is the piece that makes the whole system "dynamic": it never
hardcodes a target system's column names. Feed it a different Schema
object and it produces a different mapping, with no code changes.
"""
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from dotenv import load_dotenv
from .schema_models import Table, Schema

# CLI scripts do not automatically inherit values from the project .env file.
# Load it without overriding variables explicitly set by the process/container.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

try:
    from google import genai
except ImportError:
    genai = None

# Stable default; override with GEMINI_MODEL without changing application code.
DEFAULT_MODEL = "gemini-2.5-flash"


@dataclass
class FieldMapping:
    source_column: str
    target_table: str | None
    target_column: str | None
    confidence: float          # 0.0-1.0
    transform: str             # e.g. "none", "cast:date->datetime", "enum_remap:{...}"
    reasoning: str


PROMPT_TEMPLATE = """You are mapping fields between two database schemas for a data migration.

SOURCE TABLE (ours): {source_table_name}
Columns:
{source_columns}

CANDIDATE TARGET TABLES (theirs):
{target_tables}

For EACH source column, propose the single best matching target table + column,
a confidence score from 0.0 to 1.0, any transform needed (type cast, date format,
enum value remap, unit conversion), and a one-sentence reason.

If no reasonable match exists for a column, set target_table and target_column to null
and confidence to 0.0.

Use only these transform forms: none, trim, cast:date->datetime,
cast:str->int, cast:int->str, or enum_remap:<JSON object>.
"""


MAPPING_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "source_column": {"type": "string"},
            "target_table": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "target_column": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "transform": {"type": "string"},
            "reasoning": {"type": "string"},
        },
        "required": ["source_column", "target_table", "target_column",
                     "confidence", "transform", "reasoning"],
        "additionalProperties": False,
    },
}


def _format_columns(table: Table) -> str:
    # Schema-only (§1.2): the prompt carries column names, types, and nullability
    # — never sample row values. This system holds DepEd PII and may run on a
    # free AI tier that trains on submissions, so row data must never be sent.
    # Each column is annotated with its engine-independent GenericType (§3.5), so
    # matching does not hinge on native type spelling (varchar vs character varying).
    from .dialect import native_to_generic_any
    lines = []
    for c in table.columns:
        desc = f" -- {c.description}" if c.description else ""
        generic = native_to_generic_any(c.data_type).value
        lines.append(f"  - {c.name}: {c.data_type} [{generic}]"
                     f"{' NOT NULL' if not c.nullable else ''}{desc}")
    return "\n".join(lines)


def _format_target_tables(schema: Schema) -> str:
    blocks = []
    for t in schema.tables:
        blocks.append(f"Table: {t.name}\n{_format_columns(t)}")
    return "\n\n".join(blocks)


def _coerce(item: dict) -> dict:
    """Normalise one raw mapping object so it survives a provider that omits a
    field or returns a stringy confidence."""
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "source_column": item.get("source_column"),
        "target_table": item.get("target_table"),
        "target_column": item.get("target_column"),
        "confidence": confidence,
        "transform": item.get("transform") or "none",
        "reasoning": item.get("reasoning") or "",
    }


def _extract_array(content: str) -> list[dict]:
    """Pull the mapping array out of a raw LLM text response, tolerating markdown
    fences and an object wrapper (`{"mappings": [...]}`)."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        text = text.removeprefix("json").strip()
    data = json.loads(text)
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                return value
        return []
    return data


def _propose_gemini(prompt: str, client=None) -> list[dict]:
    if genai is None:
        raise RuntimeError("google-genai not installed")
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key and client is None:
        raise RuntimeError("no Gemini API key")
    if client is None:
        client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": MAPPING_RESPONSE_SCHEMA,
            "temperature": 0.1,
        },
    )
    if not response.text:
        raise RuntimeError("Gemini returned no content")
    return json.loads(response.text)


def _call_openai_compatible(prompt: str, *, base_url: str, api_key: str, model: str,
                            retries: int = 3) -> list[dict]:
    """One OpenAI-compatible /chat/completions call returning the mapping array.

    Retries on 429 (free-tier rate limits) with a backoff that honours the
    Retry-After header, so a bulk run paces itself instead of failing."""
    import time
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system",
             "content": "You output ONLY a JSON object of the form "
                        '{"mappings": [ ... ]}, no prose, no markdown.'},
            {"role": "user",
             "content": prompt + '\n\nReturn {"mappings": [ objects with keys '
                        "source_column, target_table, target_column, confidence, "
                        "transform, reasoning ]}."},
        ],
    }).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # A real User-Agent — the default "Python-urllib/*" is blocked (403) by
        # the Cloudflare front on Groq/Cerebras/etc.
        "User-Agent": "schema-mapper/1.0",
        "Accept": "application/json",
    }
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                out = json.loads(resp.read().decode())
            return _extract_array(out["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                try:
                    wait = float(exc.headers.get("Retry-After"))
                except (TypeError, ValueError):
                    wait = 8.0 * (attempt + 1)
                time.sleep(min(wait, 30))
                continue
            raise


# Known OpenAI-compatible providers -> (base_url, default_model). Enable one by
# setting <NAME>_API_KEY; base_url/model are overridable per provider.
_PROVIDER_DEFAULTS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
    "fallback": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
}


def _make_provider(name: str, client=None):
    """A callable(prompt)->list[dict] for a provider name, or None when it is not
    configured (so it is simply skipped in the order)."""
    name = name.strip().lower()
    if name == "gemini":
        if client is None and not (os.environ.get("GOOGLE_API_KEY")
                                   or os.environ.get("GEMINI_API_KEY")):
            return None
        return lambda prompt: _propose_gemini(prompt, client)

    env = name.upper()
    api_key = os.environ.get(f"{env}_API_KEY")
    if name == "fallback":
        api_key = api_key or os.environ.get("FALLBACK_LLM_API_KEY")
    if not api_key:
        return None
    default_base, default_model = _PROVIDER_DEFAULTS.get(name, (None, None))
    base_url = (os.environ.get(f"{env}_BASE_URL")
                or (os.environ.get("FALLBACK_LLM_BASE_URL") if name == "fallback" else None)
                or default_base or "https://api.groq.com/openai/v1")
    model = (os.environ.get(f"{env}_MODEL")
             or (os.environ.get("FALLBACK_LLM_MODEL") if name == "fallback" else None)
             or default_model or "llama-3.3-70b-versatile")
    return lambda prompt: _call_openai_compatible(prompt, base_url=base_url,
                                                  api_key=api_key, model=model)


def _norm(name: str) -> str:
    return name.lower().replace("_", "").replace(" ", "")


def heuristic_mapping(source_table: Table, target_schema: Schema) -> list[FieldMapping]:
    """Deterministic name-similarity mapping — the offline / quota-exhausted
    fallback (§1.4). No API call, no row data. Every mapping is LOW confidence
    (< the 0.7 gate) so the agent flags heuristic guesses for human review
    rather than treating them as confident."""
    targets = [(t.name, c.name, _norm(c.name))
               for t in target_schema.tables for c in t.columns]
    out: list[FieldMapping] = []
    for col in source_table.columns:
        n = _norm(col.name)
        exact = next(((tn, cn) for tn, cn, ncn in targets if ncn == n), None)
        near = exact or next(((tn, cn) for tn, cn, ncn in targets
                              if n and (n in ncn or ncn in n)), None)
        if near:
            tn, cn = near
            conf = 0.6 if exact else 0.5
            out.append(FieldMapping(col.name, tn, cn, conf, "none", "heuristic name match"))
        else:
            out.append(FieldMapping(col.name, None, None, 0.0, "none", "no heuristic match"))
    return out


def _cache_key(source_table: Table, target_schema: Schema):
    src = (source_table.name, tuple((c.name, c.data_type) for c in source_table.columns))
    tgt = tuple((t.name, tuple((c.name, c.data_type) for c in t.columns))
                for t in target_schema.tables)
    return hash((src, tgt))


def propose_mapping(source_table: Table, target_schema: Schema, client=None,
                    cache: dict | None = None) -> list[FieldMapping]:
    """One FieldMapping per source column.

    Tries the providers named in LLM_PROVIDER_ORDER (default "gemini,fallback"),
    skipping unconfigured ones and failing over to the next on any error — so a
    quota-exhausted or down provider is handled transparently. Providers:
    `gemini`, or any OpenAI-compatible one (`groq`, `cerebras`, `openrouter`,
    `mistral`, generic `fallback`) enabled via `<NAME>_API_KEY`, plus the special
    terminal `heuristic` provider (§1.4) — a deterministic, no-API fallback you
    can add to the order (e.g. `gemini,fallback,heuristic`) so a run never dies
    on a quota wall, or use alone (`LLM_PROVIDER_ORDER=heuristic`) to run offline.

    Pass a `cache` dict (§1.3) to skip the API when the source+target schema is
    unchanged.
    """
    key = None
    if cache is not None:
        key = _cache_key(source_table, target_schema)
        if key in cache:
            return cache[key]

    prompt = PROMPT_TEMPLATE.format(
        source_table_name=source_table.name,
        source_columns=_format_columns(source_table),
        target_tables=_format_target_tables(target_schema),
    )
    order = [n for n in os.environ.get("LLM_PROVIDER_ORDER", "gemini,fallback").split(",") if n.strip()]
    errors, tried = [], 0
    result: list[FieldMapping] | None = None
    for name in order:
        if name.strip().lower() == "heuristic":
            result = heuristic_mapping(source_table, target_schema)
            break
        provider = _make_provider(name, client)
        if provider is None:
            continue
        tried += 1
        try:
            result = [FieldMapping(**_coerce(item)) for item in provider(prompt)]
            break
        except Exception as exc:
            errors.append(f"{name.strip()}: {exc}")

    if result is None:
        if tried == 0:
            raise RuntimeError(
                "no LLM provider configured — set LLM_PROVIDER_ORDER and the "
                "matching <NAME>_API_KEY (e.g. GROQ_API_KEY), or add 'heuristic' "
                "to LLM_PROVIDER_ORDER to map offline without an API key")
        raise RuntimeError("all mapping providers failed — " + " | ".join(errors))

    if cache is not None:
        cache[key] = result
    return result


def mapping_to_dicts(mappings: list[FieldMapping]) -> list[dict]:
    return [asdict(m) for m in mappings]
