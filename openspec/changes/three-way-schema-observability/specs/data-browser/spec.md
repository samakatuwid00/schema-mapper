# data-browser Specification (Delta)

## ADDED Requirements

### Requirement: Read-only browsing of the canonical target database

The system SHALL expose paginated, sortable, read-only row access to the
canonical target database (`lrmis_target`, Path B) in addition to the existing
source (PostgreSQL) and staging (MySQL) sides. All three sides SHALL be
governed by the existing identifier-allowlisting, no-store, and audit rules.

#### Scenario: Manager inspects a canonical target table

- **WHEN** an authenticated operator opens a `lrmis_target` table in the data
  browser
- **THEN** the system returns its columns and a page of rows with a total row
  count, rendered in the grid, with no write path available

### Requirement: Staging-to-target row comparison

The system SHALL compare a single logical record between the staging database
(Path A) and the canonical target database (Path B), matched on the row's primary
key, returning each field's staging value, target value, and whether they match.
Because both sides share the LRMIS column layout, this comparison is meaningful.

#### Scenario: Verifying a delivered record against canonical

- **WHEN** an operator compares a staging row that has a counterpart in
  `lrmis_target`
- **THEN** the response shows both sides field by field, and fields whose
  values differ are flagged as not matching

#### Scenario: Record absent from canonical target

- **WHEN** the requested primary key exists in staging but not in
  `lrmis_target`
- **THEN** the response reports the record as missing on the target side
  rather than erroring

## MODIFIED Requirements

### Requirement: Source-to-target row comparison

The system SHALL compare a single logical record across both databases, matched
on its immutable `external_reference` UUID, returning each field's source value,
target value, and whether they match. Comparison SHALL be offered only between
schematically compatible sides: source↔staging and staging↔target (Path B) are
permitted; source↔target (Path B) SHALL NOT be offered because the two sides
have different schema designs (IRIMSV vs LRMIS).

#### Scenario: Source↔target comparison unavailable

- **WHEN** an operator views a Source table or a Path B table
- **THEN** the UI does not offer a comparison against the incompatible side
