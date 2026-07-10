/**
 * Presentation glossary.
 *
 * Internal names stay exact in code, API payloads, and the audit log. The UI,
 * however, speaks the vocabulary of a database manager. This module is the one
 * place that maps an internal term to the words a manager reads on screen.
 *
 * Never rename an internal identifier to match a label; only translate at the
 * presentation edge via `label()`.
 */

export const LABELS: Record<string, string> = {
  migrations: "Database Updates (SQL)",
  backfill: "Copy existing rows",
  deploy: "Start syncing",
  proposal: "Column match",
  entity: "Table",
  resolve: "Choose target column",
  outbox: "Sync queue",
  quarantine: "Blocked rows",
  dead_letter: "Failed rows",
  drift: "Schema change detected",
  refresh: "Rebuild staging table",
  fingerprint: "Schema version",
  onboard_bulk: "Onboard tables",
  worker: "Delivery worker",
  reconcile: "Compare source vs target",
  replay: "Retry row",
};

/**
 * Translate an internal term to its manager-facing label.
 * Falls back to the input unchanged when there is no mapping.
 */
export function label(internal: string): string {
  return LABELS[internal] ?? internal;
}

/** One-line, plain-English help for each internal term. */
export const DESCRIPTIONS: Record<string, string> = {
  migrations:
    "Applies SQL files that change the database structure. This does not move any row data.",
  backfill:
    "Copies rows that already exist in the source into the sync queue.",
  deploy:
    "Turns on continuous syncing so new source changes flow to the target.",
  proposal:
    "A suggested pairing between a source column and a target column, waiting for your review.",
  entity:
    "A single database table that is tracked for syncing.",
  resolve:
    "Picks which target column a source column should map to.",
  outbox:
    "The queue of row changes waiting to be delivered to the target database.",
  quarantine:
    "Rows held back because they could not be delivered safely and need a look.",
  dead_letter:
    "Rows that failed delivery after every retry and are set aside.",
  drift:
    "The source schema changed since the last check, so a mapping may need updating.",
  refresh:
    "Rebuilds the staging table so it matches the current source schema.",
  fingerprint:
    "A short signature of a schema's shape, used to detect when it changes.",
  onboard_bulk:
    "Sets up several tables for syncing in one pass instead of one at a time.",
  worker:
    "The background process that delivers queued rows to the target database.",
  reconcile:
    "Compares source and target row counts to confirm they are in agreement.",
  replay:
    "Sends a single failed or blocked row through delivery again.",
};
