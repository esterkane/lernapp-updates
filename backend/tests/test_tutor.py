"""Tutor sessions + turns (docs/api.md "Sessions / tutor") with fake backends."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from app.core import ledger
from app.services import tutor
from app.services.fakes import silent_wav

from tests.conftest import ledger_count


def _audio_tmp_dirs() -> list[Path]:
    return list(Path(tempfile.gettempdir()).glob("lernapp-audio-*"))


def test_text_turn_via_api(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/sessions", json={"learner_id": learner_id, "kind": "tutor"})
    assert r.status_code == 200, r.text
    head = r.json()
    assert head["kind"] == "tutor" and head["session_id"] and head["started_at"]
    sid = head["session_id"]

    before = ledger_count()
    r = client.post(f"/sessions/{sid}/turn", json={"text": "Ich habe gestern ein Meeting gehabt."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sid
    assert body["learner_text"] == "Ich habe gestern ein Meeting gehabt."
    assert body["reply_text"]
    assert body["prosody"] is None and body["pronunciation_tip"] is None
    assert body["reply_audio_mime"] in ("audio/wav", "audio/mpeg")
    assert body["reply_audio_b64"]  # fake TTS returns a wav
    assert isinstance(body["citations"], list)
    assert body["cost_eur_turn"] >= 0.0
    assert body["cost_eur_session"] >= body["cost_eur_turn"]
    assert ledger_count() - before >= 2  # llm tokens_in + tokens_out (+ tts)
    stages = {row["stage"] for row in ledger.rows_for_session(sid)}
    assert {"llm", "tts"} <= stages
    assert abs(ledger.session_cost_eur(sid) - body["cost_eur_session"]) < 1e-9

    r = client.get(f"/costs/session/{sid}")
    assert r.status_code == 200 and len(r.json()["rows"]) >= 2


def test_audio_turn_multipart(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id, "kind": "sprechen"}).json()["session_id"]
    wav = silent_wav(3.0)
    r = client.post(f"/sessions/{sid}/turn", files={"file": ("input.wav", wav, "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["learner_text"]  # transcript from fake STT
    prosody = body["prosody"]
    assert prosody is not None and prosody["n_words"] > 0
    assert prosody["pronunciation_flags"], "fake transcript contains a low-confidence word"
    assert all(f["reason"] == "möglicherweise undeutlich" for f in prosody["pronunciation_flags"])
    tip = body["pronunciation_tip"]
    assert tip is not None and tip["tip_de"] and len(tip["practice_words"]) <= 3
    assert body["reply_audio_b64"]
    assert base64.b64decode(body["reply_audio_b64"])[:4] == b"RIFF"
    assert body["duration_seconds"] >= 3.0  # audio seconds are counted as learning time
    stages = {row["stage"] for row in ledger.rows_for_session(sid)}
    assert {"stt", "llm", "tts"} <= stages
    assert not _audio_tmp_dirs(), "temp audio must be deleted after STT + prosody (KEEP_AUDIO=false)"

    # stored turns carry prosody / flags / cost in meta
    s = client.get(f"/sessions/{sid}").json()
    learner_turn = next(t for t in s["turns"] if t["role"] == "learner")
    assistant_turn = next(t for t in s["turns"] if t["role"] == "assistant")
    assert learner_turn["meta"]["input"] == "audio" and learner_turn["meta"]["flags"]
    assert "cost_eur" in assistant_turn["meta"]


def test_duration_accumulates_and_end(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id}).json()["session_id"]
    d1 = client.post(f"/sessions/{sid}/turn", json={"text": "Hallo!"}).json()["duration_seconds"]
    d2 = client.post(f"/sessions/{sid}/turn", json={"text": "Wie geht es dir?"}).json()["duration_seconds"]
    assert d2 >= d1 >= 0.0
    r = client.post(f"/sessions/{sid}/end")
    assert r.status_code == 200
    ended = r.json()
    assert ended["session_id"] == sid and ended["duration_seconds"] >= d2 and ended["cost_eur"] >= 0.0
    r = client.post(f"/sessions/{sid}/turn", json={"text": "noch einer"})
    assert r.status_code == 409
    s = client.get(f"/sessions/{sid}").json()
    assert s["ended_at"] is not None and len(s["turns"]) == 4
    assert [t["ord"] for t in s["turns"]] == [0, 1, 2, 3]


def test_history_and_listing(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id, "kind": "tutor", "task_id": "t1"}).json()[
        "session_id"
    ]
    client.post(f"/sessions/{sid}/turn", json={"text": "Erster Satz."})
    client.post(f"/sessions/{sid}/turn", json={"text": "Zweiter Satz."})
    r = client.get("/sessions", params={"learner_id": learner_id, "kind": "tutor", "limit": 5})
    assert r.status_code == 200
    rows = r.json()
    assert rows and rows[0]["session_id"] == sid and "cost_eur" in rows[0]
    assert all(x["kind"] == "tutor" for x in rows)


def test_turn_validation(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id}).json()["session_id"]
    assert client.post(f"/sessions/{sid}/turn", json={"text": "   "}).status_code == 400
    assert client.post(f"/sessions/{sid}/turn", json={}).status_code == 400
    assert client.post("/sessions/does-not-exist/turn", json={"text": "Hallo"}).status_code == 404
    assert client.post("/sessions", json={"learner_id": learner_id, "kind": "roleplay"}).status_code == 422
    assert client.get("/sessions/does-not-exist").status_code == 404


def test_idle_gap_is_excluded_short_gap_counts() -> None:
    """Learning-time rule (ADR-0019): a 2 h gap is idle → adds nothing; a 90 s gap is reading time → counts."""
    from datetime import UTC, datetime, timedelta

    from app.db.base import Session

    now = datetime.now(UTC)
    idle = Session(duration_seconds=5.0, state={"last_turn_at": (now - timedelta(hours=2)).isoformat()})
    assert tutor._accumulated(idle, now, 2.0, started_at=now) == 5.0 + 2.0
    reading = Session(duration_seconds=5.0, state={"last_turn_at": (now - timedelta(seconds=90)).isoformat()})
    assert tutor._accumulated(reading, now, 2.0, started_at=now) == 5.0 + 90.0  # Audio recording overlaps the gap.
    assert tutor.MAX_GAP_SECONDS == 300.0
