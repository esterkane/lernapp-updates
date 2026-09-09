"""A validator quota error (429) on a free-tier key retries once with the next fallback model."""

from __future__ import annotations

import pytest
from app.core import models as m
from app.services import llm as llm_mod
from app.services.schemas import PronunciationTip

from tests.conftest import ledger_count


def test_429_on_validator_falls_back_to_next_model(monkeypatch: pytest.MonkeyPatch) -> None:
    base = {"openai": True, "gemini": True}
    monkeypatch.setattr(m, "_has_key", lambda vendor: base.get(vendor, False))
    monkeypatch.setattr(llm_mod.get_settings(), "llm_backend", "litellm")
    calls: list[str] = []

    class _Msg:
        content = PronunciationTip(tip_de="ok", practice_words=[]).model_dump_json()

    class _Choice:
        message = _Msg()

    class _Resp:
        id = "resp-fallback-1"
        choices = [_Choice()]
        usage = None

    def fake_call(model: str, messages, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(model)
        if "pro" in model:
            err = llm_mod.LLMError("quota")
            err.status, err.model = 429, model
            raise err
        return _Resp()

    monkeypatch.setattr(llm_mod, "_call_litellm", fake_call)
    before = ledger_count()
    out = llm_mod.complete(
        "validator",
        [{"role": "user", "content": "x"}],
        PronunciationTip,
        prompt_version="1.0.0",
        session_id="t-fb",
        learner_id="default",
    )
    assert out.tip_de == "ok"
    assert calls[0].startswith("gemini/gemini-3.1-pro") and calls[1] != calls[0] and "gemini/" in calls[1]
    assert ledger_count() - before >= 1  # failure row + usage of the successful retry


def test_non_validator_tier_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod.get_settings(), "llm_backend", "litellm")

    def boom(model: str, messages, **kwargs):  # type: ignore[no-untyped-def]
        err = llm_mod.LLMError("quota")
        err.status = 429
        raise err

    monkeypatch.setattr(llm_mod, "_call_litellm", boom)
    with pytest.raises(llm_mod.LLMError):
        llm_mod.complete(
            "conversation",
            [{"role": "user", "content": "x"}],
            prompt_version="1.0.0",
            session_id="t-fb2",
            learner_id="default",
        )
