"""Thin HTTP client for the Lernapp backend (docs/api.md).

The Streamlit UI talks to FastAPI over HTTP only (ADR-0009) — nothing here imports ``backend.app``.
Every function raises :class:`ApiError` with a German, learner-friendly message on failure.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL: str = os.environ.get("LERNAPP_API_URL", "http://127.0.0.1:8000").rstrip("/")
LEARNER_ID: str = os.environ.get("DEFAULT_LEARNER_ID", "default")
API_TOKEN: str = os.environ.get("LERNAPP_API_TOKEN", "")  # optional bearer (backend app/api/auth.py)
CALLER: str = "ui"  # X-Lernapp-Caller: read by the backend audit trail (ui | mcp)

TIMEOUT_DEFAULT = 30.0
TIMEOUT_PROVIDER = 180.0  # calls that hit an LLM / STT / TTS provider
TIMEOUT_HEALTH = 3.0

HINT_KEY = "Prüfe in den Einstellungen, ob die Schlüssel eingetragen sind (OpenAI; für die Zweitmeinung Gemini oder Anthropic)."
HINT_CONNECTION = "Prüfe, ob die App im Hintergrund läuft, und lade die Seite neu."


class ApiError(Exception):
    """Error with a German message for the UI (``message_de``) and the HTTP status if known."""

    def __init__(self, message_de: str, status: int | None = None, detail: str = "") -> None:
        super().__init__(message_de)
        self.message_de = message_de
        self.status = status
        self.detail = detail

    def __str__(self) -> str:
        return self.message_de


def _message_for_status(status: int, detail: str) -> str:
    if status in (401, 403):
        if "API-Token" in detail:  # our own bearer check (app/api/auth.py), not a provider key
            return "Der Zugang wurde abgelehnt: Das API-Token der App fehlt oder ist falsch (LERNAPP_API_TOKEN)."
        return f"Der Zugang wurde abgelehnt. {HINT_KEY}"
    if status == 404:
        return "Das wurde nicht gefunden. Vielleicht wurde es inzwischen gelöscht."
    if status == 422 or status == 400:
        return "Die Eingabe konnte nicht verarbeitet werden. " + (detail or "Bitte prüfe deine Angaben.")
    if status == 429:
        return "Das Sprachmodell ist gerade überlastet. Bitte in einer Minute noch einmal versuchen."
    if status >= 500:
        return "In der App ist ein Fehler aufgetreten. " + (detail or "Bitte noch einmal versuchen.")
    return detail or f"Unerwartete Antwort (Status {status})."


def _headers() -> dict[str, str]:
    """Caller id on every request; bearer only when ``LERNAPP_API_TOKEN`` is set."""
    headers = {"X-Lernapp-Caller": CALLER, "X-Lernapp-Workspace": _current_learner()}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    return headers


def _extract_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)[:300]
    return str(payload)[:300]


def _request(
    method: str,
    path: str,
    *,
    json: Any | None = None,
    params: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = TIMEOUT_DEFAULT,
    raw: bool = False,
) -> Any:
    url = f"{BASE_URL}{path}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(
                method, url, json=json, params=clean_params or None, files=files, data=data, headers=_headers()
            )
    except httpx.ConnectError as exc:
        raise ApiError(f"Keine Verbindung zur App. {HINT_CONNECTION}", None, str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise ApiError(
            "Das hat zu lange gedauert. Das Sprachmodell antwortet gerade nicht. Bitte noch einmal versuchen.",
            None,
            str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise ApiError(f"Die Verbindung zur App ist fehlgeschlagen. {HINT_CONNECTION}", None, str(exc)) from exc

    if resp.status_code >= 400:
        detail = _extract_detail(resp)
        raise ApiError(_message_for_status(resp.status_code, detail), resp.status_code, detail)
    if raw:
        return resp.content
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError("Die Antwort der App konnte nicht gelesen werden.", resp.status_code, resp.text[:300]) from exc


def _get(path: str, **params: Any) -> Any:
    return _request("GET", path, params=params)


def _post(path: str, body: dict[str, Any] | None = None, *, timeout: float = TIMEOUT_DEFAULT) -> Any:
    return _request("POST", path, json=body or {}, timeout=timeout)


def _put(path: str, body: dict[str, Any]) -> Any:
    return _request("PUT", path, json=body)


def _patch(path: str, body: dict[str, Any]) -> Any:
    return _request("PATCH", path, json=body)


def _delete(path: str, **params: Any) -> Any:
    return _request("DELETE", path, params=params)


def _upload(
    path: str,
    file_bytes: bytes,
    filename: str,
    mime: str,
    data: dict[str, Any] | None = None,
    *,
    timeout: float = TIMEOUT_PROVIDER,
) -> Any:
    """Multipart POST with a single ``file`` field (documents, audio turns)."""
    clean = {k: str(v) for k, v in (data or {}).items() if v is not None}
    return _request(
        "POST",
        path,
        files={"file": (filename, file_bytes, mime or "application/octet-stream")},
        data=clean,
        timeout=timeout,
    )


# ---------------------------------------------------------------- health / settings


def health() -> dict[str, Any]:
    return _request("GET", "/health", timeout=TIMEOUT_HEALTH)


def backend_ready() -> bool:
    try:
        return health().get("status") == "ok"
    except ApiError:
        return False


def get_settings() -> dict[str, Any]:
    return _get("/settings")


def put_settings(values: dict[str, str | None]) -> dict[str, Any]:
    return _put("/settings", {"values": values})


def test_provider(provider: str) -> dict[str, Any]:
    return _post("/settings/test", {"provider": provider}, timeout=60.0)


# ---------------------------------------------------------------- blueprints


def list_blueprints() -> list[dict[str, Any]]:
    return _get("/blueprints") or []


def get_blueprint(blueprint_id: str) -> dict[str, Any]:
    return _get(f"/blueprints/{blueprint_id}")


# ---------------------------------------------------------------- costs


def costs_summary(
    from_: str | None = None,
    to: str | None = None,
    group_by: str = "stage",
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    params: dict[str, Any] = {"from": from_, "to": to, "group_by": group_by, "learner_id": learner_id}
    return _get("/costs/summary", **params)


def costs_counterfactual(variant: str, from_: str | None = None, to: str | None = None) -> dict[str, Any]:
    return _get("/costs/counterfactual", variant=variant, **{"from": from_, "to": to})


def costs_session(session_id: str) -> dict[str, Any]:
    return _get(f"/costs/session/{session_id}")


def costs_pricing() -> dict[str, Any]:
    return _get("/costs/pricing")


# ---------------------------------------------------------------- usage / budget / BYOK (docs/api.md "Cost tracking v2")


def usage_summary(learner_id: str | None = None, month: str | None = None) -> dict[str, Any]:
    """Monthly usage summary (expected cost, learning time, budget, providers, models). ``month`` = ``YYYY-MM``."""
    learner_id = learner_id or _current_learner()
    return _get("/usage/summary", learner_id=learner_id, month=month)


def usage_events(
    *,
    learner_id: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    session_id: str | None = None,
    provider: str | None = None,
    service: str | None = None,
    cost_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated usage events → ``{items: [UsageEvent], total}``."""
    learner_id = learner_id or _current_learner()
    params: dict[str, Any] = {
        "learner_id": learner_id,
        "from": from_,
        "to": to,
        "session_id": session_id,
        "provider": provider,
        "service": service,
        "cost_status": cost_status,
        "limit": limit,
        "offset": offset,
    }
    return _get("/usage/events", **params) or {"items": [], "total": 0}


def usage_session(session_id: str, learner_id: str | None = None) -> dict[str, Any]:
    """Expected cost of one session, broken down by provider and service."""
    learner_id = learner_id or _current_learner()
    return _get(f"/usage/session/{session_id}", learner_id=learner_id)


def get_usage_settings(learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _get("/usage/settings", learner_id=learner_id)


def put_usage_settings(
    monthly_budget_eur: float | None, stop_on_limit: bool, learner_id: str | None = None
) -> dict[str, Any]:
    """``monthly_budget_eur=None`` means no limit."""
    learner_id = learner_id or _current_learner()
    return _put(
        "/usage/settings",
        {"learner_id": learner_id, "monthly_budget_eur": monthly_budget_eur, "stop_on_limit": bool(stop_on_limit)},
    )


def provider_credentials(learner_id: str | None = None) -> list[dict[str, Any]]:
    """Connection status per provider (masked key only — the key itself is never returned)."""
    learner_id = learner_id or _current_learner()
    return _get("/provider-credentials", learner_id=learner_id) or []


def put_provider_credential(
    provider: str,
    api_key: str | None = None,
    pricing_tier: str | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    """Store a key and/or change the pricing tier (``free|paid|unknown``). The key is sent once and never kept."""
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {"learner_id": learner_id}
    if api_key:
        body["api_key"] = api_key
    if pricing_tier:
        body["pricing_tier"] = pricing_tier
    return _put(f"/provider-credentials/{provider}", body)


def delete_provider_credential(provider: str, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _delete(f"/provider-credentials/{provider}", learner_id=learner_id)


def test_provider_credential(provider: str, learner_id: str | None = None) -> dict[str, Any]:
    """Cheapest possible provider request → ``{ok, message_de}``."""
    learner_id = learner_id or _current_learner()
    return _post(f"/provider-credentials/{provider}/test", {"learner_id": learner_id}, timeout=60.0)


# ---------------------------------------------------------------- sessions / tutor


def create_session(kind: str = "tutor", task_id: str | None = None, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {"learner_id": learner_id, "kind": kind}
    if task_id:
        body["task_id"] = task_id
    return _post("/sessions", body)


def session_turn_text(session_id: str, text: str, defer_audio: bool = False) -> dict[str, Any]:
    return _post(
        f"/sessions/{session_id}/turn?defer_audio={str(defer_audio).lower()}", {"text": text}, timeout=TIMEOUT_PROVIDER
    )


def session_turn_audio(
    session_id: str, audio: bytes, filename: str = "aufnahme.wav", mime: str = "audio/wav"
) -> dict[str, Any]:
    return _upload(f"/sessions/{session_id}/turn", audio, filename, mime)


def end_session(session_id: str) -> dict[str, Any]:
    return _post(f"/sessions/{session_id}/end")


def get_session(session_id: str) -> dict[str, Any]:
    return _get(f"/sessions/{session_id}")


def list_sessions(kind: str | None = None, limit: int = 20, learner_id: str | None = None) -> list[dict[str, Any]]:
    learner_id = learner_id or _current_learner()
    return _get("/sessions", learner_id=learner_id, kind=kind, limit=limit) or []


# ---------------------------------------------------------------- tasks


def generate_task(
    blueprint_id: str,
    task_type: str,
    level: str = "C1",
    topic: str | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {
        "blueprint_id": blueprint_id,
        "task_type": task_type,
        "level": level,
        "learner_id": learner_id,
    }
    if topic:
        body["topic"] = topic
    return _post("/tasks/generate", body, timeout=TIMEOUT_PROVIDER)


def get_task(task_id: str) -> dict[str, Any]:
    return _get(f"/tasks/{task_id}")


def list_tasks(skill: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    return _get("/tasks", skill=skill, limit=limit) or []


def score_task(
    task_id: str, answers: dict[str, str], session_id: str | None = None, learner_id: str | None = None
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {"learner_id": learner_id, "answers": answers}
    if session_id:
        body["session_id"] = session_id
    return _post(f"/tasks/{task_id}/score", body, timeout=TIMEOUT_PROVIDER)


# ---------------------------------------------------------------- assessment


def assess_writing(
    learner_text: str,
    *,
    task_id: str | None = None,
    task_text: str | None = None,
    expected_content: list[str] | None = None,
    is_progress_point: bool = False,
    session_id: str | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {
        "learner_text": learner_text,
        "learner_id": learner_id,
        "is_progress_point": is_progress_point,
        "second_opinion": "gemini" if is_progress_point else "none",
    }
    if task_id:
        body["task_id"] = task_id
    if task_text:
        body["task_text"] = task_text
    if expected_content:
        body["expected_content"] = expected_content
    if session_id:
        body["session_id"] = session_id
    return _post("/assess/writing", body, timeout=TIMEOUT_PROVIDER)


def assess_speaking(
    transcript: str,
    *,
    task_id: str | None = None,
    task_text: str | None = None,
    prosody: dict[str, Any] | None = None,
    is_progress_point: bool = False,
    session_id: str | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {
        "transcript": transcript,
        "learner_id": learner_id,
        "is_progress_point": is_progress_point,
        "second_opinion": "gemini" if is_progress_point else "none",
    }
    if task_id:
        body["task_id"] = task_id
    if task_text:
        body["task_text"] = task_text
    if prosody:
        body["prosody"] = prosody
    if session_id:
        body["session_id"] = session_id
    return _post("/assess/speaking", body, timeout=TIMEOUT_PROVIDER)


def assess_speaking_audio(
    audio: bytes,
    *,
    filename: str = "aufnahme.wav",
    mime: str = "audio/wav",
    task_id: str | None = None,
    task_text: str | None = None,
    is_progress_point: bool = False,
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    data = {
        "learner_id": learner_id,
        "is_progress_point": "true" if is_progress_point else "false",
        "second_opinion": "gemini" if is_progress_point else "none",
        "task_id": task_id,
        "task_text": task_text,
    }
    return _upload("/assess/speaking/audio", audio, filename, mime, data)


# ---------------------------------------------------------------- roleplay


def roleplay_scenarios(category: str | None = None, difficulty: int | None = None) -> list[dict[str, Any]]:
    return _get("/roleplay/scenarios", category=category, difficulty=difficulty) or []


def roleplay_start(scenario_id: str, voice: bool = False, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _post(
        "/roleplay/start",
        {"scenario_id": scenario_id, "learner_id": learner_id, "voice": voice},
        timeout=TIMEOUT_PROVIDER,
    )


def roleplay_turn_text(session_id: str, text: str) -> dict[str, Any]:
    return _post(f"/roleplay/{session_id}/turn", {"text": text}, timeout=TIMEOUT_PROVIDER)


def roleplay_turn_audio(
    session_id: str, audio: bytes, filename: str = "aufnahme.wav", mime: str = "audio/wav"
) -> dict[str, Any]:
    return _upload(f"/roleplay/{session_id}/turn", audio, filename, mime)


def roleplay_end(session_id: str) -> dict[str, Any]:
    return _post(f"/roleplay/{session_id}/end", timeout=TIMEOUT_PROVIDER)


def roleplay_state(session_id: str) -> dict[str, Any]:
    return _get(f"/roleplay/{session_id}")


# ---------------------------------------------------------------- documents / vocab


def upload_document(
    file_bytes: bytes,
    filename: str,
    mime: str,
    tags: list[str] | None = None,
    use_in: list[str] | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    data = {
        "learner_id": learner_id,
        "tags": ",".join(tags) if tags else None,
        "use_in": ",".join(use_in) if use_in else None,
    }
    return _upload("/documents", file_bytes, filename, mime, data)


def list_documents(learner_id: str | None = None) -> list[dict[str, Any]]:
    learner_id = learner_id or _current_learner()
    return _get("/documents", learner_id=learner_id) or []


def patch_document(
    document_id: str,
    *,
    tags: list[str] | None = None,
    use_in: dict[str, bool] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if tags is not None:
        body["tags"] = tags
    if use_in is not None:
        body["use_in"] = use_in
    if title is not None:
        body["title"] = title
    return _patch(f"/documents/{document_id}", body)


def delete_document(document_id: str) -> dict[str, Any]:
    return _delete(f"/documents/{document_id}")


def search_documents(
    q: str,
    *,
    tags: list[str] | None = None,
    k: int = 8,
    use_in: str | None = None,
    learner_id: str | None = None,
) -> list[dict[str, Any]]:
    learner_id = learner_id or _current_learner()
    return (
        _get(
            "/documents/search",
            q=q,
            learner_id=learner_id,
            tags=",".join(tags) if tags else None,
            k=k,
            use_in=use_in,
        )
        or []
    )


def list_vocab(due_only: bool = True, limit: int = 20, learner_id: str | None = None) -> list[dict[str, Any]]:
    learner_id = learner_id or _current_learner()
    return _get("/vocab", learner_id=learner_id, due_only="true" if due_only else "false", limit=limit) or []


def add_vocab(wort: str, bedeutung: str, beispiel: str | None = None, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    body: dict[str, Any] = {"learner_id": learner_id, "wort": wort, "bedeutung": bedeutung}
    if beispiel:
        body["beispiel"] = beispiel
    return _post("/vocab", body)


def review_vocab(vocab_id: str, quality: int) -> dict[str, Any]:
    return _post(f"/vocab/{vocab_id}/review", {"quality": int(quality)})


def vocab_stats(learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _get("/vocab/stats", learner_id=learner_id) or {"total": 0, "due": 0}


def delete_vocab(vocab_id: str) -> dict[str, Any]:
    return _delete(f"/vocab/{vocab_id}")


# ---------------------------------------------------------------- learners / progress / privacy


def get_learner(learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _get(f"/learners/{learner_id}")


def patch_learner(values: dict[str, Any], learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _patch(f"/learners/{learner_id}", values)


def learner_progress(skill: str | None = None, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _get(f"/learners/{learner_id}/progress", skill=skill)


def learner_result(result_id: str, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _get(f"/learners/{learner_id}/results/{result_id}")


def learner_results(skill: str | None = None, limit: int = 10, learner_id: str | None = None) -> list[dict[str, Any]]:
    learner_id = learner_id or _current_learner()
    return _get(f"/learners/{learner_id}/results", skill=skill, limit=limit) or []


def export_learner(learner_id: str | None = None) -> bytes:
    """Returns the ZIP archive (JSON + CSV) as bytes."""
    learner_id = learner_id or _current_learner()
    return _request("GET", f"/learners/{learner_id}/export", timeout=120.0, raw=True)


def delete_learner(learner_id: str | None = None) -> dict[str, Any]:
    learner_id = learner_id or _current_learner()
    return _delete(f"/learners/{learner_id}")


# ---------------------------------------------------------------- evals


def run_evals(suite: str = "all") -> dict[str, Any]:
    return _post("/evals/run", {"suite": suite}, timeout=600.0)


def _current_learner() -> str:
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    if get_script_run_ctx(suppress_warning=True) is not None:
        import streamlit as st

        return str(st.session_state.get("_workspace_id") or LEARNER_ID)
    return LEARNER_ID


def list_workspaces() -> list[dict[str, Any]]:
    return _get("/workspaces") or []


def create_workspace(name: str, level: str = "B2") -> dict[str, Any]:
    return _post("/workspaces", {"name": name, "level": level})


def local_speech_status() -> dict[str, Any]:
    return _request("GET", "/settings/speech/local")


def list_links() -> list[dict[str, str]]:
    return _request("GET", "/links")


def save_link(values: dict[str, str], link_id: str | None = None) -> Any:
    return _request("PUT" if link_id else "POST", "/links" + ("/" + link_id if link_id else ""), json=values)


def remove_link(link_id: str) -> Any:
    return _request("DELETE", "/links/" + link_id)
