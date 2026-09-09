from types import SimpleNamespace

from app.core import models
from app.services import llm
from pydantic import BaseModel


class Output(BaseModel):
    count: int


def test_truncated_validator_gets_bounded_larger_budget(monkeypatch):
    monkeypatch.setattr(llm.get_settings(), "llm_backend", "litellm")
    monkeypatch.setattr(
        llm, "resolve_tier", lambda tier: models.TierConfig(model="gemini/test", max_tokens=100, repair_max_tokens=200)
    )
    monkeypatch.setattr(llm, "_api_key_kwargs", lambda *a: {})
    calls = []

    def respond(model, messages, **kwargs):
        calls.append(kwargs["max_tokens"])
        return SimpleNamespace(
            id=None,
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
            choices=[
                SimpleNamespace(
                    finish_reason="length" if len(calls) == 1 else "stop",
                    message=SimpleNamespace(content='{"count":' if len(calls) == 1 else '{"count":1}'),
                )
            ],
        )

    monkeypatch.setattr(llm, "_call_litellm", respond)
    result = llm._complete_dispatch(
        "validator",
        [{"role": "user", "content": "test"}],
        Output,
        prompt_version="test",
        session_id="truncated-test",
        learner_id="default",
        temperature=None,
        max_tokens=None,
    )
    assert result.count == 1
    assert calls == [100, 200]
