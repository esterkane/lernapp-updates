"""Roleplay API (docs/api.md "Roleplay"; ADR-0013, product rule 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.core.workspaces import current_workspace, resolve_learner
from app.services import roleplay

router = APIRouter(tags=["roleplay"])


class StartRoleplay(BaseModel):
    scenario_id: str
    learner_id: str = Field(default_factory=current_workspace)
    voice: bool = False


def _learner(learner_id: str | None) -> str:
    return resolve_learner(learner_id)


@router.get("/roleplay/scenarios")
def list_scenarios(category: str | None = None, difficulty: int | None = None) -> list[dict[str, Any]]:
    return roleplay.list_scenarios(category=category, difficulty=difficulty)


@router.post("/roleplay/start")
async def start(body: StartRoleplay) -> dict[str, Any]:
    try:
        return await run_in_threadpool(roleplay.start, body.scenario_id, _learner(body.learner_id), body.voice)
    except roleplay.ScenarioNotFound as exc:
        raise HTTPException(404, f"Szenario nicht gefunden: {exc}") from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _read_turn_input(request: Request) -> tuple[str | None, bytes | None]:
    """JSON ``{text}`` or multipart ``file`` (wav/webm/ogg from ``st.audio_input``)."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if isinstance(upload, UploadFile):
            data = await upload.read()
            if not data:
                raise HTTPException(400, "leere Audiodatei")
            return None, data
        text = form.get("text")
        if isinstance(text, str) and text.strip():
            return text, None
        raise HTTPException(400, "multipart braucht ein Feld 'file' (Audio) oder 'text'")
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "JSON-Body {text} oder multipart 'file' erwartet") from exc
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "Feld 'text' fehlt oder ist leer")
    return text, None


@router.post("/roleplay/{session_id}/turn")
async def turn(session_id: str, request: Request) -> dict[str, Any]:
    text, audio_bytes = await _read_turn_input(request)
    try:
        return await run_in_threadpool(roleplay.turn, session_id, text, audio_bytes)
    except roleplay.SessionNotFound as exc:
        raise HTTPException(404, f"Rollenspiel nicht gefunden: {exc}") from exc
    except roleplay.SessionEnded as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/roleplay/{session_id}/end")
async def end(session_id: str) -> dict[str, Any]:
    try:
        return await run_in_threadpool(roleplay.end, session_id)
    except KeyError as exc:
        raise HTTPException(404, f"Rollenspiel nicht gefunden: {exc}") from exc


@router.get("/roleplay/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        return roleplay.get(session_id)
    except KeyError as exc:
        raise HTTPException(404, f"Rollenspiel nicht gefunden: {exc}") from exc
