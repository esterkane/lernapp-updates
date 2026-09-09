"""HTTP client for the spot-check queue (docs/api.md "Learners / progress / privacy", ADR-0017).

Lives next to :mod:`lernapp_ui.api` and reuses its request helpers, so the UI still talks to the
backend over HTTP only (ADR-0009). Fold these two functions into ``api.py`` when convenient.
"""

from __future__ import annotations

from typing import Any

from lernapp_ui import api


def learner_spotchecks(learner_id: str | None = None) -> list[dict[str, Any]]:
    """Pending ``spot_check`` + ``needs_review`` results with their ReviewHandoff, oldest first."""
    learner_id = learner_id or api._current_learner()
    items: list[dict[str, Any]] = api._get(f"/learners/{learner_id}/spotchecks") or []
    return items


def label_spotcheck(
    result_id: str, scores: dict[str, int], note: str | None = None, learner_id: str | None = None
) -> dict[str, Any]:
    learner_id = learner_id or api._current_learner()
    out: dict[str, Any] = api._post(
        f"/learners/{learner_id}/spotchecks/{result_id}/label", {"scores": scores, "note": note or ""}
    )
    return out
