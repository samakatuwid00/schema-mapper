-- conversational-ai-assistant §1: agent conversation persistence (design D6).
-- (Planned as 005 in that change's draft; 005-012 were taken by the time it
-- landed.) Additive only — no existing table is altered; rollback = DROP TABLE.
-- Messages hold {role, content, tool_calls?, tool_results?, created_at} and
-- NEVER row values (schema metadata, IDs, statuses, action summaries only —
-- enforced at the application layer by redaction before persistence).
CREATE TABLE IF NOT EXISTS integration.agent_conversation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id INTEGER NOT NULL REFERENCES integration.admin_user(id),
    title TEXT NOT NULL DEFAULT '',
    autonomy_tier TEXT NOT NULL DEFAULT 'propose_only'
        CHECK (autonomy_tier IN ('propose_only', 'auto_safe')),
    messages JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Conversation listing is always "mine, newest first".
CREATE INDEX IF NOT EXISTS agent_conversation_user_updated_idx
    ON integration.agent_conversation (user_id, updated_at DESC);
