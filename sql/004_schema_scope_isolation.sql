-- Separate full external contracts from integration-owned per-table staging snapshots.
ALTER TABLE integration.schema_version
    ADD COLUMN IF NOT EXISTS scope_kind TEXT NOT NULL DEFAULT 'contract',
    ADD COLUMN IF NOT EXISTS scope_name TEXT NOT NULL DEFAULT '';

ALTER TABLE integration.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_target_system_fingerprint_key;

-- Existing one-table documents created during deployment are entity snapshots,
-- not replacements for the complete LRMIS contract.
WITH one_table AS (
    SELECT sv.id, sv.target_system,
           sv.schema_document->'tables'->0->>'name' AS table_name
    FROM integration.schema_version sv
    WHERE jsonb_typeof(sv.schema_document->'tables') = 'array'
      AND jsonb_array_length(sv.schema_document->'tables') = 1
)
UPDATE integration.schema_version sv
SET scope_kind = 'entity_staging', scope_name = one_table.table_name
FROM one_table
WHERE sv.id = one_table.id
  AND EXISTS (
      SELECT 1 FROM integration.onboarding_entity e
      WHERE e.target_system = one_table.target_system
        AND e.staging_table = one_table.table_name
  );

CREATE UNIQUE INDEX IF NOT EXISTS schema_version_scope_fingerprint_uq
    ON integration.schema_version (target_system, scope_kind, scope_name, fingerprint);

CREATE INDEX IF NOT EXISTS schema_version_scope_latest_idx
    ON integration.schema_version (target_system, scope_kind, scope_name, observed_at DESC);

ALTER TABLE integration.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_scope_kind_check;
ALTER TABLE integration.schema_version
    ADD CONSTRAINT schema_version_scope_kind_check
    CHECK (scope_kind IN ('contract', 'entity_staging'));

-- Generated source projections are deliberately outside the authoritative IRIMSV schema.
CREATE SCHEMA IF NOT EXISTS lrmis_projection;
DO $$
DECLARE generated_view RECORD;
BEGIN
    FOR generated_view IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'irimsv' AND c.relkind = 'v'
          AND c.relname LIKE '%\_for\_lrmis' ESCAPE '\'
    LOOP
        IF to_regclass(format('lrmis_projection.%I', generated_view.relname)) IS NULL THEN
            EXECUTE format('ALTER VIEW irimsv.%I SET SCHEMA lrmis_projection', generated_view.relname);
        END IF;
    END LOOP;
END $$;

UPDATE integration.onboarding_entity
SET source_schema = 'lrmis_projection', updated_at = now()
WHERE source_schema = 'irimsv'
  AND source_table LIKE '%\_for\_lrmis' ESCAPE '\';

CREATE TABLE IF NOT EXISTS integration.view_proposal (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES integration.onboarding_entity(id),
    source_schema TEXT NOT NULL,
    source_table TEXT NOT NULL,
    target_system TEXT NOT NULL,
    view_schema TEXT NOT NULL DEFAULT 'lrmis_projection',
    view_name TEXT NOT NULL,
    view_sql TEXT NOT NULL,
    joined_tables JSONB NOT NULL DEFAULT '[]',
    mapped_columns JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'rejected')),
    pending_proposal_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ,
    applied_by TEXT
);
ALTER TABLE integration.view_proposal
    ADD COLUMN IF NOT EXISTS view_schema TEXT NOT NULL DEFAULT 'lrmis_projection';
ALTER TABLE integration.view_proposal
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE integration.view_proposal
SET view_schema = 'lrmis_projection',
    view_sql = regexp_replace(
        view_sql,
        '^CREATE OR REPLACE VIEW [^.]+\.',
        'CREATE OR REPLACE VIEW lrmis_projection.'
    ),
    updated_at = now()
WHERE status = 'pending';

ALTER TABLE integration.onboarding_entity
    ADD COLUMN IF NOT EXISTS fingerprint_scope_version SMALLINT NOT NULL DEFAULT 1;
