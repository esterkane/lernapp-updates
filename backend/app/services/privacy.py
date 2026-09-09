"""Learner data export and deletion (ADR-0011, product rule 9, privacy-dsgvo skill).

Export: JSON (profile, sessions with turns, results, document metadata) + CSV (vocab, progress,
costs) — as a zip for the API and as loose files for ``scripts/export_learner_data.py``.
Deletion: the learner row is removed (FK cascades sessions/turns/results/documents/chunks/vocab);
ledger rows are kept for aggregate cost reporting but anonymised (no learner/session/meta).
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select, update

from app.core.db import db_session
from app.db.base import AudioFile, CostLedger, Document, Learner, Session, Task, UsageEvent, VocabItem
from app.services import progress as progress_service
from app.services.results import recent_results

EXPORT_MEMBERS = (
    "learner.json",
    "sessions.json",
    "results.json",
    "documents.json",
    "vocab.csv",
    "progress.csv",
    "costs.csv",
    "usage.json",
    "tasks.json",
    "exams.json",
)

VOCAB_COLUMNS = (
    "id",
    "wort",
    "bedeutung",
    "beispiel",
    "source_document_id",
    "ease",
    "interval_days",
    "repetitions",
    "due_at",
    "last_reviewed_at",
    "created_at",
)
COST_COLUMNS = (
    "id",
    "ts",
    "session_id",
    "stage",
    "provider",
    "model",
    "prompt_version",
    "unit",
    "quantity",
    "unit_price_usd",
    "cost_usd",
    "cost_eur",
    "fx_rate",
    "pricing_version",
    "local",
)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _csv(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return buf.getvalue()


def learner_dict(learner: Learner) -> dict[str, Any]:
    return {
        "id": learner.id,
        "display_name": learner.display_name,
        "level": learner.level,
        "exam_date": _iso(learner.exam_date),
        "weekly_focus": learner.weekly_focus,
        "profile": dict(learner.profile or {}),
        "created_at": _iso(learner.created_at),
    }


def _sessions(db: Any, learner_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sessions = db.execute(
        select(Session).where(Session.learner_id == learner_id).order_by(Session.started_at)
    ).scalars()
    for s in sessions:
        turns = sorted(s.turns, key=lambda t: t.ord)
        out.append(
            {
                "session_id": s.id,
                "kind": s.kind,
                "started_at": _iso(s.started_at),
                "ended_at": _iso(s.ended_at),
                "duration_seconds": s.duration_seconds,
                "state": dict(s.state or {}),
                "turns": [
                    {
                        "ord": t.ord,
                        "role": t.role,
                        "text": t.text,
                        "ts": _iso(t.ts),
                        "meta": dict(t.meta or {}),
                    }
                    for t in turns
                ],
            }
        )
    return out


def _documents(db: Any, learner_id: str) -> list[dict[str, Any]]:
    docs = db.execute(select(Document).where(Document.owner_id == learner_id).order_by(Document.created_at)).scalars()
    return [
        {
            "id": d.id,
            "title": d.title,
            "filename": d.filename,
            "mime": d.mime,
            "tags": list(d.tags or []),
            "use_in": dict(d.use_in or {}),
            "n_chunks": d.n_chunks,
            "n_chars": d.n_chars,
            "status": d.status,
            "error": d.error,
            "created_at": _iso(d.created_at),
        }
        for d in docs
    ]


def _vocab(db: Any, learner_id: str) -> list[dict[str, Any]]:
    items = db.execute(
        select(VocabItem).where(VocabItem.owner_id == learner_id).order_by(VocabItem.created_at)
    ).scalars()
    return [
        {
            "id": v.id,
            "wort": v.wort,
            "bedeutung": v.bedeutung,
            "beispiel": v.beispiel,
            "source_document_id": v.source_document_id,
            "ease": v.ease,
            "interval_days": v.interval_days,
            "repetitions": v.repetitions,
            "due_at": _iso(v.due_at),
            "last_reviewed_at": _iso(v.last_reviewed_at),
            "created_at": _iso(v.created_at),
        }
        for v in items
    ]


def _costs(db: Any, learner_id: str, session_ids: list[str]) -> list[dict[str, Any]]:
    cond = CostLedger.learner_id == learner_id
    if session_ids:
        cond = or_(cond, CostLedger.session_id.in_(session_ids))
    rows = db.execute(select(CostLedger).where(cond).order_by(CostLedger.id)).scalars()
    return [
        {
            "id": r.id,
            "ts": _iso(r.ts),
            "session_id": r.session_id,
            "stage": r.stage,
            "provider": r.provider,
            "model": r.model,
            "prompt_version": r.prompt_version,
            "unit": r.unit,
            "quantity": r.quantity,
            "unit_price_usd": r.unit_price_usd,
            "cost_usd": r.cost_usd,
            "cost_eur": r.cost_eur,
            "fx_rate": r.fx_rate,
            "pricing_version": r.pricing_version,
            "local": r.local,
        }
        for r in rows
    ]


def export_files(learner_id: str) -> dict[str, str]:
    """All export members as ``{filename: text}``. Raises ``KeyError`` for an unknown learner."""
    with db_session() as db:
        learner = db.get(Learner, learner_id)
        if learner is None:
            raise KeyError(learner_id)
        profile = learner_dict(learner)
        profile["exported_at"] = datetime.now(UTC).isoformat()
        sessions = _sessions(db, learner_id)
        documents = _documents(db, learner_id)
        vocab = _vocab(db, learner_id)
        costs = _costs(db, learner_id, [s["session_id"] for s in sessions])
        from app.core.config import get_settings

        task_condition = Task.learner_id == learner_id
        if learner_id == get_settings().default_learner_id:
            task_condition = or_(task_condition, Task.learner_id.is_(None))
        tasks = [
            {"id": task.id, "payload": task.payload, "created_at": _iso(task.created_at)}
            for task in db.execute(select(Task).where(task_condition)).scalars()
        ]
        from app.services.usage_report import event_dict

        usage = [
            event_dict(event)
            for event in db.execute(
                select(UsageEvent).where(UsageEvent.learner_id == learner_id).order_by(UsageEvent.ts)
            ).scalars()
        ]
    from app.services.exams import export_data

    exam_data = export_data(learner_id)
    results = recent_results(learner_id, limit=100_000)
    points = progress_service.points_for(learner_id)
    return {
        "learner.json": _json(profile),
        "sessions.json": _json(sessions),
        "results.json": _json(results),
        "documents.json": _json(documents),
        "vocab.csv": _csv(VOCAB_COLUMNS, vocab),
        "progress.csv": _csv(progress_service.POINT_FIELDS, points),
        "costs.csv": _csv(COST_COLUMNS, costs),
        "usage.json": _json(usage),
        "tasks.json": _json(tasks),
        "exams.json": exam_data,
    }


def export_learner(learner_id: str) -> bytes:
    """Zip archive of :func:`export_files`."""
    files = export_files(learner_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in EXPORT_MEMBERS:
            zf.writestr(name, files[name].encode("utf-8"))
    return buf.getvalue()


def write_export(learner_id: str, out_dir: Path) -> list[Path]:
    """Write the export members unpacked into ``out_dir`` (CLI use)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in export_files(learner_id).items():
        p = out_dir / name
        p.write_text(text, encoding="utf-8")
        written.append(p)
    return written


def anonymise_ledger(learner_id: str, session_ids: list[str]) -> int:
    """Keep ledger rows for aggregates but strip learner/session linkage and meta (ADR-0011)."""
    cond = CostLedger.learner_id == learner_id
    if session_ids:
        cond = or_(cond, CostLedger.session_id.in_(session_ids))
    with db_session() as db:
        res: Any = db.execute(update(CostLedger).where(cond).values(learner_id=None, session_id=None, meta={}))
        return int(getattr(res, "rowcount", 0) or 0)


def delete_learner(learner_id: str) -> dict[str, Any]:
    """Delete the learner and everything attached (FK cascade); anonymise ledger rows.

    Raises ``KeyError`` for an unknown learner.
    """
    with db_session() as db:
        learner = db.get(Learner, learner_id)
        if learner is None:
            raise KeyError(learner_id)
        session_ids = list(db.execute(select(Session.id).where(Session.learner_id == learner_id)).scalars())
    anonymised = anonymise_ledger(learner_id, session_ids)
    usage_cond = UsageEvent.learner_id == learner_id
    if session_ids:
        usage_cond = or_(usage_cond, UsageEvent.session_id.in_(session_ids))
    with db_session() as db:
        for event in db.execute(select(UsageEvent).where(usage_cond)).scalars():
            event.learner_id = None
            event.session_id = None
            event.request_id = None
            event.idempotency_key = "anonymous:" + event.id
            event.raw_usage = {}
            event.meta = {}
    with db_session() as db:
        from app.core.config import get_settings

        if learner_id == get_settings().default_learner_id:
            db.execute(delete(Task).where(Task.learner_id.is_(None)))
        audio_cond = AudioFile.learner_id == learner_id
        if session_ids:
            audio_cond = or_(audio_cond, AudioFile.session_id.in_(session_ids))
        for f in db.execute(select(AudioFile).where(audio_cond)).scalars():
            Path(f.path).unlink(missing_ok=True)
            db.delete(f)
        learner = db.get(Learner, learner_id)
        if learner is not None:
            db.delete(learner)
    return {
        "deleted": True,
        "learner_id": learner_id,
        "sessions": len(session_ids),
        "ledger_rows_anonymised": anonymised,
    }
