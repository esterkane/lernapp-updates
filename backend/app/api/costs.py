from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, SecretStr

from app.core import ledger
from app.core.pricing import load_pricing
from app.core.workspaces import resolve_learner

router = APIRouter(tags=["costs"])


def _dt(v: str | None, *, end: bool = False) -> datetime | None:
    """Parse ISO date/datetime. A date-only ``to`` value is inclusive (end of that day)."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError as exc:
        raise HTTPException(400, f"bad date {v}") from exc
    if end and len(v) == 10:
        from datetime import timedelta

        d = d + timedelta(days=1)
    if d.tzinfo is None:
        from datetime import UTC

        d = d.replace(tzinfo=UTC)
    return d


@router.get("/costs/summary")
def summary(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    group_by: str = "stage",
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    try:
        return ledger.summary(_dt(from_), _dt(to, end=True), group_by=group_by, learner_id=learner_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/costs/counterfactual")
def counterfactual(
    variant: str = "all_cloud_openai",
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    try:
        return ledger.counterfactual(variant, _dt(from_), _dt(to, end=True), learner_id=learner_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/costs/session/{session_id}")
def session_rows(session_id: str) -> dict[str, Any]:
    rows = ledger.rows_for_session(session_id)
    return {"session_id": session_id, "rows": rows, "total_eur": sum(r["cost_eur"] or 0 for r in rows)}


@router.get("/costs/pricing")
def pricing() -> dict[str, Any]:
    t = load_pricing()
    return t.model_dump(mode="json")


class BillingCheck(BaseModel):
    api_key: SecretStr | None = None
    start: date
    end: date
    project_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    organization_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    api_key_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class BillingCredential(BaseModel):
    api_key: SecretStr


@router.get("/costs/openai/credential")
def billing_credential_status() -> dict[str, bool]:
    from app.services import billing_credentials

    return {"saved": billing_credentials.saved(resolve_learner())}


@router.put("/costs/openai/credential")
def save_billing_credential(body: BillingCredential) -> dict[str, bool]:
    from app.core.credentials import CredentialError
    from app.services import billing_credentials

    try:
        billing_credentials.save(resolve_learner(), body.api_key.get_secret_value())
    except CredentialError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"saved": True}


@router.delete("/costs/openai/credential")
def remove_billing_credential() -> dict[str, bool]:
    from app.services import billing_credentials

    billing_credentials.remove(resolve_learner())
    return {"saved": False}


@router.post("/costs/openai/check")
def check_openai_billing(body: BillingCheck) -> dict[str, Any]:
    from app.core.credentials import CredentialError
    from app.services import billing_credentials, openai_billing

    try:
        key = body.api_key.get_secret_value().strip() if body.api_key else None
        key = key or billing_credentials.get(resolve_learner())
        if not key:
            raise HTTPException(400, "Bitte einen OpenAI-Admin-API-Schlüssel eingeben oder speichern.")
        return openai_billing.fetch(key, body.start, body.end, body.project_id, body.organization_id, body.api_key_id)
    except (openai_billing.BillingError, CredentialError) as exc:
        raise HTTPException(400, str(exc)) from exc
