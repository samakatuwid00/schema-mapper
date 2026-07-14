CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS integration;
CREATE SCHEMA IF NOT EXISTS lrmis_projection;

DO $$ BEGIN
    CREATE TYPE integration.event_operation AS ENUM ('insert', 'update', 'deactivate', 'backfill');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE integration.delivery_status AS ENUM ('pending', 'processing', 'delivered', 'retry', 'quarantined', 'dead_letter');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE integration.mapping_status AS ENUM ('draft', 'approved', 'superseded', 'paused');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS integration.schema_version (
    id BIGSERIAL PRIMARY KEY,
    target_system TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    schema_document JSONB NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    approved_by TEXT,
    UNIQUE (target_system, fingerprint)
);

CREATE TABLE IF NOT EXISTS integration.mapping_version (
    id BIGSERIAL PRIMARY KEY,
    source_entity TEXT NOT NULL,
    target_system TEXT NOT NULL,
    target_table TEXT NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    version INTEGER NOT NULL,
    status integration.mapping_status NOT NULL DEFAULT 'draft',
    mappings JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    approved_by TEXT,
    UNIQUE (source_entity, target_system, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_approved_mapping_per_entity
    ON integration.mapping_version (source_entity, target_system)
    WHERE status = 'approved';

CREATE TABLE IF NOT EXISTS integration.entity_control (
    source_entity TEXT NOT NULL,
    target_system TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    paused_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_entity, target_system)
);

CREATE TABLE IF NOT EXISTS integration.outbox (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system TEXT NOT NULL DEFAULT 'IRIMSV_REGION_V',
    source_entity TEXT NOT NULL,
    external_reference UUID NOT NULL,
    operation integration.event_operation NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,
    payload JSONB NOT NULL,
    payload_checksum TEXT NOT NULL,
    target_system TEXT NOT NULL DEFAULT 'LRMIS',
    mapping_version_id BIGINT REFERENCES integration.mapping_version(id),
    status integration.delivery_status NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    last_error_code TEXT,
    last_error_message TEXT
);
CREATE INDEX IF NOT EXISTS outbox_work_queue ON integration.outbox (target_system, status, available_at, created_at)
    WHERE status IN ('pending', 'retry');

CREATE TABLE IF NOT EXISTS integration.projection_record (
    event_id UUID PRIMARY KEY REFERENCES integration.outbox(event_id),
    external_reference UUID NOT NULL,
    target_system TEXT NOT NULL,
    target_table TEXT NOT NULL,
    mapping_version_id BIGINT NOT NULL REFERENCES integration.mapping_version(id),
    payload JSONB NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS integration.quarantine (
    event_id UUID PRIMARY KEY REFERENCES integration.outbox(event_id),
    errors JSONB NOT NULL,
    payload_snapshot JSONB NOT NULL,
    mapping_version_id BIGINT REFERENCES integration.mapping_version(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

CREATE TABLE IF NOT EXISTS integration.id_crosswalk (
    source_system TEXT NOT NULL,
    source_entity TEXT NOT NULL,
    external_reference UUID NOT NULL,
    target_system TEXT NOT NULL,
    target_table TEXT NOT NULL,
    target_id TEXT,
    last_event_id UUID REFERENCES integration.outbox(event_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_system, source_entity, external_reference, target_system)
);

CREATE TABLE IF NOT EXISTS integration.delivery_audit (
    id BIGSERIAL PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES integration.outbox(event_id),
    attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'rejected', 'already_processed', 'transport_error')),
    response_code TEXT,
    response_message TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration.schema_drift_report (
    id BIGSERIAL PRIMARY KEY,
    target_system TEXT NOT NULL,
    previous_fingerprint TEXT,
    observed_fingerprint TEXT NOT NULL,
    differences JSONB NOT NULL,
    impacted_entities TEXT[] NOT NULL DEFAULT '{}',
    breaking BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

-- The demo CDC trigger on the sample `irimsv.customer` table lives in
-- sql/demo_customer_cdc.sql: it hard-codes a table that only exists in the
-- sample central database, so it cannot be part of a migration that must run
-- against a central whose `irimsv` holds a restored real source.
