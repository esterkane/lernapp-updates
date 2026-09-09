"""Phase 3 (ADR-0019): provider-reported usage replaces every estimate; one provider call = one usage event.

Fake backends stay local/free; the LiteLLM paths are exercised with monkeypatched provider calls so the
extraction (request id, cached/reasoning tokens, api_key pass-through, streaming usage chunk vs.
token-counter fallback, failure events) is verified offline.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.core import credentials, usage
from app.core.config import get_settings
from app.core.db import db_session, ensure_default_learner
from app.db.base import UsageEvent
from app.services import llm as llm_mod
from app.services.fakes import silent_wav
from sqlalchemy import select


def events_for(session_id: str) -> list[UsageEvent]:
    with db_session() as db:
        return list(
            db.execute(
                select(UsageEvent).where(UsageEvent.session_id == session_id).order_by(UsageEvent.ts, UsageEvent.id)
            ).scalars()
        )


def _resp(
    *,
    rid: str = "chatcmpl-1",
    content: str = "Guten Tag!",
    prompt: int = 120,
    completion: int = 30,
    cached: int | None = 40,
    reasoning: int | None = 12,
) -> Any:
    pd = SimpleNamespace(cached_tokens=cached, audio_tokens=None, text_tokens=None) if cached is not None else None
    cd = SimpleNamespace(reasoning_tokens=reasoning, audio_tokens=None, text_tokens=None) if reasoning else None
    u = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=pd,
        completion_tokens_details=cd,
    )
    return SimpleNamespace(id=rid, usage=u, choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture()
def litellm_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_backend", "litellm")


# ---------------------------------------------------------------- normalisation


def test_extract_llm_usage_normalizes_cached_reasoning_and_request_id() -> None:
    u = usage.extract_llm_usage(_resp())
    assert u["request_id"] == "chatcmpl-1"
    assert (u["input_tokens"], u["output_tokens"], u["total_tokens"]) == (120, 30, 150)
    assert u["cached_input_tokens"] == 40 and u["reasoning_tokens"] == 12
    assert u["raw_usage"]["prompt_tokens_details"]["cached_tokens"] == 40
    assert usage.extract_llm_usage(SimpleNamespace(id=None)) == {"request_id": None}


# ---------------------------------------------------------------- complete()


def test_complete_records_provider_usage_once_per_request_id(
    litellm_backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake_call(model: str, messages: list[dict[str, str]], **kw: Any) -> Any:
        seen.append(kw)
        return _resp(rid="chatcmpl-dedupe")

    monkeypatch.setattr(llm_mod, "_call_litellm", fake_call)
    monkeypatch.setattr(credentials, "api_key_for", lambda learner_id, provider: "sk-test-key-123")
    sid = "p3-complete"
    out = llm_mod.complete(
        "conversation",
        [{"role": "user", "content": "Hallo"}],
        prompt_version="1.0.0",
        session_id=sid,
        learner_id="default",
    )
    assert out == "Guten Tag!"
    evs = events_for(sid)
    assert len(evs) == 1
    e = evs[0]
    assert e.request_id == "chatcmpl-dedupe" and e.outcome == "ok" and e.service == "llm"
    assert (e.input_tokens, e.output_tokens, e.total_tokens) == (120, 30, 150)
    assert e.cached_input_tokens == 40 and e.reasoning_tokens == 12
    assert e.raw_usage["prompt_tokens"] == 120 and e.meta["usage_source"] == "provider"
    assert e.cost_status == "estimated" and e.expected_cost_eur is not None and e.expected_cost_eur > 0
    # the learner's BYOK key reached LiteLLM (and is never stored in the event)
    assert seen[0]["api_key"] == "sk-test-key-123"
    assert "sk-test" not in str(e.raw_usage) + str(e.meta)
    # the same provider request id → duplicate suppressed (still one event)
    llm_mod.complete(
        "conversation",
        [{"role": "user", "content": "Hallo"}],
        prompt_version="1.0.0",
        session_id=sid,
        learner_id="default",
    )
    assert len(events_for(sid)) == 1


def test_complete_without_key_uses_litellm_env_fallback(litellm_backend: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_call(model: str, messages: list[dict[str, str]], **kw: Any) -> Any:
        seen.append(kw)
        return _resp(rid="chatcmpl-nokey")

    monkeypatch.setattr(llm_mod, "_call_litellm", fake_call)
    monkeypatch.setattr(credentials, "api_key_for", lambda learner_id, provider: None)
    llm_mod.complete(
        "conversation",
        [{"role": "user", "content": "Hallo"}],
        prompt_version="1.0.0",
        session_id="p3-nokey",
        learner_id="default",
    )
    assert "api_key" not in seen[0]


def test_assessment_tier_is_service_evaluation() -> None:
    from app.services.schemas import LLMRubricOutput

    llm_mod.complete(
        "assessment",
        [{"role": "user", "content": "<text>Ich bin der Meinung, dass Homeoffice sinnvoll ist.</text>"}],
        LLMRubricOutput,
        prompt_version="1.0.0",
        session_id="p3-eval",
        learner_id="default",
        prompt_name="writing_feedback",
    )
    evs = events_for("p3-eval")
    assert evs and all(e.service == "evaluation" for e in evs)


def test_failed_call_is_an_error_event_not_unpriced_usage(
    litellm_backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise llm_mod.LLMError("provider down")

    monkeypatch.setattr(llm_mod, "_call_litellm", boom)
    with pytest.raises(llm_mod.LLMError):
        llm_mod.complete(
            "conversation",
            [{"role": "user", "content": "Hallo"}],
            prompt_version="1.0.0",
            session_id="p3-fail",
            learner_id="default",
        )
    evs = events_for("p3-fail")
    assert len(evs) == 1
    e = evs[0]
    assert e.outcome == "error" and e.cost_status == "unknown" and e.expected_cost_eur is None
    assert e.meta["error"].startswith("provider down") and e.meta.get("missing_units") is None


# ---------------------------------------------------------------- stream()


def _chunks(text: str, *, with_usage: bool, rid: str = "chatcmpl-stream") -> list[Any]:
    parts = [text[i : i + 5] for i in range(0, len(text), 5)]
    out = [
        SimpleNamespace(id=rid, choices=[SimpleNamespace(delta=SimpleNamespace(content=p))], usage=None) for p in parts
    ]
    if with_usage:
        out.append(
            SimpleNamespace(
                id=rid,
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=77,
                    completion_tokens=9,
                    total_tokens=86,
                    prompt_tokens_details=None,
                    completion_tokens_details=None,
                ),
            )
        )
    return out


def test_stream_uses_the_provider_usage_chunk(litellm_backend: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_call(model: str, messages: list[dict[str, str]], **kw: Any) -> Any:
        seen.append(kw)
        return iter(_chunks("Hallo, wie geht es?", with_usage=True))

    monkeypatch.setattr(llm_mod, "_call_litellm", fake_call)
    text = "".join(
        llm_mod.stream(
            "conversation",
            [{"role": "user", "content": "Hi"}],
            prompt_version="1.0.0",
            session_id="p3-stream",
            learner_id="default",
        )
    )
    assert text == "Hallo, wie geht es?"
    assert seen[0]["stream_options"] == {"include_usage": True}
    (e,) = events_for("p3-stream")
    assert (e.input_tokens, e.output_tokens, e.total_tokens) == (77, 9, 86)
    assert e.request_id == "chatcmpl-stream"
    assert e.meta["usage_source"] == "provider" and not e.meta.get("usage_estimated")


def test_stream_without_usage_chunk_remains_unknown(litellm_backend: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    monkeypatch.setattr(
        llm_mod, "_call_litellm", lambda *a, **k: iter(_chunks("Guten Morgen!", with_usage=False, rid="chatcmpl-est"))
    )
    monkeypatch.setattr(litellm, "token_counter", lambda **kw: 7)
    "".join(
        llm_mod.stream(
            "conversation",
            [{"role": "user", "content": "Hi"}],
            prompt_version="1.0.0",
            session_id="p3-stream-est",
            learner_id="default",
        )
    )
    (e,) = events_for("p3-stream-est")
    assert (e.input_tokens, e.output_tokens) == (None, None) and e.request_id == "chatcmpl-est"
    assert e.meta["usage_source"] == "unavailable"
    assert e.cost_status == "unknown"
    assert e.expected_cost_eur is None
    from app.services import usage_report

    s = usage_report.summary("default")
    assert s["requests"]["unknown"] >= 1


# ---------------------------------------------------------------- STT / TTS / embeddings adapters


def test_cloud_stt_records_audio_seconds_request_id_and_passes_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import litellm
    from app.services.stt import LiteLLMCloudSTT

    wav = tmp_path / "in.wav"
    wav.write_bytes(silent_wav(2.5))
    seen: list[dict[str, Any]] = []

    def fake_transcription(**kw: Any) -> Any:
        seen.append(kw)
        return SimpleNamespace(text="Guten Tag", words=[], id="transcr-42", usage={"type": "duration", "seconds": 3})

    monkeypatch.setattr(litellm, "transcription", fake_transcription)
    monkeypatch.setattr(credentials, "api_key_for", lambda learner_id, provider: "sk-stt-key-999")
    t = LiteLLMCloudSTT("openai").transcribe(wav, session_id="p3-stt", learner_id="default")
    assert t.text == "Guten Tag" and seen[0]["api_key"] == "sk-stt-key-999"
    (e,) = events_for("p3-stt")
    assert (
        e.stage == "stt"
        and e.service == "stt"
        and e.provider == "openai"
        and e.model == "openai/gpt-4o-mini-transcribe"
    )
    assert e.audio_input_seconds is not None and abs(e.audio_input_seconds - 2.5) < 0.05
    assert e.request_id == "transcr-42" and e.raw_usage == {"type": "duration", "seconds": 3}
    assert e.operation == "transcription" and e.list_cost_eur is not None and e.list_cost_eur > 0
    assert "sk-stt" not in str(e.meta) + str(e.raw_usage)


def test_openai_tts_records_characters_and_request_header(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm
    from app.services.tts import OpenAIMiniTTS

    seen: list[dict[str, Any]] = []

    def fake_speech(**kw: Any) -> Any:
        seen.append(kw)
        return SimpleNamespace(content=b"ID3mp3", response=SimpleNamespace(headers={"x-request-id": "req_tts_7"}))

    monkeypatch.setattr(litellm, "speech", fake_speech)
    monkeypatch.setattr(credentials, "api_key_for", lambda learner_id, provider: "sk-tts-key-111")
    text = "Guten Tag, wie geht es Ihnen heute?"
    a = OpenAIMiniTTS().synthesize(text, session_id="p3-tts", learner_id="default")
    assert a.data == b"ID3mp3" and seen[0]["api_key"] == "sk-tts-key-111"
    (e,) = events_for("p3-tts")
    assert e.stage == "tts" and e.characters == len(text) and e.request_id == "req_tts_7"
    assert e.model == "openai/gpt-4o-mini-tts" and e.list_cost_eur is not None and e.list_cost_eur > 0


def test_embeddings_use_provider_usage_and_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm
    from app.services.embeddings import LiteLLMEmbeddings

    seen: list[dict[str, Any]] = []

    def fake_embedding(**kw: Any) -> Any:
        seen.append(kw)
        return SimpleNamespace(
            id="emb-1",
            data=[{"embedding": [0.1, 0.2]} for _ in kw["input"]],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=0, total_tokens=11, prompt_tokens_details=None),
        )

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    monkeypatch.setattr(credentials, "api_key_for", lambda learner_id, provider: "sk-emb-key-222")
    vecs = LiteLLMEmbeddings().embed(["Vertrag", "Verhandlung"], session_id="p3-emb", learner_id="default")
    assert len(vecs) == 2 and seen[0]["api_key"] == "sk-emb-key-222"
    (e,) = events_for("p3-emb")
    assert e.stage == "embed" and e.input_tokens == 11 and e.total_tokens == 11 and e.request_id == "emb-1"
    assert e.meta["usage_source"] == "provider" and e.raw_usage["prompt_tokens"] == 11


# ---------------------------------------------------------------- the request audit (docs/cost-request-audit-2026-09-08.md)


def test_one_voice_turn_is_exactly_six_events(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    """STT + prosody + RAG query embedding + tutor LLM + pronunciation-tip LLM + TTS — nothing double, nothing missing."""
    from app.services import query_cache, rag

    # Six calls require an eligible document and a cold query cache.
    doc = rag.ingest(learner_id, "usage-test.txt", b"Eine Rechnung wird bezahlt.")
    query_cache.clear()
    sid = client.post("/sessions", json={"learner_id": learner_id, "kind": "sprechen"}).json()["session_id"]
    r = client.post(f"/sessions/{sid}/turn", files={"file": ("input.wav", silent_wav(3.0), "audio/wav")})
    assert r.status_code == 200, r.text
    evs = events_for(sid)
    assert sorted(e.stage for e in evs) == ["embed", "llm", "llm", "pron", "stt", "tts"]
    assert all(e.outcome == "ok" for e in evs)
    assert all(e.cost_status == "free" and e.local for e in evs)  # fake backends are local → free
    assert sorted(e.service for e in evs) == ["embedding", "llm", "llm", "pronunciation", "stt", "tts"]
    r = client.get(f"/usage/session/{sid}", params={"learner_id": learner_id})
    assert r.status_code == 200 and r.json()["requests"] == {"total": 6, "failed": 0, "unknown": 0}
    rag.delete_document(doc["document_id"], owner_id=learner_id)


def test_one_connection_test_style_call_is_exactly_one_event() -> None:
    """A settings/provider test is ONE provider call → ONE event (the old meter(requests)+record_llm_usage double is gone)."""
    lid = ensure_default_learner()
    draft = usage.UsageDraft(
        provider="openai",
        stage="llm",
        model="openai/gpt-5.6-luna",
        learner_id=lid,
        session_id="p3-conn-test",
        service="other",
        operation="connection_test",
        request_id="req-conn-1",
        input_tokens=5,
        output_tokens=1,
        raw_usage={"prompt_tokens": 5, "completion_tokens": 1},
    )
    first = usage.record_usage(draft)
    second = usage.record_usage(draft)  # a retry / double write with the same provider request id
    assert first.duplicate is False and second.duplicate is True and second.event_id == first.event_id
    assert len(events_for("p3-conn-test")) == 1
    with db_session() as db:
        rows = db.execute(select(UsageEvent).where(UsageEvent.session_id == "p3-conn-test")).scalars().all()
    assert rows[0].operation == "connection_test" and rows[0].service == "other" and rows[0].outcome == "ok"
