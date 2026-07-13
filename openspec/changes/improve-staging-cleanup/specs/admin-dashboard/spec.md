## MODIFIED Requirements

### Requirement: Schema Changes cleanup controls

The Schema Changes page SHALL let an operator retire a deployed entity chosen from a dropdown rather than a bare numeric id, and SHALL display the structured result of a completed sweep or retire job instead of a bare "job complete" line.

#### Scenario: Retire uses a deployed-entity dropdown

- **WHEN** an operator opens the retire control
- **THEN** a dropdown lists only deployed entities as `source_table → staging_table`, and selecting one submits that entity's numeric id

#### Scenario: Completed sweep shows its result

- **WHEN** a sweep job succeeds
- **THEN** the page shows the counts and (capped) lists of orphans found, dropped, and snapshots taken

#### Scenario: Completed retire shows its result

- **WHEN** a retire job succeeds
- **THEN** the page shows the entity, its `source_table → staging_table`, whether the table was dropped, and the snapshot name
