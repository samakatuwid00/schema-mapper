# ai-mapping-engine Specification

## Purpose
TBD - created by archiving change generic-ai-db-migration-engine. Update Purpose after archive.
## Requirements
### Requirement: Schema-agnostic AI mapping

The system SHALL support AI-powered column mapping between any source table and any target table, regardless of engine, using **schema metadata only** (source/target table names, column names, generic types, and FK structure) — never row values.

#### Scenario: Map source to target across different engines

- **WHEN** onboarding a table from a Postgres source to a Postgres or MySQL target with no existing mapping
- **THEN** the AI engine receives source and target schema metadata (column names, generic types, constraints) — **no sample rows** — and returns a proposed mapping with a confidence score per column

#### Scenario: AI suggests structural patterns

- **WHEN** a source has a denormalized table and the target has normalized child tables
- **THEN** the AI mapping suggests split patterns and FK relationships from the schema structure alone

### Requirement: Provider-agnostic, free-tier execution

The AI mapping engine SHALL run over a configurable ordered list of providers (`LLM_PROVIDER_ORDER`, default `gemini,fallback`), skipping unconfigured providers and failing over on any error — including quota `429` with `Retry-After` backoff — so the system runs on free AI APIs (Gemini plus OpenAI-compatible `groq`/`cerebras`/`openrouter`/`mistral`). No single vendor SHALL be hardcoded.

#### Scenario: Failover on quota exhaustion

- **WHEN** the first provider returns `429` (free-tier quota reached)
- **THEN** the engine waits per `Retry-After` and retries, then fails over to the next configured provider, returning a mapping without operator intervention

#### Scenario: All providers exhausted → deterministic fallback

- **WHEN** every configured provider is unavailable or quota-exhausted
- **THEN** the engine produces mappings from a deterministic name/type heuristic and flags them as `heuristic` for mandatory human review, rather than failing the run

### Requirement: PII-safe prompts

The mapping engine SHALL NOT transmit source or target row values to any AI provider. Prompt payloads SHALL contain schema/metadata only.

#### Scenario: Prompt contains no row data

- **WHEN** a mapping prompt is built for any table
- **THEN** the serialized prompt body contains no row values, and this is enforced by an automated test

### Requirement: Proposal cache

The engine SHALL cache accepted mapping proposals keyed by a source+target schema fingerprint, and SHALL reuse a cached proposal when the fingerprint is unchanged instead of calling the AI provider.

#### Scenario: Unchanged schema skips the API

- **WHEN** an entity is re-processed and its source+target schema fingerprint matches a cached proposal
- **THEN** the cached mapping is returned and no AI provider call is made

### Requirement: Confidence thresholds

The AI mapping engine SHALL return a confidence score (0.0–1.0) per column mapping. Mappings below a configurable threshold (default 0.7) SHALL require human confirmation before deploy.

#### Scenario: Low-confidence mapping flagged

- **WHEN** a column mapping has confidence 0.45
- **THEN** the deploy step pauses and presents the low-confidence mapping to the admin for review or correction

