from sqlalchemy import Column, String, Integer, Boolean, Text, BigInteger, ForeignKey, DateTime, ARRAY
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.db.session import Base


def utcnow():
    return datetime.now(timezone.utc)


class Organisation(Base):
    __tablename__ = "organisations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    settings = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    users = relationship("User", back_populates="organisation")
    templates = relationship("Template", back_populates="organisation")


class OrganisationAISettings(Base):
    """Per-organisation configuration of the chat and deep-search providers.

    Split into two roles so an organisation can, for example, run a cheap model
    for everyday chat and a stronger one for research-heavy Deep Thinking runs:

    - ``chat_*``   - the generic chat model used for every message.
    - ``search_*`` - the model used for Deep Thinking runs.
    - ``search_backend*`` - where Deep Thinking's web results come from:
      ``native`` (the provider's own search API), ``tavily`` or ``firecrawl``.

    API keys are stored only as AES-256-GCM envelopes (see
    ``app.security.crypto``). The ``*_hint`` columns hold the last four
    characters so the UI can show *which* key is configured without the
    plaintext ever leaving the service.
    """

    __tablename__ = "organisation_ai_settings"

    organisation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    chat_provider = Column(String(50))
    chat_model = Column(String(200))
    chat_base_url = Column(String(500))
    chat_api_key_encrypted = Column(Text)
    chat_api_key_hint = Column(String(16))

    search_provider = Column(String(50))
    search_model = Column(String(200))
    search_base_url = Column(String(500))
    search_api_key_encrypted = Column(Text)
    search_api_key_hint = Column(String(16))

    search_backend = Column(String(50))
    search_backend_base_url = Column(String(500))
    search_backend_api_key_encrypted = Column(Text)
    search_backend_api_key_hint = Column(String(16))

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = Column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255))
    role = Column(String(50), nullable=False, default="member")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    organisation = relationship("Organisation", back_populates="users")
    sessions = relationship("ChatSession", back_populates="user")


class Template(Base):
    __tablename__ = "templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = Column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    tags = Column(ARRAY(String), default=list)
    version = Column(Integer, nullable=False, default=1)
    file_path = Column(String(500), nullable=False)
    original_filename = Column(String(255))
    file_size_bytes = Column(BigInteger)
    mime_type = Column(String(100))
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    organisation = relationship("Organisation", back_populates="templates")
    design_system_bundles = relationship("DesignSystemBundle", back_populates="template")


class DesignSystemBundle(Base):
    __tablename__ = "design_system_bundles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    palette = Column(JSONB, nullable=False, default=dict)
    typography = Column(JSONB, nullable=False, default=dict)
    spacing = Column(JSONB, nullable=False, default=dict)
    layouts = Column(JSONB, nullable=False, default=list)
    slide_dimensions = Column(JSONB, nullable=False, default=dict)
    master_elements = Column(JSONB, nullable=False, default=list)
    embedded_media = Column(JSONB, nullable=False, default=list)
    data_viz = Column(JSONB, nullable=False, default=dict)
    # How much this template puts on a content page, measured from its own
    # rendered pages. Without it the measurement is taken at upload time,
    # published to the model for one run, and then lost.
    density = Column(JSONB, nullable=False, default=dict)
    # Which of this template's pages look alike, and which of them were
    # actually looked at. Kept so naming its archetypes and scoring how much
    # of it was seen do not have to re-read the file.
    page_survey = Column(JSONB, nullable=False, default=dict)
    raw_xml_snapshot = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    template = relationship("Template", back_populates="design_system_bundles")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = Column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False, default="New Chat")
    template_id = Column(UUID(as_uuid=True), ForeignKey("templates.id", ondelete="SET NULL"))
    inline_template_path = Column(String(500))
    slide_count = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="sessions")
    messages = relationship("ChatMessage", back_populates="session", order_by="ChatMessage.created_at")
    events = relationship("AgentEvent", back_populates="session")
    generated_files = relationship("GeneratedFile", back_populates="session")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, default="")
    attachments = Column(JSONB, default=list)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    session = relationship("ChatSession", back_populates="messages")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="CASCADE"))
    event_type = Column(String(50), nullable=False)
    phase = Column(String(50))
    content = Column(JSONB, nullable=False, default=dict)
    tool_name = Column(String(100))
    tool_args = Column(JSONB)
    tool_result = Column(JSONB)
    duration_ms = Column(Integer)
    token_count = Column(Integer)
    # Full event dict in the live SSE shape (type, phase, toolName, callId,
    # result, sources, ...). Persisted so the chat event stream and cited
    # sources can be faithfully reconstructed when a session is reloaded.
    raw_event = Column(JSONB)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    session = relationship("ChatSession", back_populates="events")


class GeneratedFile(Base):
    __tablename__ = "generated_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = Column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    file_type = Column(String(50), nullable=False, default="pptx")
    file_path = Column(String(500), nullable=False)
    original_filename = Column(String(255))
    file_size_bytes = Column(BigInteger)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    session = relationship("ChatSession", back_populates="generated_files")


class IngestedDocument(Base):
    __tablename__ = "ingested_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organisation_id = Column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"))
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size_bytes = Column(BigInteger)
    mime_type = Column(String(100))
    chunk_count = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("ingested_documents.id", ondelete="CASCADE"), nullable=False)
    organisation_id = Column(UUID(as_uuid=True), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    embedding = Column(String)  # pgvector handled via raw SQL
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)