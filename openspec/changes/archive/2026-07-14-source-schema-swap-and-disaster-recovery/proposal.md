## Why

Three real gaps sit between the current system and "a complete tool a database
manager can run end to end," and each has concrete evidence behind it, not just
a feature wish:

1. **Schema adaptation is asymmetric.** `generic-ai-db-migration-engine` shipped
   a full, live-verified `schema-swap` flow — discover the new schema live, diff
   it against the approved contract, AI re-map only the affected entities
   (human-gated), recreate, redeliver — but it only runs in one direction: the
   **target**. If IRIMSV (the source) were itself restructured or pointed at a
   different database, nothing today runs the equivalent diff-and-remap for
   that side. The building block already exists (`PostgresSourceAdapter` does
   source-side discovery); it was never wired into a source-facing swap.

2. **There is no recovery path for the two failure points this project has
   already hit.** `docs/RUNBOOK_source_to_target.md` records a real incident:
   the source dump (`lrmis_dump.sql`) came out UTF-16 from a PowerShell `>`
   redirect, and `psql` couldn't read it — the nightly rebuild's guarded
   restore step had no way to proceed except an operator fixing the file by
   hand outside the system. Separately, `nightly_refresh` already takes a
   timestamped `mysqldump` backup of the target before every destructive reset
   (task 3.4, done) — but there is no UI to see those backups exist, let alone
   restore from one if a rebuild fails partway through. Both are the same
   underlying gap: the system detects and gates failure correctly, but gives
   the operator no in-UI way to recover from it.

3. **The AI agent a manager would actually talk to doesn't exist yet.** The
   backend (`MigrationAgent.plan/guide/heal`) is real and wired in, but it's a
   Python API. `openspec/changes/conversational-ai-assistant` already specs
   the chat layer a manager needs to say "map just these tables" or "why is
   this blocked" — it is not re-specified here, but this change assumes it
   lands, and extends its tool registry once it does (see Impact).

The UI-decluttering goal from the same conversation is already substantially
covered by two existing changes (`database-manager-ui-simplification`, nearly
complete, and `retire-legacy-staging`'s staging-page removal, in progress) and
is not re-specified here either — this change adds one new page (Recovery) and
otherwise leaves that work where it already lives.

## What Changes

- **Generalize `schema-swap` to be side-agnostic** rather than forking a
  parallel module: `src/services/schema_swap.py` gains a `side:
  "source"|"target"` parameter. The diff/AI-remap/human-gate/apply logic is
  symmetric — the swap doesn't care which side changed, only which adapter and
  which set of downstream entities to re-map. `scripts/schema_swap.py` and
  `scripts/sync_engine.py schema-swap` gain `--side source|target` (default
  `target`, preserving today's behavior).
- **New capability: source-side schema-swap.** Point the engine at a
  restructured or replaced IRIMSV source, diff it against the approved source
  contract, AI re-map only entities whose source columns are affected
  (human-gated on low confidence, exactly like the target direction), and
  resume delivery once approved. Never touches source data directly — this is
  a read-discovery + re-mapping operation, consistent with the project's rule
  that AI output is proposal-only.
- **New capability: disaster-recovery upload + restore**, in two parts:
  - *Source-restore recovery*: when `nightly_refresh`'s guarded restore step
    fails or `LRMIS_SOURCE_RESTORE_CMD` isn't configured, an admin uploads a
    replacement dump through the UI. The system validates it (format/magic-byte
    check reusing the existing `PGDMP` check pattern from
    `src/services/pg_restore.py`, encoding sanity check — the exact UTF-16
    problem that already happened — and a schema-target check that it
    contains the `irimsv` schema) before offering it as the restore source for
    that run. Never auto-executed; always a confirmed, audited action.
  - *Target recovery*: a new Recovery page lists the backups `nightly_refresh`
    already takes automatically (previously invisible to the UI) plus any
    manually uploaded target backup, and lets an admin restore the target from
    a selected one, gated behind the same typed-confirmation pattern every
    other destructive action in this admin UI already uses.
- **Conversational agent tool registry gains two tools** (implemented once
  `conversational-ai-assistant` lands, tracked here as a dependency, not
  duplicated): `swap_source_schema` (wraps the new source-swap dry-run/apply,
  propose-only unless `auto_safe` and confidence-gated) and `recover_from_backup`
  (always `destructive: true`, always requires confirmation regardless of
  autonomy tier).
- **BREAKING**: none. `schema_swap.py`'s `side` parameter defaults to
  `"target"`, so every existing call site and script invocation keeps working
  unmodified.

## Capabilities

### New Capabilities
- `source-schema-swap`: discover a restructured/replaced source schema live,
  diff against the approved source contract, AI-re-map affected entities only
  (schema-only prompts, human-gated), resume delivery on approval. Symmetric
  counterpart to the existing target `schema-swap`.
- `disaster-recovery-upload`: validated file upload for source-restore
  recovery and target-backup recovery, both gated behind typed confirmation
  and fully audited, reusing existing backup/restore primitives rather than
  inventing new ones.

### Modified Capabilities
- `schema-swap-remap` (from `generic-ai-db-migration-engine`): generalized
  from target-only to `side`-parameterized; existing target behavior
  unchanged by default.
- `schema-observability`: drift/contract tracking extends to cover a
  source-swap event the same way it already covers target-swap events.
- `admin-dashboard`: gains a Recovery page (Maintain nav group, alongside
  Nightly Rebuild and Schema Changes).

Note: `conversational-agent-core` (from `conversational-ai-assistant`) is
**not** modified by this change's specs — that capability doesn't exist yet
(the change that adds it hasn't landed). The two new tools it will eventually
register (`swap_source_schema`, `recover_from_backup`) are tracked as a
follow-on dependency in Impact below, not as a delta here, so this change
doesn't require a capability that doesn't exist to validate.

## Impact

- **Core**: `src/services/schema_swap.py` — add `side` param, branch adapter
  selection (`get_source_adapter` vs `get_target_adapter`) and which
  onboarding-entity field the diff checks (source column footprint vs
  `lrmis_target_tables`); `src/services/pg_restore.py` — reuse its magic-byte
  validation for uploaded files, don't reimplement it.
- **New**: `src/services/backup_recovery.py` — list `nightly_refresh` backup
  history, validate an uploaded file, wire a selected backup into a restore
  action; `src/admin_api/uploads.py` (or a router addition) — `POST
  /api/recovery/upload` (multipart, size-capped, magic-byte + encoding
  validated, written to a quarantined temp path — never directly into a path
  a restore shell command will execute), `GET /api/recovery/backups`, `POST
  /api/recovery/restore-target`, `POST /api/recovery/restore-source`.
- **CLI**: `scripts/schema_swap.py --side source|target`; new
  `scripts/recover.py --list-backups` / `--restore-target <id>` /
  `--restore-source <file>` mirroring the admin API 1:1 (same rule as the rest
  of this project: web/API and CLI both call the same service functions,
  neither bypasses the other).
- **Migration**: `sql/011_recovery.sql` — `integration.recovery_upload` table
  (id, kind [`source_dump`|`target_backup`], original filename, validated
  checksum, uploaded_by, uploaded_at, used_at, used_by) so every upload is
  audited whether or not it's ever used.
- **Web**: new `web/src/pages/Recovery.tsx`; new
  `web/src/components/BackupUpload.tsx` (drag-and-drop, progress, validation
  feedback) reused by both the source-restore and target-restore flows.
- **Depends on**: `generic-ai-db-migration-engine` (done — this reuses its
  adapters, dialect system, and `propose_mapping` failover directly).
  `conversational-ai-assistant` is a soft dependency for the tool-registry
  addition only; the swap and recovery capabilities themselves work standalone
  via CLI/UI with no dependency on the chat layer landing first.
- **Out of scope**: fully autonomous (non-gated) recovery or schema-swap
  application: NoSQL/warehouse source or target engines; automatic periodic
  backup scheduling beyond what `nightly_refresh` already does; virus/malware
  scanning of uploaded files (format/encoding validation only — treat this as
  a trusted-admin-only surface, same trust boundary as the rest of the admin
  dashboard).
