"""Multi-table mapping validation for the LRMIS target (Path B, Phase 2).

A source row now maps across several real LRMIS tables. Before such a mapping
can be deployed, it must be checked against the canonical schema: every target
table and column must exist, every column that genuinely needs a value must be
supplied (or filled by the system), and every required foreign key must have a
parent that will actually exist.

This module is pure validation over the registry and a proposal's column
mappings — it opens no connections and is not yet wired into the live deploy
(that path still targets the VARCHAR staging tables). It is the gate the
Phase 6 lrmis_target deploy will call.

A "mapping" is a dict with at least `source_column`, `target_table`, and
`target_column` (the shape onboarding_field_review / propose already produce).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..lrmis_registry import LrmisRegistry, get_registry
from ..lrmis_writer import APP_ASSIGNED_ID_TABLES, group_by_table
from .common import ValidationError


def _reg(registry: LrmisRegistry | None) -> LrmisRegistry:
    return registry or get_registry()


def target_tables_for(mappings: list[dict]) -> list[str]:
    """Sorted distinct target tables a mapping fans out into."""
    return sorted({m["target_table"] for m in mappings if m.get("target_table")})


def system_handled_columns(table: str, registry: LrmisRegistry) -> set[str]:
    """Columns the writer/allocator fills, so a mapping need not: FK columns
    (filled from parents), the app-assigned primary key (station), and
    auto-increment / defaulted / nullable columns (already not 'required')."""
    meta = registry.get_table(table)
    handled = {fk.column for fk in meta.foreign_keys}
    if table in APP_ASSIGNED_ID_TABLES and meta.primary_key:
        handled.add(meta.primary_key[0])
    return handled


def columns_a_mapping_must_supply(table: str, registry: LrmisRegistry) -> set[str]:
    """Required columns (NOT NULL, no default, not auto-increment) that the
    system does NOT fill — i.e. the source mapping is responsible for them."""
    required = set(registry.required_columns(table))
    return required - system_handled_columns(table, registry)


@dataclass
class TableCoverage:
    table: str
    exists: bool
    mapped_columns: list[str] = field(default_factory=list)
    unknown_target_columns: list[str] = field(default_factory=list)
    required_missing: list[str] = field(default_factory=list)
    system_handled: list[str] = field(default_factory=list)


@dataclass
class CoverageReport:
    tables: list[TableCoverage] = field(default_factory=list)
    unknown_target_tables: list[str] = field(default_factory=list)
    fk_unsatisfiable: list[dict] = field(default_factory=list)

    @property
    def blocking(self) -> list[str]:
        problems: list[str] = []
        for t in self.unknown_target_tables:
            problems.append(f"target table {t!r} does not exist in the LRMIS schema")
        for tc in self.tables:
            for col in tc.unknown_target_columns:
                problems.append(f"{tc.table}.{col} is not a column of {tc.table}")
            for col in tc.required_missing:
                problems.append(f"{tc.table}.{col} is required but no source column maps to it")
        for fk in self.fk_unsatisfiable:
            problems.append(
                f"{fk['table']}.{fk['column']} requires a {fk['ref_table']} row, "
                f"but {fk['ref_table']} is neither written by this mapping nor seeded")
        return problems

    @property
    def ok(self) -> bool:
        return not self.blocking


def coverage_report(mappings: list[dict], registry: LrmisRegistry | None = None,
                    seeded_tables: set[str] | None = None) -> CoverageReport:
    """Assess whether `mappings` can be deployed against the LRMIS schema."""
    registry = _reg(registry)
    if seeded_tables is None:
        seeded_tables = set(registry.seed_tables(registry.station_write_set()))

    groups = group_by_table(mappings)
    write_set = set(groups)
    report = CoverageReport()

    for table, group in groups.items():
        if not registry.has_table(table):
            report.unknown_target_tables.append(table)
            report.tables.append(TableCoverage(table=table, exists=False))
            continue

        table_cols = {c.name for c in registry.get_table(table).columns}
        mapped = [m["target_column"] for m in group if m.get("target_column")]
        mapped_set = set(mapped)

        tc = TableCoverage(
            table=table,
            exists=True,
            mapped_columns=sorted(mapped_set),
            unknown_target_columns=sorted(mapped_set - table_cols),
            required_missing=sorted(columns_a_mapping_must_supply(table, registry) - mapped_set),
            system_handled=sorted(system_handled_columns(table, registry)),
        )
        report.tables.append(tc)

        # A required, unmapped FK column needs its parent to exist somewhere.
        for fk in registry.foreign_keys(table):
            if fk.ref_table == table:
                continue  # self-reference: writer never auto-fills it
            col = registry.get_table(table).get_column(fk.column)
            if fk.column in mapped_set:
                continue  # the source supplies the FK value directly
            if col is None or col.nullable or col.has_default:
                continue  # unmapped is fine: NULL / default is acceptable
            if fk.ref_table not in write_set and fk.ref_table not in seeded_tables:
                report.fk_unsatisfiable.append(
                    {"table": table, "column": fk.column, "ref_table": fk.ref_table})

    return report


def validate_deployment(mappings: list[dict], registry: LrmisRegistry | None = None,
                        seeded_tables: set[str] | None = None) -> CoverageReport:
    """Raise ValidationError if the mapping is not deployable; else return the
    coverage report."""
    report = coverage_report(mappings, registry, seeded_tables)
    if not report.ok:
        raise ValidationError("mapping cannot be deployed:\n  - "
                              + "\n  - ".join(report.blocking))
    return report


def store_target_tables(central_conn, entity_id: int, mappings: list[dict]) -> list[str]:
    """Record an entity's LRMIS footprint (the distinct target tables it fans
    out into) on integration.onboarding_entity. Returns the tables stored."""
    import json

    tables = target_tables_for(mappings)
    with central_conn.cursor() as cur:
        cur.execute("""
            UPDATE integration.onboarding_entity
            SET lrmis_target_tables = %s, updated_at = now()
            WHERE id = %s
        """, (json.dumps(tables), entity_id))
    return tables
