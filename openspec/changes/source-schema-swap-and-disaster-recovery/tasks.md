## 0. Groundwork

- [ ] 0.1 Confirm `PostgresSourceAdapter` discovery surface covers everything a source-swap diff needs (columns, types, nullability); note any gaps before wiring
- [ ] 0.2 Confirm the approved-contract field for source columns (equivalent of `lrmis_target_tables` for the target side); document it in `schema_swap.py`'s docstring
- [ ] 0.3 Read `src/services/pg_restore.py`'s magic-byte check end to end; confirm it's reusable as-is for uploaded-file validation (not just restore-time validation)

## 1. Generalize `schema-swap` to be side-agnostic

- [ ] 1.1 Add `side: Literal["source", "target"] = "target"` to the public functions in `src/services/schema_swap.py`
- [ ] 1.2 Branch adapter selection: `get_source_adapter(...)` when `side="source"`, `get_target_adapter(...)` when `side="target"`
- [ ] 1.3 Branch the approved-contract lookup per `side` (source column footprint vs `lrmis_target_tables`)
- [ ] 1.4 Confirm diff/AI-remap/human-gate/apply logic needs no other branching — keep one code path
- [ ] 1.5 Run the full existing target-swap test suite unmodified; must stay green (proves the default preserves current behavior)
- [ ] 1.6 Add `--side source|target` to `scripts/schema_swap.py` (default `target`)
- [ ] 1.7 Add `--side source|target` to `scripts/sync_engine.py schema-swap` (default `target`)

## 2. Source-side schema-swap

- [ ] 2.1 Wire `side="source"` discovery through `PostgresSourceAdapter`
- [ ] 2.2 Diff live source schema against the approved source contract; detect added/removed/retyped columns per entity
- [ ] 2.3 AI re-map only entities whose source columns are affected (schema-only prompts — no row values, matching the existing rule)
- [ ] 2.4 Human-gate low-confidence remaps exactly like target-swap; confirmed remaps resume delivery, others sit for review
- [ ] 2.5 Confirm a source-swap never issues DDL/DML against IRIMSV — discovery + re-mapping only (design D2)
- [ ] 2.6 Unit tests: diff detection, remap trigger thresholds, human-gate behavior, no-DDL-against-source guard
- [ ] 2.7 Live smoke: point at a restructured/alternate Postgres source (mirrors the `old-lrmis.backup` target smoke already done for target-swap)

## 3. Disaster recovery — core service

- [ ] 3.1 Create `sql/011_recovery.sql`: `integration.recovery_upload` table (id, kind [`source_dump`|`target_backup`], original_filename, checksum, uploaded_by, uploaded_at, used_at, used_by)
- [ ] 3.2 Create `src/services/backup_recovery.py`: `list_target_backups()` — reads `nightly_refresh`'s existing timestamped backup files
- [ ] 3.3 `validate_upload(path, kind)` — magic-byte check (reuse `pg_restore.py` pattern), encoding sanity check (reject non-UTF-8/UTF-16-as-if-UTF-8), and for `source_dump` a check the dump contains the `irimsv` schema; returns pass/fail + reason
- [ ] 3.4 `stage_upload(file, kind)` — writes to a quarantined temp path (never a path a restore command executes directly), runs `validate_upload`, records a `recovery_upload` row
- [ ] 3.5 `restore_target(backup_id)` — wraps existing target-restore primitives, requires typed confirmation, records `used_at`/`used_by`
- [ ] 3.6 `restore_source(upload_id)` — wraps existing source-restore primitives, requires typed confirmation, records `used_at`/`used_by`
- [ ] 3.7 Unit tests: valid dump accepted, UTF-16 dump rejected with the actual historical failure reproduced as a fixture, non-dump file rejected, missing-schema source dump rejected, confirmation required for both restore paths

## 4. Disaster recovery — API and CLI

- [ ] 4.1 `POST /api/recovery/upload` — multipart, size-capped, calls `stage_upload`, returns validation result
- [ ] 4.2 `GET /api/recovery/backups` — lists target backups (from `nightly_refresh`) and validated uploads (source + target)
- [ ] 4.3 `POST /api/recovery/restore-target` — typed-confirmation body required, calls `restore_target`, audited
- [ ] 4.4 `POST /api/recovery/restore-source` — typed-confirmation body required, calls `restore_source`, audited
- [ ] 4.5 `scripts/recover.py --list-backups` / `--restore-target <id>` / `--restore-source <file>` — calls the same service functions as the API (no bypass, matches project convention)
- [ ] 4.6 Integration tests: upload → validate → list → restore (confirmation required) → audit row recorded, for both source and target

## 5. Disaster recovery — web UI

- [ ] 5.1 `web/src/components/BackupUpload.tsx` — drag-and-drop, progress, validation feedback (surfaces the actual rejection reason, e.g. "file is UTF-16, expected UTF-8")
- [ ] 5.2 `web/src/pages/Recovery.tsx` — lists existing target backups + uploaded source/target files, restore action per row
- [ ] 5.3 Typed-confirmation modal on restore actions (reuse the existing confirmation component/pattern from `nightly_refresh`'s UI)
- [ ] 5.4 Add Recovery to the Maintain nav group (alongside Nightly Rebuild and Schema Changes)
- [ ] 5.5 Component tests: upload flow, validation error display, confirmation gate, restore trigger

## 6. Conversational agent tools (deferred on `conversational-ai-assistant`)

- [ ] 6.1 Define `swap_source_schema` tool (propose-only unless `auto_safe` + confidence-gated, mirrors target-swap tool shape)
- [ ] 6.2 Define `recover_from_backup` tool (`destructive: true` always, confirmation required regardless of autonomy tier — design D4)
- [ ] 6.3 Register both in the tool registry once `src/agent/tools.py` exists; do not block this change's other sections on this task
- [ ] 6.4 Unit tests for both tool defs: schema validation, destructive-gate enforcement for `recover_from_backup`

## 7. Verify and document

- [ ] 7.1 Full `pytest -q` green
- [ ] 7.2 MySQL-target and Postgres-target schema-swap smoke, both `side=source` and `side=target`
- [ ] 7.3 Recovery smoke: stage an intentionally-bad (UTF-16) upload, confirm rejection; stage a valid one, confirm restore path works end to end against a disposable target
- [ ] 7.4 Update `docs/RUNBOOK_source_to_target.md` — mark the UTF-16 incident as now recoverable via Recovery UI instead of manual fix
- [ ] 7.5 `graphify update .`
- [ ] 7.6 `openspec validate source-schema-swap-and-disaster-recovery --strict`
