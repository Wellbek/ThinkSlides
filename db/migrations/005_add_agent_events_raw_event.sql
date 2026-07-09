-- Store the full agent event dict (live SSE shape) so the chat event stream
-- and cited sources can be reconstructed when a session is reloaded.
ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS raw_event JSONB;
