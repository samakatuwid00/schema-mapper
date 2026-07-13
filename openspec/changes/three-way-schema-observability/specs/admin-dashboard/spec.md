# admin-dashboard Specification (Delta)

## ADDED Requirements

### Requirement: Three-step schema stepper

The Schema Changes surface SHALL present the source, staging (Path A), and
canonical target (Path B) schemas as a three-step stepper — Source →
Staging → Target — with next/back navigation arrows in the card corners so a
manager can move between them in either direction.

#### Scenario: Navigating to the target (Path B) view

- **WHEN** a manager clicks the next arrow on the Staging card
- **THEN** the Target (Path B) card appears and the manager can return to
  Staging via the back arrow

### Requirement: Step-aware scan control

The Schema Changes scan control SHALL relabel and re-target based on the active
step. On the Source step the input reads "Source schema" (default `irimsv`) and
scans the source→staging pair. On the Staging step the input reads
"Staging schema" (where the manager types the staging table to inspect) and
scans the staging→target (Path B) pair.

#### Scenario: Scanning the staging contract

- **WHEN** a manager is on the Staging step, types a staging table, and runs
  "Scan schema now"
- **THEN** the system scans the staging→target (Path B) contract and reports
  whether the staging and canonical target schemas differ

#### Scenario: Drift reports filtered by step

- **WHEN** a manager views the Staging step vs the Target (Path B) step
- **THEN** the drift report list shows the `source->staging` reports on the
  Staging step and the `staging->target` reports on the Target step
