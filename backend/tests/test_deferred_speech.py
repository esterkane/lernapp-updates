from app.services import tutor


def test_text_returns_before_tts_and_audio_is_reused(client, monkeypatch):
    sid = client.post("/sessions", json={"kind": "tutor"}).json()["session_id"]
    calls = []
    original = tutor.tts.synthesize_safe

    def track(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(tutor.tts, "synthesize_safe", track)
    reply = client.post(f"/sessions/{sid}/turn?defer_audio=true", json={"text": "Guten Tag"}).json()
    assert reply["reply_text"] and not reply["reply_audio_b64"] and not calls
    url = f"/sessions/{sid}/turns/{reply['turn_ord'] + 1}/audio"
    a = client.post(url)
    b = client.post(url)
    assert a.status_code == b.status_code == 200
    assert a.json()["reply_audio_b64"] == b.json()["reply_audio_b64"]
    assert a.json()["reply_audio_b64"] and len(calls) == 1
    other = client.post("/workspaces", json={"name": "Other audio"}).json()["id"]
    assert client.post(url, headers={"X-Lernapp-Workspace": other}).status_code == 404
    assert len(calls) == 1


def test_overlapping_turns_receive_unique_numbers(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    sid = client.post("/sessions", json={"kind": "tutor"}).json()["session_id"]
    ready = Barrier(2)

    def reply(**kwargs):
        ready.wait(timeout=10)
        return kwargs["learner_text"], "test", []

    monkeypatch.setattr(tutor, "_tutor_reply", reply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(tutor.turn, sid, text, defer_audio=True) for text in ("Erste", "Zweite")]
        replies = [f.result(timeout=20) for f in futures]
    assert sorted(r["turn_ord"] for r in replies) == [0, 2]
    turns = tutor.get_session(sid)["turns"]
    assert [t["ord"] for t in turns] == [0, 1, 2, 3]
    for reply in replies:
        response = client.post(f"/sessions/{sid}/turns/{reply['turn_ord'] + 1}/audio")
        assert response.status_code == 200
        assert response.json()["reply_audio_b64"]


def test_legacy_duplicate_audio_uses_latest_answer(client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.core.db import db_session
    from app.db.base import Turn

    sid = client.post("/sessions", json={"kind": "tutor"}).json()["session_id"]
    now = datetime.now(UTC)
    with db_session() as db:
        for i, text in enumerate(("Alte Antwort", "Neue Antwort")):
            db.add(
                Turn(
                    session_id=sid,
                    ord=1,
                    role="assistant",
                    text=text,
                    ts=now + timedelta(seconds=i),
                    meta={"audio_deferred": True},
                )
            )
    spoken = []
    original = tutor.tts.synthesize_safe

    def track(text, **kwargs):
        spoken.append(text)
        return original(text, **kwargs)

    monkeypatch.setattr(tutor.tts, "synthesize_safe", track)
    url = f"/sessions/{sid}/turns/1/audio"
    first = client.post(url)
    second = client.post(url)
    assert first.status_code == second.status_code == 200
    assert first.json()["reply_audio_b64"] == second.json()["reply_audio_b64"]
    assert spoken == ["Neue Antwort"]
    assert len(tutor.get_session(sid)["turns"]) == 2
