## ADDED Requirements

### Requirement: Source-side schema discovery and diff

The system SHALL discover the live IRIMSV source schema through the source
adapter and diff it against the approved source contract, using the same
`side`-parameterized `schema_swap` pipeline as the existing target-side swap.

#### Scenario: Source schema diff detects structural changes

- **WHEN** an operator runs `schema_swap.py --side source` against a
  restructured or replacement IRIMSV source
- **THEN** the system reports added, removed, and retyped columns per entity
  relative to the approved source contract

#### Scenario: No changes reported when source is unchanged

- **WHEN** a source-swap diff runs against an IRIMSV source with no changes
- **THEN** the report shows zero affected entities and no remap is triggered

### Requirement: AI re-map limited to affected entities, human-gated

Only entities whose source columns are affected by the diff SHALL be
re-mapped; the AI proposal SHALL use schema-only prompts (no row values), and
any remap below the confidence threshold SHALL require human approval before
it can be applied.

#### Scenario: Unaffected entities are untouched

- **WHEN** a source-swap diff affects 3 of 15 deployed entities
- **THEN** only those 3 entities are re-mapped; the other 12 keep their
  existing approved mapping and continue delivering uninterrupted

#### Scenario: Low-confidence remap requires approval

- **WHEN** the AI re-map for an affected entity scores below the confidence
  threshold
- **THEN** the remap is held in a pending/review state and delivery for that
  entity does not resume until an operator approves it

#### Scenario: High-confidence remap can auto-apply

- **WHEN** the AI re-map for an affected entity scores at or above the
  confidence threshold
- **THEN** the remap may be applied without additional human approval,
  consistent with the existing target-swap behavior

### Requirement: Source-swap never writes to the source

A source-swap SHALL be a read-discovery and re-mapping operation only; it
SHALL NOT issue DDL or DML against IRIMSV.

#### Scenario: Source-swap performs no source mutation

- **WHEN** a source-swap runs, from discovery through applying an approved
  remap
- **THEN** no DDL or DML statement is ever issued against the IRIMSV source
  database
