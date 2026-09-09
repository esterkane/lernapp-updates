"""Append-only audit trail (learnings §3): one row per governed request, argument digest instead of raw text."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core import audit
from app.core.db import db_session
from app.db.base import AuditLog
from sqlalchemy import func, select

SECRET_TEXT = "Ich habe gestern ein streng vertrauliches Meeting gehabt."


def _count() -> int:
    with db_session() as db:
        return int(db.execute(select(func.count()).select_from(AuditLog)).scalar_one())


def _rows_dump() -> str:
    with db_session() as db:
        rows = db.execute(select(AuditLog)).scalars().all()
        return json.dumps(
            [{"tool": r.tool, "digest": r.arg_digest, "meta": r.meta, "session_id": r.session_id} for r in rows],
            ensure_ascii=False,
        )


def test_digest_is_stable_and_canonical() -> None:
    a = audit.arg_digest({"b": 1, "a": [1, 2], "text": "Hällo"})
    b = audit.arg_digest({"a": [1, 2], "text": "Hällo", "b": 1})
    assert a == b and len(a) == 64
    expected = hashlib.sha256(
        json.dumps(
            {"a": [1, 2], "b": 1, "text": "Hällo"}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    assert a == expected
    assert audit.arg_digest({"text": "anders"}) != a


def test_record_writes_row_without_raw_args() -> None:
    before = _count()
    row_id = audit.record(
        caller="mcp",
        tool="POST /assess/writing",
        args={"learner_text": SECRET_TEXT, "learner_id": "default"},
        outcome="ok",
        session_id="s-audit",
        duration_ms=12.5,
        meta={"status": 200},
    )
    assert row_id > 0 and _count() == before + 1
    rows = audit.recent(limit=5)
    row = next(r for r in rows if r["id"] == row_id)
    assert row["caller"] == "mcp" and row["outcome"] == "ok" and row["session_id"] == "s-audit"
    assert row["arg_digest"] == audit.arg_digest({"learner_text": SECRET_TEXT, "learner_id": "default"})
    assert SECRET_TEXT not in json.dumps(row, ensure_ascii=False)


def test_module_is_append_only() -> None:
    mutating = [n for n in dir(audit) if n.lower().startswith(("update", "delete", "purge", "remove", "truncate"))]
    assert mutating == []
    assert not hasattr(audit, "clear")


def test_middleware_records_governed_requests_only(client) -> None:  # type: ignore[no-untyped-def]
    before = _count()
    assert client.get("/health").status_code == 200
    assert client.get("/blueprints").status_code == 200
    assert _count() == before  # read-only endpoints are not governed

    sid = client.post("/sessions", json={"kind": "tutor"}, headers={"X-Lernapp-Caller": "ui"}).json()["session_id"]
    assert _count() == before + 1
    r = client.post(f"/sessions/{sid}/turn", json={"text": SECRET_TEXT}, headers={"X-Lernapp-Caller": "mcp"})
    assert r.status_code == 200
    assert _count() == before + 2
    rows = audit.recent(limit=2)
    turn_row = rows[0]
    assert turn_row["tool"] == "POST /sessions/{id}/turn" and turn_row["caller"] == "mcp"
    assert turn_row["outcome"] == "ok" and turn_row["session_id"] == sid and turn_row["duration_ms"] >= 0.0
    assert rows[1]["tool"] == "POST /sessions" and rows[1]["caller"] == "ui"
    assert SECRET_TEXT not in _rows_dump()

    # unknown caller header → "unknown"; denied (4xx) requests are recorded with a category
    r = client.post("/sessions/does-not-exist/turn", json={"text": "Hallo"})
    assert r.status_code == 404
    last = audit.recent(limit=1)[0]
    assert last["caller"] == "unknown" and last["outcome"] == "denied" and last["category"] == "business"


def test_audit_endpoint(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/sessions", json={"kind": "tutor"}, headers={"X-Lernapp-Caller": "ui"})
    r = client.get("/audit", params={"limit": 3})
    assert r.status_code == 200
    body: list[dict[str, Any]] = r.json()
    assert 1 <= len(body) <= 3
    assert {
        "id",
        "ts",
        "caller",
        "tool",
        "arg_digest",
        "outcome",
        "category",
        "session_id",
        "duration_ms",
        "meta",
    } <= set(body[0])
    assert client.get("/audit", params={"limit": 0}).status_code == 422
    assert client.get("/audit", params={"limit": 5000}).status_code == 422
