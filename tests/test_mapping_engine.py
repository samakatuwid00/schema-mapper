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
    client = _Client()
    source = Table("authors", [Column("author_name", "string")])
    target = Schema("LRMIS", [Table("author", [Column("name", "string")])])

    mappings = propose_mapping(source, target, client=client)

    assert mappings[0].target_column == "name"
    assert client.models.call["model"] == "test-gemini-model"
    assert client.models.call["config"]["response_mime_type"] == "application/json"
    assert client.models.call["config"]["response_json_schema"]["type"] == "array"
