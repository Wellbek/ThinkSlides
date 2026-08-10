"""add design_system_bundles.data_viz

Revision ID: 004
Revises: 003
Create Date: 2026-08-09 00:00:00.000000

Stores table/chart styling tokens (chart color sequence, axis/grid colors,
table header/band colors, font) derived once during design-system
extraction, so generated tables/charts can be styled from the template's
own brand colors instead of a generic default.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "design_system_bundles",
        sa.Column("data_viz", JSONB, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("design_system_bundles", "data_viz")
