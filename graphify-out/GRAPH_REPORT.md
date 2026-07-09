# Graph Report - .  (2026-07-09)

## Corpus Check
- Corpus is ~33,973 words - fits in a single context window. You may not need a graph.

## Summary
- 255 nodes · 651 edges · 16 communities (13 shown, 3 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 36 edges (avg confidence: 0.62)
- Token cost: 67,848 input · 5,200 output

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

## God Nodes (most connected - your core abstractions)
1. `Schema` - 44 edges
2. `Table` - 31 edges
3. `_onboard_single_table()` - 30 edges
4. `MySQLStagingConnector` - 26 edges
5. `PostgresCentralConnector` - 24 edges
6. `Column` - 23 edges
7. `cmd_refresh()` - 18 edges
8. `propose_mapping()` - 14 edges
9. `_run_worker_batch()` - 14 edges
10. `cmd_propose()` - 14 edges

## Surprising Connections (you probably didn't know these)
- `real_source_db (PostgreSQL 17 Real-Test Service)` --semantically_similar_to--> `central_db (PostgreSQL 16 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `real_target_db (MySQL 8.0.40 Real-Test Service)` --semantically_similar_to--> `lrmis_staging_db (MySQL 8.4 Service)`  [INFERRED] [semantically similar]
  docker-compose.real-test.yml → docker-compose.yml
- `IRIMSV (Region V Data-Entry System)` --shares_data_with--> `central_db (PostgreSQL 16 Service)`  [INFERRED]
  README.md → docker-compose.yml
- `LRMIS Staging Contract` --references--> `lrmis_staging_db (MySQL 8.4 Service)`  [INFERRED]
  README.md → docker-compose.yml
- `psycopg2-binary (PostgreSQL Driver)` --conceptually_related_to--> `central_db (PostgreSQL 16 Service)`  [INFERRED]
  requirements.txt → docker-compose.yml

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Idempotent Outbound Delivery Pipeline** — readme_outbox_pattern, readme_external_reference_uuid, readme_idempotent_delivery, src_worker, docker_compose_lrmis_staging_db [INFERRED 0.85]
- **Schema Drift Detection and Response** — scripts_schema_monitor, readme_schema_drift_policy, readme_entity_kill_switch, scripts_integration_admin [EXTRACTED 1.00]
- **Local Pilot Bootstrap Flow** — scripts_schema_monitor, scripts_create_pilot_mapping, scripts_integration_admin, scripts_insert_sample_row, src_worker, scripts_reconcile [EXTRACTED 1.00]

## Communities (16 total, 3 thin omitted)

### Community 0 - "Schema Drift Monitoring"
Cohesion: 0.10
Nodes (34): main(), observe(), Observe LRMIS staging metadata, record drift, and selectively pause mappings., Database adapters for the authoritative PostgreSQL DB and LRMIS MySQL staging., Get target schema from schema_version table., _target_schema(), diff_schemas(), impacted_entities() (+26 more)

### Community 1 - "Architecture & Deployment Docs"
Cohesion: 0.06
Nodes (35): central_db (PostgreSQL 16 Service), lrmis_staging_db (MySQL 8.4 Service), real_source_db (PostgreSQL 17 Real-Test Service), real_target_db (MySQL 8.0.40 Real-Test Service), Local Development Stack, AI Draft Mapping Proposals, Customer Pilot Entity, Entity Kill Switch (+27 more)

### Community 2 - "Outbox & Delivery Store"
Cohesion: 0.13
Nodes (27): Exception, approved_mapping(), canonical_json(), checksum(), claim_events(), delivered(), quarantine(), Durable PostgreSQL state transitions for delivery, replay, and audit. (+19 more)

### Community 3 - "AI Mapping Engine"
Cohesion: 0.16
Nodes (23): get_mapping(), load_their_schema(), main(), our_central_schema(), End-to-end demo of the pipeline using fake data, so you can see the whole flow b, This is YOUR schema -- stable, never changes per target system., google-genai (Official Gemini SDK), FieldMapping (+15 more)

### Community 4 - "Pipeline Commands & Central DB"
Cohesion: 0.16
Nodes (20): connection, PostgresCentralConnector, cmd_backfill(), cmd_monitor(), cmd_onboard(), cmd_reconcile(), cmd_resolve(), cmd_review() (+12 more)

### Community 5 - "Terminal UI & Pipeline Entry"
Cohesion: 0.17
Nodes (15): generate_external_reference(), Generic AI-Assisted Onboarding Pipeline  Usage:     python -m src.pipeline disco, Generate a deterministic UUIDv5 from source system + schema + table + canonical, print_cross_table_candidates(), print_deployment_summary(), print_field_mapping_table(), print_final_summary(), print_header() (+7 more)

### Community 6 - "MySQL Staging Connector"
Cohesion: 0.24
Nodes (4): MySQLStagingConnector, Bulk insert using executemany for better performance., Least-privilege writer. It never creates or alters LRMIS tables., test_mysql_identifier_validation_happens_before_connection()

### Community 7 - "Staging Fast Refresh"
Cohesion: 0.20
Nodes (11): drop_staging_table(), fetch_and_bulk_insert(), generate_refresh_sql(), Fast refresh module for dropping and recreating staging tables. Bypasses the out, Drop staging table if it exists., Generate PostgreSQL SELECT statement for refresh., Fetch from PostgreSQL and bulk insert to MySQL., cmd_refresh() (+3 more)

### Community 8 - "Deployment & Staging DDL"
Cohesion: 0.24
Nodes (11): cmd_deploy(), _create_source_trigger(), _create_staging_table(), _detect_collation(), _execute(), _onboard_single_table(), Query target MySQL for default collation., Onboard a single table end-to-end. Returns result dict. (+3 more)

### Community 9 - "Pilot Bootstrap Scripts"
Cohesion: 0.24
Nodes (8): Customer Pilot Bootstrap Flow, Reconciliation, scripts/create_pilot_mapping.py, insert_customer(), Simulates your real application writing a new customer. The trigger on `customer, scripts/reconcile.py, central_conn(), Backward-compatible central connection helper.  New integration code uses pooled

### Community 10 - "Mapping Proposals"
Cohesion: 0.22
Nodes (10): mapping_to_dicts(), cmd_propose(), _create_proposal(), _detect_cross_table_candidates(), _discover_target_schema(), _fetchval(), For each rejected source column, check if it appears in ANY other target table., Get target schema from schema_version table. (+2 more)

### Community 11 - "Schema Discovery"
Cohesion: 0.25
Nodes (8): cmd_discover(), _discover_source_schema(), _get_or_create_entity(), _rank_target_tables(), Ingest source schema from PostgreSQL information_schema., Rank target tables by name similarity to source table., Get existing or create new onboarding entity., Discover source tables and suggest target candidates.

## Knowledge Gaps
- **18 isolated node(s):** `import_irimsv_data.sh script`, `import_lrmis_schema.sh script`, `Graphify Knowledge Graph`, `Customer Pilot Entity`, `Reconciliation` (+13 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Schema` connect `Schema Drift Monitoring` to `Outbox & Delivery Store`, `AI Mapping Engine`, `Terminal UI & Pipeline Entry`, `Staging Fast Refresh`, `Deployment & Staging DDL`, `Mapping Proposals`, `Schema Discovery`?**
  _High betweenness centrality (0.174) - this node is a cross-community bridge._
- **Why does `PostgresCentralConnector` connect `Pipeline Commands & Central DB` to `Schema Drift Monitoring`, `Architecture & Deployment Docs`, `Outbox & Delivery Store`, `Terminal UI & Pipeline Entry`, `MySQL Staging Connector`, `Staging Fast Refresh`, `Deployment & Staging DDL`, `Mapping Proposals`, `Schema Discovery`?**
  _High betweenness centrality (0.107) - this node is a cross-community bridge._
- **Why does `MySQLStagingConnector` connect `MySQL Staging Connector` to `Schema Drift Monitoring`, `Outbox & Delivery Store`, `Pipeline Commands & Central DB`, `Terminal UI & Pipeline Entry`, `Staging Fast Refresh`, `Deployment & Staging DDL`, `Mapping Proposals`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `Schema` (e.g. with `FieldMapping` and `_Client`) actually correct?**
  _`Schema` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Table` (e.g. with `FieldMapping` and `print_field_mapping_table()`) actually correct?**
  _`Table` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `End-to-end demo of the pipeline using fake data, so you can see the whole flow b`, `This is YOUR schema -- stable, never changes per target system.`, `import_irimsv_data.sh script` to the rest of the system?**
  _80 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Schema Drift Monitoring` be split into smaller, more focused modules?**
  _Cohesion score 0.1048265460030166 - nodes in this community are weakly interconnected._