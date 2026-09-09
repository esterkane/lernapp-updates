"""Cheapest practical connection test per provider (cost-tracking model, ADR-0019).

Every provider offers an unbilled *models listing* endpoint, so a test costs nothing — it is still a real
provider request and therefore recorded as a usage event (service ``other``, operation ``connection_test``,
``meta.free_call``). Only when a listing is unavailable (404/405) does OpenAI fall back to a 1-token
completion with the cheapest configured model (billed, priced like any other LLM call).

Keys never leave the backend: they go into request headers only (no query strings, which ``httpx`` logs),
developer logs carry HTTP status, category and the provider request id, and user messages are German.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core import credentials, usage
from app.core.credentials import PROVIDER_LABEL_DE, PROVIDERS

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
ANTHROPIC_VERSION = "2023-06-01"

MSG_AUTH = "Der API-Schlüssel wurde nicht akzeptiert. Bitte prüfe, ob du ihn vollständig kopiert hast."
MSG_RATE = "Der Anbieter meldet zu viele Anfragen – bitte in einer Minute noch einmal versuchen."
MSG_NETWORK = "Keine Verbindung zum Anbieter. Prüfe deine Internetverbindung."
MSG_NO_KEY = "Für {label} ist kein Schlüssel hinterlegt (Einstellungen → AI-Anbieter)."


def _msg_provider_error(status: int) -> str:
    return f"Der Anbieter hat mit einem Fehler geantwortet (Code {status})."


MODELS_URL: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}
OPENAI_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

# Tests may install an ``httpx.MockTransport`` here; production uses httpx's default transport.
_transport: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class TestOutcome:
    ok: bool
    message_de: str
    category: str  # ok | no_key | auth | rate_limit | network | provider_error
    http_status: int | None = None
    request_id: str | None = None
    model: str | None = None


def _auth_headers(provider: str, key: str) -> dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    if provider == "gemini":
        return {"x-goog-api-key": key}  # header, never ``?key=`` (would appear in httpx's request log)
    return {"Authorization": f"Bearer {key}"}


def cheapest_model_for(provider: str) -> str | None:
    """Cheapest configured/priced model of a vendor — never hard-coded ids (ADR-0003)."""
    from app.core.models import load_models_config
    from app.core.pricing import load_pricing

    cfg = load_models_config()
    prefix = provider + "/"
    priced = [(e.input or 0.0, k) for k, e in load_pricing().llm.items() if k.startswith(prefix)]
    configured = [tc.model for tc in cfg.tiers.values() if tc.model.startswith(prefix)]
    configured += [m for m in cfg.eu_strict_overrides.values() if m.startswith(prefix)]
    if priced:
        cheapest_priced = min(priced)[1]
        if cheapest_priced in configured or not configured:
            return str(cheapest_priced)
    return str(configured[0]) if configured else None


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS, transport=_transport)


def _request_id(resp: httpx.Response) -> str | None:
    for name in ("x-request-id", "request-id", "cf-ray"):
        v = resp.headers.get(name)
        if v:
            return str(v)[:120]
    return None


def _classify(resp: httpx.Response) -> tuple[str, str]:
    st = resp.status_code
    if st in (401, 403):
        return "auth", MSG_AUTH
    if st == 429:
        return "rate_limit", MSG_RATE
    if 200 <= st < 300:
        return "ok", ""
    return "provider_error", _msg_provider_error(st)


def _list_models(provider: str, key: str) -> TestOutcome:
    label = PROVIDER_LABEL_DE.get(provider, provider)
    try:
        with _client() as c:
            resp = c.get(MODELS_URL[provider], headers=_auth_headers(provider, key))
    except httpx.HTTPError as exc:  # connect/timeout/protocol — never contains the key
        log.warning("connection test %s: network error (%s)", provider, type(exc).__name__)
        return TestOutcome(ok=False, message_de=MSG_NETWORK, category="network")
    category, message = _classify(resp)
    return TestOutcome(
        ok=category == "ok",
        message_de=message or f"{label} verbunden",
        category=category,
        http_status=resp.status_code,
        request_id=_request_id(resp),
        model=f"{provider}/models",
    )


def _openai_min_completion(key: str) -> tuple[TestOutcome, dict[str, Any]]:
    """Fallback when the listing endpoint is unavailable: one billed token with the cheapest configured model."""
    model = cheapest_model_for("openai")
    if not model:
        return TestOutcome(
            ok=False, message_de=_msg_provider_error(404), category="provider_error", http_status=404
        ), {}
    body = {"model": model.split("/", 1)[1], "messages": [{"role": "user", "content": "OK"}], "max_tokens": 1}
    try:
        with _client() as c:
            resp = c.post(OPENAI_COMPLETIONS_URL, headers=_auth_headers("openai", key), json=body)
    except httpx.HTTPError as exc:
        log.warning("connection test openai (completion): network error (%s)", type(exc).__name__)
        return TestOutcome(ok=False, message_de=MSG_NETWORK, category="network", model=model), {}
    category, message = _classify(resp)
    fields: dict[str, Any] = {}
    if category == "ok":
        try:
            u = resp.json().get("usage") or {}
            fields = {
                "input_tokens": int(u.get("prompt_tokens") or 0),
                "output_tokens": int(u.get("completion_tokens") or 0),
            }
        except (ValueError, AttributeError, TypeError):
            fields = {}
    out = TestOutcome(
        ok=category == "ok",
        message_de=message or f"{PROVIDER_LABEL_DE['openai']} verbunden",
        category=category,
        http_status=resp.status_code,
        request_id=_request_id(resp),
        model=model,
    )
    return out, fields


def _record(learner_id: str, provider: str, out: TestOutcome, *, free_call: bool, fields: dict[str, Any]) -> None:
    meta: dict[str, Any] = {"connection_test": True, "category": out.category}
    if free_call:
        meta["free_call"] = True
    if out.http_status is not None:
        meta["http_status"] = out.http_status
    if out.request_id:
        meta["provider_request_id"] = out.request_id  # informational; a test is never retried, so no dedupe key
    if not out.ok:
        meta["error"] = out.message_de
    try:
        usage.record_usage(
            usage.UsageDraft(
                provider=provider,
                stage="llm",
                model=out.model or f"{provider}/models",
                learner_id=learner_id,
                session_id=None,
                service="other",
                operation="connection_test",
                outcome="ok" if out.ok else "error",
                meta=meta,
                **fields,
            )
        )
    except Exception:  # noqa: BLE001 — a bookkeeping failure must not hide the test result
        log.exception("could not record connection test usage for %s", provider)


def run_test(learner_id: str, provider: str) -> TestOutcome:
    """Validate the learner's credential for ``provider`` with the cheapest request; records outcome + usage."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unbekannter Anbieter: {provider}")
    label = PROVIDER_LABEL_DE.get(provider, provider)
    key = credentials.api_key_for(learner_id, provider)
    if not key:
        out = TestOutcome(ok=False, message_de=MSG_NO_KEY.format(label=label), category="no_key")
        credentials.record_test(learner_id, provider, out.ok, out.message_de)
        log.info("connection test provider=%s category=%s (no request sent)", provider, out.category)
        return out
    out = _list_models(provider, key)
    free_call = True
    fields: dict[str, Any] = {}
    if provider == "openai" and out.http_status in (404, 405):
        out, fields = _openai_min_completion(key)
        free_call = False
    log.info(
        "connection test provider=%s status=%s category=%s request_id=%s",
        provider,
        out.http_status,
        out.category,
        out.request_id,
    )
    _record(learner_id, provider, out, free_call=free_call, fields=fields)  # attempted request → one event
    credentials.record_test(learner_id, provider, out.ok, out.message_de)
    return out
