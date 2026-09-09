"""Read-only view of the append-only audit trail (learnings §3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core import audit

router = APIRouter(tags=["audit"])


@router.get("/audit")
def recent_audit(limit: int = Query(50, ge=1, le=audit.MAX_LIMIT)) -> list[dict[str, Any]]:
    return audit.recent(limit=limit)
