-- scheduled-source-dump-and-unified-refresh §4.1: app-owned refresh schedule.
-- A single small key/value table rather than a schedule-specific one: the only
-- setting today is `nightly_refresh_schedule`, and the control plane already
-- keeps every other piece of admin state in Postgres. Additive only;
-- rollback = DROP TABLE.
CREATE TABLE IF NOT EXISTS integration.admin_setting (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL DEFAULT 'system'
);
