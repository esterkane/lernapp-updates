"""Learner profile block for prompts (docs/api.md "Learners / progress / privacy").

Renders prompt ``learner_profile_block`` from the learner row, recent results and vocabulary.
Only pedagogical facts leave this module — never the learner's name, e-mail or id (ADR-0011).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.db import db_session
from app.core.prompts import load_prompt
from app.core.rubrics import LABELS_DE
from app.db.base import Learner, Session, VocabItem
from app.services.results import recent_results

DEFAULT_FOCUS = "allgemein"
EXAM_DATE_UNSET = "noch nicht festgelegt"
ACTIVE_DAYS = 7


def get_learner(learner_id: str) -> Learner | None:
    with db_session() as db:
        return db.get(Learner, learner_id)


def _criterion_scores(results: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Collect rubric criterion scores across result payloads (``payload.rubric.scores``)."""
    per: dict[str, list[int]] = {}
    for r in results:
        rubric = (r.get("payload") or {}).get("rubric") or {}
        for s in rubric.get("scores") or []:
            crit = s.get("criterion")
            score = s.get("score")
            if isinstance(crit, str) and isinstance(score, int | float):
                per.setdefault(crit, []).append(int(score))
    return per


def weekly_focus(learner_id: str, days: int = ACTIVE_DAYS) -> str:
    """Explicit ``Learner.weekly_focus`` or the weakest rubric criterion of the last ``days``."""
    learner = get_learner(learner_id)
    if learner is not None and learner.weekly_focus:
        return learner.weekly_focus
    per = _criterion_scores(recent_results(learner_id, days=days))
    if not per:
        return DEFAULT_FOCUS
    averages = {c: sum(v) / len(v) for c, v in per.items()}
    weakest = min(sorted(averages), key=lambda c: averages[c])
    return LABELS_DE.get(weakest, weakest)


def top_error_tags(learner_id: str, days: int = ACTIVE_DAYS, limit: int = 5) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for r in recent_results(learner_id, days=days):
        counter.update(t for t in r.get("error_tags") or [] if isinstance(t, str))
    return [{"tag": tag, "count": n} for tag, n in counter.most_common(limit)]


def recent_vocab(learner_id: str, limit: int = 5) -> list[str]:
    with db_session() as db:
        rows = db.execute(
            select(VocabItem.wort)
            .where(VocabItem.owner_id == learner_id)
            .order_by(VocabItem.created_at.desc())
            .limit(limit)
        ).scalars()
        return [w for w in rows]


def activity_status(learner_id: str, days: int = ACTIVE_DAYS) -> str:
    since = datetime.now(UTC) - timedelta(days=days)
    with db_session() as db:
        row = db.execute(
            select(Session.id).where(Session.learner_id == learner_id, Session.started_at >= since).limit(1)
        ).first()
    return "aktiv" if row is not None else "pausiert"


def format_exam_date(d: datetime | None) -> str:
    return d.strftime("%d.%m.%Y") if d else EXAM_DATE_UNSET


def profile_block(learner_id: str) -> str:
    """Render prompt ``learner_profile_block`` (no PII: level/focus/tags/vocab/status only)."""
    learner = get_learner(learner_id)
    level = learner.level if learner else "B2"
    tags = top_error_tags(learner_id)
    vocab = recent_vocab(learner_id)
    p = load_prompt("learner_profile_block")
    return p.render(
        learning_goal=((learner.profile or {}) if learner else {}).get("learning_goal")
        or "Business-Deutsch: Finanzbereich und Forderungsmanagement (Collections Management)",
        level=level,
        exam_date=format_exam_date(learner.exam_date if learner else None),
        weekly_focus=weekly_focus(learner_id),
        top_error_tags=", ".join(f"{t['tag']} ({t['count']})" for t in tags) if tags else "—",
        recent_vocab=", ".join(vocab) if vocab else "—",
        activity_status=activity_status(learner_id),
    )
