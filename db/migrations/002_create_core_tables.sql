-- ═══════════════════════════════════════════════
-- Organisations
-- ═══════════════════════════════════════════════
CREATE TABLE organisations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) NOT NULL UNIQUE,
    settings        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ═══════════════════════════════════════════════
-- Users
-- ═══════════════════════════════════════════════
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    email           VARCHAR(255) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    display_name    VARCHAR(255),
    role            VARCHAR(50) NOT NULL DEFAULT 'member',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(organisation_id, email)
);

CREATE INDEX idx_users_organisation ON users(organisation_id);
CREATE INDEX idx_users_email ON users(email);

-- ═══════════════════════════════════════════════
-- Templates
-- ═══════════════════════════════════════════════
CREATE TABLE templates (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    tags            TEXT[] DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    file_path       VARCHAR(500) NOT NULL,
    original_filename VARCHAR(255),
    file_size_bytes BIGINT,
    mime_type       VARCHAR(100),
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_templates_organisation ON templates(organisation_id);
CREATE INDEX idx_templates_tags ON templates USING GIN(tags);

-- ═══════════════════════════════════════════════
-- Design System Bundles
-- Extracted from templates at ingestion time
-- ═══════════════════════════════════════════════
CREATE TABLE design_system_bundles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    template_id     UUID NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL DEFAULT 1,
    palette         JSONB NOT NULL DEFAULT '{}',
    typography      JSONB NOT NULL DEFAULT '{}',
    spacing         JSONB NOT NULL DEFAULT '{}',
    layouts         JSONB NOT NULL DEFAULT '[]',
    slide_dimensions JSONB NOT NULL DEFAULT '{}',
    master_elements JSONB NOT NULL DEFAULT '[]',
    embedded_media  JSONB NOT NULL DEFAULT '[]',
    raw_xml_snapshot TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_design_system_template ON design_system_bundles(template_id);