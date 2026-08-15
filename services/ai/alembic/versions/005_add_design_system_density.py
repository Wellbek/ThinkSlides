"""add design_system_bundles.density

Revision ID: 005
Revises: 004
Create Date: 2026-08-15 00:00:00.000000

Stores the density budget measured from a template's own rendered pages -
words and blocks per content page, the grid shape, words per block - derived
once during design-system extraction. Without it an uploaded template's
density is measured, published to the model for one run, and then lost, so
every later deck from the same template falls back to the built-in default.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "design_system_bundles",
        sa.Column("density", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("design_system_bundles", "density")
