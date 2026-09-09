"""MCP server (ADR-0014) without stdio: call the tool functions directly against the test app.

``lernapp_mcp.server._client()`` is patched to hand out the FastAPI ``TestClient`` (an
``httpx.Client`` subclass whose transport runs the ASGI app in-process; the fixture's
``with TestClient(app)`` already ran the lifespan). Every tool must return the ToolResult envelope
(learnings-udacity §3): ``isError``, ``data`` | ``errorCategory`` + ``isRetryable`` + ``message``.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Iterator
from typing import Any

import httpx
import lernapp_mcp.server as server
import pytest
from app.core.config import get_settings

TOOLS_WITH_LEARNER = [
    "assess_writing",
    "assess_speaking_transcript",
    "start_roleplay",
    "ingest_document",
    "search_documents",
    "learner_progress",
]
ALL_TOOLS = [
    "list_blueprints",
    "generate_task",
    "assess_writing",
    "assess_speaking_transcript",
    "start_roleplay",
    "roleplay_turn",
    "ingest_document",
    "search_documents",
    "cost_summary",
    "cost_counterfactual",
    "run_evals",
    "learner_progress",
]


@pytest.fixture()
def mcp_api(client, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:  # type: ignore[no-untyped-def]
    """Route the MCP server's HTTP calls into the in-process test app (no network, no stdio)."""
    monkeypatch.setattr(server, "_client", lambda: contextlib.nullcontext(client))
    monkeypatch.setattr(server, "TOKEN", "")
    yield client


def _assert_ok(res: dict[str, Any]) -> Any:
    assert res["isError"] is False, res
    assert "data" in res and "errorCategory" not in res
    return res["data"]


def _assert_fail(res: dict[str, Any], category: server.ErrorCategory) -> None:
    assert res["isError"] is True, res
    assert res["errorCategory"] == category.value
    assert res["isRetryable"] is (category is server.ErrorCategory.TRANSIENT)
    assert res["message"]


def test_every_tool_is_registered_with_a_contract_description() -> None:
    names = set(ALL_TOOLS)
    assert names <= set(server.CONTRACTS), names - set(server.CONTRACTS)
    for name in ALL_TOOLS:
        assert inspect.getdoc(getattr(server, name))  # docstring for humans
        rendered = server.CONTRACTS[name].render()
        for marker in ("Purpose:", "Inputs:", "Outputs:", "Do NOT use for:", "Example query:", "Edge case:"):
            assert marker in rendered, (name, marker)


def test_learner_id_defaults_to_default_everywhere() -> None:
    for name in TOOLS_WITH_LEARNER:
        sig = inspect.signature(getattr(server, name))
        assert sig.parameters["learner_id"].default == "default", name
    assert server.CONTRACTS["assess_writing"] is not None
    assert "task_text" in inspect.signature(server.assess_writing).parameters


def test_headers_carry_caller_and_optional_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "TOKEN", "")
    assert server._headers() == {"X-Lernapp-Caller": "mcp"}
    monkeypatch.setattr(server, "TOKEN", "abc")
    assert server._headers() == {"Authorization": "Bearer abc", "X-Lernapp-Caller": "mcp"}


def test_ok_envelope_list_blueprints(mcp_api: Any) -> None:
    data = _assert_ok(server.list_blueprints())
    assert isinstance(data, list) and data
    assert all("id" in b for b in data)


def test_ok_envelope_assess_writing_with_task_text(mcp_api: Any) -> None:
    data = _assert_ok(
        server.assess_writing(
            learner_text="Ich bin der Auffassung, dass Homeoffice die Produktivität steigert.",
            task_text="Nehmen Sie Stellung zum Thema Homeoffice.",
        )
    )
    assert data["label"] == "interne Übungsbewertung"
    assert "rubric" in data and data["total_max"] == 28


def test_ok_envelope_search_documents_empty_is_not_an_error(mcp_api: Any) -> None:
    data = _assert_ok(server.search_documents("Gehaltsverhandlung", k=3))
    assert isinstance(data, list)


def test_ok_envelope_learner_progress_and_cost_summary(mcp_api: Any, learner_id: str) -> None:
    data = _assert_ok(server.learner_progress())
    assert "points" in data and "top_error_tags" in data
    costs = _assert_ok(server.cost_summary(group_by="stage"))
    assert "total_eur" in costs and "groups" in costs


def test_404_is_business_not_retryable(mcp_api: Any) -> None:
    res = server.learner_progress(learner_id="niemand-hier")
    _assert_fail(res, server.ErrorCategory.BUSINESS)
    res = server.assess_writing(learner_text="Text.", task_id="gibt-es-nicht")
    _assert_fail(res, server.ErrorCategory.BUSINESS)


def test_422_is_validation(mcp_api: Any) -> None:
    res = server.generate_task(blueprint_id="x", task_type="y", level=12)  # type: ignore[arg-type]
    _assert_fail(res, server.ErrorCategory.VALIDATION)


def test_unreachable_api_is_transient_and_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "TOKEN", "")
    monkeypatch.setattr(server, "_client", lambda: httpx.Client(base_url="http://127.0.0.1:9", timeout=2.0))
    res = server.list_blueprints()
    _assert_fail(res, server.ErrorCategory.TRANSIENT)
    assert "unreachable" in res["message"]


def test_401_is_permission_when_token_required_but_unset(mcp_api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "lernapp_api_token", "s3cret")
    res = server.list_blueprints()
    _assert_fail(res, server.ErrorCategory.PERMISSION)
    monkeypatch.setattr(server, "TOKEN", "s3cret")
    _assert_ok(server.list_blueprints())
