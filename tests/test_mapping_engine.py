import json

from src.mapping_engine import propose_mapping
from src.schema_models import Column, Schema, Table


class _Response:
    text = json.dumps([{
        "source_column": "author_name",
        "target_table": "author",
        "target_column": "name",
        "confidence": 0.97,
        "transform": "trim",
        "reasoning": "Both fields contain the author name.",
    }])


class _Models:
    def __init__(self):
        self.call = None

    def generate_content(self, **kwargs):
        self.call = kwargs
        return _Response()


class _Client:
    def __init__(self):
        self.models = _Models()


def test_gemini_mapping_uses_structured_json(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "test-gemini-model")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,fallback")
    client = _Client()
    source = Table("authors", [Column("author_name", "string")])
    target = Schema("LRMIS", [Table("author", [Column("name", "string")])])

    mappings = propose_mapping(source, target, client=client)

    assert mappings[0].target_column == "name"
    assert client.models.call["model"] == "test-gemini-model"
    assert client.models.call["config"]["response_mime_type"] == "application/json"
    assert client.models.call["config"]["response_json_schema"]["type"] == "array"


def test_extract_array_tolerates_fences_and_object_wrapper():
    from src.mapping_engine import _extract_array
    assert _extract_array('```json\n{"mappings": [{"source_column": "a"}]}\n```') == [
        {"source_column": "a"}]
    assert _extract_array('[{"source_column": "b"}]') == [{"source_column": "b"}]


def test_fails_over_to_next_provider(monkeypatch):
    import src.mapping_engine as ME
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,groq")
    monkeypatch.setenv("GEMINI_API_KEY", "g")     # so gemini is attempted (then fails)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")   # so groq is configured

    def _boom(prompt, client=None):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(ME, "_propose_gemini", _boom)
    monkeypatch.setattr(ME, "_call_openai_compatible", lambda prompt, **kw: [{
        "source_column": "x", "target_table": "user", "target_column": "id",
        "confidence": 0.9, "transform": "none", "reasoning": "r",
    }])

    source = Table("users", [Column("x", "string")])
    target = Schema("LRMIS", [Table("user", [Column("id", "string")])])
    mappings = propose_mapping(source, target)  # gemini 429 -> groq handles it

    assert mappings[0].target_table == "user"
    assert mappings[0].target_column == "id"


def test_no_provider_configured_raises(monkeypatch):
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
                "CEREBRAS_API_KEY", "FALLBACK_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,groq")
    import pytest
    source = Table("users", [Column("x", "string")])
    target = Schema("LRMIS", [Table("user", [Column("id", "string")])])
    with pytest.raises(RuntimeError, match="no LLM provider configured"):
        propose_mapping(source, target)
