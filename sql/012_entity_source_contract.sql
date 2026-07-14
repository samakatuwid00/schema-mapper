-- source-schema-swap-and-disaster-recovery: per-entity approved SOURCE
-- contract documents, so a source-side schema-swap can diff the live source
-- against exactly what was approved (including types/nullability, which the
-- accepted field-review column names alone cannot express).
--
-- Rows live in the existing scope-aware integration.schema_version store:
--   scope_kind='entity_source', scope_name=<source_table>, one document per
--   fingerprint; captured on every confirmed `schema-swap --side source`
-- apply. Entities without a captured document fall back to their approved
-- mapping's source-column names (adds/removes only).
ALTER TABLE integration.schema_version
    DROP CONSTRAINT IF EXISTS schema_version_scope_kind_check;
ALTER TABLE integration.schema_version
    ADD CONSTRAINT schema_version_scope_kind_check
    CHECK (scope_kind IN ('contract', 'entity_staging', 'target_ddl', 'entity_source'));
