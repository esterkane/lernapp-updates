"""Task generation flow (generator → validator → consistency) via the HTTP API, fake backends."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from app.core.blueprints import Blueprint
from app.services import tasks as svc
from app.services.schemas import GeneratedTask, ValidationReport

from tests.conftest import ledger_count


def test_generate_writing_task_withholds_answers(client) -> None:  # type: ignore[no-untyped-def]
    before = ledger_count()
    r = client.post(
        "/tasks/generate",
        json={"blueprint_id": "testdaf_digital_schreiben", "task_type": "textproduktion_mit_quellen"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["skill"] == "schreiben" and body["task_type"] == "textproduktion_mit_quellen"
    assert body["level"] == "C1"
    task = body["task"]
    assert "expected_answers" not in task
    assert all("answer" not in it for it in task["items"])
    assert task["rubric_ref"] == "schreiben_v1"
    assert task["expected_content"]
    assert body["validation"]["ok"] is True and body["validation"]["agreement"] is None
    assert body["blueprint"]["id"] == "testdaf_digital_schreiben"
    assert body["blueprint"]["total_time_seconds"] == 3600
    assert body["models"]["generator"] and body["models"]["validator"]
    assert body["models"]["generator"].split("/")[0] != body["models"]["validator"].split("/")[0]
    assert body["prompt_versions"] == {"generator": "1.0.1", "validator": "1.0.1"}
    assert isinstance(body["cost_eur"], float)
    # generator + validator → 2 LLM calls → 4 token rows
    assert ledger_count() - before >= 4

    r2 = client.get(f"/tasks/{body['task_id']}")
    assert r2.status_code == 200
    assert "expected_answers" not in r2.json()["task"]
    assert r2.json()["task_id"] == body["task_id"]

    r3 = client.get("/tasks", params={"skill": "schreiben"})
    assert r3.status_code == 200
    assert any(h["task_id"] == body["task_id"] for h in r3.json())
    assert {"task_id", "title", "skill", "task_type", "level", "created_at"} <= set(r3.json()[0])


def test_generate_lesen_task_has_items_and_agreement(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/tasks/generate",
        json={"blueprint_id": "testdaf_digital_lesen", "task_type": "richtig_falsch_textsagtnichts"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["validation"]["agreement"] == 1.0
    items = body["task"]["items"]
    assert len(items) == 5
    for it in items:
        assert "answer" not in it and "explanation_hint" not in it
        assert it["options"] == ["richtig", "falsch", "Text sagt dazu nichts"]
    assert body["task"]["source_text"]
    # the stored payload keeps the answer key
    stored = svc.load_task(body["task_id"])
    assert len(stored.payload["expected_answers"]) == 5
    assert stored.generator_model == body["models"]["generator"]


def test_unknown_task_type_and_blueprint(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/tasks/generate", json={"blueprint_id": "testdaf_digital_lesen", "task_type": "gibt_es_nicht"})
    assert r.status_code == 400
    assert "gibt_es_nicht" in r.json()["detail"]
    r = client.post("/tasks/generate", json={"blueprint_id": "nope", "task_type": "x"})
    assert r.status_code == 404
    assert client.get("/tasks/doesnotexist").status_code == 404


def test_timings_come_from_blueprint_not_llm(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    original = svc.complete

    def tampered(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        out = original(tier, messages, schema, **kw)
        if isinstance(out, GeneratedTask):
            out.preparation_seconds = 999
            out.response_seconds = 999
            out.rubric_ref = "schreiben_v1"
        return out

    monkeypatch.setattr(svc, "complete", tampered)
    r = client.post(
        "/tasks/generate",
        json={"blueprint_id": "testdaf_digital_sprechen", "task_type": "informationen_erfragen"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["preparation_seconds"] == 30
    assert body["task"]["response_seconds"] == 60
    assert body["task"]["rubric_ref"] == "sprechen_v1"
    assert body["blueprint"]["preparation_seconds"] == 30 and body["blueprint"]["response_seconds"] == 60


def test_low_agreement_regenerates_then_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"generator": 0, "validator": 0}
    original = svc.complete

    def disagreeing(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        calls[tier] += 1
        out = original(tier, messages, schema, **kw)
        if isinstance(out, ValidationReport):
            flipped = {k: "falsch" if v != "falsch" else "richtig" for k, v in out.solved_answers.items()}
            out.solved_answers = flipped
        return out

    monkeypatch.setattr(svc, "complete", disagreeing)
    body = svc.generate_task("testdaf_digital_lesen", "richtig_falsch_textsagtnichts")
    assert body["status"] == "rejected"
    assert calls == {"generator": 3, "validator": 3}
    assert body["validation"]["ok"] is False
    assert body["validation"]["agreement"] == 0.0
    assert any(i["severity"] == "blocker" for i in body["validation"]["issues"])
    with pytest.raises(svc.TaskError):
        svc.score_task(body["task_id"], "default", {"1": "richtig"})


def test_hoeren_task_gets_metered_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    bp = Blueprint.model_validate(
        {
            "id": "test_hoeren",
            "skill": "hoeren",
            "format": "digital",
            "source_url": "https://www.testdaf.de/",
            "version": "0.0.1",
            "total_time_seconds": 600,
            "n_tasks": 1,
            "input_modality": "audio",
            "output_modality": "selection",
            "scoring": "deterministic",
            "score_scale": {"min": 0, "max": 20},
            "tasks": [{"task_type": "aussagen_pruefen", "n_items": 5}],
        }
    )
    monkeypatch.setattr(svc, "get_blueprint", lambda _id: bp)
    before = ledger_count()
    body = svc.generate_task("test_hoeren", "aussagen_pruefen")
    assert body["status"] == "ok"
    assert body["task"]["audio_mime"] == "audio/wav"
    assert base64.b64decode(body["task"]["audio_b64"])[:4] == b"RIFF"
    assert ledger_count() - before >= 5  # 4 LLM token rows + 1 TTS row
    assert body["cost_eur"] >= 0.0


def test_public_view_strips_keys() -> None:
    task = GeneratedTask(
        title="t",
        instructions_de="i",
        items=[{"id": "1", "question": "q", "answer": "richtig", "explanation_hint": "h"}],  # type: ignore[list-item]
        expected_answers={"1": "richtig"},
    )
    view = svc.public_view(task)
    assert "expected_answers" not in view
    assert view["items"] == [{"id": "1", "question": "q", "options": []}]
