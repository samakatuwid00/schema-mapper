"""LRMIS target plugin — domain specifics kept out of the generic core (§9).

The generic engine (adapters, dialect, `GenericWriter`) knows nothing about
`station`, the reserved id range, or the `delivery_audit` envelope. Those LRMIS
facts live here and are injected as configuration, so a different target loads a
different plugin (or none) without touching core code.

Nothing here imports the engine, so it is safe to import from anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TargetPlugin:
    name: str
    # Tables with no AUTO_INCREMENT PK that the pipeline may nonetheless CREATE
    # rows in (an id is allocated from a reserved range). Every other
    # no-AUTO_INCREMENT table stays resolve-only.
    app_assigned_id_tables: frozenset
    # First id handed out for app-assigned tables (LRMIS's own ids are 1..3921).
    id_sequence_start: int
    # The table a source entity fans out from; its FK-closure defines the seed
    # set. For LRMIS this is `station`.
    write_set_anchor: str | None = None
    # When True, ANY table the pipeline writes that has no DB-generated PK is
    # treated as app-assigned (id allocated), except those named in
    # `reference_tables`. This fits a target (e.g. old-lrmis Postgres) whose PKs
    # are plain integers the application assigns itself, so the writer cannot
    # rely on an AUTO_INCREMENT/serial heuristic to tell writable from lookup.
    app_assign_non_autoincrement: bool = False
    # Resolve-only lookups (never inserted), keyed to their natural key. Under
    # `app_assign_non_autoincrement`, these stay resolve-only.
    reference_tables: dict = field(default_factory=dict)


LRMIS = TargetPlugin(
    name="LRMIS",
    app_assigned_id_tables=frozenset({"station"}),
    id_sequence_start=10_000_000,
    write_set_anchor="station",
    reference_tables={"psgc": {"resolve_on": ["code"]}},
)

# old-lrmis (Postgres): verified live — 65 of 66 base tables have STRING primary
# keys (uuid/char) the application assigns and carries; only `crud_log_detail`
# (empty) has an integer PK. So delivery is by SOURCE-CARRIED id (the mapping
# supplies the PK, GenericWriter inserts it) — NOT DB-side allocation. Hence
# `app_assign_non_autoincrement=False`. `id_sequence_start` only ever applies to
# the lone int-PK table, so a modest reserved range is fine. `reference_tables`
# are the seeded lookups the pipeline must resolve (never insert/overwrite);
# this set is a curated starting point — refine per the real domain.
_OLD_LRMIS_LOOKUPS = (
    "geo_level", "gender", "psgc", "region", "province", "city_mun", "barangay",
    "legislative_dristrict", "grade_level", "subject", "school_type", "school_year",
    "circular_class", "contact_type", "station_type", "user_type", "user_status",
    "status", "position", "categories", "brand", "type_name", "crud_type",
)
OLD_LRMIS = TargetPlugin(
    name="OLD_LRMIS",
    app_assigned_id_tables=frozenset(),
    id_sequence_start=1_000_000,
    app_assign_non_autoincrement=False,
    reference_tables={t: {} for t in _OLD_LRMIS_LOOKUPS},
)

# The delivery-audit envelope table (LRMIS/MySQL). Recreated by init_lrmis_target;
# lives here because it is a Path-B/LRMIS concept, not part of the generic engine.
DELIVERY_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS `delivery_audit` (
    `event_id` CHAR(36) NOT NULL PRIMARY KEY,
    `external_reference` CHAR(36) NOT NULL,
    `source_system` VARCHAR(40),
    `operation` VARCHAR(20),
    `source_updated_at` DATETIME(6),
    `mapping_version` INT,
    `payload_checksum` CHAR(64),
    `active` TINYINT(1) DEFAULT 1,
    `accepted_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    INDEX `idx_ext_ref` (`external_reference`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""".strip()
