"""add organisation_ai_settings

Revision ID: 003
Revises: 002
Create Date: 2026-08-06 00:00:00.000000

Per-organisation configuration of the generic chat model, the Deep Thinking
model, and the deep-search backend. API keys are stored as AES-256-GCM
envelopes (see services/ai/app/security/crypto.py); the *_hint columns hold
only the last four characters for display.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organisation_ai_settings",
        sa.Column(
            "organisation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chat_provider", sa.String(50)),
        sa.Column("chat_model", sa.String(200)),
        sa.Column("chat_base_url", sa.String(500)),
        sa.Column("chat_api_key_encrypted", sa.Text),
        sa.Column("chat_api_key_hint", sa.String(16)),
        sa.Column("search_provider", sa.String(50)),
        sa.Column("search_model", sa.String(200)),
        sa.Column("search_base_url", sa.String(500)),
        sa.Column("search_api_key_encrypted", sa.Text),
        sa.Column("search_api_key_hint", sa.String(16)),
        sa.Column("search_backend", sa.String(50)),
        sa.Column("search_backend_base_url", sa.String(500)),
        sa.Column("search_backend_api_key_encrypted", sa.Text),
        sa.Column("search_backend_api_key_hint", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("organisation_ai_settings")
