-- Drift auto-resolution: track when a schema-drift report was resolved.
-- The base table (001_integration_foundation.sql) already declares resolved_at
-- and resolved_by, so these run as no-ops on freshly initialised databases;
-- IF NOT EXISTS makes the migration safe to apply on older databases that were
-- created before those columns existed.
ALTER TABLE integration.schema_drift_report
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE integration.schema_drift_report
    ADD COLUMN IF NOT EXISTS resolved_by TEXT;
