-- One row per span of an agent run's trace: why each phase, tool and model
-- call happened, how long it took, what it spent and how the run ended.
-- Written once per run by services/ai/app/observability/trace_store.py.
--
-- No foreign key to chat_sessions on purpose: the trace is written at the end
-- of the run and must not be lost because the session row is gone, or never
-- existed for a headless research run.

CREATE TABLE IF NOT EXISTS run_spans (
    id                  UUID PRIMARY KEY,
    run_id              UUID NOT NULL,
    session_id          UUID,
    parent_id           UUID,
    kind                VARCHAR(20) NOT NULL,
    name                VARCHAR(100) NOT NULL,
    reason              TEXT,
    status              VARCHAR(20) NOT NULL,
    error               TEXT,
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    duration_ms         INTEGER,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    attrs               JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_run_spans_run_id ON run_spans (run_id);
CREATE INDEX IF NOT EXISTS ix_run_spans_session_id ON run_spans (session_id);
