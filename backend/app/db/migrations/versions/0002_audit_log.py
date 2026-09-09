"""append-only audit_log (learnings §3, ADR-0016)

Revision ID: 0002_audit_log
Revises: 0001_initial
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002_audit_log"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("caller", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("tool", sa.String(120), nullable=False),
        sa.Column("arg_digest", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.Column("category", sa.String(16)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("duration_ms", sa.Float, nullable=False, server_default="0"),
        sa.Column("meta", pg.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_session_id", "audit_log", ["session_id"])
    op.create_index("ix_audit_log_tool", "audit_log", ["tool"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_tool", table_name="audit_log")
    op.drop_index("ix_audit_log_session_id", table_name="audit_log")
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_table("audit_log")
