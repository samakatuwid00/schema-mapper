# generic-adapters Specification

## Purpose
TBD - created by archiving change generic-ai-db-migration-engine. Update Purpose after archive.
## Requirements
### Requirement: Pluggable source/target adapter protocols

The system SHALL define `SourceAdapter` and `TargetAdapter` protocol interfaces that any database engine or data source can implement, providing `discover_schema()`, `dialect()`, `fetch_rows()`, `count_rows()`, `get_pk_columns()`, `recreate_schema()`, and `setup_cdc()` / `teardown_cdc()` methods.

#### Scenario: Implement a new database adapter

- **WHEN** a developer implements `SourceAdapter`/`TargetAdapter` for a new engine (e.g., MSSQL) and registers it in the adapter factory
- **THEN** the engine accepts it as a valid source or target, discovers tables and columns via `discover_schema()`, and streams rows through the pipeline

#### Scenario: Same-engine passthrough

- **WHEN** the source and target are the same engine type (e.g., Postgres→Postgres)
- **THEN** the system uses a `PassthroughAdapter` that skips type conversion and uses native DDL/DML directly

### Requirement: Target structure is discovered, not assumed

The engine SHALL treat the target engine type, schema structure, native types, and SQL dialect as **discovered at runtime from the connected target adapter**. No target engine or schema SHALL be hardcoded. Changing the target (including to a different engine) SHALL be a configuration/adapter change, after which `discover_schema()` is the source of truth.

#### Scenario: Target engine changes from MySQL to Postgres

- **WHEN** the configured target is re-pointed from the MySQL LRMIS database to a Postgres target (e.g., restored from `old-lrmis.backup`)
- **THEN** the engine loads the `PostgresTargetAdapter`, discovers the new schema and dialect from that adapter, and rebuilds the registry from the discovered structure without any code change to core

#### Scenario: Ingest a target supplied as a pg_dump/.backup archive

- **WHEN** the new target schema is provided as a `pg_dump` custom-format (`.backup`) binary archive
- **THEN** the system restores the archive into a live database and calls `discover_schema()` on the resulting connection to obtain the structure — it does NOT parse the binary archive directly

### Requirement: Adapter factory and config

The system SHALL provide an adapter factory that resolves adapter classes from an engine-level config file specifying `engine_type` and connection parameters, for both source and target.

#### Scenario: Factory resolves engine type

- **WHEN** the config specifies `engine_type: postgres` for the target
- **THEN** the factory returns a `PostgresTargetAdapter` configured with the given connection params

### Requirement: CDC strategy abstraction

The system SHALL support pluggable CDC strategies (`TriggerCDC`, `PollingCDC`, `DebeziumCDC`) behind a common `CDCStrategy` protocol, selectable via config.

#### Scenario: Polling CDC works on any engine

- **WHEN** config specifies `cdc: {strategy: polling, timestamp_column: updated_at}`
- **THEN** the engine periodically queries `SELECT ... WHERE updated_at > ?` to find changed rows

