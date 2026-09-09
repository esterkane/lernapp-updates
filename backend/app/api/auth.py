"""Optional API token (``LERNAPP_API_TOKEN``) as a pure ASGI middleware.

When a token is configured every route requires ``Authorization: Bearer <token>``; ``GET /health``
and the OpenAPI docs stay open so launcher/doctor and the UI's readiness check keep working.
Without a configured token nothing changes (single-learner desktop install, ADR-0009).

The MCP server and the Streamlit UI send the header from their own ``LERNAPP_API_TOKEN``; both
also identify themselves via ``X-Lernapp-Caller`` (``mcp`` | ``ui``) for the audit trail.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import FastAPI

from app.core.config import get_settings

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

OPEN_PATHS: frozenset[str] = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})
DETAIL_DE = "Nicht autorisiert: Für diese App ist ein API-Token konfiguriert. Sende 'Authorization: Bearer <Token>'."


def _is_open(scope: Scope) -> bool:
    if scope.get("method") == "OPTIONS":  # CORS preflight carries no Authorization header by design
        return True
    return scope.get("method") == "GET" and str(scope.get("path", "")) in OPEN_PATHS


def is_authorized(authorization: str | None, token: str) -> bool:
    if not authorization:
        return False
    scheme, _, value = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), token)


class BearerTokenMiddleware:
    """Rejects requests with 401 (German detail) unless the configured bearer token is presented."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = get_settings().lernapp_api_token  # read per request so settings reloads apply
        if not token or _is_open(scope):
            await self.app(scope, receive, send)
            return
        header = next((v for k, v in scope.get("headers", []) if k == b"authorization"), b"")
        if is_authorized(header.decode("latin-1"), token):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": DETAIL_DE}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Bearer realm="lernapp"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def install_auth(app: FastAPI) -> None:
    """Called once from ``create_app`` (single line there; the logic lives here)."""
    app.add_middleware(BearerTokenMiddleware)
    app.add_middleware(WorkspaceContextMiddleware)


class WorkspaceContextMiddleware:
    """A ContextVar isolates concurrent workspace requests, including threadpool calls."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from app.core.workspaces import selected_workspace

        header = next((v for k, v in scope.get("headers", []) if k == b"x-lernapp-workspace"), b"")
        token = selected_workspace.set(header.decode("latin-1") or None)
        try:
            await self.app(scope, receive, send)
        finally:
            selected_workspace.reset(token)
