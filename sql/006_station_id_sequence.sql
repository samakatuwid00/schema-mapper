-- Path B: app-assigned primary keys for LRMIS tables IRIMSV must be able to
-- CREATE rows in, but whose primary key is not AUTO_INCREMENT.
--
-- Verified against real data: 0 of IRIMSV's 145 schools match an existing
-- LRMIS `station`/`beis` row by any natural key. Treating `station` as
-- resolve-only (as the earlier foundation pass did) would block onboarding
-- entirely, since IRIMSV is the authoritative source and almost every school
-- is new to LRMIS. `station.id` is a plain `int`, currently 1..3921 dense, so
-- new ids are allocated from a reserved range starting well above that.
--
-- `psgc` deliberately gets NO row here: it is external geographic reference
-- data (the national PSGC registry) that IRIMSV does not carry in any form,
-- so this pipeline has no authority to mint new geographic codes for it. It
-- stays resolve-only, unlike `station`.
CREATE TABLE IF NOT EXISTS integration.id_sequence (
    table_name TEXT PRIMARY KEY,
    next_value BIGINT NOT NULL
);
