"""Persist assessment results (ADR-0004 §5: every result carries its versions).

ADR-0017: ``payload`` also carries ``routing`` (``auto_accept | needs_review | spot_check | labelled``),
the ``review_handoff`` and the human ``spot_check_label`` — JSONB only, the table schema is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.core.db import db_session
from app.core.rubrics import SCORE_MAX, SCORE_MIN, criteria, internal_scale
from app.db.base import Result
from app.services.schemas import RubricResult

PENDING_ROUTINGS = ("spot_check", "needs_review")
LABELLED = "labelled"


def store_rubric_result(
    learner_id: str,
    rubric: RubricResult,
    *,
    skill: str,
    session_id: str | None = None,
    task_id: str | None = None,
    is_progress_point: bool = False,
    extra: dict[str, Any] | None = None,
) -> str:
    shown = rubric
    # ADR-0004 §3: when validator disagrees, the *lower* score is shown/stored as score.
    total = rubric.total
    if rubric.validator is not None:
        total = min(total, sum(int(s.score) for s in rubric.validator.scores))
    with db_session() as db:
        r = Result(
            learner_id=learner_id,
            session_id=session_id,
            task_id=task_id,
            skill=skill,
            task_type=rubric.task_type,
            blueprint_id=rubric.blueprint_id,
            rubric_version=rubric.rubric_version,
            prompt_version=rubric.prompt_version,
            model_version=rubric.model_version,
            score=float(total),
            score_max=float(rubric.total_max),
            is_progress_point=is_progress_point,
            needs_review=rubric.needs_review,
            error_tags=list(rubric.error_tags),
            payload={
                "rubric": shown.model_dump(mode="json"),
                "internal_score_20": internal_scale(total),
                "routing": rubric.routing,
                "routing_reasons": list(rubric.routing_reasons),
                "review_handoff": rubric.review_handoff.model_dump(mode="json") if rubric.review_handoff else None,
                "spot_check_label": None,
                "human_label": None,
                **(extra or {}),
            },
        )
        db.add(r)
        db.flush()
        return r.id


def store_deterministic_result(
    learner_id: str,
    *,
    skill: str,
    task_type: str,
    blueprint_id: str | None,
    score: float,
    score_max: float,
    session_id: str | None = None,
    task_id: str | None = None,
    prompt_version: str | None = None,
    model_version: str | None = None,
    payload: dict[str, Any] | None = None,
    is_progress_point: bool = False,
) -> str:
    with db_session() as db:
        r = Result(
            learner_id=learner_id,
            session_id=session_id,
            task_id=task_id,
            skill=skill,
            task_type=task_type,
            blueprint_id=blueprint_id,
            rubric_version=None,
            prompt_version=prompt_version,
            model_version=model_version,
            score=float(score),
            score_max=float(score_max),
            is_progress_point=is_progress_point,
            needs_review=False,
            error_tags=[],
            payload={"internal_score_20": round(score / score_max * 20) if score_max else 0, **(payload or {})},
        )
        db.add(r)
        db.flush()
        return r.id


def get_result(result_id: str) -> dict[str, Any] | None:
    with db_session() as db:
        r = db.get(Result, result_id)
        if r is None:
            return None
        return result_to_dict(r)


def result_to_dict(r: Result) -> dict[str, Any]:
    return {
        "result_id": r.id,
        "learner_id": r.learner_id,
        "session_id": r.session_id,
        "task_id": r.task_id,
        "skill": r.skill,
        "task_type": r.task_type,
        "blueprint_id": r.blueprint_id,
        "rubric_version": r.rubric_version,
        "prompt_version": r.prompt_version,
        "model_version": r.model_version,
        "score": r.score,
        "score_max": r.score_max,
        "internal_score_20": r.payload.get("internal_score_20"),
        "is_progress_point": r.is_progress_point,
        "needs_review": r.needs_review,
        "routing": r.payload.get("routing"),
        "error_tags": list(r.error_tags or []),
        "payload": r.payload,
        "created_at": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
    }


def recent_results(
    learner_id: str, *, skill: str | None = None, days: int | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    with db_session() as db:
        q = select(Result).where(Result.learner_id == learner_id).order_by(Result.created_at.asc()).limit(limit)
        if skill:
            q = q.where(Result.skill == skill)
        if days:
            from datetime import timedelta

            q = q.where(Result.created_at >= datetime.now(UTC) - timedelta(days=days))
        return [result_to_dict(r) for r in db.execute(q).scalars()]


# ---------------------------------------------------------------- spot-checks (ADR-0017 §2, §5)


def auto_accept_count(learner_id: str, skill: str) -> int:
    """Rubric results of this learner × skill that were not sent to review (the spot-check stratum)."""
    with db_session() as db:
        n = db.execute(
            select(func.count())
            .select_from(Result)
            .where(
                Result.learner_id == learner_id,
                Result.skill == skill,
                Result.rubric_version.is_not(None),
                Result.needs_review.is_(False),
            )
        ).scalar_one()
        return int(n)


def _queue_item(r: Result) -> dict[str, Any]:
    rubric = r.payload.get("rubric") or {}
    try:
        crits = criteria(str(r.rubric_version)) if r.rubric_version else []
    except KeyError:
        crits = [str(s.get("criterion")) for s in rubric.get("scores") or []]
    return {
        "result_id": r.id,
        "learner_id": r.learner_id,
        "skill": r.skill,
        "task_type": r.task_type,
        "blueprint_id": r.blueprint_id,
        "rubric_version": r.rubric_version,
        "routing": r.payload.get("routing"),
        "reasons": list(r.payload.get("routing_reasons") or rubric.get("routing_reasons") or []),
        "criteria": crits,
        "review_handoff": r.payload.get("review_handoff"),
        "error_tags": list(r.error_tags or []),
        "score": r.score,
        "score_max": r.score_max,
        "is_progress_point": r.is_progress_point,
        "created_at": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
    }


def pending_spot_checks(learner_id: str) -> list[dict[str, Any]]:
    """Queue for the "Prüfen" page: spot_check + needs_review results without a human label, oldest first."""
    with db_session() as db:
        q = (
            select(Result)
            .where(
                Result.learner_id == learner_id,
                Result.payload["routing"].astext.in_(PENDING_ROUTINGS),
            )
            .order_by(Result.created_at.asc())
        )
        return [_queue_item(r) for r in db.execute(q).scalars() if not r.payload.get("human_label")]


def label_spot_check(result_id: str, human_scores: dict[str, int], note: str | None = None) -> dict[str, Any]:
    """Store the human label in ``payload`` and mark the item ``labelled``. The shown score is untouched —
    labels are calibration data (evals/calibration.py), not a re-grade. Raises KeyError / ValueError."""
    with db_session() as db:
        r = db.get(Result, result_id)
        if r is None:
            raise KeyError(result_id)
        if not r.rubric_version:
            raise ValueError("Nur Rubrik-Ergebnisse können geprüft werden")
        expected = criteria(r.rubric_version)
        missing = [c for c in expected if c not in human_scores]
        unknown = [c for c in human_scores if c not in expected]
        if missing or unknown:
            raise ValueError(
                f"scores müssen genau die Kriterien {expected} enthalten (fehlt: {missing}, unbekannt: {unknown})"
            )
        bad = {c: v for c, v in human_scores.items() if not SCORE_MIN <= int(v) <= SCORE_MAX}
        if bad:
            raise ValueError(f"Werte müssen zwischen {SCORE_MIN} und {SCORE_MAX} liegen: {bad}")
        label = {
            "scores": {c: int(human_scores[c]) for c in expected},
            "note": (note or "").strip(),
            "labelled_at": datetime.now(UTC).isoformat(),
            "routing_before": r.payload.get("routing"),
        }
        r.payload = {**r.payload, "human_label": label, "spot_check_label": label, "routing": LABELLED}
        db.flush()
        return result_to_dict(r)


def labelled_results(learner_id: str, skill: str | None = None) -> list[dict[str, Any]]:
    """Results with a human label (for scripts/export_spotchecks.py), oldest first."""
    with db_session() as db:
        q = (
            select(Result)
            .where(Result.learner_id == learner_id, Result.payload["routing"].astext == LABELLED)
            .order_by(Result.created_at.asc())
        )
        if skill:
            q = q.where(Result.skill == skill)
        return [result_to_dict(r) for r in db.execute(q).scalars() if r.payload.get("human_label")]
