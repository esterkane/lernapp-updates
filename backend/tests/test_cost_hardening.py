"""Regression checks for unknown usage and historical price boundaries."""

from datetime import UTC, datetime

from app.core.pricing import PriceEntry, PricingTable


def table() -> PricingTable:
    return PricingTable(
        version="test",
        fx_usd_eur_default=0.86,
        llm={"example": PriceEntry(source="fixture", input=1, output=2, valid_from="2026-01-01")},
        stt={},
        tts={},
        pronunciation={},
        embedding={},
    )


def test_missing_usage_is_unknown() -> None:
    quote = table().quote("llm", "example", {}, pricing_tier="paid")
    assert quote.cost_status == "unknown"
    assert quote.expected_usd is None


def test_price_before_first_valid_date_is_unknown() -> None:
    quote = table().quote("llm", "example", {"tokens_in": 10}, at=datetime(2025, 1, 1, tzinfo=UTC))
    assert quote.cost_status == "unknown"
    assert quote.expected_usd is None


def test_explicit_zero_usage_remains_zero() -> None:
    quote = table().quote("llm", "example", {"tokens_in": 0, "tokens_out": 0}, pricing_tier="paid")
    assert quote.cost_status == "estimated"
    assert quote.expected_usd == 0


def test_provider_missing_fields_remain_unknown() -> None:
    from types import SimpleNamespace

    from app.services.llm import _usage_fields

    fields = _usage_fields(SimpleNamespace(id="test", usage=None))
    assert fields["tokens_in"] is None
    assert fields["tokens_out"] is None


def test_request_ids_are_scoped_to_the_credential_owner() -> None:
    from app.core.usage import UsageDraft, _idempotency_key

    now = datetime.now(UTC)
    draft = UsageDraft(
        provider="openai", stage="llm", model="example", learner_id="a", session_id=None, request_id="same"
    )
    assert _idempotency_key(draft, now) != _idempotency_key(draft.model_copy(update={"learner_id": "b"}), now)


def test_event_and_units_roll_back_together(monkeypatch) -> None:
    import pytest
    from app.core.db import db_session
    from app.core.usage import UsageDraft, record_usage
    from app.db.base import CostLedger, UsageEvent
    from sqlalchemy import event, select

    def fail(*args):
        raise RuntimeError("simulated unit failure")

    event.listen(CostLedger, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            record_usage(
                UsageDraft(
                    provider="local",
                    stage="llm",
                    model="test",
                    learner_id=None,
                    session_id=None,
                    local=True,
                    input_tokens=1,
                    idempotency_key="atomic-regression",
                )
            )
    finally:
        event.remove(CostLedger, "before_insert", fail)
    with db_session() as db:
        assert db.execute(select(UsageEvent).where(UsageEvent.idempotency_key == "atomic-regression")).first() is None


def test_reasoning_included_in_output_is_not_billed_twice() -> None:
    from app.core.usage import UsageDraft

    draft = UsageDraft(
        provider="openai",
        stage="llm",
        model="example",
        learner_id=None,
        session_id=None,
        input_tokens=3,
        output_tokens=5,
        reasoning_tokens=10,
    )
    assert "tokens_reasoning" not in draft.units()
    assert draft.model_copy(update={"meta": {"reasoning_excluded_from_output": True}}).units()["tokens_reasoning"] == 10


def test_audio_recording_overlaps_inter_turn_gap() -> None:
    from datetime import timedelta

    from app.services.learning_time import learning_increment

    now = datetime.now(UTC)
    assert learning_increment(now - timedelta(seconds=20), now, now, 15) == 20


def test_retry_attempts_are_observable(monkeypatch) -> None:
    import litellm
    from app.services import llm

    class Transient(Exception):
        status_code = 429

    attempts = []

    def complete(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise Transient()
        return "ok"

    monkeypatch.setattr(litellm, "completion", complete)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    retries = []
    assert llm._call_litellm("example", [], on_retry=lambda attempt, status: retries.append((attempt, status))) == "ok"
    assert retries == [(1, 429), (2, 429)]


def test_historical_rule_does_not_inherit_current_rates() -> None:
    pricing = table()
    pricing.llm["example"].history = [{"valid_from": "2025-01-01", "valid_to": "2026-01-01", "input": 0.5}]
    quote = pricing.quote("llm", "example", {"tokens_out": 100}, at=datetime(2025, 6, 1, tzinfo=UTC))
    assert quote.cost_status == "unknown"


def test_raw_usage_discards_text_values() -> None:
    from app.core.usage import _sanitize_raw

    assert _sanitize_raw({"prompt": "private text", "authorization": "secret", "prompt_tokens": 12}) == {
        "prompt_tokens": 12
    }
