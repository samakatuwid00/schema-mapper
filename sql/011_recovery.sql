-- source-schema-swap-and-disaster-recovery §3: audited recovery uploads.
-- Every uploaded source dump / target backup gets a row here whether or not
-- validation passes and whether or not it is ever used — the upload itself is
-- the audited event; used_at/used_by record the (separately confirmed) restore.
CREATE TABLE IF NOT EXISTS integration.recovery_upload (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('source_dump', 'target_backup')),
    original_filename TEXT NOT NULL,
    -- Quarantined staging path (never a path any restore command executes
    -- without an explicit, typed-confirmation restore action).
    stored_path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    valid BOOLEAN NOT NULL DEFAULT false,
    invalid_reason TEXT,
    uploaded_by TEXT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    used_at TIMESTAMPTZ,
    used_by TEXT
);

CREATE INDEX IF NOT EXISTS recovery_upload_kind_idx
    ON integration.recovery_upload (kind, uploaded_at DESC);
