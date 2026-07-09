CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS integration;
CREATE SCHEMA IF NOT EXISTS lrmis_projection;

CREATE TYPE integration.event_operation AS ENUM ('insert', 'update', 'deactivate', 'backfill');
CREATE TYPE integration.delivery_status AS ENUM ('pending', 'processing', 'delivered', 'retry', 'quarantined', 'dead_letter');
CREATE TYPE integration.mapping_status AS ENUM ('draft', 'approved', 'superseded', 'paused');

CREATE TABLE integration.schema_version (
    id BIGSERIAL PRIMARY KEY,
    target_system TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    schema_document JSONB NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    approved_by TEXT,
    UNIQUE (target_system, fingerprint)
);

CREATE TABLE integration.mapping_version (
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
CREATE UNIQUE INDEX one_approved_mapping_per_entity
    ON integration.mapping_version (source_entity, target_system)
    WHERE status = 'approved';

CREATE TABLE integration.entity_control (
    source_entity TEXT NOT NULL,
    target_system TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    paused_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_entity, target_system)
);

CREATE TABLE integration.outbox (
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
CREATE INDEX outbox_work_queue ON integration.outbox (target_system, status, available_at, created_at)
    WHERE status IN ('pending', 'retry');

CREATE TABLE integration.projection_record (
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

CREATE TABLE integration.quarantine (
    event_id UUID PRIMARY KEY REFERENCES integration.outbox(event_id),
    errors JSONB NOT NULL,
    payload_snapshot JSONB NOT NULL,
    mapping_version_id BIGINT REFERENCES integration.mapping_version(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

CREATE TABLE integration.id_crosswalk (
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

CREATE TABLE integration.delivery_audit (
    id BIGSERIAL PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES integration.outbox(event_id),
    attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'rejected', 'already_processed', 'transport_error')),
    response_code TEXT,
    response_message TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE integration.schema_drift_report (
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

CREATE OR REPLACE FUNCTION integration.enqueue_customer_change() RETURNS trigger AS $$
DECLARE
    record_json JSONB;
    record_ref UUID;
    op integration.event_operation;
BEGIN
    record_json := to_jsonb(COALESCE(NEW, OLD));
    record_ref := COALESCE(NEW.external_reference, OLD.external_reference);
    op := CASE WHEN TG_OP = 'DELETE' THEN 'deactivate'::integration.event_operation
               WHEN TG_OP = 'INSERT' THEN 'insert'::integration.event_operation
               ELSE 'update'::integration.event_operation END;
    IF TG_OP = 'DELETE' THEN
        record_json := record_json || jsonb_build_object('active', false, 'deactivated_at', now());
    END IF;
    INSERT INTO integration.outbox
        (source_entity, external_reference, operation, payload, payload_checksum, source_updated_at)
    VALUES
        ('customer', record_ref, op, record_json,
         encode(digest(record_json::text, 'sha256'), 'hex'), now());
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_customer_integration_outbox ON irimsv.customer;
CREATE TRIGGER trg_customer_integration_outbox
AFTER INSERT OR UPDATE OR DELETE ON irimsv.customer
FOR EACH ROW EXECUTE FUNCTION integration.enqueue_customer_change();
