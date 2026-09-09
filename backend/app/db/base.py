"""SQLAlchemy ORM models (ADR-0006, ADR-0007)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: ARRAY(String)}


class Learner(Base):
    __tablename__ = "learners"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="Lernende")
    level: Mapped[str] = mapped_column(String(8), default="B2")
    exam_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    weekly_focus: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # tutor|sprechen|schreiben|lesen|hoeren|roleplay|eval
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    turns: Mapped[list[Turn]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # learner|assistant|system
    text: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    session: Mapped[Session] = relationship(back_populates="turns")


class Task(Base):
    __tablename__ = "tasks"
    learner_id: Mapped[str | None] = mapped_column(
        ForeignKey("learners.id", ondelete="CASCADE"), nullable=True, index=True
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    blueprint_id: Mapped[str] = mapped_column(String(64), index=True)
    blueprint_version: Mapped[str] = mapped_column(String(16))
    skill: Mapped[str] = mapped_column(String(16))
    task_type: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(8))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    generator_model: Mapped[str] = mapped_column(String(120))
    validator_model: Mapped[str] = mapped_column(String(120))
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|rejected
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Result(Base):
    __tablename__ = "results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    skill: Mapped[str] = mapped_column(String(16), index=True)
    task_type: Mapped[str] = mapped_column(String(64))
    blueprint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rubric_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    score: Mapped[float] = mapped_column(Float)
    score_max: Mapped[float] = mapped_column(Float)
    is_progress_point: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    error_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class CostLedger(Base):
    __tablename__ = "cost_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    learner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(8))  # stt|llm|tts|pron|embed
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[float] = mapped_column(Float)
    unit_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    fx_rate: Mapped[float] = mapped_column(Float)
    pricing_version: Mapped[str] = mapped_column(String(24))
    local: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    event_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class UsageEvent(Base):
    """One provider call = one event (cost-tracking model, ADR-0019). Ledger rows are its priced units."""

    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    learner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    service: Mapped[str] = mapped_column(String(16), index=True)  # llm|stt|tts|pronunciation|embedding|evaluation|other
    stage: Mapped[str] = mapped_column(String(8))  # ledger stage for the unit rows (llm|stt|tts|pron|embed)
    tier_name: Mapped[str | None] = mapped_column(String(32), nullable=True)  # conversation|assessment|...
    model: Mapped[str] = mapped_column(String(120), index=True)
    operation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(String(8), default="ok")  # ok|error
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_input_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_output_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    characters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pricing_tier: Mapped[str] = mapped_column(String(8), default="unknown")  # free|paid|unknown
    cost_status: Mapped[str] = mapped_column(String(10), default="unknown")  # free|estimated|confirmed|unknown
    list_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    list_cost_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_cost_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    pricing_rule_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    pricing_version: Mapped[str] = mapped_column(String(24))
    fx_rate: Mapped[float] = mapped_column(Float)
    fx_source: Mapped[str] = mapped_column(String(64), default="config")
    fx_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    local: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ProviderCredential(Base):
    """BYOK provider key, encrypted at rest (Fernet, key from CREDENTIAL_ENCRYPTION_KEY). One per learner+provider."""

    __tablename__ = "provider_credentials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)  # None → app-level key from the environment
    key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)  # last 3 characters only
    pricing_tier: Mapped[str] = mapped_column(String(8), default="unknown")  # free|paid|unknown
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    __table_args__ = (UniqueConstraint("learner_id", "provider", name="uq_provider_credentials_learner_provider"),)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    use_in: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {uebungen, rollenspiel, tutor}
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)
    n_chars: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="hochgeladen")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    chunks: Mapped[list[Chunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('german', text)", persisted=True))
    document: Mapped[Document] = relationship(back_populates="chunks")
    __table_args__ = (Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),)


class VocabItem(Base):
    __tablename__ = "vocab_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"), index=True)
    wort: Mapped[str] = mapped_column(String(255))
    bedeutung: Mapped[str] = mapped_column(Text)
    beispiel: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    difficulty: Mapped[int] = mapped_column(Integer)
    title_de: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(16), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AudioFile(Base):
    """Only used when KEEP_AUDIO=true (ADR-0011): encrypted at rest, purged after retention."""

    __tablename__ = "audio_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    learner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuditLog(Base):
    """Append-only who-did-what trail (learnings §3): argument digest, never raw learner text."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    caller: Mapped[str] = mapped_column(String(16), default="unknown")  # ui|mcp|unknown
    tool: Mapped[str] = mapped_column(String(120), index=True)
    arg_digest: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(8))  # ok|denied|error
    category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# Keep JSON import referenced for type-checkers in case of dialect fallbacks.
_ = JSON
