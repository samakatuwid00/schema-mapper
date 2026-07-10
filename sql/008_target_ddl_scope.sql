-- Path B, Phase 7: allow schema_version to hold the canonical LRMIS DDL
-- fingerprint (the sha256 of lrmis.sql), so drift in the target *structure*
-- itself can be detected the same way contract/entity drift already is.
ALTER TABLE integration.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_scope_kind_check;
ALTER TABLE integration.schema_version
    ADD CONSTRAINT schema_version_scope_kind_check
    CHECK (scope_kind IN ('contract', 'entity_staging', 'target_ddl'));
