-- Admin UI control-plane tables (see openspec/changes/add-admin-database-dashboard)
CREATE SCHEMA IF NOT EXISTS integration;

-- 'deploying' intermediate status guards concurrent deploys (drop+re-add is idempotent)
ALTER TABLE integration.onboarding_proposal
    DROP CONSTRAINT IF EXISTS onboarding_proposal_status_check;
ALTER TABLE integration.onboarding_proposal
    ADD CONSTRAINT onboarding_proposal_status_check CHECK (status IN (
        'draft', 'auto_approved', 'needs_review', 'approved', 'rejected', 'deploying'
    ));

CREATE TABLE IF NOT EXISTS integration.schema_migrations (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by TEXT NOT NULL,
    success BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS integration.admin_user (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('operator', 'admin')),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS integration.admin_action_audit (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    request_id TEXT,
    reason TEXT,
    details JSONB,
    result TEXT NOT NULL CHECK (result IN ('success', 'failure')),
    error_message TEXT,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS admin_action_audit_performed_at
    ON integration.admin_action_audit (performed_at DESC);

CREATE TABLE IF NOT EXISTS integration.admin_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER,
    heartbeat_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    result JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS admin_job_queue
    ON integration.admin_job (status, created_at)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS integration.admin_job_event (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES integration.admin_job(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT,
    data JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS admin_job_event_stream
    ON integration.admin_job_event (job_id, id);
