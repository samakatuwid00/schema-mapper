## MODIFIED Requirements

### Requirement: Data browser

The admin dashboard SHALL browse source and real-target data only; the staging
data browser and any staging-specific pages/actions SHALL be removed, and data
movement SHALL be presented uniformly as "delivery".

#### Scenario: No staging browser in the UI

- **WHEN** an admin opens the data browser
- **THEN** only source and real-target tables are browsable; there is no staging
  view and no "staging" terminology

#### Scenario: Delivery is the single concept

- **WHEN** an admin views data-movement controls/status
- **THEN** they see a single "delivery" concept, with no Migration-vs-Backfill or
  staging-vs-target split
