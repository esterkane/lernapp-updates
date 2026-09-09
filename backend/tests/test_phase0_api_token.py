"""Optional API token (``LERNAPP_API_TOKEN``): bearer required on every route except health/docs.

The token is dev/ops only — the Einstellungen page never shows it, but ``GET /settings`` reports
``api_token_active`` so the UI can explain a 401.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.core.config import get_settings

TOKEN = "test-token-123"


@pytest.fixture()
def token_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(get_settings(), "lernapp_api_token", TOKEN)
    yield TOKEN


def test_no_token_configured_means_open(client) -> None:  # type: ignore[no-untyped-def]
    assert get_settings().lernapp_api_token in (None, "")
    assert client.get("/blueprints").status_code == 200
    assert client.get("/settings").json()["api_token_active"] is False


def test_missing_header_is_401_with_german_detail(client, token_on: str) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/blueprints")
    assert r.status_code == 401
    assert "Token" in r.json()["detail"]
    assert r.headers.get("www-authenticate", "").startswith("Bearer")


def test_wrong_scheme_or_wrong_token_is_401(client, token_on: str) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/blueprints", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert client.get("/blueprints", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/sessions", json={"kind": "tutor"}).status_code == 401


def test_correct_bearer_is_200(client, token_on: str) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/blueprints", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    s = client.get("/settings", headers={"Authorization": f"Bearer {TOKEN}"})
    assert s.status_code == 200
    assert s.json()["api_token_active"] is True
    assert TOKEN not in s.text  # never echoed back


def test_health_and_docs_always_open(client, token_on: str) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_ui_client_sends_bearer_and_caller_header(monkeypatch: pytest.MonkeyPatch) -> None:
    import lernapp_ui.api as ui_api

    monkeypatch.setattr(ui_api, "API_TOKEN", TOKEN)
    h = ui_api._headers()
    assert h["Authorization"] == f"Bearer {TOKEN}"
    assert h["X-Lernapp-Caller"] == "ui"
    monkeypatch.setattr(ui_api, "API_TOKEN", "")
    h = ui_api._headers()
    assert "Authorization" not in h and h["X-Lernapp-Caller"] == "ui"
