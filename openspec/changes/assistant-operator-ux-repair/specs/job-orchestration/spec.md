## ADDED Requirements

### Requirement: Recoverable failed deploy jobs

Failed deploy jobs SHALL expose enough safe metadata for the UI and assistant to recover the mapping proposal involved in the failure.

#### Scenario: Failed deploy job exposes proposal recovery metadata

- **WHEN** a `deploy_lrmis` job fails and its params include `proposal_id`
- **THEN** the job read model exposes a safe proposal recovery field or enough safe params for the UI to link to the mapping review

#### Scenario: Job card starts repair flow

- **WHEN** a failed deploy job has recoverable proposal metadata
- **THEN** the job UI offers actions to open the proposal and start assistant repair for that job

