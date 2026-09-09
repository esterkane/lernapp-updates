from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.blueprints import get_blueprint, load_blueprints

router = APIRouter(tags=["blueprints"])


@router.get("/blueprints")
def list_blueprints() -> list[dict[str, Any]]:
    return [
        {**b.model_dump(mode="json"), "is_verified": b.is_verified, "task_types": b.task_types}
        for b in load_blueprints().values()
    ]


@router.get("/blueprints/{blueprint_id}")
def get_one(blueprint_id: str) -> dict[str, Any]:
    try:
        b = get_blueprint(blueprint_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {**b.model_dump(mode="json"), "is_verified": b.is_verified, "task_types": b.task_types}
