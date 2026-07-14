# schema-swap-remap Specification

## Purpose
TBD - created by archiving change generic-ai-db-migration-engine. Update Purpose after archive.
## Requirements
### Requirement: One-command schema swap with gated re-map, either side

The system SHALL provide a single command (`sync-engine schema-swap`) that
accepts a `side` parameter of `source` or `target` (defaulting to `target`
so existing behavior is unchanged), adopts a new schema on that side (or a
new engine, target-side only), re-maps only the affected deployed entities,
and re-delivers the kept entities. Non-destructive steps (re-discover, diff,
propose re-maps) SHALL run first; the destructive recreate and re-deliver
(target side) or the approval of a re-map (source side) SHALL require
explicit confirmation.

#### Scenario: Dry-run previews the swap (target, unchanged default)

- **WHEN** an admin runs `sync-engine schema-swap --dry-run` (or
  `--side target --dry-run`) after re-pointing the target
- **THEN** the system re-discovers the new target schema, diffs it against
  the last approved fingerprint, and reports the affected deployed entities
  plus proposed re-mappings — changing nothing

#### Scenario: Confirmed apply recreates and re-delivers (target)

- **WHEN** an admin runs `sync-engine schema-swap --confirm`
- **THEN** the system recreates the target from the new schema, re-seeds the
  FK-closure lookup tables, and re-delivers only the kept (selectively
  onboarded) entities — leaving un-onboarded entities out

#### Scenario: Dry-run previews a source-side swap

- **WHEN** an admin runs `sync-engine schema-swap --side source --dry-run`
  after IRIMSV is restructured or replaced
- **THEN** the system re-discovers the new source schema, diffs it against
  the last approved source contract, and reports the affected entities plus
  proposed re-mappings — issuing no DDL/DML against the source

### Requirement: Ingest the new schema by discovery, source or target

The schema-swap flow SHALL obtain the new structure from the adapter
matching the requested `side` (`get_target_adapter` for `side=target`,
`get_source_adapter` for `side=source`) against a live database, including
when a target schema originates from a `pg_dump`/`.backup` archive (restore
first, then discover). It SHALL NOT parse a binary dump archive directly.

#### Scenario: New Postgres target from a .backup

- **WHEN** the new target is a Postgres database restored from
  `old-lrmis.backup`
- **THEN** the swap flow discovers its `information_schema`, rebuilds the
  registry (FK graph + topological order) from the discovered structure, and
  uses the Postgres dialect for all generated SQL

#### Scenario: Restructured source discovered via the source adapter

- **WHEN** `side=source` is requested against a restructured IRIMSV database
- **THEN** the swap flow discovers the source's live structure through
  `PostgresSourceAdapter` and diffs it against the approved source contract,
  not the target's `lrmis_target_tables` footprint

### Requirement: Affected-entity detection

The schema-swap flow SHALL determine which deployed entities are affected by the new schema by diffing the previously approved structure against the newly discovered one, and SHALL re-map only those entities — reusing the existing schema fingerprint/drift machinery.

#### Scenario: Only affected entities are re-mapped

- **WHEN** a target change alters two of the tables that some deployed entities write to
- **THEN** only the entities whose mappings touch the changed tables are re-proposed; entities unaffected by the change keep their existing mappings

### Requirement: Human-gated re-mapping

Re-mapped columns produced during a schema swap SHALL be subject to the mapping confidence threshold; any re-mapping below the threshold SHALL pause for human review before the confirmed apply.

#### Scenario: Low-confidence re-map blocks apply

- **WHEN** a re-proposed mapping for an affected entity has a column below the confidence threshold
- **THEN** the confirmed apply is blocked for that entity until the admin reviews and resolves the low-confidence column

