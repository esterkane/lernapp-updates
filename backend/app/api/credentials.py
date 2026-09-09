"""BYOK provider credentials (cost-tracking model, ADR-0019; contract: docs/api.md "Cost tracking v2").

Responses carry the masked form only (12 dots + last 3 characters). The plaintext key is validated,
encrypted and forgotten; it appears in no response, no log and no validation error (see api/errors.py).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import credentials
from app.core.credentials import PROVIDERS, CredentialError, CredentialView
from app.core.workspaces import resolve_learner
from app.services import provider_test

router = APIRouter(tags=["provider-credentials"])


class CredentialUpdate(BaseModel):
    learner_id: str
    api_key: str | None = None
    pricing_tier: Literal["free", "paid", "unknown"] | None = None


class CredentialTest(BaseModel):
    learner_id: str


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(400, f"Unbekannter Anbieter: {provider}. Möglich sind: {', '.join(PROVIDERS)}.")
    return provider


def _require_learner(learner_id: str) -> str:
    from app.core.db import db_session
    from app.db.base import Learner

    lid = learner_id.strip()
    if not lid:
        raise HTTPException(400, "learner_id fehlt.")
    with db_session() as db:
        if db.get(Learner, lid) is None:
            raise HTTPException(404, f"Lernende nicht gefunden: {lid}")
    return lid


@router.get("/provider-credentials")
def list_provider_credentials(learner_id: str) -> list[CredentialView]:
    learner_id = resolve_learner(learner_id)
    return credentials.list_credentials(_require_learner(learner_id))


@router.put("/provider-credentials/{provider}")
def put_provider_credential(provider: str, body: CredentialUpdate) -> CredentialView:
    provider = _provider(provider)
    learner_id = _require_learner(body.learner_id)
    if body.api_key is None and body.pricing_tier is None:
        raise HTTPException(400, "Bitte einen API-Schlüssel oder einen Tarif (free/paid/unknown) angeben.")
    try:
        if body.api_key is not None:
            return credentials.set_credential(learner_id, provider, body.api_key, pricing_tier=body.pricing_tier)
        assert body.pricing_tier is not None  # noqa: S101 — checked above
        return credentials.set_pricing_tier(learner_id, provider, body.pricing_tier)
    except CredentialError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/provider-credentials/{provider}")
def delete_provider_credential(provider: str, learner_id: str) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    provider = _provider(provider)
    return {"deleted": credentials.delete_credential(_require_learner(learner_id), provider)}


@router.post("/provider-credentials/{provider}/test")
def test_provider_credential(provider: str, body: CredentialTest) -> dict[str, Any]:
    provider = _provider(provider)
    out = provider_test.run_test(_require_learner(body.learner_id), provider)
    return {"ok": out.ok, "message_de": out.message_de}
