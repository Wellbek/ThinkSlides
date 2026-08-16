"""add design_system_bundles.page_survey

Revision ID: 006
Revises: 005
Create Date: 2026-08-15 00:00:00.000000

Stores the layout survey of a template's own pages - which pages look alike,
which one represents each group, and which of them were shown to the vision
model. Computed without an LLM at extraction time; kept so that naming a
template's archetypes and scoring how much of it was seen do not have to
re-read the file.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "design_system_bundles",
        sa.Column("page_survey", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("design_system_bundles", "page_survey")
