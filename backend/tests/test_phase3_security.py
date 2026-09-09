"""Phase 3 — security audit gate for credential handling (ADR-0019).

``scripts/security_audit.py`` must report zero findings; ``credentials.redact`` masks configured key values and
key-shaped substrings; ``usage.record_usage`` applies it to every ``meta["error"]`` (events + ledger rows).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from app.core import credentials, usage
from app.core.db import db_session
from app.db.base import CostLedger, ProviderCredential, UsageEvent
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[2]


def _audit() -> Any:
    path = ROOT / "scripts" / "security_audit.py"
    spec = importlib.util.spec_from_file_location("security_audit", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["security_audit"] = mod  # dataclasses resolve postponed annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _encryption_key() -> Iterator[None]:
    credentials.ensure_encryption_key()
    yield


def test_security_audit_has_zero_findings() -> None:
    report = _audit().run()
    assert report["findings"] == [], "\n".join(f"{h['path']}:{h['line']}: {h['text']}" for h in report["findings"])
    # env-name literals in the frontend are listed (settings UI field names), never read from the environment
    info = report["info"]
    assert all(h["check"] in ("frontend_env_key", "log_credential_arg") for h in info)
    assert set(report["checks"]) == {
        "browser_storage",
        "frontend_env_key",
        "log_credential_arg",
        "key_in_url",
        "structural",
    }


def test_audit_detects_log_calls_with_credential_arguments() -> None:
    mod = _audit()
    bad = 'log.info("token %s", api_key)\nprint(headers)\nlog.warning("x", resp.headers["Authorization"])\n'
    hits = mod.log_hits(bad, "backend/app/x.py")
    assert [h.line for h in hits] == [1, 2, 3] and all(h.severity == "finding" for h in hits)
    ok = 'log.info("credential stored (hint only: …%s)", hint)\nlog.error("API key missing for %s", provider)\n'
    assert mod.log_hits(ok, "backend/app/x.py") == []


def test_audit_detects_key_in_url_and_browser_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _audit()
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "a.py").write_text(
        'x = "https://generativelanguage.googleapis.com/v1beta/models?key=" + k\n', encoding="utf-8"
    )
    (fe / "b.js").write_text("localStorage.setItem('k', key)\n", encoding="utf-8")
    (fe / "c.py").write_text('k = os.environ["OPENAI_API_KEY"]\n', encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "FRONTEND", fe)
    monkeypatch.setattr(mod, "BACKEND_APP", tmp_path / "backend" / "app")
    checks = {h.check for h in mod.check_key_in_url() + mod.check_browser_storage() + mod.check_frontend_env_names()}
    assert checks == {"key_in_url", "browser_storage", "frontend_env_key"}


# ------------------------------------------------------------------ redaction


def test_redact_masks_configured_and_key_shaped_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "m1stralPlainValue2024xyz")
    text = (
        "AuthenticationError: api_key=sk-proj-abcdefghijklmnop rejected; "
        "url ?key=AIzaSyD-1234567890abcdef; Authorization: Bearer m1stralPlainValue2024xyz; "
        "x-api-key: sk-ant-api03-zzzzzzzzzzzz"
    )
    out = credentials.redact(text)
    for secret in ("sk-proj-abcdefghijklmnop", "AIzaSyD-1234567890abcdef", "m1stralPlainValue2024xyz", "sk-ant-api03"):
        assert secret not in out
    assert "AuthenticationError" in out and "rejected" in out
    assert credentials.redact(None) == "" and credentials.redact("") == ""
    assert credentials.redact("Modell openai/gpt-4o-mini unbekannt") == "Modell openai/gpt-4o-mini unbekannt"


def test_redact_masks_the_learners_encrypted_key(learner_id: str) -> None:
    key = "totally-random-mistral-value-987654"
    with db_session() as db:
        db.execute(delete(ProviderCredential).where(ProviderCredential.learner_id == learner_id))
    credentials.set_credential(learner_id, "mistral", key)
    try:
        assert credentials.redact(f"provider said: {key} invalid", learner_id) == "provider said: *** invalid"
        assert key in credentials.redact(f"provider said: {key} invalid")  # unknown learner → pattern-only
    finally:
        credentials.delete_credential(learner_id, "mistral")


def test_record_usage_redacts_error_meta_in_event_and_ledger(learner_id: str) -> None:
    key = "sk-leaked-through-exception-0123456789"
    rec = usage.record_usage(
        usage.UsageDraft(
            provider="openai",
            stage="llm",
            model="openai/gpt-4o-mini",
            learner_id=learner_id,
            session_id="sec-test",
            outcome="error",
            meta={"tier": "tutor", "error": f"AuthenticationError: Incorrect API key provided: {key}"},
        )
    )
    with db_session() as db:
        ev = db.get(UsageEvent, rec.event_id)
        assert ev is not None
        assert key not in json.dumps(ev.meta) and "***" in ev.meta["error"]
        rows = db.execute(select(CostLedger).where(CostLedger.event_id == rec.event_id)).scalars().all()
        assert rows and all(key not in json.dumps(r.meta) for r in rows)


def test_free_call_events_are_priced_as_free(learner_id: str) -> None:
    rec = usage.record_usage(
        usage.UsageDraft(
            provider="anthropic",
            stage="llm",
            model="anthropic/models",
            learner_id=learner_id,
            session_id=None,
            service="other",
            operation="connection_test",
            meta={"connection_test": True, "free_call": True},
        )
    )
    assert rec.cost_status == "free" and rec.expected_eur == 0.0 and rec.list_eur == 0.0 and rec.ledger_ids == []


def test_validation_handler_strips_secret_inputs() -> None:
    from app.api.errors import _strip_secret_input

    out = _strip_secret_input({"learner_id": 1, "api_key": "sk-x", "nested": [{"token": "t", "ok": "v"}]})
    assert out == {"learner_id": 1, "api_key": "***", "nested": [{"token": "***", "ok": "v"}]}


def test_public_package_contains_security_audit_tool() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "packaging/common"))
    from source_payload import files

    assert ROOT / "scripts/security_audit.py" in set(files(ROOT))
