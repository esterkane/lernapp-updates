"""Workspace model choice and bounded validator fallback chains."""

from types import SimpleNamespace

from app.core import models
from app.services import llm


def test_explicit_validator_choice_is_workspace_scoped(client):
    a = client.post("/workspaces", json={"name": "Validator A"}).json()["id"]
    b = client.post("/workspaces", json={"name": "Validator B"}).json()["id"]
    model = "gemini/gemini-3.1-flash-lite"
    response = client.patch(f"/learners/{a}", json={"validator_model": model}, headers={"X-Lernapp-Workspace": a})
    assert response.status_code == 200, response.text
    assert response.json()["validator_model"] == model
    state = client.get("/settings", headers={"X-Lernapp-Workspace": a}).json()["validator"]
    assert state["selected"] == model and state["active"] == model
    assert client.get("/settings", headers={"X-Lernapp-Workspace": b}).json()["validator"]["selected"] is None
    assert client.patch(f"/learners/{a}", json={"validator_model": "openai/gpt-5.6-terra"}).status_code == 400
    assert client.patch(f"/learners/{a}", json={"validator_model": "made-up-model"}).status_code == 400
    assert client.patch(f"/learners/{a}", json={"validator_model": None}).json()["validator_model"] is None


def test_fallback_reaches_third_candidate_without_revisiting(monkeypatch):
    monkeypatch.setattr(models, "_has_key", lambda provider: provider in {"openai", "gemini"})
    monkeypatch.setattr(llm.get_settings(), "llm_backend", "litellm")
    sequence = ["gemini/gemini-3.1-pro-preview", "gemini/gemini-3.7-flash", "gemini/gemini-3.1-flash-lite"]
    monkeypatch.setattr(models, "validator_candidates", lambda: sequence)
    calls = []

    def completion(model, messages, **kwargs):
        calls.append(model)
        if model != sequence[-1]:
            error = llm.LLMError("unavailable")
            error.status = 404
            raise error
        return SimpleNamespace(
            id="three-candidate-test",
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hallo"))],
        )

    monkeypatch.setattr(llm, "_call_litellm", completion)
    assert (
        llm.complete(
            "validator",
            [{"role": "user", "content": "Hallo"}],
            prompt_version="test",
            session_id="three-model-chain",
            learner_id="default",
        )
        == "Hallo"
    )
    assert calls == sequence
    assert models.recorded_model("validator", "default", "three-model-chain", "wrong-default") == sequence[-1]


def test_fallback_exhaustion_is_bounded(monkeypatch):
    import pytest

    monkeypatch.setattr(models, "_has_key", lambda provider: provider in {"openai", "gemini"})
    monkeypatch.setattr(llm.get_settings(), "llm_backend", "litellm")
    calls = []

    def unavailable(model, messages, **kwargs):
        calls.append(model)
        error = llm.LLMError("quota")
        error.status = 429
        raise error

    monkeypatch.setattr(llm, "_call_litellm", unavailable)
    with pytest.raises(llm.LLMError, match="Zweitmeinung"):
        llm.complete(
            "validator",
            [{"role": "user", "content": "Hallo"}],
            prompt_version="test",
            session_id="all-models-unavailable",
            learner_id="default",
        )
    assert len(calls) == len(set(calls))
    assert len(calls) <= len(models.validator_choices())
