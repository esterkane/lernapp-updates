"""Phase 3 (ADR-0019): ``/usage/*`` — the contract in docs/api.md "Cost tracking v2", per-learner scoped."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.core import credentials, usage
from app.core.db import db_session
from app.db.base import Learner, Session, UsageEvent
from app.services import usage_report

MONTH = "2026-03"
T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def _learner(lid: str) -> str:
    with db_session() as db:
        if db.get(Learner, lid) is None:
            db.add(Learner(id=lid, display_name="Test", level="B2"))
    return lid


def _event(lid: str, **kw: Any) -> usage.RecordedUsage:
    base: dict[str, Any] = {
        "provider": "openai",
        "stage": "llm",
        "model": "openai/gpt-5.6-luna",
        "learner_id": lid,
        "session_id": f"sum-{lid}",
        "tier_name": "conversation",
        "ts": T0,
    }
    base.update(kw)
    return usage.record_usage(usage.UsageDraft(**base))


@pytest.fixture()
def seeded() -> str:
    """Six events in March 2026 for one learner: 2 paid OpenAI, 1 free-tier Gemini, 1 local STT, 1 unpriced TTS, 1 failed."""
    lid = _learner("sum-learner")
    credentials.set_pricing_tier(lid, "openai", "paid")
    credentials.set_pricing_tier(lid, "gemini", "free")
    with db_session() as db:
        for e in db.execute(select_events(lid)).scalars():
            db.delete(e)
    _event(lid, request_id="s1", input_tokens=1_000_000, output_tokens=0)  # 0.20 USD list
    _event(lid, request_id="s2", input_tokens=0, output_tokens=1_000_000, tier_name="assessment", service="evaluation")
    _event(lid, provider="gemini", model="gemini/gemini-3.1-flash-lite", request_id="g1", input_tokens=1_000_000)
    _event(
        lid,
        provider="local",
        stage="stt",
        model="local/faster-whisper",
        audio_input_seconds=30,
        local=True,
        ts=T0 + timedelta(seconds=1),
    )
    _event(lid, provider="acme", stage="tts", model="acme/unknown-voice", characters=100, ts=T0 + timedelta(seconds=2))
    _event(
        lid,
        provider="anthropic",
        model="anthropic/claude-haiku-4-5",
        tier_name="validator",
        outcome="error",
        ts=T0 + timedelta(seconds=3),
    )
    return lid


def select_events(lid: str) -> Any:
    from sqlalchemy import select

    return select(UsageEvent).where(UsageEvent.learner_id == lid)


def test_month_bounds_and_label() -> None:
    start, end, label = usage_report.month_bounds("2026-12")
    assert (start.year, start.month, start.day) == (2026, 12, 1) and end == datetime(2027, 1, 1, tzinfo=UTC)
    assert label == "Dezember 2026"
    with pytest.raises(ValueError):
        usage_report.month_bounds("2026-13")
    assert usage_report.format_eur_de(1234.5) == "1.234,50"


def test_summary_counts_expected_vs_list_cost(seeded: str) -> None:
    s = usage_report.summary(seeded, MONTH)
    fx = s["fx_rate"]
    assert s["period"]["label_de"] == "März 2026" and s["period"]["from"].startswith("2026-03-01")
    assert s["requests"] == {"total": 6, "priced": 4, "free": 2, "unknown": 1, "failed": 1, "usage_estimated": 0}
    # expected: only the two paid OpenAI calls (0.20 + 1.20 USD); the free-tier Gemini call is expected 0
    assert s["expected_cost_eur"] == pytest.approx(1.40 * fx, rel=1e-6)
    # list: OpenAI 1.40 + Gemini 0.25 USD (the unpriced acme call and the failed call add nothing)
    assert s["list_cost_eur"] == pytest.approx(1.65 * fx, rel=1e-6)
    prov = {p["provider"]: p for p in s["providers"]}
    assert prov["gemini"]["pricing_tier"] == "free" and prov["gemini"]["free_tier_label_de"] == "Kostenlose Stufe"
    assert prov["gemini"]["expected_eur"] == 0.0 and prov["gemini"]["list_eur"] == pytest.approx(0.25 * fx)
    assert prov["openai"]["pricing_tier"] == "paid" and prov["openai"]["free_tier_label_de"] is None
    assert prov["openai"]["label_de"] == "OpenAI" and prov["openai"]["requests"] == 2
    assert prov["acme"]["unknown"] == 1 and prov["local"]["pricing_tier"] == "free"
    assert prov["anthropic"]["requests"] == 1 and prov["anthropic"]["expected_eur"] == 0.0
    svc = {x["service"]: x for x in s["services"]}
    assert svc["llm"]["label_de"] == "Sprachmodell" and svc["evaluation"]["label_de"] == "Bewertung"
    assert svc["stt"]["label_de"] == "Spracherkennung" and svc["tts"]["label_de"] == "Stimme"
    models = {(m["provider"], m["model"]): m for m in s["models"]}
    assert models[("openai", "openai/gpt-5.6-luna")]["input_tokens"] == 1_000_000
    assert models[("local", "local/faster-whisper")]["audio_seconds"] == 30.0
    assert models[("acme", "acme/unknown-voice")]["characters"] == 100
    assert s["unknown_models"] == [
        {"provider": "acme", "model": "acme/unknown-voice", "service": "tts", "requests": 1, "missing_units": ["chars"]}
    ]
    assert s["status_note_de"] == (
        "4 von 6 Anfragen vollständig berechnet. 1 Anfrage hat noch keine Preisinformation. "
        "1 Anfrage ist fehlgeschlagen und wird nicht als Kosten gezählt."
    )
    assert s["pricing_version"] and s["fx_rate"] > 0


def test_summary_rate_needs_enough_learning_time(seeded: str) -> None:
    s = usage_report.summary(seeded, MONTH)
    assert s["learning_seconds"] == 0.0 and s["cost_per_learning_hour_eur"] is None
    assert s["rate_available"] is False and s["rate_hint_de"] == "Noch nicht genügend Daten"
    with db_session() as db:
        db.add(Session(learner_id=seeded, kind="tutor", started_at=T0, duration_seconds=1800.0))
    s = usage_report.summary(seeded, MONTH)
    assert s["learning_seconds"] == 1800.0 and s["rate_available"] is True and s["rate_hint_de"] is None
    assert s["cost_per_learning_hour_eur"] == pytest.approx(s["expected_cost_eur"] / 0.5, rel=1e-6)
    short = _learner("sum-short")
    with db_session() as db:
        db.add(Session(learner_id=short, kind="tutor", started_at=T0, duration_seconds=120.0))
    assert usage_report.summary(short, MONTH)["cost_per_learning_hour_eur"] is None  # < 300 s


def test_summary_is_scoped_per_learner(seeded: str) -> None:
    other = _learner("sum-other")
    s = usage_report.summary(other, MONTH)
    assert s["requests"]["total"] == 0 and s["expected_cost_eur"] == 0.0
    assert s["status_note_de"] == "Noch keine Anfragen in diesem Zeitraum."
    assert usage_report.list_events(other, from_=T0 - timedelta(days=1), to=T0 + timedelta(days=1))["total"] == 0


def test_summary_budget_block_and_warning_levels(seeded: str) -> None:
    s = usage_report.summary(seeded, MONTH)
    assert s["budget"] == {
        "monthly_budget_eur": None,
        "used_eur": s["expected_cost_eur"],
        "share": None,
        "warning_level": "none",
        "stop_on_limit": False,
    }
    used = s["expected_cost_eur"]
    for budget, level in (
        (used * 2, "none"),
        (used / 0.85, "80"),
        (used / 0.97, "95"),
        (used, "100"),
        (used / 2, "100"),
    ):
        usage_report.set_budget_settings(seeded, budget, False)
        assert usage_report.summary(seeded, MONTH)["budget"]["warning_level"] == level, budget
    usage_report.set_budget_settings(seeded, None, False)


# ---------------------------------------------------------------- HTTP


def test_usage_summary_endpoint(client, seeded: str) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/usage/summary", params={"learner_id": seeded, "month": MONTH})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {
        "period", "expected_cost_eur", "list_cost_eur", "learning_seconds", "cost_per_learning_hour_eur", "rate_available",
        "rate_hint_de", "budget", "requests", "providers", "services", "models", "unknown_models", "status_note_de",
        "pricing_version", "fx_rate",
    }  # fmt: skip
    assert body["requests"]["total"] == 6
    assert client.get("/usage/summary", params={"learner_id": seeded, "month": "2026-99"}).status_code == 400
    # default month = the current one; default learner = the install's default learner
    r = client.get("/usage/summary")
    assert r.status_code == 200 and r.json()["period"]["from"].startswith(datetime.now(UTC).strftime("%Y-%m-01"))


def test_usage_events_endpoint_filters_and_paginates(client, seeded: str) -> None:  # type: ignore[no-untyped-def]
    p = {"learner_id": seeded, "from": "2026-03-01", "to": "2026-03-31"}
    r = client.get("/usage/events", params=p)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 6 and len(body["items"]) == 6
    item = body["items"][0]
    assert set(item) >= {
        "id", "ts", "session_id", "provider", "service", "model", "tier_name", "outcome", "input_tokens", "cached_input_tokens",
        "output_tokens", "reasoning_tokens", "audio_input_seconds", "audio_output_seconds", "characters", "pricing_tier",
        "cost_status", "expected_cost_eur", "list_cost_eur", "pricing_rule_id", "pricing_version", "fx_rate", "meta", "raw_usage",
    }  # fmt: skip
    assert "messages" not in item and "content" not in item and "text" not in item
    assert client.get("/usage/events", params={**p, "provider": "openai"}).json()["total"] == 2
    assert client.get("/usage/events", params={**p, "service": "evaluation"}).json()["total"] == 1
    assert client.get("/usage/events", params={**p, "cost_status": "unknown"}).json()["total"] == 2  # unpriced + failed
    assert client.get("/usage/events", params={**p, "outcome": "error"}).json()["total"] == 1
    assert client.get("/usage/events", params={**p, "session_id": f"sum-{seeded}"}).json()["total"] == 6
    page = client.get("/usage/events", params={**p, "limit": 2, "offset": 4}).json()
    assert page["total"] == 6 and len(page["items"]) == 2
    assert client.get("/usage/events", params={**p, "learner_id": "sum-other"}).json()["total"] == 0
    assert client.get("/usage/events", params={**p, "from": "gestern"}).status_code == 400


def test_usage_session_endpoint_is_learner_scoped(client, seeded: str) -> None:  # type: ignore[no-untyped-def]
    sid = f"sum-{seeded}"
    r = client.get(f"/usage/session/{sid}", params={"learner_id": seeded})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sid and body["kind"] is None and len(body["events"]) == 6
    assert body["expected_cost_eur"] == pytest.approx(1.40 * body["events"][0]["fx_rate"], rel=1e-6)
    by_prov = {x["provider"]: x["expected_eur"] for x in body["by_provider"]}
    assert set(by_prov) == {"openai", "gemini", "local"} and by_prov["openai"] > 0 and by_prov["gemini"] == 0.0
    by_svc = {x["service"]: x["label_de"] for x in body["by_service"]}
    assert by_svc == {"llm": "Sprachmodell", "evaluation": "Bewertung", "stt": "Spracherkennung"}
    assert client.get(f"/usage/session/{sid}", params={"learner_id": "sum-other"}).status_code == 404
    assert client.get("/usage/session/nope", params={"learner_id": seeded}).status_code == 404
    # a real tutor session of another learner is not visible either
    with db_session() as db:
        s = Session(learner_id=seeded, kind="tutor")
        db.add(s)
        db.flush()
        real = s.id
    assert client.get(f"/usage/session/{real}", params={"learner_id": seeded}).json()["kind"] == "tutor"
    assert client.get(f"/usage/session/{real}", params={"learner_id": "sum-other"}).status_code == 404


def test_usage_settings_roundtrip(client, seeded: str) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/usage/settings", params={"learner_id": seeded})
    assert r.status_code == 200
    assert r.json() == {"monthly_budget_eur": None, "stop_on_limit": False, "warn_levels": [80, 95, 100]}
    r = client.put("/usage/settings", json={"learner_id": seeded, "monthly_budget_eur": 5.5, "stop_on_limit": True})
    assert r.status_code == 200 and r.json() == {
        "monthly_budget_eur": 5.5,
        "stop_on_limit": True,
        "warn_levels": [80, 95, 100],
    }
    assert client.get("/usage/settings", params={"learner_id": seeded}).json()["monthly_budget_eur"] == 5.5
    with db_session() as db:
        learner = db.get(Learner, seeded)
        assert learner is not None and learner.profile == {"monthly_budget_eur": 5.5, "stop_on_limit": True}
    assert client.put("/usage/settings", json={"learner_id": seeded, "monthly_budget_eur": -1}).status_code in (
        400,
        422,
    )
    r = client.put("/usage/settings", json={"learner_id": seeded, "monthly_budget_eur": None, "stop_on_limit": False})
    assert r.json()["monthly_budget_eur"] is None
    assert client.get("/usage/settings", params={"learner_id": "no-such-learner"}).status_code == 404


def test_costs_summary_is_marked_as_list_price(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/costs/summary")
    assert r.status_code == 200 and r.json()["basis"] == "list_price"
