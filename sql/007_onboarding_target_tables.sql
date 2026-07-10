-- Path B, Phase 2: record which LRMIS tables an entity's rows fan out into.
--
-- One source row now maps across several real LRMIS tables (station +
-- station_address + beis + ...) rather than one VARCHAR staging table. Storing
-- the distinct target tables on the entity lets the writer and the refresh/
-- delete paths know an entity's footprint without re-deriving it from the
-- field reviews each time.
--
-- Additive and nullable: existing entities (the 75 still delivering to
-- lrmis_staging) are untouched and simply carry NULL here.
ALTER TABLE integration.onboarding_entity
    ADD COLUMN IF NOT EXISTS lrmis_target_tables JSONB;
