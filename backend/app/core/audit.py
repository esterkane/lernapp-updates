"""Append-only audit trail (learnings §3): who called which governed tool, with an argument digest.

Ledger = cost; audit = who-did-what. Rows carry ``arg_digest`` (sha256 of the canonical JSON of the
arguments) — never raw learner text — and there is deliberately no update/delete API here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

from sqlalchemy import select

from app.core.db import db_session
from app.db.base import AuditLog

log = logging.getLogger(__name__)

Caller = Literal["ui", "mcp", "unknown"]
Outcome = Literal["ok", "denied", "error"]
CALLERS: frozenset[str] = frozenset({"ui", "mcp"})
MAX_LIMIT = 500


def canonical_json(args: Any) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def arg_digest(args: Any) -> str:
    """sha256 over the canonical JSON of ``args`` (stable across key order)."""
    return hashlib.sha256(canonical_json(args).encode("utf-8")).hexdigest()


def caller_from_header(value: str | None) -> Caller:
    v = (value or "").strip().lower()
    return v if v in CALLERS else "unknown"  # type: ignore[return-value]


def record(
    *,
    caller: str,
    tool: str,
    args: Any = None,
    outcome: Outcome,
    category: str | None = None,
    session_id: str | None = None,
    duration_ms: float = 0.0,
    meta: dict[str, Any] | None = None,
    digest: str | None = None,
) -> int:
    """Append one row; returns its id. ``digest`` may be precomputed (middleware hashes the body stream)."""
    with db_session() as db:
        row = AuditLog(
            caller=caller_from_header(caller),
            tool=tool[:120],
            arg_digest=digest or arg_digest(args),
            outcome=outcome,
            category=category,
            session_id=session_id,
            duration_ms=float(duration_ms),
            meta=meta or {},
        )
        db.add(row)
        db.flush()
        return int(row.id)


def _row(r: AuditLog) -> dict[str, Any]:
    return {
        "id": r.id,
        "ts": r.ts.isoformat() if r.ts else None,
        "caller": r.caller,
        "tool": r.tool,
        "arg_digest": r.arg_digest,
        "outcome": r.outcome,
        "category": r.category,
        "session_id": r.session_id,
        "duration_ms": r.duration_ms,
        "meta": dict(r.meta or {}),
    }


def recent(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    with db_session() as db:
        rows = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).scalars().all()
        return [_row(r) for r in rows]
