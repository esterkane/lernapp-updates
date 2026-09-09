"""Pure ASGI middleware: one ``audit_log`` row per governed request (learnings §3).

The request body is hashed as it streams through (never buffered, never stored); the row carries
``caller`` (``X-Lernapp-Caller``: ui | mcp | unknown), the normalized tool name
(``POST /sessions/{id}/turn``), the argument digest, outcome (ok | denied | error) and duration.
Audit failures are logged and never break the request.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.core import audit

log = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

GOVERNED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/sessions(/.*)?$")),
    ("POST", re.compile(r"^/assess/.+$")),
    ("POST", re.compile(r"^/tasks/.+$")),
    ("POST", re.compile(r"^/roleplay/.+$")),
    ("POST", re.compile(r"^/documents.*$")),
    ("DELETE", re.compile(r"^/documents/.+$")),
    ("POST", re.compile(r"^/evals/run$")),
    ("DELETE", re.compile(r"^/learners/.+$")),
)
_ID_SEGMENT = re.compile(r"^[0-9a-f]{32}$|^[0-9a-f-]{36}$|^(task|score|eval)-[0-9a-f-]+$")
_SESSION_PATH = re.compile(r"^/(?:sessions|roleplay)/([^/]+)/(?:turn|end)$")
CALLER_HEADER = b"x-lernapp-caller"


def is_governed(method: str, path: str) -> bool:
    return any(method == m and p.match(path) for m, p in GOVERNED)


def tool_name(method: str, path: str) -> str:
    parts = [("{id}" if _ID_SEGMENT.match(seg) else seg) for seg in path.split("/")]
    return f"{method} {'/'.join(parts)}"


def session_id_of(path: str) -> str | None:
    m = _SESSION_PATH.match(path)
    return m.group(1) if m else None


def outcome_of(status: int) -> tuple[str, str | None]:
    if status < 400:
        return "ok", None
    if status >= 500:
        return "error", "transient"
    if status in (401, 403):
        return "denied", "permission"
    if status == 422:
        return "denied", "validation"
    return "denied", "business"


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        if scope.get("type") != "http" or not is_governed(method, path):
            await self.app(scope, receive, send)
            return

        hasher = hashlib.sha256()
        query = bytes(scope.get("query_string", b"") or b"")
        headers = dict(scope.get("headers") or [])
        caller = headers.get(CALLER_HEADER, b"").decode("latin-1", errors="replace")
        status = {"code": 500}
        body_bytes = {"n": 0}
        start = time.perf_counter()

        async def recv() -> Message:
            msg = await receive()
            if msg.get("type") == "http.request":
                chunk = msg.get("body", b"") or b""
                hasher.update(chunk)
                body_bytes["n"] += len(chunk)
            return msg

        async def snd(msg: Message) -> None:
            if msg.get("type") == "http.response.start":
                status["code"] = int(msg.get("status", 500))
            await send(msg)

        error: str | None = None
        try:
            await self.app(scope, recv, snd)
        except Exception as exc:
            error = type(exc).__name__
            status["code"] = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            outcome, category = outcome_of(status["code"])
            digest = audit.arg_digest(
                {"method": method, "path": path, "query": query.decode("latin-1"), "body_sha256": hasher.hexdigest()}
            )
            meta: dict[str, Any] = {"status": status["code"], "body_bytes": body_bytes["n"], "path": path}
            if error:
                meta["exception"] = error
            try:
                await run_in_threadpool(
                    audit.record,
                    caller=caller,
                    tool=tool_name(method, path),
                    outcome=outcome,  # type: ignore[arg-type]
                    category=category,
                    session_id=session_id_of(path),
                    duration_ms=duration_ms,
                    meta=meta,
                    digest=digest,
                )
            except Exception:  # noqa: BLE001 — audit must never break the request
                log.exception("audit row could not be written for %s %s", method, path)
