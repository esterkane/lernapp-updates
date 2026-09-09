"""Imported model tests, original assets and practice attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_exams"
down_revision = "0004_task_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exams",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("pdf", sa.LargeBinary, nullable=False),
        sa.Column("audio", sa.LargeBinary, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exams_owner_id", "exams", ["owner_id"])
    op.create_table(
        "exam_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("exam_id", sa.String(32), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_exam_attempts_exam_id", "exam_attempts", ["exam_id"])
    op.create_index("ix_exam_attempts_owner_id", "exam_attempts", ["owner_id"])


def downgrade() -> None:
    op.drop_table("exam_attempts")
    op.drop_table("exams")
