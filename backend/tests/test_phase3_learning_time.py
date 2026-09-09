"""Phase 3 (ADR-0019): ``Session.duration_seconds`` counts only genuine learning time.

Rule (``services/learning_time.py``): per turn audio input seconds + request→response time (≤ 120 s)
+ the gap since the previous turn when it is ≤ 5 min; longer gaps are idle and add nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.db import db_session
from app.db.base import Session
from app.services import learning_time, tutor
from app.services.fakes import silent_wav

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def test_increment_rule() -> None:
    inc = learning_time.learning_increment
    # first turn: no gap; 3 s audio; 4 s processing
    assert inc(None, NOW, NOW + timedelta(seconds=4), 3.0) == 7.0
    # 90 s since the previous turn = reading/thinking → counts
    assert inc(NOW - timedelta(seconds=90), NOW, NOW, 0.0) == 90.0
    # exactly 5 min still counts, one second more is idle → 0
    assert inc(NOW - timedelta(seconds=300), NOW, NOW, 0.0) == 300.0
    assert inc(NOW - timedelta(seconds=301), NOW, NOW, 0.0) == 0.0
    assert inc(NOW - timedelta(hours=2), NOW, NOW + timedelta(seconds=2), 5.0) == 7.0
    # processing time is capped at 120 s (a hung provider must not inflate the rate)
    assert inc(None, NOW, NOW + timedelta(seconds=600), 0.0) == 120.0
    # clock skew never subtracts
    assert inc(NOW + timedelta(seconds=10), NOW, NOW - timedelta(seconds=1), -3.0) == 0.0
    assert learning_time.MAX_GAP_SECONDS == 300.0 and learning_time.MAX_PROCESSING_SECONDS == 120.0


def test_tutor_turn_reports_learning_seconds(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id, "kind": "tutor"}).json()["session_id"]
    t1 = client.post(f"/sessions/{sid}/turn", json={"text": "Hallo!"}).json()
    assert 0.0 <= t1["learning_seconds"] <= learning_time.MAX_PROCESSING_SECONDS
    assert t1["duration_seconds"] == t1["learning_seconds"]
    t2 = client.post(f"/sessions/{sid}/turn", json={"text": "Wie geht es?"}).json()
    assert abs(t2["duration_seconds"] - (t1["duration_seconds"] + t2["learning_seconds"])) < 1e-6
    assert t2["learning_seconds"] < learning_time.MAX_GAP_SECONDS + learning_time.MAX_PROCESSING_SECONDS


def test_audio_seconds_count_as_learning_time(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id, "kind": "sprechen"}).json()["session_id"]
    body = client.post(f"/sessions/{sid}/turn", files={"file": ("in.wav", silent_wav(3.0), "audio/wav")}).json()
    assert body["learning_seconds"] >= 3.0 and body["duration_seconds"] >= 3.0


def test_idle_gap_between_turns_is_excluded(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = client.post("/sessions", json={"learner_id": learner_id, "kind": "tutor"}).json()["session_id"]
    d1 = client.post(f"/sessions/{sid}/turn", json={"text": "Erster Satz."}).json()["duration_seconds"]
    with db_session() as db:  # the learner walked away for two hours
        s = db.get(Session, sid)
        assert s is not None
        s.state = {**(s.state or {}), "last_turn_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}
    t2 = client.post(f"/sessions/{sid}/turn", json={"text": "Zweiter Satz."}).json()
    assert t2["duration_seconds"] - d1 < learning_time.MAX_PROCESSING_SECONDS  # no 7200 s jump
    assert t2["learning_seconds"] < learning_time.MAX_PROCESSING_SECONDS
    # a short gap before ending the session counts as reading time; an idle one does not
    with db_session() as db:
        s = db.get(Session, sid)
        assert s is not None
        s.state = {**(s.state or {}), "last_turn_at": (datetime.now(UTC) - timedelta(seconds=60)).isoformat()}
    ended = client.post(f"/sessions/{sid}/end").json()
    assert 60.0 <= ended["duration_seconds"] - t2["duration_seconds"] < 61.0


def test_end_after_idle_adds_nothing() -> None:
    from app.core.db import ensure_default_learner

    lid = ensure_default_learner()
    sid = tutor.create_session(lid, "tutor")["session_id"]
    with db_session() as db:
        s = db.get(Session, sid)
        assert s is not None
        s.duration_seconds = 42.0
        s.state = {**(s.state or {}), "last_turn_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat()}
    assert tutor.end_session(sid)["duration_seconds"] == 42.0


def test_roleplay_turn_uses_the_same_rule(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    scenario = client.get("/roleplay/scenarios").json()[0]["id"]
    sid = client.post("/roleplay/start", json={"scenario_id": scenario, "learner_id": learner_id}).json()["session_id"]
    with db_session() as db:  # idle for an hour after the opening line
        s = db.get(Session, sid)
        assert s is not None
        s.state = {**(s.state or {}), "last_turn_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    body = client.post(f"/roleplay/{sid}/turn", json={"text": "Guten Tag, ich möchte über den Preis sprechen."}).json()
    assert body["duration_seconds"] < learning_time.MAX_PROCESSING_SECONDS
    assert abs(body["duration_seconds"] - body["learning_seconds"]) < 1e-6


def test_usage_summary_reads_session_learning_time(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    with db_session() as db:
        db.add(Session(learner_id=learner_id, kind="tutor", started_at=datetime.now(UTC), duration_seconds=900.0))
    s = client.get("/usage/summary", params={"learner_id": learner_id}).json()
    assert s["learning_seconds"] >= 900.0 and s["rate_available"] is True
