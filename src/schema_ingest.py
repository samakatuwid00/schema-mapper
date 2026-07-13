"""
Turns whatever the other system gives you (a CREATE TABLE dump, a JSON
export from their DB, or later a live information_schema query) into the
same normalized Schema object. This is the ONLY place that needs to
change when your ingestion source changes -- everything downstream
(mapping engine, transform engine) only ever sees a Schema object.
"""
import re
import hashlib
import json
from .schema_models import Schema, Table, Column

_TYPE_MAP = {
    "varchar": "string", "char": "string", "text": "text", "nvarchar": "string",
    "int": "integer", "integer": "integer", "bigint": "integer", "smallint": "integer",
    "decimal": "float", "numeric": "float", "float": "float", "double": "float", "real": "float",
    "bool": "boolean", "boolean": "boolean", "bit": "boolean",
    "date": "date", "datetime": "datetime", "timestamp": "datetime", "time": "datetime",
    "uuid": "uuid", "uniqueidentifier": "uuid",
    "json": "json", "jsonb": "json",
    "enum": "enum",
}


def _normalize_type(raw_type: str) -> str:
    raw = raw_type.strip().lower()
    base = re.split(r"[\s(]", raw, maxsplit=1)[0]
    return _TYPE_MAP.get(base, "string")


def parse_ddl(ddl_text: str, system_name: str) -> Schema:
    """
    Lightweight parser for a static DDL dump, e.g.:

        CREATE TABLE customers (
            cust_id INT PRIMARY KEY,
            cust_nm VARCHAR(120) NOT NULL,
            email_addr VARCHAR(255),
            created DATETIME
        );

    This intentionally handles the common 80% case. For messier or
    vendor-specific DDL (T-SQL, PL/SQL quirks), swap this out for
    sqlglot (`pip install sqlglot`) and feed its parsed AST into the
    same Column/Table/Schema builders below -- the rest of the
    pipeline doesn't change.
    """
    tables = []
    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF NOT EXISTS\s+)?[\[`\"]?(\w+)[\]`\"]?\s*\((.*?)\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )
    for match in table_pattern.finditer(ddl_text):
        table_name = match.group(1)
        body = match.group(2)
        columns = []
        # split on commas that aren't inside parens (e.g. VARCHAR(120))
        depth = 0
        parts, current = [], ""
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current)
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current)

        for part in parts:
            part = part.strip()
            if not part or re.match(r"^(PRIMARY|FOREIGN|UNIQUE|CONSTRAINT|KEY)\b", part, re.IGNORECASE):
                continue
            col_match = re.match(r"[\[`\"]?(\w+)[\]`\"]?\s+([A-Za-z]+(?:\([^)]*\))?)(.*)", part)
            if not col_match:
                continue
            name, raw_type, rest = col_match.groups()
            nullable = "NOT NULL" not in rest.upper()
            is_pk = "PRIMARY KEY" in rest.upper()
            columns.append(Column(
                name=name,
                data_type=_normalize_type(raw_type),
                nullable=nullable,
                is_primary_key=is_pk,
            ))
        tables.append(Table(name=table_name, columns=columns))

    return Schema(system_name=system_name, tables=tables)


def from_json_export(json_dict: dict, system_name: str) -> Schema:
    """
    Use this when the other system hands you a structured export instead
    of raw DDL, e.g. {"customers": [{"name": "cust_id", "type": "int", ...}]}
    """
    tables = []
    for table_name, cols in json_dict.items():
        columns = [
            Column(
                name=c["name"],
                data_type=_normalize_type(c.get("type", "string")),
                nullable=c.get("nullable", True),
                is_primary_key=c.get("primary_key", False),
                description=c.get("description", ""),
            )
            for c in cols
        ]
        tables.append(Table(name=table_name, columns=columns))
    return Schema(system_name=system_name, tables=tables)


def from_information_schema(rows: list[dict], system_name: str) -> Schema:
    """Normalize PostgreSQL/MySQL information_schema.columns rows."""
    grouped = {}
    for row in rows:
        normalized = {str(k).lower(): v for k, v in row.items()}
        grouped.setdefault(normalized["table_name"], []).append(Column(
            name=normalized["column_name"],
            data_type=_normalize_type(normalized["data_type"]),
            nullable=str(normalized.get("is_nullable", "YES")).upper() == "YES",
            is_primary_key=str(normalized.get("column_key", "")).upper() == "PRI",
        ))
    return Schema(system_name=system_name, tables=[
        Table(name=name, columns=columns) for name, columns in sorted(grouped.items())
    ])


def schema_fingerprint(schema: Schema) -> str:
    canonical = json.dumps(schema.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def schema_subset(schema: Schema, table_names: list[str] | set[str] | tuple[str, ...]) -> Schema:
    """Return a stable schema document containing only the named tables.

    Entity drift checks must never fingerprint an entire database: unrelated
    tables and generated projections would otherwise pause every entity.
    """
    wanted = set(table_names)
    return Schema(
        system_name=schema.system_name,
        tables=[table for table in schema.tables if table.name in wanted],
        version=schema.version,
    )


def table_schema(schema: Schema, table_name: str) -> Schema | None:
    """Return the single-table contract used for an entity fingerprint."""
    table = schema.get_table(table_name)
    if table is None:
        return None
    return Schema(system_name=schema.system_name, tables=[table], version=schema.version)
