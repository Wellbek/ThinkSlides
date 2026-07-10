from sqlalchemy import Column, String, Integer, Boolean, Text, BigInteger, ForeignKey, DateTime, JSON, ARRAY
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