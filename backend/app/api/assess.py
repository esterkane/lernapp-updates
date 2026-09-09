"""Assessment API (docs/api.md "Assessment"): rubric feedback for Schreiben / Sprechen."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.core.workspaces import current_workspace, resolve_learner
from app.services import assessment as svc
from app.services.llm import LLMError
from app.services.schemas import ProsodyReport


async def second_opinion_context(request: Request) -> AsyncIterator[None]:
    from app.core.config import get_settings
    from app.core.models import requested_validator_provider, validator_candidates

    try:
        body = (
            await request.form()
            if request.headers.get("content-type", "").startswith("multipart/")
            else await request.json()
        )
    except ValueError as exc:
        raise HTTPException(400, "Ungültige Anfrage.") from exc
    choice = body.get("second_opinion") if hasattr(body, "get") else None
    token = requested_validator_provider.set("gemini" if choice == "gemini" else None)
    try:
        if choice == "gemini":
            if get_settings().eu_strict_mode:
                raise HTTPException(400, "Gemini-Gegenprüfung ist im EU-Modus nicht verfügbar.")
            if not validator_candidates():
                raise HTTPException(
                    400,
                    "Bitte zuerst Gemini unter Einstellungen → AI-Anbieter verbinden. Es wurde keine Bewertung gestartet.",
                )
        yield
    finally:
        requested_validator_provider.reset(token)


router = APIRouter(tags=["assess"], dependencies=[Depends(second_opinion_context)])


class WritingRequest(BaseModel):
    second_opinion: Literal["none", "gemini"] | None = None
    task_id: str | None = None
    task_text: str | None = None
    expected_content: list[str] | None = None
    learner_text: str
    learner_id: str = Field(default_factory=current_workspace)
    is_progress_point: bool = False
    session_id: str | None = None


class SpeakingRequest(BaseModel):
    second_opinion: Literal["none", "gemini"] | None = None
    task_id: str | None = None
    task_text: str | None = None
    transcript: str
    prosody: ProsodyReport | None = None
    learner_id: str = Field(default_factory=current_workspace)
    is_progress_point: bool = False
    session_id: str | None = None


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, svc.TaskNotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(502, str(exc))


@router.post("/assess/writing")
def writing(req: WritingRequest) -> dict[str, Any]:
    try:
        return svc.assess_writing(
            req.learner_text,
            task_id=req.task_id,
            task_text=req.task_text,
            expected_content=req.expected_content,
            learner_id=req.learner_id,
            is_progress_point=(req.second_opinion == "gemini")
            if req.second_opinion is not None
            else req.is_progress_point,
            session_id=req.session_id,
        )
    except (svc.TaskNotFound, ValueError, LLMError) as exc:
        raise _http(exc) from exc


@router.post("/assess/speaking")
def speaking(req: SpeakingRequest) -> dict[str, Any]:
    try:
        return svc.assess_speaking(
            req.transcript,
            task_id=req.task_id,
            task_text=req.task_text,
            prosody=req.prosody,
            learner_id=req.learner_id,
            is_progress_point=(req.second_opinion == "gemini")
            if req.second_opinion is not None
            else req.is_progress_point,
            session_id=req.session_id,
        )
    except (svc.TaskNotFound, ValueError, LLMError) as exc:
        raise _http(exc) from exc


@router.post("/assess/speaking/audio")
async def speaking_audio(
    file: UploadFile = File(...),
    task_id: str | None = Form(None),
    task_text: str | None = Form(None),
    learner_id: str | None = Form(None),
    is_progress_point: bool = Form(False),
    second_opinion: Literal["none", "gemini"] | None = Form(None),
    session_id: str | None = Form(None),
) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    data = await file.read()
    try:
        return svc.assess_speaking_audio(
            data,
            task_id=task_id or None,
            task_text=task_text or None,
            learner_id=learner_id,
            is_progress_point=(second_opinion == "gemini") if second_opinion is not None else is_progress_point,
            session_id=session_id or None,
        )
    except (svc.TaskNotFound, ValueError, LLMError) as exc:
        raise _http(exc) from exc
