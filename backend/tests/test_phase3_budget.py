"""Phase 3 (ADR-0019): the ``budget_guard`` pre-hook. Deny → dispatch provably never runs; warning-only allows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.core import credentials, usage
from app.core.db import db_session
from app.core.hooks import HOOKS, SessionState, TierCall, budget_guard, engine
from app.db.base import Learner
from app.services import usage_report


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, call: TierCall) -> str:
        self.calls += 1
        return "ok"


def _learner(lid: str) -> str:
    with db_session() as db:
        if db.get(Learner, lid) is None:
            db.add(Learner(id=lid, display_name="Budget", level="B2"))
    credentials.set_pricing_tier(lid, "openai", "paid")
    return lid


def _spend(lid: str, usd_list: float = 2.0) -> None:
    """One paid OpenAI call this month worth ``usd_list`` at list price (gpt-5.6-terra input = 2 USD / 1M)."""
    usage.record_usage(
        usage.UsageDraft(
            provider="openai",
            stage="llm",
            model="openai/gpt-5.6-terra",
            learner_id=lid,
            session_id=f"budget-{lid}",
            input_tokens=int(usd_list / 2.0 * 1_000_000),
            output_tokens=0,  # Explicitly reported zero, not missing usage.
            ts=datetime.now(UTC),
        )
    )
    usage_report.invalidate_budget_cache(lid)


def _call(lid: str, **kw: Any) -> TierCall:
    base: dict[str, Any] = {
        "kind": "llm",
        "tier": "conversation",
        "model": "openai/gpt-5.6-luna",
        "prompt_name": "tutor_system",
        "prompt_version": "1.0.0",
        "session_id": "s-budget",
        "learner_id": lid,
    }
    base.update(kw)
    return TierCall(**base)


def test_budget_guard_is_registered_as_pre_hook() -> None:
    assert HOOKS["budget_guard"]["kind"] == "pre" and "budget_guard" in engine.pre_hooks
    assert "ADR-0019" in HOOKS["budget_guard"]["rule"]


def test_stop_on_limit_denies_and_dispatch_never_runs() -> None:
    lid = _learner("budget-stop")
    usage_report.set_budget_settings(lid, 1.0, True)
    _spend(lid, usd_list=2.0)  # ≈ 1.72 € expected > 1.00 € budget
    for kind, extra in (
        ("llm", {}),
        ("stt", {"tier": "stt", "model": "openai/gpt-4o-mini-transcribe", "payload_summary": {"backend": "openai"}}),
        ("tts", {"tier": "tts", "model": "openai/gpt-4o-mini-tts", "payload_summary": {"backend": "openai_mini_tts"}}),
        ("embed", {"tier": "embedding", "model": "openai/text-embedding-3-small"}),
    ):
        counter = Counter()
        res = engine.execute(_call(lid, kind=kind, **extra), SessionState(), counter)
        assert counter.calls == 0, kind
        assert not res.success and res.error is not None and res.error.category == "business"
        assert "budget_guard" in res.error.message and "Monatslimit erreicht (" in res.error.message
        assert "von 1,00 €" in res.error.message
        assert "Erhöhe das Limit unter Kosten oder schalte den Stopp aus." in res.error.message
    usage_report.set_budget_settings(lid, None, False)


def test_warning_only_is_the_default_and_allows() -> None:
    lid = _learner("budget-warn")
    usage_report.set_budget_settings(lid, 1.0, False)  # limit set, stop off
    _spend(lid, usd_list=2.0)
    counter = Counter()
    res = engine.execute(_call(lid), SessionState(), counter)
    assert counter.calls == 1 and res.success
    assert usage_report.budget_state(lid).warning_level == "100"
    # no budget at all → allow, whatever was spent
    usage_report.set_budget_settings(lid, None, True)
    counter = Counter()
    assert engine.execute(_call(lid), SessionState(), counter).success and counter.calls == 1


def test_below_the_limit_allows_even_with_stop_on() -> None:
    lid = _learner("budget-below")
    usage_report.set_budget_settings(lid, 100.0, True)
    _spend(lid, usd_list=2.0)
    counter = Counter()
    assert engine.execute(_call(lid), SessionState(), counter).success and counter.calls == 1
    assert budget_guard(_call(lid), SessionState()).action == "allow"


def test_local_backends_and_rag_are_never_blocked() -> None:
    lid = _learner("budget-local")
    usage_report.set_budget_settings(lid, 0.5, True)
    _spend(lid, usd_list=2.0)
    assert (
        budget_guard(_call(lid, kind="stt", tier="stt", model="local/faster-whisper"), SessionState()).action == "allow"
    )
    assert (
        budget_guard(
            _call(lid, kind="tts", tier="tts", model="google/wavenet-de", payload_summary={"backend": "fake"}),
            SessionState(),
        ).action
        == "allow"
    )
    assert (
        budget_guard(_call(lid, kind="rag", tier="rag", model="hybrid", owner_id=lid), SessionState()).action == "allow"
    )
    assert budget_guard(_call(lid, learner_id=None), SessionState()).action == "allow"  # ledger_required handles that
    assert budget_guard(_call(lid), SessionState()).action == "deny"


def test_budget_state_is_cached_for_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    lid = _learner("budget-cache")
    usage_report.invalidate_budget_cache(lid)
    hits: list[str] = []
    real = usage_report.expected_cost_eur

    def counting(learner_id: str | None, start: datetime, end: datetime) -> float:
        hits.append(str(learner_id))
        return real(learner_id, start, end)

    monkeypatch.setattr(usage_report, "expected_cost_eur", counting)
    usage_report.budget_state(lid)
    usage_report.budget_state(lid)
    usage_report.budget_state(lid)
    assert hits == [lid]  # one DB aggregate, then cache
    usage_report.budget_state(lid, max_age=0.0)
    assert hits == [lid, lid]
    usage_report.set_budget_settings(lid, 3.0, False)  # PUT invalidates → next read hits the DB
    usage_report.budget_state(lid)
    assert hits == [lid, lid, lid] and usage_report.BUDGET_CACHE_TTL_SECONDS == 30.0


def test_http_turn_is_400_business_when_stopped(client) -> None:  # type: ignore[no-untyped-def]
    lid = _learner("budget-http")
    usage_report.set_budget_settings(lid, 1.0, True)
    _spend(lid, usd_list=2.0)
    sid = client.post("/sessions", json={"learner_id": lid, "kind": "tutor"}).json()["session_id"]
    r = client.post(f"/sessions/{sid}/turn", json={"text": "Hallo"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error_category"] == "business" and "Monatslimit erreicht" in body["detail"]
    # raising the limit lifts the stop immediately (cache invalidated by PUT)
    r = client.put("/usage/settings", json={"learner_id": lid, "monthly_budget_eur": 50.0, "stop_on_limit": True})
    assert r.status_code == 200
    assert client.post(f"/sessions/{sid}/turn", json={"text": "Hallo"}).status_code == 200
    usage_report.set_budget_settings(lid, None, False)
