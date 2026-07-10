# bulk-onboarding Specification (Delta)

## ADDED Requirements

### Requirement: One-click onboarding of many tables

The system SHALL provide a single allowlisted background job that onboards a selected set
of source tables, reporting per-table progress while it runs and a per-table outcome when
it finishes. Each table SHALL be classified into exactly one of: onboarded, needs review,
skipped because already deployed, or failed.

#### Scenario: Manager onboards a whole schema

- **WHEN** an operator selects every discovered table and starts a bulk onboard
- **THEN** one job runs, emits progress as each table is processed, and returns counts and
  table names for each of the four outcome buckets

#### Scenario: Progress is observable

- **WHEN** a bulk onboard is running and another admin has the dashboard open
- **THEN** that admin sees the job's current table and progress advance without reloading

### Requirement: Uncertain mappings are never deployed

A table SHALL only be deployed and backfilled when its mapping proposal is auto-approved
and has no unmet required target columns. Any table with a mapping below the auto-approval
confidence threshold, or with an unmet required column, SHALL be routed to a review queue
with its proposal identifier and SHALL NOT be deployed by the bulk job.

#### Scenario: Mid-confidence column blocks deployment

- **WHEN** a table's proposal contains a column mapped with confidence below the
  auto-approval threshold
- **THEN** that table appears in the needs-review bucket with its proposal id, no staging
  table is created for it, and no outbox events are enqueued for it

#### Scenario: Confident table proceeds

- **WHEN** a table's proposal is auto-approved with no unmet required columns
- **THEN** the system deploys it and enqueues its existing rows for delivery

### Requirement: Non-destructive bulk onboarding

The bulk job SHALL compose the existing deploy and backfill services and SHALL NOT drop
and bulk-load target tables. Rows SHALL reach the target only through the audited delivery
worker.

#### Scenario: Existing staging data survives

- **WHEN** a bulk onboard runs over a schema that includes an already-deployed table
- **THEN** that table is skipped, its staging table is not dropped, and its existing rows
  remain

### Requirement: Resilient batch execution

A failure while processing one table SHALL NOT abort the batch. The failing table SHALL be
recorded with its error and the remaining tables SHALL still be processed.

#### Scenario: One table errors mid-batch

- **WHEN** the third of five tables raises an error during proposal
- **THEN** the job continues through tables four and five, and the third appears in the
  failed bucket with its error message

### Requirement: Batch concurrency guard

Two bulk onboard jobs targeting an overlapping set of tables SHALL NOT run concurrently.
The second submission SHALL be rejected with a conflict identifying the running job.

#### Scenario: Two admins start the same batch

- **WHEN** two operators submit bulk onboard for the same schema and tables at once
- **THEN** exactly one job is created and the other receives a conflict response
