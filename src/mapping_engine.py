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
    lines = []
    for c in table.columns:
        desc = f" -- {c.description}" if c.description else ""
        samples = f" (e.g. {c.sample_values[:3]})" if c.sample_values else ""
        lines.append(f"  - {c.name}: {c.data_type}{' NOT NULL' if not c.nullable else ''}{desc}{samples}")
    return "\n".join(lines)


def _format_target_tables(schema: Schema) -> str:
    blocks = []
    for t in schema.tables:
        blocks.append(f"Table: {t.name}\n{_format_columns(t)}")
    return "\n\n".join(blocks)


def propose_mapping(source_table: Table, target_schema: Schema, client=None) -> list[FieldMapping]:
    """
    Returns one FieldMapping per source column. Requires GEMINI_API_KEY (or
    GOOGLE_API_KEY) unless a pre-configured client is passed in for testing.
    """
    if genai is None:
        raise RuntimeError("pip install google-genai")

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key and client is None:
        raise RuntimeError("Set GOOGLE_API_KEY or GEMINI_API_KEY environment variable")

    if client is None:
        client = genai.Client(api_key=api_key)

    prompt = PROMPT_TEMPLATE.format(
        source_table_name=source_table.name,
        source_columns=_format_columns(source_table),
        target_tables=_format_target_tables(target_schema),
    )

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
        raise RuntimeError("Gemini returned no mapping content")
    raw = json.loads(response.text)
    return [FieldMapping(**item) for item in raw]


def mapping_to_dicts(mappings: list[FieldMapping]) -> list[dict]:
    return [asdict(m) for m in mappings]
