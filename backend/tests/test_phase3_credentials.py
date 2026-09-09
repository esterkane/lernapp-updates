"""Phase 3 — BYOK provider credentials (ADR-0019): encrypted at rest, masked only, per learner, tested cheaply.

Contract: docs/api.md "Cost tracking v2". No test needs a real provider: the connection test runs through
``httpx.MockTransport`` installed on ``app.services.provider_test._transport``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.core import credentials
from app.core.config import get_settings
from app.core.db import db_session
from app.core.paths import data_dir
from app.db.base import Learner, ProviderCredential, UsageEvent
from app.services import provider_test
from sqlalchemy import delete, func, select

KEY_A = "sk-test-learner-a-0123456789abcdefXYZ"
KEY_B = "sk-test-learner-b-9876543210zyxwvuLMN"
LEARNER_B = "learner-b"


@pytest.fixture(autouse=True)
def _encryption_key() -> Iterator[None]:
    """Same bootstrap as the app lifespan: generate CREDENTIAL_ENCRYPTION_KEY into <data_dir>/.env once."""
    credentials.ensure_encryption_key()
    yield


@pytest.fixture()
def clean_store(learner_id: str) -> Iterator[str]:
    with db_session() as db:
        if db.get(Learner, LEARNER_B) is None:
            db.add(Learner(id=LEARNER_B, display_name="B", level="B2"))
        db.execute(delete(ProviderCredential))
    for name in credentials.ENV_FOR.values():
        os.environ.pop(name, None)
    yield learner_id
    with db_session() as db:
        db.execute(delete(ProviderCredential))
    for name in credentials.ENV_FOR.values():
        os.environ.pop(name, None)


def _walk(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)
    else:
        yield str(obj)


def assert_no_key(body: Any, *keys: str) -> None:
    text = " ".join(_walk(body)) + json.dumps(body, ensure_ascii=False, default=str)
    for k in keys:
        assert k not in text, f"secret leaked in response: {k[:6]}…"
        assert k[:-3] not in text, "more than the 3-character hint leaked"


def _test_events(learner_id: str) -> list[UsageEvent]:
    """Connection-test events of a learner, oldest first (``[-1]`` = the newest one)."""
    with db_session() as db:
        rows = db.execute(
            select(UsageEvent)
            .where(UsageEvent.learner_id == learner_id, UsageEvent.operation == "connection_test")
            .order_by(UsageEvent.ts.asc(), UsageEvent.id.asc())
        ).scalars()
        return [r for r in rows]


def _event_count(learner_id: str) -> int:
    with db_session() as db:
        return int(
            db.execute(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.learner_id == learner_id, UsageEvent.operation == "connection_test")
            ).scalar_one()
        )


def _mock(status: int, body: dict[str, Any] | None = None, seen: list[httpx.Request] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body or {"data": []}, headers={"x-request-id": "req_test_123"})

    provider_test._transport = httpx.MockTransport(handler)


@pytest.fixture()
def transport() -> Iterator[None]:
    yield
    provider_test._transport = None


# ------------------------------------------------------------------ encryption at rest


def test_put_stores_ciphertext_not_plaintext(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    r = client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True and body["source"] == "encrypted"
    assert body["masked"] == "•" * 12 + KEY_A[-3:]
    assert body["pricing_tier"] == "paid"  # OpenAI has no free inference tier → default
    assert_no_key(body, KEY_A)
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == clean_store, ProviderCredential.provider == "openai"
            )
        ).scalar_one()
        assert row.ciphertext and row.ciphertext != KEY_A and KEY_A not in row.ciphertext
        assert row.ciphertext.startswith("gAAAA")  # Fernet token
        assert row.key_hint == KEY_A[-3:]
    assert credentials.get_api_key(clean_store, "openai") == KEY_A
    # the app-level env file never receives the key
    env_text = (data_dir() / ".env").read_text(encoding="utf-8") if (data_dir() / ".env").exists() else ""
    assert KEY_A not in env_text


def test_rotated_encryption_key_cannot_decrypt(
    clean_store: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from cryptography.fernet import Fernet

    credentials.set_credential(clean_store, "openai", KEY_A)
    assert credentials.get_api_key(clean_store, "openai") == KEY_A
    monkeypatch.setattr(get_settings(), "credential_encryption_key", Fernet.generate_key().decode())
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with caplog.at_level(logging.ERROR, logger="app.core.credentials"), pytest.raises(credentials.CredentialError):
        credentials.api_key_for(clean_store, "openai")
    assert any("cannot decrypt" in rec.getMessage() for rec in caplog.records)
    assert KEY_A not in caplog.text
    # the view still reports "connected" (ciphertext exists) with the hint only
    assert credentials.get_view(clean_store, "openai").masked == "•" * 12 + KEY_A[-3:]


def test_store_refuses_without_encryption_key(clean_store: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "credential_encryption_key", None)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(credentials.CredentialError):
        credentials.set_credential(clean_store, "openai", KEY_A)


# ------------------------------------------------------------------ masked form / isolation


def test_list_shows_masked_form_only(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    r = client.get("/provider-credentials", params={"learner_id": clean_store})
    assert r.status_code == 200
    items = r.json()
    assert [i["provider"] for i in items] == list(credentials.PROVIDERS)
    openai = next(i for i in items if i["provider"] == "openai")
    assert openai["masked"] == "•" * 12 + KEY_A[-3:] and openai["label_de"] == "OpenAI"
    assert all(i["masked"] is None and i["connected"] is False for i in items if i["provider"] != "openai")
    assert_no_key(items, KEY_A)


def test_learner_b_cannot_read_or_use_learner_a_key(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    assert credentials.api_key_for(clean_store, "openai") == KEY_A
    assert credentials.api_key_for(LEARNER_B, "openai") is None  # no env fallback in tests
    assert credentials.get_api_key(LEARNER_B, "openai") is None
    r = client.get("/provider-credentials", params={"learner_id": LEARNER_B})
    openai = next(i for i in r.json() if i["provider"] == "openai")
    assert openai["connected"] is False and openai["masked"] is None and openai["source"] == "none"
    assert_no_key(r.json(), KEY_A)
    # and vice versa
    client.put("/provider-credentials/openai", json={"learner_id": LEARNER_B, "api_key": KEY_B})
    assert credentials.api_key_for(clean_store, "openai") == KEY_A
    assert credentials.api_key_for(LEARNER_B, "openai") == KEY_B


def test_unknown_learner_and_provider(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    r = client.put("/provider-credentials/openai", json={"learner_id": "nobody", "api_key": KEY_A})
    assert r.status_code == 404 and KEY_A not in r.text
    r = client.put("/provider-credentials/foo", json={"learner_id": clean_store, "api_key": KEY_A})
    assert r.status_code == 400 and "Unbekannter Anbieter" in r.json()["detail"] and KEY_A not in r.text
    r = client.get("/provider-credentials", params={"learner_id": "nobody"})
    assert r.status_code == 404


# ------------------------------------------------------------------ PUT semantics


def test_put_tier_only_keeps_key(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    client.put("/provider-credentials/gemini", json={"learner_id": clean_store, "api_key": "AIzaTestKey1234567890"})
    r = client.put("/provider-credentials/gemini", json={"learner_id": clean_store, "pricing_tier": "free"})
    assert r.status_code == 200
    body = r.json()
    assert body["pricing_tier"] == "free" and body["masked"] == "•" * 12 + "890" and body["source"] == "encrypted"
    assert credentials.get_api_key(clean_store, "gemini") == "AIzaTestKey1234567890"
    assert credentials.pricing_tier_for(clean_store, "gemini") == "free"
    # tier without any key: allowed (row without ciphertext), still "not connected"
    r = client.put("/provider-credentials/mistral", json={"learner_id": clean_store, "pricing_tier": "paid"})
    assert r.status_code == 200 and r.json()["connected"] is False and r.json()["pricing_tier"] == "paid"


def test_put_validation_errors_are_german_400_without_key(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    r = client.put("/provider-credentials/openai", json={"learner_id": clean_store})
    assert r.status_code == 400 and "Schlüssel" in r.json()["detail"]
    r = client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": "sk-1"})
    assert r.status_code == 400 and "unvollständig" in r.json()["detail"]
    r = client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": "sk-has space 12345"})
    assert r.status_code == 400 and "sk-has" not in r.text
    r = client.put(
        "/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A, "pricing_tier": "x"}
    )
    assert r.status_code == 422
    assert_no_key(r.json(), KEY_A)


def test_422_never_echoes_the_submitted_key(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    # wrong type for api_key → error located at body.api_key → input stripped
    r = client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": [KEY_A]})
    assert r.status_code == 422
    assert_no_key(r.json(), KEY_A)
    # error on a sibling field → the whole body would be echoed as `input` → api_key masked
    r = client.put("/provider-credentials/openai", json={"learner_id": 123, "api_key": KEY_A})
    assert r.status_code == 422
    assert_no_key(r.json(), KEY_A)
    # missing body entirely
    r = client.put("/provider-credentials/openai", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


# ------------------------------------------------------------------ delete / migrate


def test_delete_removes_ciphertext_and_env_line(client, clean_store: str, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    env_path = data_dir() / ".env"
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    env_key = "sk-env-fallback-abcdefghijklmnop"
    env_path.write_text(before + f"OPENAI_API_KEY={env_key}\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", env_key)
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    r = client.delete("/provider-credentials/openai", params={"learner_id": clean_store})
    assert r.status_code == 200 and r.json() == {"deleted": True}
    with db_session() as db:
        assert (
            db.execute(
                select(func.count())
                .select_from(ProviderCredential)
                .where(ProviderCredential.learner_id == clean_store, ProviderCredential.provider == "openai")
            ).scalar_one()
            == 0
        )
    text = env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in text and env_key not in text
    assert "CREDENTIAL_ENCRYPTION_KEY=" in text  # the encryption key itself stays
    assert os.environ.get("OPENAI_API_KEY") in (None, "")
    assert credentials.api_key_for(clean_store, "openai") is None
    r = client.get("/provider-credentials", params={"learner_id": clean_store})
    assert next(i for i in r.json() if i["provider"] == "openai")["connected"] is False
    assert client.delete("/provider-credentials/openai", params={"learner_id": clean_store}).json() == {
        "deleted": False
    }


def test_migrate_env_keys_imports_and_removes_lines(clean_store: str, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = data_dir() / ".env"
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    gem = "AIzaMigrateMe0123456789"
    env_path.write_text(before + f'GEMINI_API_KEY="{gem}"\nMISTRAL_API_KEY=\n', encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", gem)
    migrated = credentials.migrate_env_keys(clean_store)
    assert migrated == ["gemini"]
    text = env_path.read_text(encoding="utf-8")
    assert gem not in text and "GEMINI_API_KEY" not in text
    assert credentials.get_api_key(clean_store, "gemini") == gem
    view = credentials.get_view(clean_store, "gemini")
    assert view.source == "encrypted" and view.masked == "•" * 12 + gem[-3:]
    assert credentials.migrate_env_keys(clean_store) == []  # idempotent
    env_path.write_text(text.replace("MISTRAL_API_KEY=\n", ""), encoding="utf-8")


# ------------------------------------------------------------------ connection test


def test_connection_test_401_gives_german_message_and_one_error_event(  # type: ignore[no-untyped-def]
    client, clean_store: str, transport: None
) -> None:
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    _mock(401, {"error": {"message": f"Incorrect API key provided: {KEY_A}"}})
    n0 = _event_count(clean_store)
    r = client.post("/provider-credentials/openai/test", json={"learner_id": clean_store})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message_de": provider_test.MSG_AUTH}
    assert _event_count(clean_store) == n0 + 1
    ev = _test_events(clean_store)[-1]
    assert ev.outcome == "error" and ev.service == "other" and ev.provider == "openai"
    assert ev.cost_status == "unknown" and ev.expected_cost_eur is None
    assert ev.meta["http_status"] == 401 and ev.meta["category"] == "auth"
    assert KEY_A not in json.dumps(ev.meta)
    view = credentials.get_view(clean_store, "openai")
    assert view.last_test_ok is False and view.last_test_message == provider_test.MSG_AUTH
    assert view.last_test_at is not None


def test_connection_test_success_records_exactly_one_free_event(  # type: ignore[no-untyped-def]
    client, clean_store: str, transport: None
) -> None:
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    seen: list[httpx.Request] = []
    _mock(200, {"object": "list", "data": [{"id": "gpt-x"}]}, seen)
    n0 = _event_count(clean_store)
    r = client.post("/provider-credentials/openai/test", json={"learner_id": clean_store})
    assert r.json() == {"ok": True, "message_de": "OpenAI verbunden"}
    assert _event_count(clean_store) == n0 + 1
    ev = _test_events(clean_store)[-1]
    assert ev.outcome == "ok" and ev.model == "openai/models" and ev.operation == "connection_test"
    assert ev.cost_status == "free" and ev.expected_cost_eur == 0.0 and ev.list_cost_eur == 0.0
    assert ev.meta.get("free_call") is True and ev.local is False
    assert seen[0].headers["authorization"] == f"Bearer {KEY_A}" and seen[0].url.host == "api.openai.com"
    assert "key" not in str(seen[0].url)
    view = credentials.get_view(clean_store, "openai")
    assert view.last_test_ok is True and view.last_test_message == "OpenAI verbunden"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, provider_test.MSG_AUTH),
        (429, provider_test.MSG_RATE),
        (500, "Der Anbieter hat mit einem Fehler geantwortet (Code 500)."),
        (503, "Der Anbieter hat mit einem Fehler geantwortet (Code 503)."),
    ],
)
def test_connection_test_status_mapping(  # type: ignore[no-untyped-def]
    client, clean_store: str, transport: None, status: int, expected: str
) -> None:
    client.put("/provider-credentials/anthropic", json={"learner_id": clean_store, "api_key": "sk-ant-test-0123456789"})
    _mock(status, {"error": "x"})
    r = client.post("/provider-credentials/anthropic/test", json={"learner_id": clean_store})
    assert r.json() == {"ok": False, "message_de": expected}


def test_connection_test_network_error(client, clean_store: str, transport: None) -> None:  # type: ignore[no-untyped-def]
    client.put("/provider-credentials/mistral", json={"learner_id": clean_store, "api_key": "mistral-key-0123456789"})

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    provider_test._transport = httpx.MockTransport(handler)
    n0 = _event_count(clean_store)
    r = client.post("/provider-credentials/mistral/test", json={"learner_id": clean_store})
    assert r.json() == {"ok": False, "message_de": provider_test.MSG_NETWORK}
    assert _event_count(clean_store) == n0 + 1 and _test_events(clean_store)[-1].outcome == "error"


def test_connection_test_without_key_sends_nothing(client, clean_store: str, transport: None) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    _mock(200, {"data": []}, seen)
    n0 = _event_count(clean_store)
    r = client.post("/provider-credentials/gemini/test", json={"learner_id": clean_store})
    assert r.json()["ok"] is False and "kein Schlüssel" in r.json()["message_de"]
    assert seen == [] and _event_count(clean_store) == n0


def test_gemini_key_goes_into_a_header_not_the_url(client, clean_store: str, transport: None) -> None:  # type: ignore[no-untyped-def]
    gem = "AIzaHeaderOnly0123456789"
    client.put("/provider-credentials/gemini", json={"learner_id": clean_store, "api_key": gem})
    seen: list[httpx.Request] = []
    _mock(200, {"models": []}, seen)
    r = client.post("/provider-credentials/gemini/test", json={"learner_id": clean_store})
    assert r.json() == {"ok": True, "message_de": "Google Gemini verbunden"}
    assert seen[0].headers["x-goog-api-key"] == gem and gem not in str(seen[0].url)


def test_openai_falls_back_to_one_token_completion_when_listing_unavailable(  # type: ignore[no-untyped-def]
    client, clean_store: str, transport: None
) -> None:
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(404, json={"error": "gone"})
        return httpx.Response(200, json={"usage": {"prompt_tokens": 3, "completion_tokens": 1}})

    provider_test._transport = httpx.MockTransport(handler)
    n0 = _event_count(clean_store)
    r = client.post("/provider-credentials/openai/test", json={"learner_id": clean_store})
    assert r.json()["ok"] is True
    assert [q.url.path for q in seen] == ["/v1/models", "/v1/chat/completions"]
    assert json.loads(seen[1].content)["max_tokens"] == 1
    assert _event_count(clean_store) == n0 + 1
    ev = _test_events(clean_store)[-1]
    assert ev.model == provider_test.cheapest_model_for("openai") and ev.input_tokens == 3 and ev.output_tokens == 1
    assert ev.cost_status in ("estimated", "unknown") and not ev.meta.get("free_call")


def test_legacy_settings_test_path_produces_exactly_one_event(  # type: ignore[no-untyped-def]
    client, clean_store: str, transport: None
) -> None:
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    _mock(200, {"data": []})
    n0 = _event_count(clean_store)
    r = client.post("/settings/test", json={"provider": "openai"})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["detail"] == "OpenAI verbunden"
    assert _event_count(clean_store) == n0 + 1
    assert client.post("/settings/test", json={"provider": "nope"}).status_code == 400


# ------------------------------------------------------------------ legacy /settings


def test_settings_put_rejects_provider_keys(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    r = client.put("/settings", json={"values": {"OPENAI_API_KEY": KEY_A}})
    assert r.status_code == 400
    assert r.json()["detail"] == "Schlüssel werden jetzt verschlüsselt gespeichert: Einstellungen → AI-Anbieter"
    assert KEY_A not in r.text
    env_path = data_dir() / ".env"
    assert KEY_A not in (env_path.read_text(encoding="utf-8") if env_path.exists() else "")
    assert credentials.api_key_for(clean_store, "openai") is None
    # non-secret settings still work
    r = client.put("/settings", json={"values": {"DEFAULT_LEVEL": "C1"}})
    assert r.status_code == 200 and r.json()["default_level"] == "C1"
    client.put("/settings", json={"values": {"DEFAULT_LEVEL": None}})


def test_write_env_file_guard() -> None:
    from app.api.settings import write_env_file

    with pytest.raises(ValueError):
        write_env_file({"MISTRAL_API_KEY": "x" * 20})


def test_settings_get_shows_masked_secrets_and_configured_providers(client, clean_store: str) -> None:  # type: ignore[no-untyped-def]
    assert get_settings().default_learner_id == clean_store
    r = client.get("/settings")
    assert r.json()["secrets"]["OPENAI_API_KEY"] is None and r.json()["providers_configured"]["openai"] is False
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    r = client.get("/settings")
    body = r.json()
    assert body["secrets"]["OPENAI_API_KEY"] == "•" * 12 + KEY_A[-3:]
    assert body["providers_configured"]["openai"] is True  # via credentials.has_key, no env var set
    assert body["credentials"][0]["provider"] == "openai" and body["credentials"][0]["source"] == "encrypted"
    assert_no_key(body, KEY_A)
    assert get_settings().configured_providers()["openai"] is True
    assert credentials.has_key(clean_store, "openai") and not credentials.has_key(LEARNER_B, "openai")
    r = client.get("/health")
    assert r.json()["providers_configured"]["openai"] is True and KEY_A not in r.text


# ------------------------------------------------------------------ logs & docs


def test_no_key_in_any_log_record(  # type: ignore[no-untyped-def]
    client, clean_store: str, transport: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client.put("/provider-credentials/openai", json={"learner_id": clean_store, "api_key": KEY_A})
    _mock(401, {"error": {"message": f"bad key {KEY_A}"}})
    client.post("/provider-credentials/openai/test", json={"learner_id": clean_store})
    _mock(200, {"data": []})
    client.post("/provider-credentials/openai/test", json={"learner_id": clean_store})
    client.get("/provider-credentials", params={"learner_id": clean_store})
    client.delete("/provider-credentials/openai", params={"learner_id": clean_store})
    assert caplog.records, "expected log records (httpx + app)"
    for rec in caplog.records:
        msg = rec.getMessage()
        assert KEY_A not in msg, f"key leaked into log record: {rec.name}"
        assert KEY_A not in str(rec.args)
    assert any("connection test provider=openai status=401 category=auth" in r.getMessage() for r in caplog.records)


def test_env_example_documents_credential_encryption_key() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / ".env.example").read_text(encoding="utf-8")
    assert "CREDENTIAL_ENCRYPTION_KEY=" in text
