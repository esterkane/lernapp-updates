"""Run the eval harness from the API/MCP (eval-harness skill)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.paths import repo_root

router = APIRouter(tags=["evals"])


class EvalRequest(BaseModel):
    suite: str = "all"


@router.post("/evals/run")
def run_evals(body: EvalRequest) -> dict[str, Any]:
    root: Path = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from evals.run_evals import run
    except ImportError as exc:
        raise HTTPException(500, f"eval harness not importable: {exc}") from exc
    if body.suite not in ("all", "writing", "rag", "tasks"):
        raise HTTPException(400, "suite must be all|writing|rag|tasks")
    return run(body.suite)
