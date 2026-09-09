from typing import Any, Literal

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.workspaces import current_workspace
from app.services import exam_import_jobs, exam_pages, exam_sources, exams, llm, media_lessons
from app.services.exam_schemas import ExamDraft

router = APIRouter(prefix="/exams", tags=["model tests"])


class Save(BaseModel):
    draft: ExamDraft
    reviewed: bool = False
    revision: int = Field(ge=1)


class Pages(BaseModel):
    first: int = Field(ge=1)
    last: int = Field(ge=1)


class Start(BaseModel):
    timed: bool = False


class Answers(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class Progress(Answers):
    position: int = Field(default=0, ge=0)
    revision: int = Field(ge=0)


class ResetProgress(BaseModel):
    revision: int = Field(ge=0)


class CheckAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)
    revision: int = Field(ge=0)


def run(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (httpx.HTTPError, llm.LLMError) as exc:
        raise HTTPException(
            502, "Importdienst derzeit nicht verfügbar. Der gespeicherte Entwurf bleibt erhalten."
        ) from exc


@router.get("")
def listing() -> Any:
    return exams.list_exams(current_workspace())


@router.get("/sources")
def sources() -> Any:
    return [{"id": key, "title": item["title"], "url": exam_sources.PAGE} for key, item in exam_sources.CATALOG.items()]


@router.post("/sources/{key}")
def import_source(key: str) -> Any:
    return run(exam_sources.import_source, key, current_workspace())


@router.post("")
async def upload(file: UploadFile = File(...), audio: UploadFile | None = File(None), title: str = Form("")) -> Any:
    pdf = await file.read(exams.PDF_LIMIT + 1)
    sound = await audio.read(exams.AUDIO_LIMIT + 1) if audio else None
    return await run_in_threadpool(
        run, exams.create, current_workspace(), title or file.filename or "Test.pdf", pdf, sound
    )


@router.post("/media")
async def upload_media(transcript: UploadFile = File(...), audio: UploadFile = File(...), title: str = Form("")) -> Any:
    text = await transcript.read(media_lessons.VTT_LIMIT + 1)
    sound = await audio.read(exams.AUDIO_LIMIT + 1)
    return await run_in_threadpool(run, media_lessons.create, current_workspace(),
                                  title or (transcript.filename or "Lesung").rsplit(".", 1)[0], text, sound)


@router.get("/attempts")
def history() -> Any:
    return exams.attempts(current_workspace())


@router.get("/attempts/{attempt_id}")
def attempt(attempt_id: str) -> Any:
    return exams.get_attempt(attempt_id, current_workspace())


@router.post("/attempts/{attempt_id}/submit")
def submit(attempt_id: str, body: Answers) -> Any:
    return run(exams.submit, attempt_id, current_workspace(), body.answers)


@router.put("/attempts/{attempt_id}/progress")
def progress(attempt_id: str, body: Progress) -> Any:
    return run(exams.save_progress, attempt_id, current_workspace(), body.answers, body.position, body.revision)


@router.post("/attempts/{attempt_id}/reset")
def reset_progress(attempt_id: str, body: ResetProgress) -> Any:
    return run(exams.save_progress, attempt_id, current_workspace(), {}, 0, body.revision, True)


@router.post("/attempts/{attempt_id}/questions/{question_id}/check")
def check_answer(attempt_id: str, question_id: str, body: CheckAnswer) -> Any:
    return run(exams.check_answer, attempt_id, current_workspace(), question_id, body.answer, body.revision)


@router.get("/attempts/{attempt_id}/questions/{question_id}/audio")
def question_audio(attempt_id: str, question_id: str) -> Response:
    return Response(run(exams.question_audio, attempt_id, current_workspace(), question_id), media_type="audio/mpeg")


@router.get("/{exam_id}/editor")
def editor(exam_id: str) -> Any:
    return exams.editor(exam_id, current_workspace())


@router.put("/{exam_id}")
def save(exam_id: str, body: Save) -> Any:
    return run(exams.save, exam_id, current_workspace(), body.draft, body.reviewed, body.revision)


@router.post("/{exam_id}/extract")
def extract(exam_id: str, body: Pages) -> Any:
    return run(exams.extract, exam_id, current_workspace(), body.first, body.last)


@router.post("/{exam_id}/start")
def start(exam_id: str, body: Start) -> Any:
    return exams.start(exam_id, current_workspace(), body.timed)


@router.get("/{exam_id}/assets/{kind}")
def asset(exam_id: str, kind: Literal["pdf", "audio", "source_audio", "transcript"]) -> Response:
    data = exams.asset(exam_id, current_workspace(), kind)
    if kind == "audio":
        data = run(media_lessons.playback, data)
    mime = {"pdf": "application/pdf", "transcript": "text/vtt"}.get(kind) or (
        "audio/webm" if data.startswith(b"\x1aE\xdf\xa3") else "audio/wav" if data.startswith(b"RIFF") else "audio/mpeg"
    )
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})


@router.delete("/{exam_id}")
def delete(exam_id: str) -> Any:
    exams.delete(exam_id, current_workspace())
    return {"deleted": True}


@router.post("/{exam_id}/audio")
async def attach_audio(exam_id: str, file: UploadFile = File(...)) -> Any:
    data = await file.read(exams.AUDIO_LIMIT + 1)
    return await run_in_threadpool(run, exams.attach_audio, exam_id, current_workspace(), data)


@router.post("/{exam_id}/audio-suggestions")
def audio_suggestions(exam_id: str) -> Any:
    return run(exams.transcribe_audio, exam_id, current_workspace())


@router.post("/{exam_id}/import-job")
def start_import_job(exam_id: str) -> Any:
    return run(exam_import_jobs.start, exam_id, current_workspace())


@router.get("/{exam_id}/import-job")
def import_job(exam_id: str) -> Any:
    return run(exam_import_jobs.status, exam_id, current_workspace())


@router.get("/{exam_id}/pages/{page}")
def page_image(exam_id: str, page: int, attempt_id: str | None = None) -> Response:
    data = run(exam_pages.render, exam_id, current_workspace(), page, attempt_id)
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
