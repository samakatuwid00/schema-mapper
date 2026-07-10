# data-browser Specification (Delta)

## ADDED Requirements

### Requirement: Read-only row browsing of both databases

The system SHALL expose paginated, sortable, read-only row access to the source schema
tables (PostgreSQL) and the target staging tables (MySQL). The system SHALL NOT provide
any facility to write, update, delete, or execute user-supplied SQL through this
capability.

#### Scenario: Manager inspects staging contents

- **WHEN** an authenticated operator opens a deployed entity's staging table in the data
  browser
- **THEN** the system returns that table's columns and a page of rows with a total row
  count, and the UI renders them in a grid

#### Scenario: Write attempts are impossible

- **WHEN** any request is made to the data-browser endpoints
- **THEN** only `SELECT` statements are issued to the database, and no endpoint accepts a
  SQL fragment from the client

### Requirement: Identifier allowlisting

The system SHALL validate every requested table against the live `information_schema`
listing for the requested side, and every requested sort column against that table's
discovered columns, rejecting anything not present. Source reads SHALL be confined to the
configured source schema and target reads to the configured staging database. Page size
SHALL be capped, and sort direction SHALL be restricted to ascending or descending.

#### Scenario: Unknown table rejected

- **WHEN** a request asks for rows of a table that does not exist in that side's
  `information_schema`
- **THEN** the request is rejected with a not-found error and no SQL is executed against
  that identifier

#### Scenario: Injected sort column rejected

- **WHEN** a request supplies a sort column that is not one of the table's columns
- **THEN** the request is rejected with a validation error before any query runs

#### Scenario: Oversized page clamped

- **WHEN** a request asks for a page size above the maximum
- **THEN** the system clamps it to the maximum rather than honoring it

### Requirement: Audited and non-cached access

Because browsed rows contain personal data, every successful or failed row read SHALL
write an `integration.admin_action_audit` row recording the acting session user, the table
read, and the outcome. Responses carrying row data SHALL set `Cache-Control: no-store`.
Unauthenticated requests SHALL be rejected.

#### Scenario: Browse is attributable

- **WHEN** an operator reads rows of the `farmers` staging table
- **THEN** exactly one audit row records that operator as actor with action `data_browse`
  and the table as target

### Requirement: Source-to-target row comparison

The system SHALL compare a single logical record across both databases, matched on its
immutable `external_reference` UUID, returning each field's source value, target value,
and whether they match.

#### Scenario: Verifying a delivered record

- **WHEN** an operator compares a record that has been delivered to staging
- **THEN** the response shows both sides field by field, and fields whose values differ
  are flagged as not matching

#### Scenario: Record absent from target

- **WHEN** the requested `external_reference` exists in the source but not in staging
- **THEN** the response reports the record as missing on the target side rather than
  erroring
