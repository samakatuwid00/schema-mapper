-- Path B foundation: one source row may land in several LRMIS tables.
--
-- integration.id_crosswalk already carries a target_table column, but its
-- primary key stops at target_system. Writing a source row across N tables
-- therefore produced N rows colliding on the same key. Widen the key so the
-- crosswalk can record "this external_reference became id X in table T".

ALTER TABLE integration.id_crosswalk
    DROP CONSTRAINT IF EXISTS id_crosswalk_pkey;

-- Defensive: pre-existing rows must be unique under the wider key before it
-- is enforced. Duplicates here would mean an earlier bug, not a migration one.
DELETE FROM integration.id_crosswalk a
USING integration.id_crosswalk b
WHERE a.ctid < b.ctid
  AND a.source_system = b.source_system
  AND a.source_entity = b.source_entity
  AND a.external_reference = b.external_reference
  AND a.target_system = b.target_system
  AND a.target_table = b.target_table;

ALTER TABLE integration.id_crosswalk
    ADD CONSTRAINT id_crosswalk_pkey
    PRIMARY KEY (source_system, source_entity, external_reference,
                 target_system, target_table);

-- The writer resolves "which rows did we write for this entity?" when a
-- refresh must delete only pipeline-owned rows (never TRUNCATE a shared
-- LRMIS table), and resolves parent ids during FK propagation.
CREATE INDEX IF NOT EXISTS id_crosswalk_entity_table_idx
    ON integration.id_crosswalk (source_entity, target_system, target_table);

CREATE INDEX IF NOT EXISTS id_crosswalk_reference_idx
    ON integration.id_crosswalk (external_reference, target_table);
