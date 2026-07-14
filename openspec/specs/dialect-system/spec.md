# dialect-system Specification

## Purpose
TBD - created by archiving change generic-ai-db-migration-engine. Update Purpose after archive.
## Requirements
### Requirement: Generic type system

The system SHALL define a `GenericType` enum (`STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `DATE`, `DATETIME`, `BINARY`, `JSON`, `UUID`) that bridges native database types across engines. Each adapter SHALL declare `native_to_generic()` and `generic_to_ddl()` mappings.

#### Scenario: Type mapped across engines

- **WHEN** source has `varchar(255)` and target has `NVARCHAR(255)`
- **THEN** both map to `GenericType.STRING`, and the mapping engine matches them correctly regardless of native type name

### Requirement: Dialect-aware SQL generation

The system SHALL provide a `Dialect` protocol with methods for quoting, upsert, insert, update, bulk insert, truncate, and engine-specific UUID generation. Built-in dialects SHALL include `MySQLDialect`, `PostgresDialect`, and `MSSQLDialect`.

#### Scenario: Dialect generates correct upsert

- **WHEN** the target is MySQL
- **THEN** `Dialect.upsert_sql()` generates `INSERT ... ON DUPLICATE KEY UPDATE`
- **WHEN** the target is Postgres
- **THEN** `Dialect.upsert_sql()` generates `INSERT ... ON CONFLICT DO UPDATE`

#### Scenario: Engine-specific refresh SQL

- **WHEN** running a full refresh on PostgreSQL target
- **THEN** the dialect generates SQL using `gen_random_uuid()` and `uuid_generate_v5()` as available
- **WHEN** running a full refresh on MySQL target
- **THEN** the dialect generates SQL using `UUID()` and `MD5()` equivalents

