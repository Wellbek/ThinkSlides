"""add organisation_connections

Revision ID: 009
Revises: 008
Create Date: 2026-08-17 00:00:00.000000

Per-organisation external connections: MCP servers, image and figure renderers,
data sources. Unlike organisation_ai_settings this is a list rather than a
singleton row, because an organisation can have several MCP servers.

Secrets are stored as AES-256-GCM envelopes in a JSONB map keyed by field name
(see services/ai/app/connections/store.py). The AAD binds each envelope to its
organisation, its row and its field, so a ciphertext cannot be moved between
connections and still open. secret_hints holds only the last four characters,
for display.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organisation_connections",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "organisation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organisations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connection_id", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "config",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "secrets_encrypted",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "secret_hints",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint(
            "organisation_id", "connection_id", "slug",
            name="uq_organisation_connections_slug",
        ),
    )
    # Every read is "the connections for this organisation", so that is the
    # index. The unique constraint above covers it left-to-right, but naming it
    # separately keeps the intent legible when the constraint changes.
    op.create_index(
        "ix_organisation_connections_organisation",
        "organisation_connections",
        ["organisation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_organisation_connections_organisation",
                  table_name="organisation_connections")
    op.drop_table("organisation_connections")
