# guided-workflow Specification (Delta)

## ADDED Requirements

### Requirement: No hand-typed object identifiers

The system SHALL expose a list of mapping proposals filterable by status, and the UI SHALL
let an operator select a table or proposal from a list. The UI SHALL NOT require an
operator to type a numeric proposal identifier to reach any workflow.

#### Scenario: Reviewing without knowing an id

- **WHEN** an operator opens the review queue with proposals awaiting review
- **THEN** the pending tables are listed by name and clicking one opens its field mappings

#### Scenario: Empty review queue

- **WHEN** no proposals are awaiting review
- **THEN** the queue shows an explanatory empty state rather than an id input

### Requirement: One state and one next action per table

The setup surface SHALL present each source table with a single current state and the
single next action available for that state, and SHALL carry the selected table through
the workflow without requiring the operator to re-enter it.

#### Scenario: Table needing review

- **WHEN** a table's mapping proposal needs review
- **THEN** its row shows a needs-review state and its only action opens the review for
  that table

#### Scenario: Review happens without losing place

- **WHEN** an operator reviews a table's column mappings
- **THEN** the review opens in place and, on completion, the operator returns to the table
  list without manual navigation

### Requirement: Manager-facing terminology

The UI SHALL present internal domain terms using database-manager vocabulary, while the
database, service layer, API payloads, and audit records SHALL retain the internal names
unchanged. In particular, the SQL file runner SHALL NOT be labeled simply "Migrations" in
a way that implies moving row data, and the action that copies existing rows SHALL be
labeled as copying rows.

#### Scenario: SQL runner is not confused with data movement

- **WHEN** an operator views the navigation
- **THEN** the SQL file runner is labeled as applying database updates, distinct from the
  action that copies existing rows into the sync queue

#### Scenario: Audit trail keeps internal names

- **WHEN** an operator performs an action whose UI label differs from its internal name
- **THEN** the audit row records the internal action name, not the presented label

### Requirement: Progressive disclosure of internals

Fingerprints, confidence scores, proposal identifiers, and raw JSON job results SHALL NOT
appear in the default view of a page; they SHALL be available behind an explicit details
disclosure.

#### Scenario: Scan results stay readable

- **WHEN** a schema scan completes
- **THEN** the page summarizes the outcome in prose and any raw JSON is collapsed behind a
  details control
