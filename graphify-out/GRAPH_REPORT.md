# Graph Report - schema_mapper  (2026-07-09)

## Corpus Check
- 113 files · ~57,378 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 880 nodes · 1881 edges · 78 communities (53 shown, 25 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 74 edges (avg confidence: 0.61)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `89ae59d5`
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

## God Nodes (most connected - your core abstractions)
1. `PostgresCentralConnector` - 54 edges
2. `Schema` - 46 edges
3. `MySQLStagingConnector` - 44 edges
4. `Table` - 31 edges
5. `_onboard_single_table()` - 30 edges
6. `NotFoundError` - 24 edges
7. `AdminUser` - 23 edges
8. `Column` - 23 edges
9. `errMsg()` - 22 edges
10. `ValidationError` - 21 edges

## Surprising Connections (you probably didn't know these)
- `real_source_db (PostgreSQL 17 Real-Test Service)` --semantically_similar_to--> `central_db (PostgreSQL 16 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `real_target_db (MySQL 8.0.40 Real-Test Service)` --semantically_similar_to--> `lrmis_staging_db (MySQL 8.4 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `test_unmanaged_migration_file_rejected()` --indirect_call--> `NotFoundError`  [INFERRED]
  tests/test_admin_api.py → src/services/common.py
- `test_worker_controller_stop_without_start_conflicts()` --indirect_call--> `ConflictError`  [INFERRED]
  tests/test_admin_api.py → src/services/common.py
- `test_enqueue_validates_type_before_db()` --indirect_call--> `ValidationError`  [INFERRED]
  tests/test_admin_api.py → src/services/common.py

## Import Cycles
- None detected.

## Communities (78 total, 25 thin omitted)

### Community 0 - "Schema Drift Monitoring"
Cohesion: 0.06
Nodes (73): get_mapping(), load_their_schema(), main(), our_central_schema(), End-to-end demo of the pipeline using fake data, so you can see the whole flow b, This is YOUR schema -- stable, never changes per target system., AI Draft Mapping Proposals, google-genai (Official Gemini SDK) (+65 more)

### Community 1 - "Architecture & Deployment Docs"
Cohesion: 0.33
Nodes (6): central_db (PostgreSQL 16 Service), real_source_db (PostgreSQL 17 Real-Test Service), psycopg2-binary (PostgreSQL Driver), sql/001_integration_foundation.sql, sql/002_onboarding_metadata.sql, sql/central_db_init.sql

### Community 2 - "Outbox & Delivery Store"
Cohesion: 0.12
Nodes (25): Event, main(), Minimal administrator/auditor CLI; suitable for wrapping in a future web UI., set_enabled(), status(), canonical_json(), checksum(), claim_events() (+17 more)

### Community 3 - "AI Mapping Engine"
Cohesion: 0.06
Nodes (71): AdminUser, BaseModel, Request, main(), Bootstrap or update an admin UI user.  Usage: python scripts/create_admin_user.p, audited(), list_audit(), Uniform admin_action_audit writer for every mutating endpoint and job. (+63 more)

### Community 4 - "Pipeline Commands & Central DB"
Cohesion: 0.10
Nodes (29): ApiError, AdminUser, AuditRow, CreateJobPayload, CreateJobResponse, DeadLetterRow, DriftReport, EntityControl (+21 more)

### Community 5 - "Terminal UI & Pipeline Entry"
Cohesion: 0.06
Nodes (69): connection, drop_staging_table(), Drop staging table if it exists., mapping_to_dicts(), cmd_backfill(), cmd_deploy(), cmd_discover(), cmd_monitor() (+61 more)

### Community 6 - "MySQL Staging Connector"
Cohesion: 0.12
Nodes (17): central(), Process-wide pooled connectors shared by all request handlers and jobs., staging(), MySQLStagingConnector, Bulk insert using executemany for better performance., Least-privilege writer. It never creates or alters LRMIS tables., restore_staging_snapshot(), staging_snapshots() (+9 more)

### Community 7 - "Staging Fast Refresh"
Cohesion: 0.15
Nodes (23): PostgresCentralConnector, fetch_and_bulk_insert(), generate_refresh_sql(), Fast refresh module for dropping and recreating staging tables. Bypasses the out, Generate PostgreSQL SELECT statement for refresh., Fetch from PostgreSQL and bulk insert to MySQL., approved_mapping(), Look for approved mapping in both mapping_version and onboarding_proposal tables (+15 more)

### Community 8 - "Deployment & Staging DDL"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 9 - "Pilot Bootstrap Scripts"
Cohesion: 0.47
Nodes (4): insert_customer(), Simulates your real application writing a new customer. The trigger on `customer, central_conn(), Backward-compatible central connection helper.  New integration code uses pooled

### Community 10 - "Mapping Proposals"
Cohesion: 0.21
Nodes (18): approveSchema(), getDriftReports(), getJob(), listMigrations(), markMigrationApplied(), NAV_ITEMS, Shell(), useAuth() (+10 more)

### Community 11 - "Schema Discovery"
Cohesion: 0.08
Nodes (25): dependencies, react, react-dom, react-router-dom, @tanstack/react-query, devDependencies, jsdom, @testing-library/jest-dom (+17 more)

### Community 15 - "Package Init"
Cohesion: 0.13
Nodes (16): get_job(), _h_backfill(), _h_deploy(), _h_discover(), _h_propose(), Durable allowlisted job orchestration (job-orchestration spec).  Jobs live in in, NotFoundError, Requested entity/proposal/event does not exist. (+8 more)

### Community 16 - "ValidationError"
Cohesion: 0.16
Nodes (17): FastAPI, create_app(), FastAPI app factory and entrypoint (python -m src.admin_api.app)., enqueue(), _scope(), ConflictError, Exception, Typed exceptions shared by all services; the API maps them to HTTP codes. (+9 more)

### Community 17 - "JobRunner"
Cohesion: 0.13
Nodes (6): _h_refresh(), _h_schema_scan(), JobContext, JobRunner, runner(), WorkerController

### Community 18 - "test_admin_api.py"
Cohesion: 0.11
Nodes (9): read_migration_sql(), admin_client(), _client_as(), operator_client(), Admin API tests: auth gating, role checks, job allowlist, guard tiers.  These ru, Spoofed actor/by fields are ignored - identity comes from the session., test_action_bodies_do_not_accept_actor_fields(), test_migration_sql_readable_for_managed_files() (+1 more)

### Community 19 - "Overview.tsx"
Cohesion: 0.16
Nodes (17): createJob(), createUser(), getSnapshots(), login(), logout(), post(), request(), restoreSnapshot() (+9 more)

### Community 20 - "compilerOptions"
Cohesion: 0.11
Nodes (17): compilerOptions, isolatedModules, jsx, lib, module, moduleDetection, moduleResolution, noEmit (+9 more)

### Community 21 - "Decisions"
Cohesion: 0.13
Nodes (14): Context, D1. In-process services, not subprocesses, D2. FastAPI + SSE, D3. Durable jobs: `admin_job` + `admin_job_event`, D4. Concurrency guards live in Postgres, D5. Migration tracking, home-grown, D6. Auth and audit, D7. Guarded one-click contract (+6 more)

### Community 22 - "WorkerQueues.tsx"
Cohesion: 0.24
Nodes (14): get(), getAudit(), getDeadLetter(), getMigrationSql(), getQuarantine(), getSchemas(), getWorkerStatus(), listUsers() (+6 more)

### Community 23 - "ADDED Requirements"
Cohesion: 0.15
Nodes (12): ADDED Requirements, admin-dashboard Specification (Delta), Requirement: Centralized admin web UI, Requirement: Database-focused presentation, Requirement: Live overview of integration health, Requirement: One-click workflow launch with guarded tiers, Scenario: Admin reaches all workflows from one place, Scenario: Destructive action demands typed confirmation (+4 more)

### Community 24 - "ADDED Requirements"
Cohesion: 0.15
Nodes (12): ADDED Requirements, audit-and-approval Specification (Delta), Requirement: Approval remains a human gate, Requirement: Per-admin authentication with roles, Requirement: Reversibility affordances for dangerous actions, Requirement: Uniform audit trail on every mutation, Scenario: Audit log is browsable, Scenario: Kill switch toggle audited (+4 more)

### Community 25 - "JobDrawer.tsx"
Cohesion: 0.23
Nodes (9): listJobs(), EVENT_TYPES, JobEvent, JobEventPayload, JobEventType, SseHandle, subscribeJobEvents(), JobDrawer() (+1 more)

### Community 26 - "StatusChip.tsx"
Cohesion: 0.22
Nodes (9): ProposalField, confidenceClass(), GROUP_ORDER, MappingLanes(), MappingLanesProps, TRANSFORM_OPTIONS, COLOR_BY_STATUS, StatusChip() (+1 more)

### Community 27 - "ADDED Requirements"
Cohesion: 0.17
Nodes (11): ADDED Requirements, job-orchestration Specification (Delta), Requirement: Concurrent execution guards, Requirement: Controllable delivery worker, Requirement: Durable allowlisted jobs, Requirement: Live job event streaming, Scenario: Double deploy from two tabs, Scenario: Job survives an API restart (+3 more)

### Community 28 - "MappingReview.tsx"
Cohesion: 0.30
Nodes (9): approveMapping(), getProposal(), resolveMapping(), SchemaSystem, CopyButton(), SchemaTree(), SchemaTreeProps, MappingReview() (+1 more)

### Community 29 - "Onboarding.tsx"
Cohesion: 0.23
Nodes (8): getStatus(), GuardedActionModalProps, GuardTier, DiscoveredTable, extractTables(), Onboarding(), STEPS, errStatus()

### Community 30 - "SKILL.md"
Cohesion: 0.18
Nodes (10): Check for context, Ending Discovery, Guardrails, Handling Different Entry Points, OpenSpec Awareness, The Stance, What You Don't Have To Do, What You Might Do (+2 more)

### Community 31 - "ADDED Requirements"
Cohesion: 0.18
Nodes (10): ADDED Requirements, migration-management Specification (Delta), Requirement: Idempotent foundation SQL, Requirement: Safe migration apply, Requirement: Tracked migration state, Scenario: Concurrent applies serialized, Scenario: Edited already-applied file is rejected, Scenario: Failed migration is a no-op (+2 more)

### Community 32 - "auth.tsx"
Cohesion: 0.27
Nodes (7): me(), setUnauthorizedHandler(), User, AuthContext, AuthContextValue, AuthProvider(), queryClient

### Community 33 - "explore.md"
Cohesion: 0.20
Nodes (9): Check for context, Ending Discovery, Guardrails, OpenSpec Awareness, The Stance, What You Don't Have To Do, What You Might Do, When a change exists (+1 more)

### Community 34 - "ADDED Requirements"
Cohesion: 0.20
Nodes (9): ADDED Requirements, Requirement: Drift visibility and side-effect transparency, Requirement: On-demand schema scanning, Requirement: Queue and entity health API, Scenario: Paused entities called out, Scenario: Quarantine inspection, Scenario: Scan detects target drift, Scenario: Scan with no changes (+1 more)

### Community 35 - "Tasks: Add Admin Database Dashboard"
Cohesion: 0.20
Nodes (9): 1. Foundations (SQL + service extraction), 2. Backend API core, 3. Job orchestration, 4. Schema observability, 5. Migration management, 6. Frontend, 7. Tests & verification, 8. Docs & context refresh (+1 more)

### Community 36 - "Region V IRIMSV-to-LRMIS integration"
Cohesion: 0.20
Nodes (9): Admin web UI, Continuous operation, Local pilot, Production configuration, Region V IRIMSV-to-LRMIS integration, Safety model, Schema drift policy, Verification (+1 more)

### Community 37 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 38 - "migrations.py"
Cohesion: 0.39
Nodes (8): Path, apply_migration(), _checksum(), _ensure_tracker(), list_migrations(), mark_applied(), Tracked, checksummed, advisory-lock-guarded SQL migration runner (central Postgr, Record a file as applied without executing it (Docker-initialized databases).

### Community 39 - "Add Admin Database Dashboard"
Cohesion: 0.25
Nodes (7): Add Admin Database Dashboard, Capabilities, Impact, Modified Capabilities, New Capabilities, What Changes, Why

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

## Knowledge Gaps
- **219 isolated node(s):** `import_irimsv_data.sh script`, `import_lrmis_schema.sh script`, `name`, `private`, `version` (+214 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PostgresCentralConnector` connect `Staging Fast Refresh` to `Schema Drift Monitoring`, `Outbox & Delivery Store`, `AI Mapping Engine`, `Terminal UI & Pipeline Entry`, `MySQL Staging Connector`, `migrations.py`, `Package Init`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Why does `Schema` connect `Schema Drift Monitoring` to `Terminal UI & Pipeline Entry`, `Package Init`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Why does `MySQLStagingConnector` connect `MySQL Staging Connector` to `Schema Drift Monitoring`, `Outbox & Delivery Store`, `Terminal UI & Pipeline Entry`, `Staging Fast Refresh`, `Package Init`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Schema` (e.g. with `FieldMapping` and `_Client`) actually correct?**
  _`Schema` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Table` (e.g. with `FieldMapping` and `print_field_mapping_table()`) actually correct?**
  _`Table` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `End-to-end demo of the pipeline using fake data, so you can see the whole flow b`, `This is YOUR schema -- stable, never changes per target system.`, `import_irimsv_data.sh script` to the rest of the system?**
  _312 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Schema Drift Monitoring` be split into smaller, more focused modules?**
  _Cohesion score 0.05713058419243986 - nodes in this community are weakly interconnected._