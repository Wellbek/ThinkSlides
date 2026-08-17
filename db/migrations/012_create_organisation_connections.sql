-- Per-organisation external connections: MCP servers, image and figure
-- renderers, data sources.
--
-- Unlike organisation_ai_settings, this is a LIST rather than a singleton row:
-- an organisation can have several MCP servers, and the nav rail's "N
-- connections active" count is the length of it. Hence a surrogate id with
-- (organisation_id, connection_id, slug) unique.
--
-- slug is load-bearing. It is the <server> in the mcp__<server>__<tool> names
-- discovered tools are registered under, so it has to be stable, unique within
-- the organisation, and safe in an identifier. Renaming what a user *calls* a
-- connection changes label and leaves slug alone, because a tool name that
-- moves breaks the transcript of every past run.
--
-- config holds the fields the catalogue marks non-secret - an endpoint URL, a
-- preset id - and is readable, because a value that cannot be read back cannot
-- be edited.
--
-- secrets_encrypted holds one AES-256-GCM envelope per secret field, keyed by
-- field name, produced by services/ai/app/security/crypto.py. The AAD
-- binds each envelope to its organisation, its row AND its field, so a
-- ciphertext cannot be moved to another field, another connection or another
-- organisation and still open. That is wider than the binding
-- organisation_ai_settings uses, and it has to be: an organisation can hold
-- several MCP servers, so binding to the field name alone would let one
-- server's token be copied into another's row.
--
-- secret_hints holds only the last four characters of each credential, so the
-- UI can show which one is configured without it being readable back.

CREATE TABLE IF NOT EXISTS organisation_connections (
    id                UUID PRIMARY KEY,
    organisation_id   UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,

    -- The ConnectionSpec id from services/ai/app/connections/catalog.py.
    connection_id     VARCHAR(100) NOT NULL,
    slug              VARCHAR(64)  NOT NULL,
    label             VARCHAR(200) NOT NULL,

    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    config            JSONB   NOT NULL DEFAULT '{}'::jsonb,
    secrets_encrypted JSONB   NOT NULL DEFAULT '{}'::jsonb,
    secret_hints      JSONB   NOT NULL DEFAULT '{}'::jsonb,

    -- What the last discovery attempt found. Written after every run and read
    -- by the connections panel, because between runs there is nobody to ask
    -- and "we could not reach this an hour ago" is what a user needs to see.
    -- last_error is empty on a healthy connection, which is the only thing
    -- that distinguishes "reached, working" from "never tried".
    last_checked_at   TIMESTAMPTZ,
    last_error        TEXT,
    tool_count        INTEGER,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_organisation_connections_slug
        UNIQUE (organisation_id, connection_id, slug)
);

-- Every read is "the connections for this organisation".
CREATE INDEX IF NOT EXISTS ix_organisation_connections_organisation
    ON organisation_connections (organisation_id);
