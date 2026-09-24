"""create run_spans

Revision ID: 011
Revises: 010
Create Date: 2026-09-24 00:00:00.000000

One row per span of an agent run's trace: why each phase, tool and model
call happened, how long it took, what it spent and how the run ended.
Written once per run by app/observability/trace_store.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attrs", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_run_spans_run_id", "run_spans", ["run_id"])
    op.create_index("ix_run_spans_session_id", "run_spans", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_run_spans_session_id", table_name="run_spans")
    op.drop_index("ix_run_spans_run_id", table_name="run_spans")
    op.drop_table("run_spans")
