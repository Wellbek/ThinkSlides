"""add design_system_bundles.archetypes

Revision ID: 008
Revises: 007
Create Date: 2026-08-15 00:00:00.000000

Stores the named archetypes read from a template's own pages - each with its
region grammar, its density and the register its titles are in - and the
chapter the template repeats them in. Found without an LLM at extraction time;
kept so a later deck can instantiate the pattern per subject rather than being
planned slide by slide.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "design_system_bundles",
        sa.Column("archetypes", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("design_system_bundles", "archetypes")
