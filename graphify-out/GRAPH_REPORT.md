# Graph Report - schema_mapper  (2026-07-10)

## Corpus Check
- 138 files · ~73,145 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1170 nodes · 2369 edges · 96 communities (71 shown, 25 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 87 edges (avg confidence: 0.62)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `acc29f59`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Schema Drift Monitoring
- Architecture & Deployment Docs
- Outbox & Delivery Store
- AI Mapping Engine
- Pipeline Commands & Central DB
- Terminal UI & Pipeline Entry
- MySQL Staging Connector
- Staging Fast Refresh
- Deployment & Staging DDL
- Pilot Bootstrap Scripts
- Mapping Proposals
- Schema Discovery
- IRIMSV Data Import
- LRMIS Schema Import
- Agent Tooling
- Package Init
- ValidationError
- JobRunner
- test_admin_api.py
- Overview.tsx
- compilerOptions
- Decisions
- WorkerQueues.tsx
- ADDED Requirements
- ADDED Requirements
- JobDrawer.tsx
- StatusChip.tsx
- ADDED Requirements
- MappingReview.tsx
- Onboarding.tsx
- SKILL.md
- ADDED Requirements
- auth.tsx
- explore.md
- ADDED Requirements
- Tasks: Add Admin Database Dashboard
- Region V IRIMSV-to-LRMIS integration
- graphify reference: extra exports and benchmark
- migrations.py
- Add Admin Database Dashboard
- Project Context
- graphify reference: query, path, explain
- lrmis_staging_db (MySQL 8.4 Service)
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- CLAUDE.md
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- CLAUDE.md
- extraction-spec.md
- LOCAL_SETUP.md
- Local Development Stack
- Customer Pilot Bootstrap Flow
- Customer Pilot Entity
- Entity Kill Switch
- Event Replay
- External Reference UUID
- Idempotent Delivery
- IRIMSV (Region V Data-Entry System)
- LRMIS (Target System)
- LRMIS Staging Contract
- Outbox Events
- Quarantine and Dead Letter Queues
- Reconciliation
- scripts/backfill_customers.py
- scripts/create_pilot_mapping.py
- scripts/reconcile.py
- Requirements
- Requirements
- ADDED Requirements
- ADDED Requirements
- Requirements
- ADDED Requirements
- Requirement: Safe migration apply
- Migrations.tsx
- ADDED Requirements
- Requirements
- ADDED Requirements
- Tasks: Add Admin Database Dashboard
- Requirement: Durable allowlisted jobs
- Add Admin Database Dashboard
- Tasks: Simplify the Database Manager Experience
- PipelineDiagram.tsx

## God Nodes (most connected - your core abstractions)
1. `PostgresCentralConnector` - 64 edges
2. `MySQLStagingConnector` - 53 edges
3. `Schema` - 46 edges
4. `ValidationError` - 34 edges
5. `NotFoundError` - 32 edges
6. `Table` - 31 edges
7. `_onboard_single_table()` - 30 edges
8. `errMsg()` - 25 edges
9. `AdminUser` - 23 edges
10. `Column` - 23 edges

## Surprising Connections (you probably didn't know these)
- `real_source_db (PostgreSQL 17 Real-Test Service)` --semantically_similar_to--> `central_db (PostgreSQL 16 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `real_target_db (MySQL 8.0.40 Real-Test Service)` --semantically_similar_to--> `lrmis_staging_db (MySQL 8.4 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `test_mysql_identifier_validation_happens_before_connection()` --calls--> `MySQLStagingConnector`  [EXTRACTED]
  tests/test_integration_core.py → src/connectors.py
- `_Response` --uses--> `Column`  [INFERRED]
  tests/test_mapping_engine.py → src/schema_models.py
- `_Response` --uses--> `Table`  [INFERRED]
  tests/test_mapping_engine.py → src/schema_models.py

## Import Cycles
- None detected.

## Communities (96 total, 25 thin omitted)

### Community 0 - "Schema Drift Monitoring"
Cohesion: 0.14
Nodes (26): get_mapping(), load_their_schema(), main(), our_central_schema(), End-to-end demo of the pipeline using fake data, so you can see the whole flow b, This is YOUR schema -- stable, never changes per target system., AI Draft Mapping Proposals, google-genai (Official Gemini SDK) (+18 more)

### Community 1 - "Architecture & Deployment Docs"
Cohesion: 0.33
Nodes (6): central_db (PostgreSQL 16 Service), real_source_db (PostgreSQL 17 Real-Test Service), psycopg2-binary (PostgreSQL Driver), sql/001_integration_foundation.sql, sql/002_onboarding_metadata.sql, sql/central_db_init.sql

### Community 2 - "Outbox & Delivery Store"
Cohesion: 0.12
Nodes (31): Event, approved_mapping(), canonical_json(), checksum(), claim_events(), delivered(), Exception, quarantine() (+23 more)

### Community 3 - "AI Mapping Engine"
Cohesion: 0.06
Nodes (77): AdminUser, BaseModel, Request, main(), Bootstrap or update an admin UI user.  Usage: python scripts/create_admin_user.p, audited(), list_audit(), Uniform admin_action_audit writer for every mutating endpoint and job. (+69 more)

### Community 4 - "Pipeline Commands & Central DB"
Cohesion: 0.07
Nodes (45): ApiError, createUser(), login(), logout(), me(), post(), request(), setUnauthorizedHandler() (+37 more)

### Community 5 - "Terminal UI & Pipeline Entry"
Cohesion: 0.09
Nodes (34): drop_staging_table(), Drop staging table if it exists., mapping_to_dicts(), cmd_onboard(), cmd_refresh(), _create_staging_table(), _detect_collation(), _detect_cross_table_candidates() (+26 more)

### Community 6 - "MySQL Staging Connector"
Cohesion: 0.05
Nodes (60): main(), Minimal administrator/auditor CLI; suitable for wrapping in a future web UI., set_enabled(), status(), central(), Process-wide pooled connectors shared by all request handlers and jobs., staging(), MySQLStagingConnector (+52 more)

### Community 7 - "Staging Fast Refresh"
Cohesion: 0.10
Nodes (35): connection, cmd_backfill(), cmd_deploy(), cmd_discover(), cmd_monitor(), cmd_propose(), cmd_reconcile(), cmd_resolve() (+27 more)

### Community 8 - "Deployment & Staging DDL"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 9 - "Pilot Bootstrap Scripts"
Cohesion: 0.47
Nodes (4): insert_customer(), Simulates your real application writing a new customer. The trigger on `customer, central_conn(), Backward-compatible central connection helper.  New integration code uses pooled

### Community 10 - "Mapping Proposals"
Cohesion: 0.12
Nodes (21): approveMapping(), getProposal(), resolveMapping(), NAV_GROUPS, NavGroup, NavItem, Shell(), useAuth() (+13 more)

### Community 11 - "Schema Discovery"
Cohesion: 0.07
Nodes (26): dependencies, lucide-react, react, react-dom, react-router-dom, @tanstack/react-query, devDependencies, jsdom (+18 more)

### Community 15 - "Package Init"
Cohesion: 0.15
Nodes (15): Get target schema from schema_version table., _target_schema(), from_json_export(), _normalize_type(), Turns whatever the other system gives you (a CREATE TABLE dump, a JSON export fr, Use this when the other system hands you a structured export instead     of raw, Column, System-agnostic representation of a database schema.  Both YOUR central schema a (+7 more)

### Community 16 - "ValidationError"
Cohesion: 0.19
Nodes (15): FastAPI, create_app(), FastAPI app factory and entrypoint (python -m src.admin_api.app)., ConflictError, Exception, Typed exceptions shared by all services; the API maps them to HTTP codes., A concurrent operation holds the resource (maps to HTTP 409)., Input or state precondition failed (maps to HTTP 422). (+7 more)

### Community 17 - "JobRunner"
Cohesion: 0.07
Nodes (16): enqueue(), get_job(), _h_backfill(), _h_deploy(), _h_discover(), _h_onboard_bulk(), _h_propose(), _h_refresh() (+8 more)

### Community 18 - "test_admin_api.py"
Cohesion: 0.08
Nodes (9): admin_client(), _client_as(), operator_client(), Admin API tests: auth gating, role checks, job allowlist, guard tiers.  These ru, The handler must refuse an empty batch rather than 'succeed' on nothing., Spoofed actor/by fields are ignored - identity comes from the session., test_action_bodies_do_not_accept_actor_fields(), test_migration_sql_readable_for_managed_files() (+1 more)

### Community 19 - "Overview.tsx"
Cohesion: 0.20
Nodes (13): getSnapshots(), getStatus(), getWorkerStatus(), restoreSnapshot(), toggleEntity(), HealthCard(), HealthCardProps, Sparkline() (+5 more)

### Community 20 - "compilerOptions"
Cohesion: 0.11
Nodes (17): compilerOptions, isolatedModules, jsx, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 21 - "Decisions"
Cohesion: 0.17
Nodes (20): main(), observe(), Observe LRMIS staging metadata, record drift, and selectively pause mappings., diff_schemas(), impacted_entities(), Schema comparison and selective pause policy., record_drift(), from_information_schema() (+12 more)

### Community 22 - "WorkerQueues.tsx"
Cohesion: 0.22
Nodes (11): getDeadLetter(), getQuarantine(), replayEvent(), startWorker(), stopWorker(), Semantic, SEMANTIC_BY_STATUS, StatusChip() (+3 more)

### Community 23 - "ADDED Requirements"
Cohesion: 0.20
Nodes (15): _FakeCentral, _proposal(), onboard_bulk: conservative bucketing, non-destructiveness, resilience.  The serv, Stand-in for the service functions, recording what bulk actually calls., _Recorder, _run(), test_already_deployed_table_is_skipped_untouched(), test_confident_table_is_deployed_and_backfilled() (+7 more)

### Community 24 - "ADDED Requirements"
Cohesion: 0.13
Nodes (18): compareRow(), get(), getAudit(), getDataRows(), getDataTables(), getSchemas(), listUsers(), qs() (+10 more)

### Community 25 - "JobDrawer.tsx"
Cohesion: 0.11
Nodes (21): getProposals(), listJobs(), EVENT_TYPES, JobEvent, JobEventPayload, JobEventType, SseHandle, subscribeJobEvents() (+13 more)

### Community 26 - "StatusChip.tsx"
Cohesion: 0.36
Nodes (6): ProposalField, confidenceClass(), GROUP_ORDER, MappingLanes(), MappingLanesProps, TRANSFORM_OPTIONS

### Community 27 - "ADDED Requirements"
Cohesion: 0.19
Nodes (11): _FakeCentral, _FakeStaging, Data browser: allowlisting, clamping, and identifier safety.  Runs without a dat, test_bad_direction_is_rejected(), test_page_below_one_is_clamped(), test_page_size_is_clamped_not_rejected(), test_sort_column_not_in_table_is_rejected(), test_target_side_reads_staging() (+3 more)

### Community 28 - "MappingReview.tsx"
Cohesion: 0.23
Nodes (12): approveSchema(), getDriftReports(), SchemaSystem, CopyButton(), SchemaTree(), SchemaTreeProps, DESCRIPTIONS, label() (+4 more)

### Community 29 - "Onboarding.tsx"
Cohesion: 0.20
Nodes (17): _columns_for(), compare_row(), fetch_rows(), list_browsable_tables(), _pipeline(), Read-only row access to the source and target databases (data-browser spec).  Se, Both sides' tables with column and row counts, plus the entity link., One page of rows. Size is clamped, never rejected, so a UI cannot wedge. (+9 more)

### Community 30 - "SKILL.md"
Cohesion: 0.18
Nodes (10): Check for context, Ending Discovery, Guardrails, Handling Different Entry Points, OpenSpec Awareness, The Stance, What You Don't Have To Do, What You Might Do (+2 more)

### Community 31 - "ADDED Requirements"
Cohesion: 0.13
Nodes (14): Context, D1. In-process services, not subprocesses, D2. FastAPI + SSE, D3. Durable jobs: `admin_job` + `admin_job_event`, D4. Concurrency guards live in Postgres, D5. Migration tracking, home-grown, D6. Auth and audit, D7. Guarded one-click contract (+6 more)

### Community 32 - "auth.tsx"
Cohesion: 0.13
Nodes (14): ADDED Requirements, bulk-onboarding Specification (Delta), Requirement: Batch concurrency guard, Requirement: Non-destructive bulk onboarding, Requirement: One-click onboarding of many tables, Requirement: Resilient batch execution, Requirement: Uncertain mappings are never deployed, Scenario: Confident table proceeds (+6 more)

### Community 33 - "explore.md"
Cohesion: 0.20
Nodes (9): Check for context, Ending Discovery, Guardrails, OpenSpec Awareness, The Stance, What You Don't Have To Do, What You Might Do, When a change exists (+1 more)

### Community 34 - "ADDED Requirements"
Cohesion: 0.13
Nodes (14): ADDED Requirements, data-browser Specification (Delta), Requirement: Audited and non-cached access, Requirement: Identifier allowlisting, Requirement: Read-only row browsing of both databases, Requirement: Source-to-target row comparison, Scenario: Browse is attributable, Scenario: Injected sort column rejected (+6 more)

### Community 35 - "Tasks: Add Admin Database Dashboard"
Cohesion: 0.14
Nodes (13): admin-dashboard Specification (Delta), MODIFIED Requirements, Requirement: Centralized admin web UI, Requirement: Database-focused presentation, Requirement: Live overview of integration health, Scenario: Admin reaches all workflows from one place, Scenario: Drift alert surfaces on overview, Scenario: Mapping review shows lanes (+5 more)

### Community 36 - "Region V IRIMSV-to-LRMIS integration"
Cohesion: 0.20
Nodes (9): Admin web UI, Continuous operation, Local pilot, Production configuration, Region V IRIMSV-to-LRMIS integration, Safety model, Schema drift policy, Verification (+1 more)

### Community 37 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 38 - "migrations.py"
Cohesion: 0.29
Nodes (12): Path, NotFoundError, Requested entity/proposal/event does not exist., apply_migration(), _checksum(), _ensure_tracker(), list_migrations(), mark_applied() (+4 more)

### Community 39 - "Add Admin Database Dashboard"
Cohesion: 0.14
Nodes (13): ADDED Requirements, guided-workflow Specification (Delta), Requirement: Manager-facing terminology, Requirement: No hand-typed object identifiers, Requirement: One state and one next action per table, Requirement: Progressive disclosure of internals, Scenario: Audit trail keeps internal names, Scenario: Empty review queue (+5 more)

### Community 40 - "Project Context"
Cohesion: 0.29
Nodes (6): Agent Context Systems, Domain Glossary, Project Context, Project Conventions, Purpose, Tech Stack

### Community 41 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 42 - "lrmis_staging_db (MySQL 8.4 Service)"
Cohesion: 0.33
Nodes (6): lrmis_staging_db (MySQL 8.4 Service), real_target_db (MySQL 8.0.40 Real-Test Service), mysql-connector-python (MySQL Driver), sql/lrmis.sql, sql/lrmis_staging_init.sql, sql/real_test_target_setup.sql

### Community 43 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 44 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 45 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 78 - "Requirements"
Cohesion: 0.14
Nodes (13): admin-dashboard Specification, Purpose, Requirement: Centralized admin web UI, Requirement: Database-focused presentation, Requirement: Live overview of integration health, Requirement: One-click workflow launch with guarded tiers, Requirements, Scenario: Admin reaches all workflows from one place (+5 more)

### Community 79 - "Requirements"
Cohesion: 0.14
Nodes (13): audit-and-approval Specification, Purpose, Requirement: Approval remains a human gate, Requirement: Per-admin authentication with roles, Requirement: Reversibility affordances for dangerous actions, Requirement: Uniform audit trail on every mutation, Requirements, Scenario: Audit log is browsable (+5 more)

### Community 80 - "ADDED Requirements"
Cohesion: 0.15
Nodes (12): ADDED Requirements, admin-dashboard Specification (Delta), Requirement: Centralized admin web UI, Requirement: Database-focused presentation, Requirement: Live overview of integration health, Requirement: One-click workflow launch with guarded tiers, Scenario: Admin reaches all workflows from one place, Scenario: Destructive action demands typed confirmation (+4 more)

### Community 81 - "ADDED Requirements"
Cohesion: 0.15
Nodes (12): ADDED Requirements, audit-and-approval Specification (Delta), Requirement: Approval remains a human gate, Requirement: Per-admin authentication with roles, Requirement: Reversibility affordances for dangerous actions, Requirement: Uniform audit trail on every mutation, Scenario: Audit log is browsable, Scenario: Kill switch toggle audited (+4 more)

### Community 82 - "Requirements"
Cohesion: 0.15
Nodes (12): job-orchestration Specification, Purpose, Requirement: Concurrent execution guards, Requirement: Controllable delivery worker, Requirement: Durable allowlisted jobs, Requirement: Live job event streaming, Requirements, Scenario: Double deploy from two tabs (+4 more)

### Community 83 - "ADDED Requirements"
Cohesion: 0.17
Nodes (11): ADDED Requirements, job-orchestration Specification (Delta), Requirement: Concurrent execution guards, Requirement: Controllable delivery worker, Requirement: Durable allowlisted jobs, Requirement: Live job event streaming, Scenario: Double deploy from two tabs, Scenario: Job survives an API restart (+3 more)

### Community 84 - "Requirement: Safe migration apply"
Cohesion: 0.17
Nodes (11): migration-management Specification, Purpose, Requirement: Idempotent foundation SQL, Requirement: Safe migration apply, Requirement: Tracked migration state, Requirements, Scenario: Concurrent applies serialized, Scenario: Edited already-applied file is rejected (+3 more)

### Community 85 - "Migrations.tsx"
Cohesion: 0.30
Nodes (10): createJob(), getJob(), getMigrationSql(), listMigrations(), markMigrationApplied(), CreateJobPayload, JobRunner, useJobRunner() (+2 more)

### Community 86 - "ADDED Requirements"
Cohesion: 0.18
Nodes (10): ADDED Requirements, migration-management Specification (Delta), Requirement: Idempotent foundation SQL, Requirement: Safe migration apply, Requirement: Tracked migration state, Scenario: Concurrent applies serialized, Scenario: Edited already-applied file is rejected, Scenario: Failed migration is a no-op (+2 more)

### Community 87 - "Requirements"
Cohesion: 0.18
Nodes (10): Purpose, Requirement: Drift visibility and side-effect transparency, Requirement: On-demand schema scanning, Requirement: Queue and entity health API, Requirements, Scenario: Paused entities called out, Scenario: Quarantine inspection, Scenario: Scan detects target drift (+2 more)

### Community 88 - "ADDED Requirements"
Cohesion: 0.20
Nodes (9): ADDED Requirements, Requirement: Drift visibility and side-effect transparency, Requirement: On-demand schema scanning, Requirement: Queue and entity health API, Scenario: Paused entities called out, Scenario: Quarantine inspection, Scenario: Scan detects target drift, Scenario: Scan with no changes (+1 more)

### Community 89 - "Tasks: Add Admin Database Dashboard"
Cohesion: 0.20
Nodes (9): 1. Foundations (SQL + service extraction), 2. Backend API core, 3. Job orchestration, 4. Schema observability, 5. Migration management, 6. Frontend, 7. Tests & verification, 8. Docs & context refresh (+1 more)

### Community 90 - "Requirement: Durable allowlisted jobs"
Cohesion: 0.20
Nodes (9): job-orchestration Specification (Delta), MODIFIED Requirements, Requirement: Concurrent execution guards, Requirement: Durable allowlisted jobs, Scenario: Bulk onboard is an allowlisted type, Scenario: Double deploy from two tabs, Scenario: Job survives an API restart, Scenario: Overlapping bulk onboards (+1 more)

### Community 91 - "Add Admin Database Dashboard"
Cohesion: 0.25
Nodes (7): Add Admin Database Dashboard, Capabilities, Impact, Modified Capabilities, New Capabilities, What Changes, Why

### Community 92 - "Tasks: Simplify the Database Manager Experience"
Cohesion: 0.25
Nodes (7): 1. Data browser backend, 2. Bulk onboard + proposal listing, 3. Backend tests, 4. Design system, 5. Frontend workflow, 6. Verification and docs, Tasks: Simplify the Database Manager Experience

### Community 93 - "PipelineDiagram.tsx"
Cohesion: 0.29
Nodes (3): Health, PipelineDiagramProps, TONE

## Knowledge Gaps
- **307 isolated node(s):** `import_irimsv_data.sh script`, `import_lrmis_schema.sh script`, `name`, `private`, `version` (+302 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PostgresCentralConnector` connect `MySQL Staging Connector` to `Outbox & Delivery Store`, `AI Mapping Engine`, `Terminal UI & Pipeline Entry`, `migrations.py`, `Staging Fast Refresh`, `Decisions`, `Onboarding.tsx`?**
  _High betweenness centrality (0.025) - this node is a cross-community bridge._
- **Why does `ValidationError` connect `ValidationError` to `AI Mapping Engine`, `migrations.py`, `MySQL Staging Connector`, `JobRunner`, `test_admin_api.py`, `ADDED Requirements`, `Onboarding.tsx`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **Why does `Schema` connect `Package Init` to `Schema Drift Monitoring`, `Outbox & Delivery Store`, `AI Mapping Engine`, `Terminal UI & Pipeline Entry`, `MySQL Staging Connector`, `Staging Fast Refresh`, `Decisions`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Schema` (e.g. with `FieldMapping` and `_Client`) actually correct?**
  _`Schema` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `ValidationError` (e.g. with `create_app()` and `test_enqueue_validates_type_before_db()`) actually correct?**
  _`ValidationError` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `NotFoundError` (e.g. with `create_app()` and `test_unmanaged_migration_file_rejected()`) actually correct?**
  _`NotFoundError` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `End-to-end demo of the pipeline using fake data, so you can see the whole flow b`, `This is YOUR schema -- stable, never changes per target system.`, `import_irimsv_data.sh script` to the rest of the system?**
  _415 weakly-connected nodes found - possible documentation gaps or missing edges._