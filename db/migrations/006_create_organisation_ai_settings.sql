-- Per-organisation AI provider configuration.
--
-- Two independent model roles are configured here:
--   chat_*   - the generic chat model used for every message
--   search_* - the model used for Deep Thinking (web-search-backed) runs
-- plus the deep-search backend that supplies the web results
--   ('native' = the provider's own search API, or 'tavily' / 'firecrawl').
--
-- API keys are NEVER stored in plaintext. The *_encrypted columns hold an
-- AES-256-GCM envelope produced by services/ai/app/security/crypto.py: a
-- per-secret data key sealed under a key-encryption key that lives only in the
-- environment, with the organisation id and column name bound in as associated
-- data. The *_hint columns hold the last four characters of the key so the UI
-- can show which credential is configured without it being readable back.

CREATE TABLE IF NOT EXISTS organisation_ai_settings (
    organisation_id                  UUID PRIMARY KEY REFERENCES organisations(id) ON DELETE CASCADE,

    -- Generic chat model
    chat_provider                    VARCHAR(50),
    chat_model                       VARCHAR(200),
    chat_base_url                    VARCHAR(500),
    chat_api_key_encrypted           TEXT,
    chat_api_key_hint                VARCHAR(16),

    -- Deep Thinking model
    search_provider                  VARCHAR(50),
    search_model                     VARCHAR(200),
    search_base_url                  VARCHAR(500),
    search_api_key_encrypted         TEXT,
    search_api_key_hint              VARCHAR(16),

    -- Deep Thinking web-search backend
    search_backend                   VARCHAR(50),
    search_backend_base_url          VARCHAR(500),
    search_backend_api_key_encrypted TEXT,
    search_backend_api_key_hint      VARCHAR(16),

    created_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
