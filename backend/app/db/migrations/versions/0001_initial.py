"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "learners",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False, server_default="Lernende"),
        sa.Column("level", sa.String(8), nullable=False, server_default="B2"),
        sa.Column("exam_date", sa.DateTime(timezone=True)),
        sa.Column("weekly_focus", sa.String(64)),
        sa.Column("profile", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("learner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("state", pg.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_sessions_learner_id", "sessions", ["learner_id"])
    op.create_table(
        "turns",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("meta", pg.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_turns_session_id", "turns", ["session_id"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("blueprint_id", sa.String(64), nullable=False),
        sa.Column("blueprint_version", sa.String(16), nullable=False),
        sa.Column("skill", sa.String(16), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("generator_model", sa.String(120), nullable=False),
        sa.Column("validator_model", sa.String(120), nullable=False),
        sa.Column("prompt_versions", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("validation", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tasks_blueprint_id", "tasks", ["blueprint_id"])
    op.create_table(
        "results",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("learner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(32)),
        sa.Column("task_id", sa.String(32)),
        sa.Column("skill", sa.String(16), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("blueprint_id", sa.String(64)),
        sa.Column("rubric_version", sa.String(32)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("model_version", sa.String(120)),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("score_max", sa.Float, nullable=False),
        sa.Column("is_progress_point", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error_tags", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("payload", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_results_learner_id", "results", ["learner_id"])
    op.create_index("ix_results_skill", "results", ["skill"])
    op.create_index("ix_results_created_at", "results", ["created_at"])
    op.create_table(
        "cost_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("session_id", sa.String(64)),
        sa.Column("learner_id", sa.String(64)),
        sa.Column("stage", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("unit", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("unit_price_usd", sa.Float),
        sa.Column("cost_usd", sa.Float),
        sa.Column("cost_eur", sa.Float),
        sa.Column("fx_rate", sa.Float, nullable=False),
        sa.Column("pricing_version", sa.String(24), nullable=False),
        sa.Column("local", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("meta", pg.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_cost_ledger_ts", "cost_ledger", ["ts"])
    op.create_index("ix_cost_ledger_session_id", "cost_ledger", ["session_id"])
    op.create_index("ix_cost_ledger_learner_id", "cost_ledger", ["learner_id"])
    op.create_table(
        "documents",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("tags", pg.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("use_in", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("n_chunks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_chars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="hochgeladen"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("document_id", sa.String(32), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("heading", sa.String(255)),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector()),
        sa.Column("embedding_model", sa.String(120)),
        sa.Column("tsv", pg.TSVECTOR, sa.Computed("to_tsvector('german', text)", persisted=True)),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_owner_id", "chunks", ["owner_id"])
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_table(
        "vocab_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wort", sa.String(255), nullable=False),
        sa.Column("bedeutung", sa.Text, nullable=False),
        sa.Column("beispiel", sa.Text),
        sa.Column("source_document_id", sa.String(32)),
        sa.Column("ease", sa.Float, nullable=False, server_default="2.5"),
        sa.Column("interval_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("repetitions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vocab_items_owner_id", "vocab_items", ["owner_id"])
    op.create_index("ix_vocab_items_due_at", "vocab_items", ["due_at"])
    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("difficulty", sa.Integer, nullable=False),
        sa.Column("title_de", sa.String(255), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="seed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scenarios_category", "scenarios", ["category"])
    op.create_table(
        "audio_files",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("session_id", sa.String(32)),
        sa.Column("learner_id", sa.String(64)),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audio_files_expires_at", "audio_files", ["expires_at"])


def downgrade() -> None:
    for t in (
        "audio_files",
        "scenarios",
        "vocab_items",
        "chunks",
        "documents",
        "cost_ledger",
        "results",
        "tasks",
        "turns",
        "sessions",
        "learners",
    ):
        op.drop_table(t)
