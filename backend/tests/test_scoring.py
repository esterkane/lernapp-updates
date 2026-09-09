"""ADR-0004 §1: Lesen/Hören are scored deterministically; the LLM only explains wrong items."""

from __future__ import annotations

from app.services import results
from app.services import tasks as svc

from tests.conftest import ledger_count


def _lesen_task(client) -> dict:  # type: ignore[no-untyped-def, type-arg]
    r = client.post(
        "/tasks/generate",
        json={"blueprint_id": "testdaf_digital_lesen", "task_type": "richtig_falsch_textsagtnichts"},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_score_all_correct_has_no_llm_call(client) -> None:  # type: ignore[no-untyped-def]
    body = _lesen_task(client)
    key = svc.load_task(body["task_id"]).payload["expected_answers"]
    before = ledger_count()
    r = client.post(f"/tasks/{body['task_id']}/score", json={"learner_id": "default", "answers": key})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["score"] == 5 and out["score_max"] == 5
    assert out["internal_score_20"] == 20
    assert sorted(out["correct"]) == sorted(key)
    assert out["wrong"] == []
    assert out["explanations_de"] is None
    assert out["source_text"]
    assert out["cost_eur"] == 0.0
    assert ledger_count() == before  # no explanation call when nothing is wrong
    stored = results.get_result(out["result_id"])
    assert stored is not None
    assert stored["skill"] == "lesen" and stored["score"] == 5.0 and stored["score_max"] == 5.0
    assert stored["blueprint_id"] == "testdaf_digital_lesen"
    assert stored["rubric_version"] is None
    assert stored["payload"]["internal_score_20"] == 20


def test_score_with_wrong_items_explains(client) -> None:  # type: ignore[no-untyped-def]
    body = _lesen_task(client)
    key: dict[str, str] = svc.load_task(body["task_id"]).payload["expected_answers"]
    ids = sorted(key)
    answers = dict(key)
    # two wrong answers, one of them only differing in case/whitespace (must still count as correct)
    answers[ids[0]] = "  " + key[ids[0]].upper() + " "
    answers[ids[1]] = "richtig" if key[ids[1]] != "richtig" else "falsch"
    del answers[ids[2]]  # unanswered counts as wrong
    before = ledger_count()
    r = client.post(
        f"/tasks/{body['task_id']}/score",
        json={"learner_id": "default", "answers": answers, "session_id": "s-score"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["score"] == 3 and out["score_max"] == 5
    assert out["internal_score_20"] == 12
    assert ids[0] in out["correct"]
    wrong_ids = {w["id"] for w in out["wrong"]}
    assert wrong_ids == {ids[1], ids[2]}
    for w in out["wrong"]:
        assert w["expected"] == key[w["id"]]
        assert "explanation_de" in w
    assert w["given"] is None or isinstance(w["given"], str)
    assert out["explanations_de"] and "Item" in out["explanations_de"]
    assert ledger_count() - before == 2  # exactly one explanation call (tokens_in + tokens_out)
    assert out["cost_eur"] >= 0.0
    stored = results.get_result(out["result_id"])
    assert stored is not None
    assert stored["session_id"] == "s-score"
    assert stored["payload"]["explanations_de"] == out["explanations_de"]
    assert stored["prompt_version"] == "1.0.0"


def test_score_rejects_rubric_skills(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/tasks/generate",
        json={"blueprint_id": "testdaf_digital_schreiben", "task_type": "kurztext"},
    )
    assert r.status_code == 200
    r2 = client.post(f"/tasks/{r.json()['task_id']}/score", json={"answers": {"1": "x"}})
    assert r2.status_code == 400
    assert client.post("/tasks/nope/score", json={"answers": {}}).status_code == 404


def test_normalize_and_agreement() -> None:
    assert svc.normalize_answer("  Richtig \n") == "richtig"
    assert svc.agreement_ratio({"1": "a", "2": "b"}, {"1": "A", "2": "c"}) == 0.5
    assert svc.agreement_ratio({}, {}) == 0.0
