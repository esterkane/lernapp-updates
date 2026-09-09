"""Usage / cost API (docs/api.md "Cost tracking v2", ADR-0019).

Every endpoint is learner-scoped: ``learner_id`` (default: the install's default learner) selects
whose events are returned; another learner's events are never visible. Money fields are EUR floats;
``expected_cost_eur`` is the primary figure, ``list_cost_eur`` the provider list price for reference.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.workspaces import current_workspace, resolve_learner
from app.services import usage_report

router = APIRouter(tags=["usage"])


def _learner(learner_id: str | None) -> str:
    return resolve_learner(learner_id)


def _dt(v: str | None, *, end: bool = False) -> datetime | None:
    """ISO date/datetime → aware UTC datetime; a date-only ``to`` is inclusive (end of that day)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError as exc:
        raise HTTPException(400, f"Ungültiges Datum: {v}") from exc
    if end and len(v) == 10:
        d = d + timedelta(days=1)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class UsageSettingsUpdate(BaseModel):
    learner_id: str = Field(default_factory=current_workspace)
    monthly_budget_eur: float | None = Field(default=None, ge=0)
    stop_on_limit: bool = False


@router.get("/usage/summary")
def summary(learner_id: str | None = None, month: str | None = None) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    try:
        return usage_report.summary(_learner(learner_id), month)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/usage/events")
def events(
    learner_id: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    session_id: str | None = None,
    provider: str | None = None,
    service: str | None = None,
    cost_status: str | None = None,
    outcome: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    return usage_report.list_events(
        _learner(learner_id),
        from_=_dt(from_),
        to=_dt(to, end=True),
        session_id=session_id,
        provider=provider,
        service=service,
        cost_status=cost_status,
        outcome=outcome,
        limit=limit,
        offset=offset,
    )


@router.get("/usage/session/{session_id}")
def session(session_id: str, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    out = usage_report.session_report(session_id, _learner(learner_id))
    if out is None:
        raise HTTPException(404, f"Session nicht gefunden: {session_id}")
    return out


@router.get("/usage/settings")
def get_settings_(learner_id: str | None = None) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    try:
        return usage_report.get_budget_settings(_learner(learner_id)).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(404, f"Lernende nicht gefunden: {exc}") from exc


@router.put("/usage/settings")
def put_settings(body: UsageSettingsUpdate) -> dict[str, Any]:
    try:
        out = usage_report.set_budget_settings(_learner(body.learner_id), body.monthly_budget_eur, body.stop_on_limit)
    except KeyError as exc:
        raise HTTPException(404, f"Lernende nicht gefunden: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return out.model_dump(mode="json")
