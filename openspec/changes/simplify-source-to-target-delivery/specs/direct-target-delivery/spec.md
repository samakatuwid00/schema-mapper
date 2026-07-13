## ADDED Requirements

### Requirement: Single direct delivery path

The delivery worker SHALL deliver every approved outbox event through one path: directly into the LRMIS target database (`lrmis_target`) using the entity's approved multi-table mappings. The system SHALL NOT have a staging-database delivery route, and SHALL NOT branch delivery behavior on whether an entity has been "onboarded to the target" — all entities deliver directly.

#### Scenario: Every entity delivers to the target

- **WHEN** the worker claims an approved event for any onboarded entity
- **THEN** the event is delivered into the `lrmis_target` tables via that entity's approved mappings, and no `lrmis_staging` table is written

#### Scenario: No staging route exists

- **WHEN** the codebase is inspected for the legacy single-table staging delivery route
- **THEN** no `_deliver_legacy` path, staging connector delivery role, or `irimsv_*_staging` write remains

### Requirement: Multi-table fan-out from approved mappings

A single source row SHALL be delivered across one or more LRMIS target tables according to its approved per-column mappings (`source_column → target_table.target_column` with an optional transform), writing parent tables before child tables and propagating generated or app-assigned parent ids into child foreign keys.

#### Scenario: One source row splits across tables

- **WHEN** an entity's approved mappings place its columns into more than one target table
- **THEN** the source row is written to each target table, with child-table foreign keys populated from the parent rows written in the same event

#### Scenario: App-assigned id where no natural match exists

- **WHEN** a target table has no auto-increment key and the source row has no matching existing target row
- **THEN** the writer assigns an id from the reserved application range and records the mapping in the crosswalk

### Requirement: Mapping validation gate before delivery

An entity SHALL NOT be eligible for delivery until its mappings pass validation against the live LRMIS target schema: full coverage of required target columns, no unknown target table or column, and no unsatisfiable foreign key. A validation failure SHALL block deployment of that entity rather than failing at delivery time.

#### Scenario: Unknown target column blocks deploy

- **WHEN** an entity's mapping references a target column that does not exist in the LRMIS target schema
- **THEN** the validation gate rejects the mapping and the entity is not deployed for delivery

### Requirement: Per-event atomicity and quarantine

Each event's fan-out across target tables SHALL commit as a single unit and roll back entirely on any failure, so a partial fan-out never persists. An event whose mapping produces a transform error, no target values, or a delivery error SHALL be routed to quarantine (or retried up to the configured maximum) without stopping the rest of the batch.

#### Scenario: Partial fan-out never persists

- **WHEN** the write to the second of three target tables for one event fails
- **THEN** the writes to the first table are rolled back and the event is quarantined or retried, leaving no partial rows in the target

#### Scenario: Bad row does not poison the batch

- **WHEN** one event in a batch fails validation and the others are valid
- **THEN** the failing event is quarantined and the remaining events are delivered

### Requirement: Idempotent upsert and non-destructive deactivate

Delivery SHALL upsert on the immutable `external_reference` via the crosswalk so that re-delivering the same event does not create duplicate target rows. A `deactivate` operation SHALL NOT delete target rows; it SHALL mark the delivery envelope inactive, preserving the project rule that target rows are never destroyed by ordinary delivery.

#### Scenario: Re-delivery is idempotent

- **WHEN** the same event is delivered twice
- **THEN** the target ends with a single set of rows for that `external_reference`, updated in place

#### Scenario: Deactivate keeps the row

- **WHEN** a `deactivate` event is delivered for an existing external reference
- **THEN** the target rows remain and the delivery envelope's active flag is set false
