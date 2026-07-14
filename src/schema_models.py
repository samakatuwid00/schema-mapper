"""
System-agnostic representation of a database schema.

Both YOUR central schema and ANY target system's schema get normalized
into these same structures. Nothing downstream needs to know or care
whose schema it originally was.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class Column:
    name: str
    data_type: str                 # normalized: string, integer, float, boolean, date, datetime, enum, uuid, text, json
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: Optional[str] = None   # "table.column" if FK
    enum_values: Optional[list[str]] = None
    description: str = ""          # comment/doc string if available, helps the AI mapper
    sample_values: list = field(default_factory=list)


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def get_column(self, name: str) -> Optional[Column]:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Schema:
    system_name: str
    tables: list[Table] = field(default_factory=list)
    version: str = "1"

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def get_table(self, name: str) -> Optional[Table]:
        return next((t for t in self.tables if t.name == name), None)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, path: str = None) -> str:
        s = json.dumps(self.to_dict(), indent=2)
        if path:
            with open(path, "w") as f:
                f.write(s)
        return s

    @staticmethod
    def from_dict(d: dict) -> "Schema":
        tables = []
        for t in d.get("tables", []):
            cols = [Column(**c) for c in t.get("columns", [])]
            tables.append(Table(name=t["name"], columns=cols))
        return Schema(system_name=d["system_name"], tables=tables, version=d.get("version", "1"))

    @staticmethod
    def from_json(path: str) -> "Schema":
        with open(path) as f:
            return Schema.from_dict(json.load(f))
