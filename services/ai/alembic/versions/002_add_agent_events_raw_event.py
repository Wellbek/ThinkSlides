"""add agent_events.raw_event

Revision ID: 002
Revises: 001
Create Date: 2026-07-09 00:00:00.000000

Stores the full agent event dict (live SSE shape) so the chat event stream
and cited sources can be reconstructed when a session is reloaded.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_events", sa.Column("raw_event", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("agent_events", "raw_event")
