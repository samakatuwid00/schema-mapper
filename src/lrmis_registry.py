"""Typed registry of the canonical LRMIS schema (Path B, Phase 0).

Parses the LRMIS DDL into tables, columns, and a foreign-key graph so the
writer can order inserts parents-first and propagate FK values.

Two facts about the real LRMIS schema drive this module's shape:

* `station` and `psgc` are the only tables whose primary key is **not**
  AUTO_INCREMENT, and they are also the only self-referencing tables
  (`station.parent_station -> station.id`). They are therefore treated as
  read-only reference tables: the pipeline resolves an existing id, it never
  inserts one (`LAST_INSERT_ID()` would return 0 for them).
* Self-referencing foreign keys make a naive Kahn's sort report a cycle, so
  self-loops are excluded from the ordering graph.

The DDL file ships alongside ~200MB of seed INSERTs, so parsing streams the
file and only retains CREATE TABLE blocks.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_CREATE_RE = re.compile(r"CREATE TABLE `([^`]+)`")
_COLUMN_RE = re.compile(r"^\s*`(\w+)`\s+([a-zA-Z]+(?:\([^)]*\))?(?:\s+unsigned)?)", re.I)
_PK_RE = re.compile(r"PRIMARY KEY \(([^)]+)\)")
_FK_RE = re.compile(
    r"FOREIGN KEY \(`([^`]+)`\) REFERENCES `([^`]+)` \(`([^`]+)`\)")
_ENUM_RE = re.compile(r"enum\((.*)\)", re.I)


class SchemaCycleError(RuntimeError):
    """The FK graph has a cycle that is not a simple self-reference."""


@dataclass(frozen=True)
class LrmisColumn:
    name: str
    data_type: str            # raw MySQL type, e.g. "int", "varchar(255)"
    nullable: bool
    is_primary_key: bool
    auto_increment: bool
    enum_values: tuple[str, ...] = ()

    @property
    def base_type(self) -> str:
        return self.data_type.split("(")[0].strip().lower()


@dataclass(frozen=True)
class LrmisForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class LrmisTable:
    name: str
    columns: list[LrmisColumn] = field(default_factory=list)
    foreign_keys: list[LrmisForeignKey] = field(default_factory=list)
    create_sql: str = ""

    @property
    def primary_key(self) -> list[str]:
        return [c.name for c in self.columns if c.is_primary_key]

    @property
    def auto_increment_column(self) -> str | None:
        for c in self.columns:
            if c.auto_increment:
                return c.name
        return None

    def get_column(self, name: str) -> LrmisColumn | None:
        return next((c for c in self.columns if c.name == name), None)


def _parse_column(line: str, pk_columns: set[str]) -> LrmisColumn | None:
    m = _COLUMN_RE.match(line)
    if not m:
        return None
    name, data_type = m.group(1), m.group(2)
    lowered = line.lower()
    enum_values: tuple[str, ...] = ()
    if lowered.lstrip().startswith(f"`{name.lower()}` enum"):
        em = _ENUM_RE.search(line)
        if em:
            enum_values = tuple(v.strip().strip("'") for v in em.group(1).split(","))
            data_type = f"enum({em.group(1)})"
    return LrmisColumn(
        name=name,
        data_type=data_type,
        nullable="not null" not in lowered,
        is_primary_key=name in pk_columns,
        auto_increment="auto_increment" in lowered,
        enum_values=enum_values,
    )


def parse_ddl(text: str) -> dict[str, LrmisTable]:
    """Parse CREATE TABLE blocks out of a MySQL dump."""
    tables: dict[str, LrmisTable] = {}
    for block in _iter_create_blocks(text.splitlines(keepends=True)):
        table = _parse_block(block)
        tables[table.name] = table
    return tables


def _iter_create_blocks(lines):
    buf, inside = [], False
    for line in lines:
        if not inside:
            if _CREATE_RE.match(line):
                buf, inside = [line], True
            continue
        buf.append(line)
        # mysqldump closes with ") ENGINE=InnoDB ...;"
        if line.lstrip().startswith(")") and "ENGINE" in line:
            yield "".join(buf)
            inside = False


def _parse_block(block: str) -> LrmisTable:
    name = _CREATE_RE.match(block).group(1)
    pk_match = _PK_RE.search(block)
    pk_columns = (
        {c.strip(" `") for c in pk_match.group(1).split(",")} if pk_match else set()
    )
    table = LrmisTable(name=name, create_sql=block.strip())
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(("PRIMARY KEY", "KEY ", "UNIQUE KEY", "CONSTRAINT")):
            continue
        col = _parse_column(line, pk_columns)
        if col:
            table.columns.append(col)
    for m in _FK_RE.finditer(block):
        table.foreign_keys.append(
            LrmisForeignKey(column=m.group(1), ref_table=m.group(2), ref_column=m.group(3)))
    return table


class LrmisRegistry:
    def __init__(self, tables: dict[str, LrmisTable]):
        self._tables = tables

    # -- construction -------------------------------------------------------

    @classmethod
    def from_sql_file(cls, path: str | Path) -> "LrmisRegistry":
        """Stream the dump; only CREATE TABLE blocks are retained."""
        path = Path(path)
        tables: dict[str, LrmisTable] = {}
        with path.open("r", encoding="utf8", errors="replace") as handle:
            for block in _iter_create_blocks(handle):
                table = _parse_block(block)
                tables[table.name] = table
        if not tables:
            raise ValueError(f"no CREATE TABLE blocks found in {path}")
        return cls(tables)

    @classmethod
    def from_information_schema(cls, staging, database: str | None = None) -> "LrmisRegistry":
        """Fallback when the DDL file is unavailable: read the live database."""
        rows = staging.information_schema(database)
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            r = {k.lower(): v for k, v in row.items()}
            grouped.setdefault(r["table_name"], []).append(r)
        tables: dict[str, LrmisTable] = {}
        for name, cols in grouped.items():
            table = LrmisTable(name=name)
            for r in sorted(cols, key=lambda c: c["ordinal_position"]):
                table.columns.append(LrmisColumn(
                    name=r["column_name"],
                    data_type=r["data_type"],
                    nullable=r["is_nullable"] == "YES",
                    is_primary_key=r.get("column_key") == "PRI",
                    auto_increment="auto_increment" in (r.get("extra") or "").lower(),
                ))
            tables[name] = table
        return cls(tables)

    # -- lookups ------------------------------------------------------------

    @property
    def table_names(self) -> list[str]:
        return sorted(self._tables)

    def has_table(self, name: str) -> bool:
        return name in self._tables

    def get_table(self, name: str) -> LrmisTable:
        if name not in self._tables:
            raise KeyError(f"{name!r} is not an LRMIS table")
        return self._tables[name]

    def get_column_type(self, table: str, column: str) -> str:
        col = self.get_table(table).get_column(column)
        if col is None:
            raise KeyError(f"{table}.{column} does not exist")
        return col.data_type

    def get_create_sql(self, table: str) -> str:
        return self.get_table(table).create_sql

    def foreign_keys(self, table: str) -> list[LrmisForeignKey]:
        return list(self.get_table(table).foreign_keys)

    def get_fk_column(self, table: str, ref_table: str) -> str | None:
        """The column on `table` that points at `ref_table` (first match)."""
        for fk in self.get_table(table).foreign_keys:
            if fk.ref_table == ref_table:
                return fk.column
        return None

    def get_parent_tables(self, table: str) -> list[str]:
        return sorted({fk.ref_table for fk in self.get_table(table).foreign_keys
                       if fk.ref_table != table})

    def get_referencing_tables(self, table: str) -> list[str]:
        return sorted({t.name for t in self._tables.values()
                       if t.name != table
                       and any(fk.ref_table == table for fk in t.foreign_keys)})

    def auto_increment_column(self, table: str) -> str | None:
        return self.get_table(table).auto_increment_column

    def is_reference_table(self, table: str) -> bool:
        """True when the pipeline must not INSERT into this table.

        A table whose primary key is not AUTO_INCREMENT cannot have an id
        generated by MySQL, so `LAST_INSERT_ID()` would yield 0 and any child
        FK would silently point at a non-existent parent. In the real LRMIS
        schema this is exactly `station` and `psgc`, both of which are
        pre-seeded reference data owned by LRMIS.
        """
        return self.get_table(table).auto_increment_column is None

    def reference_tables(self) -> list[str]:
        return sorted(t for t in self._tables if self.is_reference_table(t))

    def self_referencing_tables(self) -> list[str]:
        return sorted(t.name for t in self._tables.values()
                      if any(fk.ref_table == t.name for fk in t.foreign_keys))

    # -- ordering -----------------------------------------------------------

    def topological_order(self, subset: list[str] | None = None) -> list[str]:
        """Parent-first ordering. Self-loops are ignored; real cycles raise.

        `subset` restricts the ordering to the given tables (their relative
        parent-first order is preserved).
        """
        names = list(self._tables) if subset is None else list(subset)
        for n in names:
            self.get_table(n)  # validate
        selected = set(names)

        children: dict[str, set[str]] = {n: set() for n in names}
        indegree: dict[str, int] = {n: 0 for n in names}
        for child in names:
            for fk in self.get_table(child).foreign_keys:
                parent = fk.ref_table
                if parent == child or parent not in selected:
                    continue  # self-loop, or an FK out of the subset
                if child not in children[parent]:
                    children[parent].add(child)
                    indegree[child] += 1

        # Deterministic order for stable output across runs.
        queue = sorted(n for n in names if indegree[n] == 0)
        ordered: list[str] = []
        while queue:
            node = queue.pop(0)
            ordered.append(node)
            for child in sorted(children[node]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
                    queue.sort()

        if len(ordered) != len(names):
            stuck = sorted(set(names) - set(ordered))
            raise SchemaCycleError(
                f"foreign-key cycle among {stuck} (self-references excluded)")
        return ordered


DEFAULT_DDL_PATH = r"C:\Users\deped\Documents\lrmis-main\lrmis_db\lrmis.sql"


def ddl_path() -> str:
    return os.environ.get("LRMIS_DDL_PATH", DEFAULT_DDL_PATH)


@lru_cache(maxsize=1)
def get_registry() -> LrmisRegistry:
    """Process-wide registry, parsed once from the DDL file."""
    return LrmisRegistry.from_sql_file(ddl_path())


def reset_registry_cache() -> None:
    get_registry.cache_clear()
