"""Learners / progress / privacy API (docs/api.md "Learners / progress / privacy")."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core.db import db_session
from app.core.workspaces import resolve_learner
from app.db.base import Learner
from app.services import learner_profile, privacy, progress, results

router = APIRouter(tags=["learners"])

LEVELS = ("A2", "B1", "B2", "C1", "C2")


class SpotCheckLabel(BaseModel):
    scores: dict[str, int] = Field(default_factory=dict)  # criterion → 0–4, all rubric criteria required
    note: str | None = Field(default=None, max_length=2000)


class LearnerPatch(BaseModel):
    learning_goal: str | None = Field(default=None, max_length=600)
    economy_mode: bool | None = None
    validator_model: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    level: str | None = None
    exam_date: str | None = None  # ISO date / datetime, "" or null → unset
    weekly_focus: str | None = Field(default=None, max_length=64)


def _learner_dict(learner: Learner) -> dict[str, Any]:
    return {
        "id": learner.id,
        "learning_goal": (learner.profile or {}).get("learning_goal", ""),
        "economy_mode": bool((learner.profile or {}).get("economy_mode", False)),
        "validator_model": (learner.profile or {}).get("validator_model"),
        "display_name": learner.display_name,
        "level": learner.level,
        "exam_date": learner.exam_date.date().isoformat() if learner.exam_date else None,
        "weekly_focus": learner.weekly_focus,
        "activity_status": learner_profile.activity_status(learner.id),
        "created_at": learner.created_at.isoformat() if learner.created_at else None,
    }


def _parse_exam_date(value: str) -> datetime:
    try:
        d = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
    except ValueError:
        try:
            d = datetime.strptime(value, "%d.%m.%Y")
        except ValueError as exc:
            raise HTTPException(400, f"Ungültiges Datum: {value}") from exc
    dt = datetime(d.year, d.month, d.day, tzinfo=UTC) if isinstance(d, date) and not isinstance(d, datetime) else d
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/learners/{learner_id}")
def get_learner(learner_id: str) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    with db_session() as db:
        learner = db.get(Learner, learner_id)
        if learner is None:
            raise HTTPException(404, f"Lernende nicht gefunden: {learner_id}")
        return _learner_dict(learner)


@router.patch("/learners/{learner_id}")
def patch_learner(learner_id: str, body: LearnerPatch) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("validator_model"):
        from app.core.config import get_settings
        from app.core.models import validator_choices

        if get_settings().eu_strict_mode or fields["validator_model"] not in validator_choices():
            raise HTTPException(400, "Bitte ein verfügbares Zweitmeinungsmodell eines anderen Anbieters auswählen.")
    if "level" in fields and fields["level"] is not None and fields["level"].upper() not in LEVELS:
        raise HTTPException(400, f"level muss eines von {LEVELS} sein")
    with db_session() as db:
        learner = db.get(Learner, learner_id)
        if learner is None:
            raise HTTPException(404, f"Lernende nicht gefunden: {learner_id}")
        if "display_name" in fields and fields["display_name"]:
            learner.display_name = fields["display_name"]
        if "level" in fields and fields["level"]:
            learner.level = fields["level"].upper()
        if "learning_goal" in fields:
            learner.profile = {**(learner.profile or {}), "learning_goal": (fields["learning_goal"] or "").strip()}
        if "economy_mode" in fields and fields["economy_mode"] is not None:
            learner.profile = {**(learner.profile or {}), "economy_mode": fields["economy_mode"]}
        if "validator_model" in fields:
            profile = dict(learner.profile or {})
            if fields["validator_model"]:
                profile["validator_model"] = fields["validator_model"]
            else:
                profile.pop("validator_model", None)
            learner.profile = profile
        if "weekly_focus" in fields:
            learner.weekly_focus = fields["weekly_focus"] or None
        if "exam_date" in fields:
            raw = fields["exam_date"]
            learner.exam_date = _parse_exam_date(raw) if raw else None
        db.flush()
        return _learner_dict(learner)


@router.get("/learners/{learner_id}/progress")
def get_progress(learner_id: str, skill: str | None = None) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    _require(learner_id)
    return progress.progress(learner_id, skill=skill)


@router.get("/learners/{learner_id}/results")
def list_results(learner_id: str, skill: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    learner_id = resolve_learner(learner_id)
    _require(learner_id)
    rows = results.recent_results(learner_id, skill=skill, limit=100_000)
    return list(reversed(rows))[: max(1, min(limit, 500))]


@router.get("/learners/{learner_id}/results/{result_id}")
def get_result(learner_id: str, result_id: str) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    r = results.get_result(result_id)
    if r is None or r.get("learner_id") != learner_id:
        raise HTTPException(404, f"Ergebnis nicht gefunden: {result_id}")
    return r


@router.get("/learners/{learner_id}/spotchecks")
def list_spotchecks(learner_id: str) -> list[dict[str, Any]]:
    """Pending ``spot_check`` + ``needs_review`` results with their ReviewHandoff (ADR-0017 §2, §5)."""
    learner_id = resolve_learner(learner_id)
    _require(learner_id)
    return results.pending_spot_checks(learner_id)


@router.post("/learners/{learner_id}/spotchecks/{result_id}/label")
def label_spotcheck(learner_id: str, result_id: str, body: SpotCheckLabel) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    _require(learner_id)
    existing = results.get_result(result_id)
    if existing is None or existing.get("learner_id") != learner_id:
        raise HTTPException(404, f"Ergebnis nicht gefunden: {result_id}")
    try:
        return results.label_spot_check(result_id, body.scores, body.note)
    except KeyError as exc:
        raise HTTPException(404, f"Ergebnis nicht gefunden: {result_id}") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/learners/{learner_id}/export")
async def export_learner(learner_id: str) -> Response:
    learner_id = resolve_learner(learner_id)
    try:
        data = await run_in_threadpool(privacy.export_learner, learner_id)
    except KeyError as exc:
        raise HTTPException(404, f"Lernende nicht gefunden: {exc}") from exc
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="lernapp-export-{learner_id}-{stamp}.zip"'},
    )


@router.delete("/learners/{learner_id}")
def delete_learner(learner_id: str) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    try:
        return privacy.delete_learner(learner_id)
    except KeyError as exc:
        raise HTTPException(404, f"Lernende nicht gefunden: {exc}") from exc


def _require(learner_id: str) -> None:
    if learner_profile.get_learner(learner_id) is None:
        raise HTTPException(404, f"Lernende nicht gefunden: {learner_id}")
