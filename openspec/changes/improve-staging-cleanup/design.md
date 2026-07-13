## Context

The earlier session built `staging_cleanup.py` with `sweep_orphans()` and `retire_entity()`, wired both into the admin API and added buttons in SchemaChanges.tsx. However, two issues remain:

1. **No result visibility**: After a sweep/retire job completes, the UI shows only "Cleanup job complete — job #N." The structured result (`orphans_found`, `dropped`, `snapshots`) is stored in the job row but never displayed.
2. **Poor Retire UX**: The "Entity ID" field is a bare text input requiring the operator to know the numeric `integration.onboarding_entity.id` — a value that is not surfaced anywhere in the UI except as a React key in the Overview entities table.
3. **Connector lifecycle asymmetry**: `MySQLStagingConnector` lacks a `close()` method, so the `finally` block in `sweep_orphans` only closes the central connector.

## Goals / Non-Goals

**Goals:**
- After a sweep or retire job completes, show the full result data in the Schema Changes page (orphans found, dropped, snapshots)
- Replace the Entity ID text input with a `<select>` dropdown of deployed entities (showing `source_table` → `staging_table`, status)
- Add `close()` to `MySQLStagingConnector` and fix the `finally` block in `sweep_orphans` for connector symmetry
- Keep the same confirmation tier (typed) for both actions — no change to security posture

**Non-Goals:**
- Adding a dedicated "Cleanup Results" page or history — results display inline in the existing success/error area
- Changing the scope guard or job execution model
- Adding bulk retire or scheduled sweep — those are future concerns

## Decisions

### 1. Result display: inline JSON vs. structured rendering

**Decision:** Render structured result data from the job's `result` field as a formatted summary (counts + lists), not raw JSON.

**Rationale:** The job handlers already return structured dicts (`orphans_found`, `dropped`, `snapshots`, etc.). The `useJobRunner` hook holds the completed job in state, so accessing `job.result` is straightforward. Formatting as human-readable text (e.g., "Found 3 orphans, dropped 2, 2 snapshots taken") is more useful than dumping JSON. For large lists (e.g., 50+ orphans), truncate with a "… and N more" note.

### 2. Entity dropdown: source of truth

**Decision:** Fetch the entity list from the existing `/api/status` endpoint (which returns `entities: Array<{id, source_table, staging_table, status, ...}>`).

**Rationale:** No new endpoint needed — `/api/status` already returns all entities with their metadata. Filter to `status = 'deployed'` on the client side. The `<select>` renders one `<option>` per entity with `value={id}` and label `{source_table} → {staging_table}`.

### 3. `MySQLStagingConnector.close()`

**Decision:** Add a no-op `close()` method to `MySQLStagingConnector` and call it from the `finally` block in both `sweep_orphans` and `retire_entity`.

**Rationale:** The MySQL connector uses a connection pool; individual connections auto-close in the `connection()` context manager. `close()` on the connector itself would need to close all pooled connections, but MySQL connector's pool doesn't expose `close`. Instead, make `close()` a no-op (for interface symmetry with `PostgresCentralConnector`) and ensure the `finally` block references `owns_staging` — even though it's a no-op, it makes the API consistent and avoids confusion.

### 4. Sweep query optimization

**Decision:** Replace the `information_schema.columns` query in `sweep_orphans` with a direct `information_schema.tables` query.

**Rationale:** The current code calls `staging.information_schema()` which queries `information_schema.columns` (one row per column, not per table) just to build a set of table names via `{r["table_name"] for r in tables}`. This is O(columns) when O(tables) suffices. A dedicated `information_schema.tables` query is more efficient and semantically clearer. Add a `table_names()` helper to `MySQLStagingConnector`.

## Risks / Trade-offs

- **[Risk] Entity dropdown shows stale data** if the `/api/status` cache is behind — The dropdown refreshes each time the modal opens (no stale cache). The Schema Changes page already re-fetches on mount.
- **[Risk] Large orphan list overwhelms UI** — Cap display at 20 items with a "… and N more" overflow line. The complete list is still in the job result if the admin needs it.
- **[Trade-off] No-op `close()` feels incomplete** — A true `close()` would require closing all pooled MySQL connections, which the mysql-connector-python pool API does not support cleanly. The no-op is safe because connections are returned to the pool after each `connection()` context manager exit.
