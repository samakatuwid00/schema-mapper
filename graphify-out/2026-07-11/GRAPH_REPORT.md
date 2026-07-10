# Graph Report - schema_mapper  (2026-07-11)

## Corpus Check
- 151 files · ~85,317 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1390 nodes · 2880 edges · 112 communities (86 shown, 26 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 107 edges (avg confidence: 0.61)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `67602013`
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
- view_proposer.py
- JobRunner
- lrmis_registry.py
- lrmis_writer.py
- snapshots.py
- JobDrawer.tsx
- Onboarding.tsx
- integration_admin.py
- onboarding.py
- _Cursor
- rebaseline_entity_fingerprints
- db.py
- labels.ts
- group_by_table
- WriterError
- refresh

## God Nodes (most connected - your core abstractions)
1. `PostgresCentralConnector` - 70 edges
2. `MySQLStagingConnector` - 58 edges
3. `Schema` - 51 edges
4. `ValidationError` - 39 edges
5. `LrmisRegistry` - 35 edges
6. `NotFoundError` - 35 edges
7. `Table` - 34 edges
8. `_onboard_single_table()` - 30 edges
9. `Column` - 26 edges
10. `AdminUser` - 25 edges

## Surprising Connections (you probably didn't know these)
- `real_source_db (PostgreSQL 17 Real-Test Service)` --semantically_similar_to--> `central_db (PostgreSQL 16 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `real_target_db (MySQL 8.0.40 Real-Test Service)` --semantically_similar_to--> `lrmis_staging_db (MySQL 8.4 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `_Cursor` --uses--> `LrmisRegistry`  [INFERRED]
  tests/test_lrmis_writer.py → src/lrmis_registry.py
- `FakeConn` --uses--> `LrmisRegistry`  [INFERRED]
  tests/test_lrmis_writer.py → src/lrmis_registry.py
- `test_seed_tables_are_all_in_the_schema()` --calls--> `get_registry()`  [EXTRACTED]
  tests/test_init_lrmis_target.py → src/lrmis_registry.py

## Import Cycles
- None detected.

## Communities (112 total, 26 thin omitted)

### Community 0 - "Schema Drift Monitoring"
Cohesion: 0.12
Nodes (20): connection, cmd_deploy(), cmd_reconcile(), cmd_refresh(), cmd_review(), cmd_status(), _create_staging_table(), _detect_collation() (+12 more)

### Community 1 - "Architecture & Deployment Docs"
Cohesion: 0.33
Nodes (6): central_db (PostgreSQL 16 Service), real_source_db (PostgreSQL 17 Real-Test Service), psycopg2-binary (PostgreSQL Driver), sql/001_integration_foundation.sql, sql/002_onboarding_metadata.sql, sql/central_db_init.sql

### Community 2 - "Outbox & Delivery Store"
Cohesion: 0.16
Nodes (24): Event, approved_mapping(), canonical_json(), checksum(), claim_events(), delivered(), Exception, quarantine() (+16 more)

### Community 3 - "AI Mapping Engine"
Cohesion: 0.05
Nodes (83): AdminUser, BaseModel, main(), Bootstrap or update an admin UI user.  Usage: python scripts/create_admin_user.p, audited(), list_audit(), Uniform admin_action_audit writer for every mutating endpoint and job., Audit success or failure of the wrapped block (failure re-raises). (+75 more)

### Community 4 - "Pipeline Commands & Central DB"
Cohesion: 0.08
Nodes (38): ApiError, getJob(), ViewProposal, AdminUser, AuditRow, CompareField, CompareResponse, CreateJobPayload (+30 more)

### Community 5 - "Terminal UI & Pipeline Entry"
Cohesion: 0.13
Nodes (28): cmd_backfill(), cmd_monitor(), cmd_propose(), cmd_resolve(), _create_proposal(), _create_source_trigger(), _detect_cross_table_candidates(), _discover_source_schema() (+20 more)

### Community 6 - "MySQL Staging Connector"
Cohesion: 0.14
Nodes (27): main(), Preview or apply the one-time entity fingerprint scope migration., PostgresCentralConnector, approve_mapping(), approve_schema(), cancel_queue(), _entity_fingerprints(), get_schema_trees() (+19 more)

### Community 7 - "Staging Fast Refresh"
Cohesion: 0.14
Nodes (8): _clamp_mysql_dates(), Database adapters for the authoritative PostgreSQL DB and LRMIS MySQL staging., Return a qualified table name, optionally database-prefixed for views., Reject anything that is not a bare SQL identifier.      Callers must additionall, Bulk insert using executemany for better performance., Replace out-of-range Python date/datetime objects (year > 9999) with None., safe_identifier(), _sort_clause()

### Community 8 - "Deployment & Staging DDL"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 9 - "Pilot Bootstrap Scripts"
Cohesion: 0.47
Nodes (4): insert_customer(), Simulates your real application writing a new customer. The trigger on `customer, central_conn(), Backward-compatible central connection helper.  New integration code uses pooled

### Community 10 - "Mapping Proposals"
Cohesion: 0.13
Nodes (20): listMigrations(), login(), logout(), markMigrationApplied(), me(), setUnauthorizedHandler(), User, NAV_GROUPS (+12 more)

### Community 11 - "Schema Discovery"
Cohesion: 0.07
Nodes (26): dependencies, lucide-react, react, react-dom, react-router-dom, @tanstack/react-query, devDependencies, jsdom (+18 more)

### Community 15 - "Package Init"
Cohesion: 0.17
Nodes (27): A read-only reference row (e.g. psgc) could not be resolved., ReferenceRowNotFound, FakeConn, _mysql(), Writer: parent-first order, read-only reference tables, app-assigned ids for `st, Records SQL. `responses` is an ordered list of (marker, row) pairs;     the firs, A stateful counter mimicking allocate_id's atomic increment., The pipeline must never mint new geographic codes. (+19 more)

### Community 16 - "ValidationError"
Cohesion: 0.16
Nodes (17): FastAPI, create_app(), FastAPI app factory and entrypoint (python -m src.admin_api.app)., _h_cancel_queue(), _h_refresh_all(), ConflictError, Exception, Typed exceptions shared by all services; the API maps them to HTTP codes. (+9 more)

### Community 17 - "JobRunner"
Cohesion: 0.07
Nodes (18): write_audit(), enqueue(), _h_backfill(), _h_deploy(), _h_discover(), _h_onboard_bulk(), _h_propose(), _h_refresh() (+10 more)

### Community 18 - "test_admin_api.py"
Cohesion: 0.08
Nodes (10): admin_client(), _client_as(), operator_client(), Admin API tests: auth gating, role checks, job allowlist, guard tiers.  These ru, The handler must refuse an empty batch rather than 'succeed' on nothing., Spoofed actor/by fields are ignored - identity comes from the session., Assert on named files; keyed off MIGRATION_FILES[-1] this broke whenever     a n, test_action_bodies_do_not_accept_actor_fields() (+2 more)

### Community 19 - "Overview.tsx"
Cohesion: 0.19
Nodes (14): createJob(), getSnapshots(), getWorkerStatus(), restoreSnapshot(), toggleEntity(), HealthCard(), HealthCardProps, Sparkline() (+6 more)

### Community 20 - "compilerOptions"
Cohesion: 0.11
Nodes (17): compilerOptions, isolatedModules, jsx, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 21 - "Decisions"
Cohesion: 0.12
Nodes (6): LrmisRegistry, LrmisTable, Fallback when the DDL file is unavailable: read the live database., The column on `table` that points at `ref_table` (first match)., True when the pipeline must not INSERT into this table.          A table whose p, Parent-first ordering. Self-loops are ignored; real cycles raise.          `subs

### Community 22 - "WorkerQueues.tsx"
Cohesion: 0.22
Nodes (11): getDeadLetter(), getQuarantine(), replayEvent(), startWorker(), stopWorker(), Semantic, SEMANTIC_BY_STATUS, StatusChip() (+3 more)

### Community 23 - "ADDED Requirements"
Cohesion: 0.20
Nodes (15): _FakeCentral, _proposal(), onboard_bulk: conservative bucketing, non-destructiveness, resilience.  The serv, Stand-in for the service functions, recording what bulk actually calls., _Recorder, _run(), test_already_deployed_table_is_skipped_untouched(), test_confident_table_is_deployed_and_backfilled() (+7 more)

### Community 24 - "ADDED Requirements"
Cohesion: 0.12
Nodes (19): compareRow(), get(), getDataRows(), getDataTables(), getMigrationSql(), getSchemas(), getViewProposals(), listUsers() (+11 more)

### Community 25 - "JobDrawer.tsx"
Cohesion: 0.13
Nodes (18): applyView(), cancelQueue(), createUser(), generateView(), post(), request(), setUserActive(), EntityControl (+10 more)

### Community 26 - "StatusChip.tsx"
Cohesion: 0.23
Nodes (11): approveMapping(), getProposal(), getProposals(), resolveMapping(), ProposalField, confidenceClass(), GROUP_ORDER, MappingLanes() (+3 more)

### Community 27 - "ADDED Requirements"
Cohesion: 0.19
Nodes (11): _FakeCentral, _FakeStaging, Data browser: allowlisting, clamping, and identifier safety.  Runs without a dat, test_bad_direction_is_rejected(), test_page_below_one_is_clamped(), test_page_size_is_clamped_not_rejected(), test_sort_column_not_in_table_is_rejected(), test_target_side_reads_staging() (+3 more)

### Community 28 - "MappingReview.tsx"
Cohesion: 0.25
Nodes (13): approveSchema(), getAudit(), getDriftReports(), SchemaSystem, CopyButton(), SchemaTree(), SchemaTreeProps, AuditLog() (+5 more)

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
Cohesion: 0.26
Nodes (13): get_job(), NotFoundError, Requested entity/proposal/event does not exist., apply_migration(), _checksum(), _ensure_tracker(), list_migrations(), mark_applied() (+5 more)

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
Cohesion: 0.10
Nodes (8): parse_ddl(), RuntimeError, Parse CREATE TABLE blocks out of a MySQL dump., The FK graph has a cycle that is not a simple self-reference., SchemaCycleError, Registry: DDL parsing, reference-table detection, self-loop-safe topo sort.  The, registry(), test_real_cycle_raises()

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
Cohesion: 0.22
Nodes (4): Health, PipelineDiagramProps, TONE, base

### Community 96 - "view_proposer.py"
Cohesion: 0.15
Nodes (22): _all_columns(), _all_tables(), apply_view(), _discover_fks(), _ensure_table(), _fetchval(), _find_join_path(), _generate_view_sql() (+14 more)

### Community 97 - "JobRunner"
Cohesion: 0.16
Nodes (15): cmd_onboard(), _infer_column_type(), _onboard_single_table(), Onboard a single table end-to-end. Returns result dict., Onboard one or more tables end-to-end., Map target column type to MySQL DDL type., print_cross_table_candidates(), print_deployment_summary() (+7 more)

### Community 98 - "lrmis_registry.py"
Cohesion: 0.12
Nodes (19): iter_seed_statements(), main(), Path B, Phase 1: create and seed the `lrmis_target` database.  Builds a parallel, Yield complete `INSERT INTO <wanted>` statements from a mysqldump.      Streams, Drop degenerate self-referential FKs (a column referencing itself).      e.g. `C, _root_config(), sanitize_ddl(), ddl_path() (+11 more)

### Community 99 - "lrmis_writer.py"
Cohesion: 0.18
Nodes (21): allocate_id(), _apply_foreign_keys(), _crosswalk_lookup(), _crosswalk_record(), crosswalk_rows_for_entity(), delete_entity_rows(), _insert(), _quote() (+13 more)

### Community 100 - "snapshots.py"
Cohesion: 0.22
Nodes (16): MySQLStagingConnector, Least-privilege writer. It never creates or alters LRMIS tables., deploy(), restore_staging_snapshot(), staging_snapshots(), list_snapshots(), prune_snapshots(), _qt() (+8 more)

### Community 101 - "JobDrawer.tsx"
Cohesion: 0.23
Nodes (9): listJobs(), EVENT_TYPES, JobEvent, JobEventPayload, JobEventType, SseHandle, subscribeJobEvents(), JobDrawer() (+1 more)

### Community 102 - "Onboarding.tsx"
Cohesion: 0.23
Nodes (8): getStatus(), GuardedActionModalProps, GuardTier, DiscoveredTable, extractTables(), Onboarding(), STEPS, errStatus()

### Community 103 - "integration_admin.py"
Cohesion: 0.31
Nodes (7): main(), Minimal administrator/auditor CLI; suitable for wrapping in a future web UI., set_enabled(), status(), replay(), approve(), Immutable, reviewed database-backed mapping versions.

### Community 104 - "onboarding.py"
Cohesion: 0.06
Nodes (80): get_mapping(), load_their_schema(), main(), our_central_schema(), End-to-end demo of the pipeline using fake data, so you can see the whole flow b, This is YOUR schema -- stable, never changes per target system., AI Draft Mapping Proposals, google-genai (Official Gemini SDK) (+72 more)

### Community 105 - "_Cursor"
Cohesion: 0.15
Nodes (6): RuntimeError, Base class for multi-table write failures., A mapping names a table that is not part of the LRMIS schema., UnknownTargetTable, WriterError, _Cursor

### Community 107 - "db.py"
Cohesion: 0.40
Nodes (3): central(), Process-wide pooled connectors shared by all request handlers and jobs., staging()

### Community 108 - "labels.ts"
Cohesion: 0.70
Nodes (3): DESCRIPTIONS, label(), LABELS

### Community 109 - "group_by_table"
Cohesion: 0.33
Nodes (6): cmd_discover(), _get_or_create_entity(), _rank_target_tables(), Rank target tables by name similarity to source table., Get existing or create new onboarding entity., Discover source tables and suggest target candidates.

### Community 110 - "WriterError"
Cohesion: 0.67
Nodes (3): group_by_table(), Group column mappings by their `target_table`., test_group_by_table_splits_columns()

### Community 112 - "refresh"
Cohesion: 0.28
Nodes (8): drop_staging_table(), fetch_and_bulk_insert(), generate_refresh_sql(), _qt(), Fast refresh module for dropping and recreating staging tables. Bypasses the out, Fetch from PostgreSQL and bulk insert to MySQL., Drop staging table if it exists., Generate PostgreSQL SELECT statement for refresh.

## Knowledge Gaps
- **310 isolated node(s):** `import_irimsv_data.sh script`, `import_lrmis_schema.sh script`, `name`, `private`, `version` (+305 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **26 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PostgresCentralConnector` connect `MySQL Staging Connector` to `Schema Drift Monitoring`, `JobRunner`, `Outbox & Delivery Store`, `AI Mapping Engine`, `snapshots.py`, `Terminal UI & Pipeline Entry`, `migrations.py`, `Staging Fast Refresh`, `onboarding.py`, `integration_admin.py`, `view_proposer.py`, `db.py`, `group_by_table`, `Onboarding.tsx`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Why does `MySQLStagingConnector` connect `snapshots.py` to `Schema Drift Monitoring`, `JobRunner`, `Outbox & Delivery Store`, `Terminal UI & Pipeline Entry`, `MySQL Staging Connector`, `Staging Fast Refresh`, `onboarding.py`, `db.py`, `Onboarding.tsx`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Why does `Schema` connect `onboarding.py` to `JobRunner`, `Outbox & Delivery Store`, `AI Mapping Engine`, `snapshots.py`, `Terminal UI & Pipeline Entry`, `group_by_table`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Schema` (e.g. with `FieldMapping` and `_Client`) actually correct?**
  _`Schema` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `ValidationError` (e.g. with `create_app()` and `test_enqueue_validates_type_before_db()`) actually correct?**
  _`ValidationError` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `LrmisRegistry` (e.g. with `ReferenceRowNotFound` and `UnknownTargetTable`) actually correct?**
  _`LrmisRegistry` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `End-to-end demo of the pipeline using fake data, so you can see the whole flow b`, `This is YOUR schema -- stable, never changes per target system.`, `import_irimsv_data.sh script` to the rest of the system?**
  _469 weakly-connected nodes found - possible documentation gaps or missing edges._