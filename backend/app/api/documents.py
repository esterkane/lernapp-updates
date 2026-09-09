"""Documents API (docs/api.md "Documents & vocab"): upload, list, patch, delete, hybrid search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.db import db_session, ensure_default_learner
from app.core.workspaces import resolve_learner
from app.db.base import Learner
from app.services import rag

router = APIRouter(tags=["documents"])


class DocumentPatch(BaseModel):
    tags: list[str] | str | None = None
    use_in: dict[str, bool] | list[str] | str | None = None
    title: str | None = None


def _ensure_learner(learner_id: str) -> str:
    if learner_id == get_settings().default_learner_id:
        return ensure_default_learner()
    with db_session() as db:
        if db.get(Learner, learner_id) is None:
            raise HTTPException(404, f"Lernende:r '{learner_id}' nicht gefunden")
    return learner_id


@router.post("/documents")
async def upload_document(
    file: UploadFile = File(...),
    learner_id: str | None = Form(None),
    tags: str = Form(""),
    use_in: str | None = Form(None),
) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    learner = _ensure_learner(learner_id)
    filename = file.filename or "upload.txt"
    data = await file.read()
    if not data:
        raise HTTPException(400, "Leere Datei")
    try:
        return rag.ingest(learner, filename, data, tags=tags, use_in=use_in)
    except ValueError as exc:
        raise HTTPException(415 if "Dateiformat" in str(exc) else 400, str(exc)) from exc


@router.get("/documents")
def list_documents(learner_id: str | None = None) -> list[dict[str, Any]]:
    learner_id = resolve_learner(learner_id)
    return rag.list_documents(learner_id)


@router.get("/documents/search")
def search_documents(
    q: str,
    learner_id: str | None = None,
    tags: str | None = None,
    k: int = 8,
    use_in: str | None = None,
) -> list[dict[str, Any]]:
    learner_id = resolve_learner(learner_id)
    try:
        hits = rag.search(q, learner_id, tags=tags, k=max(1, min(k, 50)), use_in=use_in or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [h.model_dump(mode="json") for h in hits]


@router.patch("/documents/{document_id}")
def patch_document(document_id: str, body: DocumentPatch, learner_id: str | None = None) -> dict[str, Any]:
    learner_id = resolve_learner(learner_id)
    try:
        doc = rag.update_document(
            document_id, owner_id=learner_id, tags=body.tags, use_in=body.use_in, title=body.title
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if doc is None:
        raise HTTPException(404, "Dokument nicht gefunden")
    return doc


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, learner_id: str | None = None) -> dict[str, bool]:
    learner_id = resolve_learner(learner_id)
    if not rag.delete_document(document_id, owner_id=learner_id):
        raise HTTPException(404, "Dokument nicht gefunden")
    return {"deleted": True}
