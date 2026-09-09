"""Sessions / tutor API (docs/api.md "Sessions / tutor")."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.core.workspaces import current_workspace, resolve_learner
from app.services import tutor

router = APIRouter(tags=["sessions"])


class CreateSession(BaseModel):
    learner_id: str = Field(default_factory=current_workspace)
    kind: Literal["tutor", "sprechen"] = "tutor"
    task_id: str | None = None


def _learner(learner_id: str | None) -> str:
    return resolve_learner(learner_id)


@router.post("/sessions")
def create_session(body: CreateSession) -> dict[str, Any]:
    try:
        return tutor.create_session(_learner(body.learner_id), body.kind, body.task_id)
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


@router.post("/sessions/{session_id}/turn")
async def turn(session_id: str, request: Request, defer_audio: bool = False) -> dict[str, Any]:
    text, audio_bytes = await _read_turn_input(request)
    try:
        return await run_in_threadpool(tutor.turn, session_id, text, audio_bytes, defer_audio=defer_audio)
    except tutor.SessionNotFound as exc:
        raise HTTPException(404, f"Session nicht gefunden: {exc}") from exc
    except tutor.SessionEnded as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str) -> dict[str, Any]:
    try:
        return tutor.end_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, f"Session nicht gefunden: {exc}") from exc


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        return tutor.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, f"Session nicht gefunden: {exc}") from exc


@router.get("/sessions")
def list_sessions(learner_id: str | None = None, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    learner_id = resolve_learner(learner_id)
    return tutor.list_sessions(learner_id=_learner(learner_id), kind=kind, limit=max(1, min(limit, 200)))


@router.post("/sessions/{session_id}/turns/{ord_}/audio")
def deferred_audio(session_id: str, ord_: int) -> dict[str, Any]:
    try:
        return tutor.deferred_audio(session_id, ord_)
    except tutor.SessionNotFound as exc:
        raise HTTPException(404, "Gespräch nicht gefunden.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
