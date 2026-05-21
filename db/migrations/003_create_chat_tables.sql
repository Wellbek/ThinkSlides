-- ═══════════════════════════════════════════════
-- Chat Sessions
-- ═══════════════════════════════════════════════
CREATE TABLE chat_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(500) NOT NULL DEFAULT 'New Chat',
    template_id     UUID REFERENCES templates(id) ON DELETE SET NULL,
    inline_template_path VARCHAR(500),
    slide_count     INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_sessions_org_user ON chat_sessions(organisation_id, user_id);
CREATE INDEX idx_chat_sessions_updated ON chat_sessions(updated_at DESC);

-- ═══════════════════════════════════════════════
-- Chat Messages
-- ═══════════════════════════════════════════════
CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL DEFAULT '',
    attachments     JSONB DEFAULT '[]',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);

-- ═══════════════════════════════════════════════
-- Agent Events
-- Streams all agent thoughts, tool calls, results
-- ═══════════════════════════════════════════════
CREATE TABLE agent_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    message_id      UUID REFERENCES chat_messages(id) ON DELETE CASCADE,
    event_type      VARCHAR(50) NOT NULL,
    phase           VARCHAR(50),
    content         JSONB NOT NULL DEFAULT '{}',
    tool_name       VARCHAR(100),
    tool_args       JSONB,
    tool_result     JSONB,
    duration_ms     INTEGER,
    token_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_events_session ON agent_events(session_id, created_at);
CREATE INDEX idx_agent_events_type ON agent_events(event_type);

-- ═══════════════════════════════════════════════
-- Generated Files
-- ═══════════════════════════════════════════════
CREATE TABLE generated_files (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    file_type       VARCHAR(50) NOT NULL DEFAULT 'pptx',
    file_path       VARCHAR(500) NOT NULL,
    original_filename VARCHAR(255),
    file_size_bytes BIGINT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_generated_files_session ON generated_files(session_id);

-- ═══════════════════════════════════════════════
-- Document Ingestion
-- ═══════════════════════════════════════════════
CREATE TABLE ingested_documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    session_id      UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_path       VARCHAR(500) NOT NULL,
    file_size_bytes BIGINT,
    mime_type       VARCHAR(100),
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ingested_docs_session ON ingested_documents(session_id);

-- ═══════════════════════════════════════════════
-- Document Chunks (for RAG)
-- ═══════════════════════════════════════════════
CREATE TABLE document_chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID NOT NULL REFERENCES ingested_documents(id) ON DELETE CASCADE,
    organisation_id UUID NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    embedding       vector(1536),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_doc_chunks_document ON document_chunks(document_id);
CREATE INDEX idx_doc_chunks_org ON document_chunks(organisation_id);

-- Cosine similarity search index
CREATE INDEX idx_doc_chunks_embedding ON document_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);