## ADDED Requirements

### Requirement: Exactly one delivery path

The pipeline SHALL deliver every source event through a single path — the source
row fanned directly into the real target tables via the dialect-aware writer
(FK propagation, crosswalk idempotency). No legacy single-table staging route,
`irimsv_*_staging` tables, or staging/views databases SHALL exist.

#### Scenario: A delivered event writes only real target tables

- **WHEN** the worker delivers an approved event
- **THEN** it writes into the entity's real target tables (never an
  `irimsv_*_staging` table), and no `lrmis_staging`/`lrmis_staging_views` object is
  touched

#### Scenario: No legacy routing remains

- **WHEN** the worker claims a batch of events
- **THEN** it delivers them all through the direct path with no per-entity
  legacy/direct fork (`is_path_b_entity` is gone)

### Requirement: Cutover is gated on full migration

The destructive cutover SHALL NOT proceed while any `status='deployed'` entity
still lacks a direct-delivery target footprint (`lrmis_target_tables IS NULL`).

#### Scenario: Cutover aborts with an un-migrated entity

- **WHEN** a cutover precheck finds a deployed entity with `lrmis_target_tables IS NULL`
- **THEN** the cutover refuses to run and reports the un-migrated entities

#### Scenario: Cutover proceeds when all entities are migrated

- **WHEN** every deployed entity has a target footprint set
- **THEN** the precheck passes and the destructive steps may run behind explicit
  confirmation

### Requirement: Staging databases are removed

After cutover the `lrmis_staging` and `lrmis_staging_views` databases SHALL NOT
exist, and no code path SHALL reference them.

#### Scenario: Staging databases dropped behind confirmation

- **WHEN** the operator runs the database-drop step with the required typed
  confirmation
- **THEN** `lrmis_staging` and `lrmis_staging_views` are dropped (after a backup)
  and their `.env`/compose entries are removed
