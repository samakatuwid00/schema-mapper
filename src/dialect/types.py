"""Generic type system: bridge native DB types across engines (§4).

The AI mapping prompt and the writer reason about `GenericType` names rather than
raw `varchar(255)` / `character varying`, so a mapping matches regardless of the
engine's native type spelling.
"""
from __future__ import annotations

from enum import Enum


class GenericType(Enum):
    STRING = "string"       # varchar, char, text, nvarchar, enum
    INTEGER = "integer"     # int, bigint, smallint, serial, identity
    FLOAT = "float"         # float, double, decimal, numeric
    BOOLEAN = "boolean"     # bool, bit
    DATE = "date"           # date
    DATETIME = "datetime"   # datetime, timestamp, timestamptz
    BINARY = "binary"       # blob, bytea, varbinary
    JSON = "json"           # json, jsonb
    UUID = "uuid"           # uuid, char(36)


_MYSQL: dict[str, GenericType] = {
    "varchar": GenericType.STRING, "char": GenericType.STRING,
    "text": GenericType.STRING, "tinytext": GenericType.STRING,
    "mediumtext": GenericType.STRING, "longtext": GenericType.STRING,
    "enum": GenericType.STRING, "set": GenericType.STRING,
    "int": GenericType.INTEGER, "integer": GenericType.INTEGER,
    "bigint": GenericType.INTEGER, "smallint": GenericType.INTEGER,
    "mediumint": GenericType.INTEGER, "tinyint": GenericType.INTEGER,
    "year": GenericType.INTEGER,
    "bool": GenericType.BOOLEAN, "boolean": GenericType.BOOLEAN, "bit": GenericType.BOOLEAN,
    "decimal": GenericType.FLOAT, "numeric": GenericType.FLOAT,
    "float": GenericType.FLOAT, "double": GenericType.FLOAT, "real": GenericType.FLOAT,
    "date": GenericType.DATE,
    "datetime": GenericType.DATETIME, "timestamp": GenericType.DATETIME,
    "blob": GenericType.BINARY, "tinyblob": GenericType.BINARY,
    "mediumblob": GenericType.BINARY, "longblob": GenericType.BINARY,
    "binary": GenericType.BINARY, "varbinary": GenericType.BINARY,
    "json": GenericType.JSON,
}

_POSTGRES: dict[str, GenericType] = {
    "character varying": GenericType.STRING, "varchar": GenericType.STRING,
    "character": GenericType.STRING, "char": GenericType.STRING,
    "text": GenericType.STRING, "citext": GenericType.STRING, "name": GenericType.STRING,
    "integer": GenericType.INTEGER, "int": GenericType.INTEGER, "int4": GenericType.INTEGER,
    "bigint": GenericType.INTEGER, "int8": GenericType.INTEGER,
    "smallint": GenericType.INTEGER, "int2": GenericType.INTEGER,
    "serial": GenericType.INTEGER, "bigserial": GenericType.INTEGER,
    "boolean": GenericType.BOOLEAN, "bool": GenericType.BOOLEAN,
    "numeric": GenericType.FLOAT, "decimal": GenericType.FLOAT,
    "real": GenericType.FLOAT, "double precision": GenericType.FLOAT,
    "float4": GenericType.FLOAT, "float8": GenericType.FLOAT,
    "date": GenericType.DATE,
    "timestamp without time zone": GenericType.DATETIME,
    "timestamp with time zone": GenericType.DATETIME,
    "timestamptz": GenericType.DATETIME, "timestamp": GenericType.DATETIME,
    "bytea": GenericType.BINARY,
    "json": GenericType.JSON, "jsonb": GenericType.JSON,
    "uuid": GenericType.UUID,
}

_BY_ENGINE: dict[str, dict[str, GenericType]] = {
    "mysql": _MYSQL, "mariadb": _MYSQL,
    "postgres": _POSTGRES, "postgresql": _POSTGRES, "pg": _POSTGRES,
}


def native_to_generic(engine: str, native_type: str) -> GenericType:
    """Map a native column type to a `GenericType`. Unknown types fall back to
    STRING (the safest carrier). The type's base name is used, so `varchar(255)`
    and `numeric(10,2)` resolve like `varchar` / `numeric`."""
    table = _BY_ENGINE.get((engine or "").strip().lower())
    if table is None:
        raise ValueError(f"no type map for engine {engine!r}")
    base = native_type.split("(")[0].strip().lower()
    return table.get(base, GenericType.STRING)


def native_to_generic_any(native_type: str) -> GenericType:
    """Best-effort generic type when the source engine is unknown — tries the
    Postgres then MySQL maps (so `tinyint`, `character varying`, etc. all resolve),
    falling back to STRING. Used to annotate the AI prompt engine-independently."""
    base = (native_type or "").split("(")[0].strip().lower()
    for table in (_POSTGRES, _MYSQL):
        if base in table:
            return table[base]
    return GenericType.STRING


def cast_value(value, generic_type: GenericType):
    """Coerce a source value toward the target column's generic type.

    Conservative on purpose: it fixes the common cross-engine mismatches (a
    numeric-looking string into an INTEGER/FLOAT column, a truthy token into a
    BOOLEAN) and otherwise returns the value unchanged. Anything it cannot cast
    cleanly is left for the driver — casting must never turn a deliverable row
    into a lossy or wrong one. DATE/DATETIME/JSON/BINARY/UUID are left as-is.
    """
    if value is None:
        return None
    try:
        if generic_type is GenericType.INTEGER:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value) if value.is_integer() else value
            s = str(value).strip()
            return int(s) if s.lstrip("-").isdigit() else value
        if generic_type is GenericType.FLOAT:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return float(str(value).strip())
        if generic_type is GenericType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return bool(value)
            s = str(value).strip().lower()
            if s in ("true", "t", "1", "yes", "y"):
                return True
            if s in ("false", "f", "0", "no", "n", ""):
                return False
            return value
        if generic_type is GenericType.STRING:
            return value if isinstance(value, str) else str(value)
    except (ValueError, TypeError):
        return value
    return value
