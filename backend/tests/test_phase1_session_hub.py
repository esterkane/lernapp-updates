"""Session hub (learnings §1 hub-and-spoke, errors as values): STT failure is fatal (502), prosody and TTS
failures are tolerated and reported in ``partial_failures``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.core.errors import FailureContext
from app.services import pronunciation, stt, tts
from app.services.fakes import silent_wav
from app.services.schemas import AudioBytes, Transcript
from app.services.stt import FakeSTT
from app.services.tts import FakeTTS


def _boom(*a: Any, **k: Any) -> Any:
    raise RuntimeError("backend down")


def _session(client, kind: str = "sprechen") -> str:  # type: ignore[no-untyped-def]
    return str(client.post("/sessions", json={"kind": kind}).json()["session_id"])


def test_safe_adapters_return_failure_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(silent_wav(1.0))
    ok = stt.transcribe_safe(wav, session_id="s", learner_id="l")
    assert isinstance(ok, Transcript) and ok.text
    monkeypatch.setattr(FakeSTT, "transcribe", _boom)
    failed = stt.transcribe_safe(wav, session_id="s", learner_id="l")
    assert isinstance(failed, FailureContext)
    assert failed.category == "transient" and "STT_BACKEND=openai" in failed.alternatives
    assert "Spracherkennung" in failed.message

    spoken = tts.synthesize_safe("Hallo", session_id="s", learner_id="l")
    assert isinstance(spoken, AudioBytes) and spoken.data
    monkeypatch.setattr(FakeTTS, "synthesize", _boom)
    failed_tts = tts.synthesize_safe("Hallo", session_id="s", learner_id="l")
    assert isinstance(failed_tts, FailureContext) and failed_tts.category == "transient"
    assert any(alt.startswith("TTS_BACKEND=") for alt in failed_tts.alternatives)


def test_hook_denial_is_a_business_failure_context(tmp_path: Path) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(silent_wav(1.0))
    failed = stt.transcribe_safe(wav, session_id=None, learner_id="l")
    assert isinstance(failed, FailureContext) and failed.category == "business"


def test_audio_turn_reports_no_partial_failures_when_all_ok(client) -> None:  # type: ignore[no-untyped-def]
    sid = _session(client)
    r = client.post(f"/sessions/{sid}/turn", files={"file": ("in.wav", silent_wav(2.0), "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial_failures"] == [] and body["prosody"] is not None and body["reply_audio_b64"]


def test_tts_failure_is_tolerated(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(FakeTTS, "synthesize", _boom)
    sid = _session(client)
    r = client.post(f"/sessions/{sid}/turn", files={"file": ("in.wav", silent_wav(2.0), "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial_failures"] == ["tts"]
    assert body["reply_text"] and body["reply_audio_b64"] == "" and body["reply_audio_mime"] is None
    assert body["prosody"] is not None and body["pronunciation_tip"] is not None
    s = client.get(f"/sessions/{sid}").json()
    assistant = next(t for t in s["turns"] if t["role"] == "assistant")
    assert assistant["meta"]["partial_failures"] == ["tts"]


def test_prosody_failure_is_tolerated(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(pronunciation, "analyze_prosody_metered", _boom)
    sid = _session(client)
    r = client.post(f"/sessions/{sid}/turn", files={"file": ("in.wav", silent_wav(2.0), "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial_failures"] == ["prosody"]
    assert body["prosody"] is None and body["pronunciation_tip"] is None
    assert body["learner_text"] and body["reply_text"] and body["reply_audio_b64"]


def test_stt_failure_is_fatal_502_with_alternatives(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(FakeSTT, "transcribe", _boom)
    sid = _session(client)
    r = client.post(f"/sessions/{sid}/turn", files={"file": ("in.wav", silent_wav(2.0), "audio/wav")})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["error_category"] == "transient"
    assert "Spracherkennung" in body["detail"] and "STT_BACKEND=openai" in body["alternatives"]
    # nothing was stored for the failed turn
    s = client.get(f"/sessions/{sid}").json()
    assert s["turns"] == []


def test_text_turn_has_empty_partial_failures(client) -> None:  # type: ignore[no-untyped-def]
    sid = _session(client, "tutor")
    body = client.post(f"/sessions/{sid}/turn", json={"text": "Hallo!"}).json()
    assert body["partial_failures"] == []
