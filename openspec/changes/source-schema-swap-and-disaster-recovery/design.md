## Context

Two capabilities already exist and both stop at the target side of the
pipeline:

- **`schema-swap`** (`src/services/schema_swap.py`) — discover the live
  target schema, diff it against the approved contract, AI re-map only the
  affected deployed entities (schema-only prompts, human-gated on low
  confidence), recreate, redeliver. Live-verified against `old-lrmis.backup`.
  Only ever points at the **target** adapter.
- **`nightly_refresh`** — restores the source dump, truncates
  pipeline-written target tables, reseeds FK-closure lookups, redelivers.
  It already takes a timestamped `mysqldump` backup of the target before
  every destructive reset (task 3.4 of an earlier change, done). Neither the
  restore step nor the backup step has a recovery path when it fails: the
  restore step needs a readable dump on disk and gives up if it isn't one
  (the recorded UTF-16 incident); the backup exists on disk but nothing in
  the admin UI lists it or offers to restore from it.

This change closes both gaps by extending existing machinery rather than
building parallel systems: `schema_swap.py` gains a `side` parameter instead
of a sibling module, and recovery reuses `pg_restore.py`'s validation and the
existing typed-confirmation pattern instead of inventing new safety rails.

## Goals / Non-Goals

**Goals**
- Source and target are symmetric for schema adaptation: either side can be
  discovered, diffed, AI-remapped (human-gated), and cut over to.
- An admin can recover from a bad source dump or a failed target rebuild
  entirely from the UI, without shell access to the box running the pipeline.
- Every recovery action is validated before it's offered, and confirmed
  before it executes — never automatic.
- Reuse: no second schema-diff engine, no second file-validation routine, no
  second confirmation pattern.

**Non-Goals**
- Not a general-purpose backup product — recovery covers exactly the two
  failure points this project has already hit (source dump unreadable,
  target rebuild fails partway).
- No autonomous execution of a swap or a restore, at any autonomy tier
  (`recover_from_backup` is always gated — see Decision D4).
- No new source of truth for "what the schema should be" — a source-swap
  still diffs against the same approved-contract mechanism the target-swap
  already uses; this change does not redesign contracts.
- No change to `nightly_refresh`'s own backup-taking logic — it already
  works; this change only exposes and makes it restorable.

## Decisions

### D1 — `side` parameter, not a parallel module
`schema_swap.py`'s discover → diff → AI-remap → human-gate → apply pipeline
does not care which side changed; it only needs to know which adapter to
discover through (`get_source_adapter` vs `get_target_adapter`) and which
onboarding-entity field represents the approved contract for that side
(source column footprint vs `lrmis_target_tables`). Adding
`side: "source"|"target" = "target"` to the existing functions keeps one
tested code path instead of two, and defaults preserve every existing call
site. Rejected alternative: a new `source_schema_swap.py` mirroring the
target file — rejected because the two would drift the moment one got a bug
fix the other didn't, which is exactly the duplication problem
`retire-legacy-staging` is separately removing on the delivery side.

### D2 — Source-swap is discovery + re-mapping only, never a source write
A source-swap diffs and re-maps; it never issues DDL or DML against IRIMSV.
This matches the project's standing rule that AI output is proposal-only,
and it means a source-swap has no destructive step to gate — the only gated
action is *approving* a low-confidence remap, identical to today's
target-swap human-gate.

### D3 — Recovery uploads are quarantined and validated before they're offered
An uploaded file is never written to a path a restore command will execute
directly. It lands in a quarantined temp path, gets a magic-byte check
(reusing `pg_restore.py`'s existing `PGDMP`-style check), an encoding sanity
check (the exact UTF-16-from-PowerShell failure mode that already happened),
and — for source dumps — a check that the dump actually contains the
`irimsv` schema before it's ever presented as a restore candidate. Only
after all three pass does it appear as a selectable option; selecting it is
a separate, audited, typed-confirmation action.

### D4 — Recovery is always destructive-gated, regardless of autonomy tier
Every other tool the conversational agent will eventually expose can be
tiered (`propose_only` vs `auto_safe`). `recover_from_backup` is hard-coded
`destructive: true` so it always requires confirmation even under
`auto_safe` — restoring a backup discards whatever's on the target (or
overwrites the pending source dump) right now, and that's not a call an
agent should make unattended no matter how high its trust tier gets. This
mirrors `nightly_refresh --confirm <db>` and D5 of `retire-legacy-staging`.

### D5 — Recovery UI surfaces existing backups; it does not change what gets backed up
The Recovery page's `GET /api/recovery/backups` lists what `nightly_refresh`
already wrote (previously invisible), plus any manual upload. This change
does not add new automatic backup triggers or retention policy — that stays
`nightly_refresh`'s job. Scope creep into a general backup scheduler is
explicitly out (see proposal Impact).

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Source-swap re-maps against a moving target if IRIMSV changes again mid-review | Same human-gate as target-swap: low-confidence remaps sit for approval before anything is marked ready; re-running discovery re-diffs from scratch. |
| An uploaded "recovery" file is itself corrupt or malicious | D3: magic-byte + encoding + schema-content validation before it's ever offered; quarantined temp path; admin-only surface (no scanning promised — documented as out of scope, same trust boundary as the rest of the admin dashboard). |
| Restoring the wrong backup wipes current target data | D4: typed-confirmation, always gated, audited with uploaded_by/used_by/used_at; `nightly_refresh`'s own pre-reset backup means even a bad restore choice is itself recoverable. |
| `side` param subtly breaks the existing target-swap through shared-code regression | Default is `"target"`; existing target-swap test suite runs unchanged plus new source-swap-specific tests before this ships; no behavior change for callers that don't pass `side`. |
| Conversational-agent tools reference a chat layer that doesn't exist yet | Tracked as a soft dependency (Impact); `swap_source_schema`/`recover_from_backup` are additive tool-registry entries added when `conversational-ai-assistant` lands — CLI/API/UI paths for both capabilities work standalone without it. |

## Sequence

1. **Core** — add `side` to `schema_swap.py`; branch adapter selection and
   contract-field lookup; full existing target-swap test suite must stay
   green unmodified.
2. **Source-swap** — wire `PostgresSourceAdapter` discovery into the `side="source"`
   path; add source-swap-specific tests (diff detection, re-map trigger,
   human-gate on low confidence); CLI `--side source|target` on
   `scripts/schema_swap.py` and `scripts/sync_engine.py schema-swap`.
3. **Recovery core** — `src/services/backup_recovery.py` (list backups,
   validate upload, wire selected backup into a restore action);
   `sql/011_recovery.sql` (`integration.recovery_upload` audit table).
4. **Recovery API** — `POST /api/recovery/upload`,
   `GET /api/recovery/backups`, `POST /api/recovery/restore-target`,
   `POST /api/recovery/restore-source`; `scripts/recover.py` mirroring the
   API 1:1.
5. **Recovery UI** — `web/src/pages/Recovery.tsx` +
   `web/src/components/BackupUpload.tsx`; add to the Maintain nav group.
6. **Agent tools (deferred)** — `swap_source_schema` and
   `recover_from_backup` added to the tool registry once
   `conversational-ai-assistant`'s registry exists; not blocking on the rest
   of this change.
7. **Verify + document** — full `pytest`; MySQL-target and Postgres-target
   swap/recovery smokes; `openspec validate source-schema-swap-and-disaster-recovery --strict`.

Rollback: steps 1–2 and 3–5 revert via git independently (source-swap and
recovery are decoupled — either can ship without the other). No destructive
database action is taken by this change itself; the recovery *feature* only
ever restores from a backup that already existed or a file an admin
explicitly confirmed, so there is nothing here for this change's own rollout
to undo at the data layer.
