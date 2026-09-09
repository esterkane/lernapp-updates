"""Progress view over stored results (docs/api.md ``GET /learners/{id}/progress``).

Every point carries its rubric/prompt/model versions (ADR-0004 §5); ``version_breaks`` marks
where the (rubric_version, prompt_version) pair changed for a skill so the UI can draw a
vertical line instead of comparing scores across incompatible versions.
"""

from __future__ import annotations

from typing import Any

from app.services import learner_profile
from app.services.results import pending_spot_checks, recent_results

POINT_FIELDS = (
    "result_id",
    "created_at",
    "skill",
    "task_type",
    "score",
    "score_max",
    "internal_score_20",
    "rubric_version",
    "prompt_version",
    "model_version",
    "needs_review",
    "routing",
    "is_progress_point",
)


def points_for(learner_id: str, skill: str | None = None) -> list[dict[str, Any]]:
    return [{k: r.get(k) for k in POINT_FIELDS} for r in recent_results(learner_id, skill=skill)]


def version_breaks(points: list[dict[str, Any]]) -> list[str]:
    """``created_at`` of points whose (rubric_version, prompt_version) differs from the previous
    point of the same skill (points must be in chronological order)."""
    last: dict[str, tuple[str | None, str | None]] = {}
    breaks: list[str] = []
    for p in points:
        key = (p.get("rubric_version"), p.get("prompt_version"))
        skill = str(p.get("skill"))
        if skill in last and last[skill] != key:
            breaks.append(str(p["created_at"]))
        last[skill] = key
    return breaks


def progress(learner_id: str, skill: str | None = None) -> dict[str, Any]:
    points = points_for(learner_id, skill)
    return {
        "points": points,
        "weekly_focus": learner_profile.weekly_focus(learner_id),
        "top_error_tags": learner_profile.top_error_tags(learner_id),
        "activity_status": learner_profile.activity_status(learner_id),
        "version_breaks": version_breaks(points),
        "pending_spotchecks": len(pending_spot_checks(learner_id)),
    }
