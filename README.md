# Region V IRIMSV-to-LRMIS integration

IRIMSV is the sole Region V data-entry system. This service keeps IRIMSV's PostgreSQL
schema authoritative, transforms approved shared-workflow records into an LRMIS contract,
stores a reproducible outbound projection, and idempotently publishes it to an LRMIS-owned
MySQL staging database. It never writes to LRMIS production.

## Safety model

- `irimsv` contains authoritative operational data.
- `integration` contains immutable mapping/schema versions, outbox events, projections,
  crosswalks, quarantine, retries, drift reports, entity kill switches, and audit records.
- `lrmis_projection` is reserved for reviewed SQL projections as entities are added.
- Every synchronized record has an immutable `external_reference` UUID.
- MySQL delivery uses that UUID as a unique upsert key, making retries safe.
- AI output may create draft mapping proposals only. An administrator must approve them.
- Breaking target changes pause only mappings that write to affected LRMIS tables.
- Deletes publish `operation=deactivate` and `active=false`; target rows are not destroyed.

## Local pilot

Requirements: Docker Compose and Python 3.10+.

```bash
pip install -r requirements.txt
docker compose up -d
python scripts/schema_monitor.py --approve-initial --by YOUR_NAME
python -m src.pipeline onboard --source-schema irimsv --source-table customer --target-system LRMIS --by YOUR_NAME
python scripts/insert_sample_row.py "Ada Lovelace" ada@example.com
python -m src.worker
python -m src.pipeline reconcile --entity customer
```

(The former `create_pilot_mapping.py`, `reconcile.py`, and `backfill_customers.py`
scripts were superseded by `python -m src.pipeline onboard/reconcile/backfill`.)

The first schema observation may be approved with `--approve-initial`. That flag never
approves later changes. For a later compatible contract, explicitly run:

```bash
python scripts/integration_admin.py approve-schema FINGERPRINT --by YOUR_NAME
```

Then create and approve a mapping version bound to that exact fingerprint.

## Continuous operation

Run the worker under a process supervisor; the default interval is five minutes:

```bash
python -m src.worker --loop --interval 300
python scripts/schema_monitor.py
python scripts/integration_admin.py status
```

Schedule `schema_monitor.py` before delivery. Configure monitoring for non-empty
`dead_letter`/`quarantined` queues, disabled entities, oldest pending event age, failed
schema checks, and reconciliation differences.

Useful controls:

```bash
# Kill switch for one entity; other entities keep running
python scripts/integration_admin.py entity customer --disable --reason "contract review"
python scripts/integration_admin.py entity customer --enable

# Reprocess a quarantined, dead-letter, or previously delivered event
python scripts/integration_admin.py replay EVENT_UUID

# Repeat-safe initial load through the same validation and delivery path
python -m src.pipeline backfill --entity customer
```

## Admin web UI

A guarded web dashboard centralizes every operation above (schema scans, onboarding,
mapping review, deploy, backfill, refresh, worker control, replay, kill switches,
migrations, audit log) with real-time job progress. Governing specs live in
`openspec/changes/add-admin-database-dashboard/`.

```bash
# one-time: admin tables (auto-applied on fresh docker volumes) + first login
docker exec -i schema_mapper_central_db psql -U postgres -d central < sql/003_admin_ui.sql
python scripts/create_admin_user.py YOUR_NAME --role admin

# set ADMIN_SESSION_SECRET in .env, then start the API (serves web/dist too)
python -m src.admin_api.app

# frontend dev server (proxies /api to :8400) or production build
cd web && npm install && npm run dev     # http://localhost:5173
cd web && npm run build                  # emits web/dist, served by the API
```

Dangerous actions are tiered: safe workflows are one click; deploy/backfill need a
reason; refresh, redeploy, and migration apply require typing the target name. Every
mutation is written to `integration.admin_action_audit` under the logged-in user -
client-supplied actor names are never trusted.

## Production configuration

Copy `.env.example` into a secret manager or service environment. In production:

- Require TLS (`LRMIS_STAGING_SSL_DISABLED=false`), private networking or IP allowlisting,
  and certificate verification.
- Give the MySQL account only `SELECT`, `INSERT`, and `UPDATE` on approved staging tables.
- Give the worker a PostgreSQL role scoped to `integration` plus read access to selected
  IRIMSV records; do not use database-owner credentials.
- Use encrypted backups, PostgreSQL point-in-time recovery, credential rotation, database
  monitoring, and tested restore procedures.
- Obtain a signed contract from LRMIS covering tables, required fields, enums, stable
  external references, acknowledgement semantics, retention, and privacy requirements.

The included `customer` entity is only a pilot. Inventory the actual duplicated teacher
workflow and add one reviewed entity at a time in dependency order.

## Schema drift policy

`schema_monitor.py` reads MySQL `information_schema`, normalizes it, and fingerprints the
contract. Added nullable fields are non-breaking. Removed tables/columns, datatype or key
changes, and newly required fields are breaking. A drift report is stored, affected entity
controls are disabled, and their approved mappings are paused. Unaffected entities continue.

No process automatically alters LRMIS staging or activates an AI-proposed mapping.

## Verification

```bash
pytest -q
python -m compileall -q src scripts tests
```

Tests cover schema normalization/fingerprints, breaking drift classification, business
validation versus envelope fields, and the outbound deactivation contract. Database-level
acceptance should additionally test outages, duplicate and out-of-order delivery, retries,
credential expiry, restore/replay, expected regional load, and field-level reconciliation.
