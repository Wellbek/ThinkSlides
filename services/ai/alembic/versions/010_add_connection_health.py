"""add connection health columns

Revision ID: 010
Revises: 009
Create Date: 2026-08-17 00:00:00.000000

What each connection's last discovery attempt found. Written after every run,
read by the connections panel so a user can tell "working" from "we could not
reach this an hour ago" - there is nobody to ask between runs.

last_error is empty on a healthy connection. That is what distinguishes
"reached, working" from "never tried"; last_checked_at alone cannot say which.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organisation_connections",
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
    )
    op.add_column("organisation_connections", sa.Column("last_error", sa.Text))
    op.add_column("organisation_connections", sa.Column("tool_count", sa.Integer))


def downgrade() -> None:
    op.drop_column("organisation_connections", "tool_count")
    op.drop_column("organisation_connections", "last_error")
    op.drop_column("organisation_connections", "last_checked_at")
