"""Vocab API (docs/api.md "Documents & vocab"): SM-2 spaced repetition over learner vocabulary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.db import ensure_default_learner
from app.core.workspaces import current_workspace, resolve_learner
from app.services import vocab

router = APIRouter(tags=["vocab"])


class VocabCreate(BaseModel):
    learner_id: str = Field(default_factory=current_workspace)
    wort: str
    bedeutung: str
    beispiel: str | None = None


class ReviewBody(BaseModel):
    quality: int = Field(ge=0, le=5)


@router.get("/vocab/stats")
def vocab_stats(learner_id: str | None = None) -> dict[str, int]:
    learner_id = resolve_learner(learner_id)
    return vocab.stats(learner_id)


@router.get("/vocab")
def list_vocab(learner_id: str | None = None, due_only: bool = True, limit: int = 20) -> list[dict[str, Any]]:
    learner_id = resolve_learner(learner_id)
    return vocab.due_items(learner_id, limit=max(1, min(limit, 500)), due_only=due_only)


@router.post("/vocab")
def create_vocab(body: VocabCreate) -> dict[str, Any]:
    if body.learner_id == get_settings().default_learner_id:
        ensure_default_learner()
    try:
        return vocab.add_item(body.learner_id, body.wort, body.bedeutung, body.beispiel)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/vocab/{item_id}/review")
def review_vocab(item_id: str, body: ReviewBody, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    item = vocab.review(item_id, body.quality, owner_id=learner_id)
    if item is None:
        raise HTTPException(404, "Vokabel nicht gefunden")
    return item


@router.delete("/vocab/{item_id}")
def delete_vocab(item_id: str, learner_id: str | None = None) -> dict[str, bool]:
    learner_id = resolve_learner(learner_id)
    if not vocab.delete_item(item_id, owner_id=learner_id):
        raise HTTPException(404, "Vokabel nicht gefunden")
    return {"deleted": True}


class ImportItem(BaseModel):
    wort: str = Field(min_length=1, max_length=255)
    bedeutung: str = Field(min_length=1, max_length=2000)
    beispiel: str | None = Field(default=None, max_length=2000)


class ImportItems(BaseModel):
    items: list[ImportItem] = Field(min_length=1, max_length=3000)
    source_document_id: str | None = None


@router.post('/vocab/import')
def import_vocabulary(body: ImportItems) -> dict[str, int]:
    from app.services import vocab_import
    try:
        return vocab_import.import_items(current_workspace(), [item.model_dump() for item in body.items], body.source_document_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/vocab/preview')
async def preview_vocabulary(file: UploadFile = File(...)) -> list[dict[str, Any]]:
    from fastapi.concurrency import run_in_threadpool

    from app.services import vocab_import
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, 'Bitte eine Datei bis 25 MB auswählen.')
    try:
        return await run_in_threadpool(vocab_import.preview, file.filename or '', data)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
