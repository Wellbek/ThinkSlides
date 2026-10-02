-- The state of a session's latest finished deck, so a message sent after the
-- deck exists amends it instead of starting a new one.

CREATE TABLE IF NOT EXISTS deck_snapshots (
    session_id UUID PRIMARY KEY REFERENCES chat_sessions (id) ON DELETE CASCADE,
    state      JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
