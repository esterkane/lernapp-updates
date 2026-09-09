"""Tasks API (docs/api.md "Tasks"): generate → validate → persist; deterministic scoring."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.models import ConfigError
from app.core.workspaces import current_workspace
from app.services import tasks as svc
from app.services.llm import LLMError

router = APIRouter(tags=["tasks"])


class GenerateRequest(BaseModel):
    blueprint_id: str
    task_type: str
    level: str = "C1"
    topic: str | None = None
    learner_id: str = Field(default_factory=current_workspace)


class ScoreRequest(BaseModel):
    learner_id: str = Field(default_factory=current_workspace)
    answers: dict[str, str] = Field(default_factory=dict)
    session_id: str | None = None


@router.post("/tasks/generate")
def generate(req: GenerateRequest) -> dict[str, Any]:
    try:
        return svc.generate_task(
            req.blueprint_id, req.task_type, level=req.level, topic=req.topic, learner_id=req.learner_id
        )
    except svc.TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except svc.TaskError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (ConfigError, LLMError) as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/tasks")
def list_tasks(skill: str | None = None, limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    return svc.list_tasks(skill=skill, limit=limit)


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    try:
        return svc.get_task(task_id)
    except svc.TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/tasks/{task_id}/score")
def score(task_id: str, req: ScoreRequest) -> dict[str, Any]:
    try:
        return svc.score_task(task_id, req.learner_id, req.answers, session_id=req.session_id)
    except svc.TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except svc.TaskError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
