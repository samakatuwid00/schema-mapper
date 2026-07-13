-- Three-way schema observability: tag each drift report with the database pair
-- it compares. 'source->staging' is the existing IRIMSV -> lrmis_staging pair
-- (the default, so historical rows keep reading correctly); 'staging->target'
-- is the new lrmis_staging -> lrmis_target (Path B) pair.
--
-- The Path B contract fingerprint itself needs no new table: it is stored in the
-- existing integration.schema_version (scope-aware since 008_target_ddl_scope.sql)
-- under scope_kind='contract', scope_name='target_b'.
ALTER TABLE integration.schema_drift_report
    ADD COLUMN IF NOT EXISTS drift_pair TEXT NOT NULL DEFAULT 'source->staging';

CREATE INDEX IF NOT EXISTS idx_schema_drift_report_pair
    ON integration.schema_drift_report (drift_pair, created_at DESC);
