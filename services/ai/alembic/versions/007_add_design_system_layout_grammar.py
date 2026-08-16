"""add design_system_bundles.layout_grammar

Revision ID: 007
Revises: 006
Create Date: 2026-08-15 00:00:00.000000

Stores the region grammar read from a template's own rendered pages - rails,
ground splits, band rows, card rows, banners - with fractional geometry, in
the vocabulary the renderer composes in. Read without an LLM at extraction
time; kept so a deck generated later from the same template can be composed
into its layouts rather than into placeholder boxes.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "design_system_bundles",
        sa.Column("layout_grammar", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("design_system_bundles", "layout_grammar")
