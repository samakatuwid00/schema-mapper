## MODIFIED Requirements

### Requirement: Drift visibility and side-effect transparency

The UI SHALL list all drift reports with their fingerprints, affected entities, resolution status, and pause side effects. Because scans can pause entities as a side effect, the scan result SHALL explicitly enumerate any entities it paused. The UI SHALL offer a "Resolve Drift" action for each unresolved drift report and a "Resolve All" bulk action.

#### Scenario: Paused entities called out

- **WHEN** a scan pauses two entities due to breaking drift
- **THEN** the scan completion event and Drift Reports page both name the two paused entities and the drift report that caused it

#### Scenario: Resolve from drift report

- **WHEN** an admin views a drift report with paused entities
- **THEN** the UI shows a "Resolve Drift" action per entity and a "Resolve All" action for the report
