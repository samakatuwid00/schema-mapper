-- Onboarding metadata for the generic AI-assisted pipeline
-- Tracks source entities, proposals, deployments, and schema fingerprints

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS integration.onboarding_entity (
    id BIGSERIAL PRIMARY KEY,
    source_system TEXT NOT NULL DEFAULT 'IRIMSV_REGION_V',
    source_schema TEXT NOT NULL DEFAULT 'irimsv',
    source_table TEXT NOT NULL,
    primary_key_columns JSONB NOT NULL DEFAULT '["id"]',
    updated_at_column TEXT,
    target_system TEXT NOT NULL DEFAULT 'LRMIS',
    staging_table TEXT,
    source_fingerprint TEXT,
    target_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN (
        'discovered', 'proposed', 'reviewed', 'deployed', 'paused', 'disabled'
    )),
    deployed_by TEXT,
    deployed_at TIMESTAMPTZ,
    paused_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, source_schema, source_table, target_system)
);

CREATE TABLE IF NOT EXISTS integration.onboarding_proposal (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES integration.onboarding_entity(id),
    source_fingerprint TEXT NOT NULL,
    target_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
        'draft', 'auto_approved', 'needs_review', 'approved', 'rejected'
    )),
    mappings JSONB NOT NULL DEFAULT '[]',
    ignored_source_columns JSONB NOT NULL DEFAULT '[]',
    unmet_required_columns JSONB NOT NULL DEFAULT '[]',
    gemini_raw_response JSONB,
    auto_approved_count INTEGER NOT NULL DEFAULT 0,
    needs_review_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration.onboarding_field_review (
    id BIGSERIAL PRIMARY KEY,
    proposal_id BIGINT NOT NULL REFERENCES integration.onboarding_proposal(id),
    source_column TEXT NOT NULL,
    suggested_target_table TEXT,
    suggested_target_column TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    transform TEXT NOT NULL DEFAULT 'none',
    reasoning TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'accepted', 'rejected', 'resolved'
    )),
    resolved_target_column TEXT,
    resolved_transform TEXT,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    cross_table_candidate BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration.onboarding_audit (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT REFERENCES integration.onboarding_entity(id),
    proposal_id BIGINT REFERENCES integration.onboarding_proposal(id),
    action TEXT NOT NULL,
    details JSONB,
    performed_by TEXT NOT NULL,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_onboarding_entity_status ON integration.onboarding_entity(status);
CREATE INDEX IF NOT EXISTS idx_onboarding_entity_source ON integration.onboarding_entity(source_system, source_schema, source_table);
CREATE INDEX IF NOT EXISTS idx_onboarding_proposal_entity ON integration.onboarding_proposal(entity_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_proposal_status ON integration.onboarding_proposal(status);
CREATE INDEX IF NOT EXISTS idx_onboarding_field_review_proposal ON integration.onboarding_field_review(proposal_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_field_review_status ON integration.onboarding_field_review(status);
CREATE INDEX IF NOT EXISTS idx_onboarding_audit_entity ON integration.onboarding_audit(entity_id);
