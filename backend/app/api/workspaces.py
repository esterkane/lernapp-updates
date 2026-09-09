"""Local workspaces, owned by the signed-in operating-system user (ADR-0020)."""

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import db_session
from app.core.workspaces import resolve_learner, selected_workspace
from app.db.base import Document, Learner, Result, Session, Task, VocabItem, new_id

router = APIRouter(tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    level: str = "B2"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Bitte einen Namen eingeben.")
        return value

    @field_validator("level")
    @classmethod
    def valid_level(cls, value: str) -> str:
        if value not in {"A2", "B1", "B2", "C1", "C2"}:
            raise ValueError("Bitte ein gültiges Sprachniveau wählen.")
        return value


@router.get("/workspaces")
def list_workspaces() -> list[dict[str, str]]:
    with db_session() as db:
        return [
            {"id": row.id, "name": row.display_name, "level": row.level}
            for row in db.execute(select(Learner).order_by(Learner.created_at, Learner.id)).scalars()
        ]


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceCreate) -> dict[str, str]:
    with db_session() as db:
        row = Learner(id=new_id(), display_name=body.name, level=body.level)
        db.add(row)
        db.flush()
        return {"id": row.id, "name": row.display_name, "level": row.level}


async def enforce_workspace(request: Request) -> None:
    """Check identifiers before any handler can read, mutate or make provider calls.

    Header-less clients retain the explicit learner API for local scripts. A workspace
    header scopes the complete request; it is organization, not a user login token.
    """
    workspace = selected_workspace.get()
    if not workspace or request.url.path in {"/health", "/workspaces"}:
        return
    with db_session() as db:
        if db.get(Learner, workspace) is None:
            raise HTTPException(404, "Lernbereich nicht gefunden. Bitte einen anderen auswählen.")
    if request.url.path.startswith(("/audit", "/evals")):
        raise HTTPException(403, "Diese Diagnose ist nur über den lokalen Entwicklerzugang verfügbar.")
    values: list[Mapping[str, Any]] = [request.path_params, request.query_params]
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            values.append(body)
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        values.append(await request.form())
    resources = {
        "session_id": (Session, "learner_id"),
        "document_id": (Document, "owner_id"),
        "item_id": (VocabItem, "owner_id"),
        "result_id": (Result, "learner_id"),
        "task_id": (Task, "learner_id"),
    }
    with db_session() as db:
        for value in values:
            for field in ("learner_id", "owner_id"):
                if value.get(field):
                    resolve_learner(str(value[field]))
            for field, (model, owner_field) in resources.items():
                identifier = value.get(field)
                if not identifier:
                    continue
                row = db.get(model, str(identifier))
                owner = getattr(row, owner_field, None) if row else None
                if model is Task and row is not None and owner is None:
                    owner = get_settings().default_learner_id
                if row is None or owner != workspace:
                    raise HTTPException(404, "Eintrag in diesem Lernbereich nicht gefunden.")
