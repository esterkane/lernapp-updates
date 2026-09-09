"""Vocabulary items with SM-2 spaced repetition (ADR-0008: CSV lists bypass embeddings)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.db import db_session
from app.db.base import VocabItem

log = logging.getLogger(__name__)

MIN_EASE = 1.3
HEADER_WORDS = {"wort", "word", "vokabel", "begriff", "term"}


# ---------------------------------------------------------------- SM-2


def sm2(ease: float, interval: int, repetitions: int, quality: int) -> tuple[float, int, int]:
    """SuperMemo-2 update. Returns ``(ease, interval_days, repetitions)``.

    quality 0–5; < 3 resets repetitions and schedules for tomorrow. Ease never drops below 1.3.
    Interval sequence: 1, 6, then ``round(previous * ease)``.
    """
    if not 0 <= quality <= 5:
        raise ValueError("quality muss zwischen 0 und 5 liegen")
    q = quality
    new_ease = max(MIN_EASE, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    new_ease = round(new_ease, 3)
    if q < 3:
        return new_ease, 1, 0
    reps = repetitions + 1
    if reps == 1:
        new_interval = 1
    elif reps == 2:
        new_interval = 6
    else:
        new_interval = max(1, round(max(interval, 1) * new_ease))
    return new_ease, new_interval, reps


# ---------------------------------------------------------------- CSV import


def parse_csv(data: bytes) -> list[tuple[str, str, str | None]]:
    """``wort,bedeutung[,beispiel]`` rows; header optional; delimiter ``,`` or ``;``; BOM-tolerant."""
    text: str
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    delimiter = ";" if first.count(";") > first.count(",") else ","
    rows: list[tuple[str, str, str | None]] = []
    for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter)):
        cells = [c.strip() for c in row]
        if i == 0 and cells and cells[0].lower().strip("﻿") in HEADER_WORDS:
            continue
        if len(cells) < 2 or not cells[0] or not cells[1]:
            continue
        beispiel = cells[2] if len(cells) > 2 and cells[2] else None
        rows.append((cells[0][:255], cells[1], beispiel))
    return rows


def import_csv(owner_id: str, data: bytes, *, source_document_id: str | None = None) -> int:
    rows = parse_csv(data)
    if not rows:
        return 0
    from app.services.vocab_import import import_items
    result = import_items(owner_id, [
        {"wort": w, "bedeutung": m, "beispiel": e} for w, m, e in rows
    ], source_document_id)
    return result["added"]



# ---------------------------------------------------------------- items


def _item_dict(v: VocabItem) -> dict[str, Any]:
    return {
        "id": v.id,
        "wort": v.wort,
        "bedeutung": v.bedeutung,
        "beispiel": v.beispiel,
        "source_document_id": v.source_document_id,
        "ease": v.ease,
        "interval_days": v.interval_days,
        "repetitions": v.repetitions,
        "due_at": v.due_at.isoformat() if v.due_at else None,
        "last_reviewed_at": v.last_reviewed_at.isoformat() if v.last_reviewed_at else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def add_item(owner_id: str, wort: str, bedeutung: str, beispiel: str | None = None) -> dict[str, Any]:
    if not wort.strip() or not bedeutung.strip():
        raise ValueError("wort und bedeutung dürfen nicht leer sein")
    with db_session() as db:
        item = VocabItem(
            owner_id=owner_id,
            wort=wort.strip()[:255],
            bedeutung=bedeutung.strip(),
            beispiel=(beispiel or "").strip() or None,
        )
        db.add(item)
        db.flush()
        return _item_dict(item)


def get_item(item_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
    with db_session() as db:
        item = db.get(VocabItem, item_id)
        if item is None or (owner_id is not None and item.owner_id != owner_id):
            return None
        return _item_dict(item)


def due_items(owner_id: str, limit: int = 20, *, due_only: bool = True) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    stmt = select(VocabItem).where(VocabItem.owner_id == owner_id)
    if due_only:
        stmt = stmt.where(VocabItem.due_at <= now)
    stmt = stmt.order_by(VocabItem.due_at, VocabItem.created_at, VocabItem.id).limit(max(1, limit))
    with db_session() as db:
        return [_item_dict(v) for v in db.execute(stmt).scalars()]


def review(item_id: str, quality: int, *, owner_id: str | None = None) -> dict[str, Any] | None:
    with db_session() as db:
        item = db.get(VocabItem, item_id)
        if item is None or (owner_id is not None and item.owner_id != owner_id):
            return None
        ease, interval, reps = sm2(item.ease, item.interval_days, item.repetitions, quality)
        now = datetime.now(UTC)
        item.ease = ease
        item.interval_days = interval
        item.repetitions = reps
        item.last_reviewed_at = now
        item.due_at = now + timedelta(days=interval)
        db.flush()
        return _item_dict(item)


def stats(owner_id: str) -> dict[str, int]:
    now = datetime.now(UTC)
    with db_session() as db:
        total = db.execute(select(func.count()).where(VocabItem.owner_id == owner_id)).scalar_one()
        due = db.execute(
            select(func.count()).where(VocabItem.owner_id == owner_id, VocabItem.due_at <= now)
        ).scalar_one()
    return {"total": int(total), "due": int(due)}


def delete_item(item_id: str, owner_id: str | None = None) -> bool:
    with db_session() as db:
        item = db.get(VocabItem, item_id)
        if item is None or (owner_id is not None and item.owner_id != owner_id):
            return False
        db.delete(item)
    return True
