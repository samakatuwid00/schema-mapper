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
_DEFAULT_RE = re.compile(r"\bDEFAULT\s+('(?:[^']|'')*'|[^\s,]+)", re.I)


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
    default: str | None = None   # raw DEFAULT token, None if the column has none

    @property
    def base_type(self) -> str:
        return self.data_type.split("(")[0].strip().lower()

    @property
    def has_default(self) -> bool:
        return self.default is not None

    @property
    def is_required(self) -> bool:
        """A value must be supplied on insert: NOT NULL, no default, not
        auto-increment. FK/PK handling is a separate concern the caller layers
        on top (see services.lrmis_mapping)."""
        return not self.nullable and self.default is None and not self.auto_increment


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


def _parse_default(line: str) -> str | None:
    """Return the raw DEFAULT token, or None. `DEFAULT NULL` reads as no default
    (the column falls back to NULL, which only helps a nullable column)."""
    m = _DEFAULT_RE.search(line)
    if not m:
        return None
    token = m.group(1)
    if token.upper() == "NULL":
        return None
    return token


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
        default=_parse_default(line),
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
    def from_discovery(cls, column_rows, fk_rows=None) -> "LrmisRegistry":
        """Build a registry from live-discovered metadata (engine-agnostic).

        `column_rows` is one dict per column with case-insensitive keys
        `table_name, column_name, data_type, is_nullable, ordinal_position`
        plus optional `column_key` ('PRI'), `extra` ('auto_increment'), and
        `column_default`. `fk_rows` is one dict per foreign key with
        `table_name, column_name, ref_table, ref_column`.

        Target adapters (see `src/adapters/`) normalise their engine's native
        `information_schema` into this shape, so the FK graph, topological sort,
        and seed-set logic behave identically whether the target is MySQL or
        Postgres — unlike a columns-only read, which leaves the FK graph empty.
        """
        grouped: dict[str, list[dict]] = {}
        for row in column_rows:
            r = {k.lower(): v for k, v in row.items()}
            grouped.setdefault(r["table_name"], []).append(r)
        tables: dict[str, LrmisTable] = {}
        for name, cols in grouped.items():
            table = LrmisTable(name=name)
            for r in sorted(cols, key=lambda c: c["ordinal_position"]):
                raw_default = r.get("column_default")
                table.columns.append(LrmisColumn(
                    name=r["column_name"],
                    data_type=r["data_type"],
                    nullable=str(r.get("is_nullable")).upper() in ("YES", "1", "TRUE"),
                    is_primary_key=r.get("column_key") == "PRI",
                    auto_increment="auto_increment" in str(r.get("extra") or "").lower(),
                    default=None if raw_default in (None, "NULL") else str(raw_default),
                ))
            tables[name] = table
        for fr in (fk_rows or []):
            r = {k.lower(): v for k, v in fr.items()}
            table = tables.get(r["table_name"])
            if table is None:
                continue
            table.foreign_keys.append(LrmisForeignKey(
                column=r["column_name"], ref_table=r["ref_table"],
                ref_column=r["ref_column"]))
        if not tables:
            raise ValueError("no tables found in discovery rows")
        return cls(tables)

    @classmethod
    def from_information_schema(cls, staging, database: str | None = None) -> "LrmisRegistry":
        """Read the live database's schema through a connector/adapter.

        Also pulls foreign keys when the connector exposes `foreign_keys()` (the
        target adapters do), so a discovered registry can order inserts
        parents-first. Falls back to a columns-only (FK-less) registry otherwise.
        """
        column_rows = staging.information_schema(database)
        fk_rows = (staging.foreign_keys(database)
                   if hasattr(staging, "foreign_keys") else None)
        return cls.from_discovery(column_rows, fk_rows)

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

    def required_columns(self, table: str) -> list[str]:
        """Columns a source mapping MUST supply a value for on insert.

        Excludes columns the system fills anyway: nullable, defaulted, and
        auto-increment. FK columns are still listed here (the writer fills them
        from parents, but that is layered on in services.lrmis_mapping so the
        registry stays purely structural)."""
        return [c.name for c in self.get_table(table).columns if c.is_required]

    def fk_closure(self, tables) -> set[str]:
        """Every table transitively reachable via foreign keys from `tables`
        (parents, grandparents, ...), excluding self-loops. Includes the input
        tables' parents, not the inputs themselves unless reached as a parent."""
        seen: set[str] = set()
        frontier = set(tables)
        while frontier:
            t = frontier.pop()
            for fk in self.get_table(t).foreign_keys:
                parent = fk.ref_table
                if parent != t and parent not in seen:
                    seen.add(parent)
                    frontier.add(parent)
        return seen

    def seed_tables(self, write_set) -> list[str]:
        """Lookup tables that must hold data for the pipeline's inserts to
        satisfy their foreign keys: the FK-closure of the write set minus the
        write set itself (the pipeline populates those, including app-assigned
        station)."""
        write_set = set(write_set)
        return sorted(self.fk_closure(write_set) - write_set)

    def station_write_set(self) -> set[str]:
        """The tables a school row fans out into: station plus everything that
        references it."""
        return {"station"} | set(self.get_referencing_tables("station"))

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
