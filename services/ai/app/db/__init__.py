"""
Database package: the session/engine plumbing and the ORM models.

**These imports are load-bearing, not conveniences.** Importing a submodule
imports its package first, so ``from app.db.session import Base`` - which is
what ``alembic/env.py`` does, and all it does - runs this file and thereby
registers every model on ``Base.metadata``. Delete the model imports and
autogenerate sees an empty metadata and proposes dropping the whole schema.

``__all__`` is what says that to a linter, which would otherwise read them as
unused and offer to remove them.
"""

from app.db.models import (
    AgentEvent,
    ChatMessage,
    ChatSession,
    DesignSystemBundle,
    DocumentChunk,
    GeneratedFile,
    IngestedDocument,
    Organisation,
    OrganisationAISettings,
    OrganisationConnection,
    DeckSnapshot,
    RunSpan,
    Template,
    User,
)
from app.db.session import get_db, init_db

__all__ = [
    "AgentEvent",
    "ChatMessage",
    "ChatSession",
    "DesignSystemBundle",
    "DocumentChunk",
    "GeneratedFile",
    "IngestedDocument",
    "Organisation",
    "OrganisationAISettings",
    "OrganisationConnection",
    "DeckSnapshot",
    "RunSpan",
    "Template",
    "User",
    "get_db",
    "init_db",
]
