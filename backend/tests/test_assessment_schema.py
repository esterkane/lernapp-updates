"""assessment-rubrics skill: 7 criteria, bounds 0–4, error tags in vocabulary, internal scale, label."""

from __future__ import annotations

import re

from app.core.rubrics import criteria, error_tag_vocabulary
from app.services import assessment as svc
from app.services.fakes import silent_wav
from app.services.schemas import CriterionScore, LLMRubricOutput, RubricResult

from tests.conftest import ledger_count

TDN_RE = re.compile(r"TDN\s?[3-5]")
PCT_RE = re.compile(r"\d{1,3}\s?%")

LEARNER_TEXT = (
    "Die Grafik zeigt, dass die Zahl der Studienabbrüche seit 2015 gestiegen ist. Meiner Meinung nach "
    "liegt das vor allem an finanziellen Belastungen. Andererseits fehlt vielen Studierenden eine gute "
    "Beratung. Deshalb schlage ich vor, Mentoring-Programme auszubauen."
)


def _check_rubric(rubric: dict, rubric_version: str) -> None:  # type: ignore[type-arg]
    assert rubric["rubric_version"] == rubric_version
    assert [s["criterion"] for s in rubric["scores"]] == criteria(rubric_version)
    assert len(rubric["scores"]) == 7
    for s in rubric["scores"]:
        assert 0 <= s["score"] <= 4
        assert isinstance(s["evidence"], list)
        assert s["comment_de"]
    assert set(rubric["error_tags"]) <= set(error_tag_vocabulary())
    assert len(rubric["better_formulations"]) <= 5
    from app.core.prompts import load_prompt

    expected = load_prompt("writing_feedback" if rubric_version == "schreiben_v1" else "speaking_feedback").version
    assert rubric["model_version"] and rubric["prompt_version"] == expected
    assert not TDN_RE.search(rubric["summary_de"])


def test_assess_writing_api(client) -> None:  # type: ignore[no-untyped-def]
    before = ledger_count()
    r = client.post(
        "/assess/writing",
        json={
            "task_text": "Fassen Sie die Grafik zusammen und nehmen Sie Stellung.",
            "expected_content": ["Grafik zusammenfassen", "Stellung nehmen"],
            "learner_text": LEARNER_TEXT,
            "learner_id": "default",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    _check_rubric(out["rubric"], "schreiben_v1")
    assert out["rubric"]["blueprint_id"] == "adhoc_schreiben"
    assert out["rubric"]["validator"] is None and out["needs_review"] is False
    assert out["total"] == sum(s["score"] for s in out["rubric"]["scores"])
    assert out["total_max"] == 28
    assert 0 <= out["internal_score_20"] <= 20
    assert out["label"] == "interne Übungsbewertung"
    assert out["result_id"] and isinstance(out["cost_eur"], float)
    assert ledger_count() - before == 2  # one assessment call, no validator
    assert not TDN_RE.search(r.text)


def test_assess_writing_with_task(client) -> None:  # type: ignore[no-untyped-def]
    g = client.post(
        "/tasks/generate",
        json={"blueprint_id": "testdaf_digital_schreiben", "task_type": "textproduktion_mit_quellen"},
    )
    assert g.status_code == 200
    task_id = g.json()["task_id"]
    r = client.post("/assess/writing", json={"task_id": task_id, "learner_text": LEARNER_TEXT})
    assert r.status_code == 200, r.text
    assert r.json()["rubric"]["blueprint_id"] == "testdaf_digital_schreiben"
    assert r.json()["rubric"]["task_type"] == "textproduktion_mit_quellen"
    assert client.post("/assess/writing", json={"task_id": "nope", "learner_text": "x"}).status_code == 404
    assert client.post("/assess/writing", json={"learner_text": "   "}).status_code == 400


def test_assess_speaking_api_with_prosody(client) -> None:  # type: ignore[no-untyped-def]
    prosody = {
        "wpm": 120.0,
        "duration_s": 30.0,
        "n_words": 60,
        "long_pauses": [{"start": 3.0, "end": 5.0, "seconds": 2.0}],
        "fillers": {"ähm": 2},
        "n_fillers": 2,
        "low_confidence_words": ["Prüfung"],
        "pronunciation_flags": [{"word": "Prüfung", "reason": "möglicherweise undeutlich", "position": 1.0}],
        "tempo_label_de": "angemessen",
    }
    before = ledger_count()
    r = client.post(
        "/assess/speaking",
        json={"task_text": "Beschreiben Sie die Grafik.", "transcript": LEARNER_TEXT, "prosody": prosody},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    _check_rubric(out["rubric"], "sprechen_v1")
    assert out["rubric"]["blueprint_id"] == "adhoc_sprechen"
    assert out["label"] == "interne Übungsbewertung"
    tip = out["pronunciation_tip"]
    assert tip["tip_de"] and len(tip["practice_words"]) <= 3
    assert not PCT_RE.search(tip["tip_de"])
    assert ledger_count() - before == 4  # assessment + pronunciation tip


def test_assess_speaking_without_prosody_gives_neutral_tip(client) -> None:  # type: ignore[no-untyped-def]
    before = ledger_count()
    r = client.post("/assess/speaking", json={"transcript": LEARNER_TEXT})
    assert r.status_code == 200, r.text
    assert r.json()["pronunciation_tip"]["practice_words"] == []
    assert "Audioaufnahme" in r.json()["pronunciation_tip"]["tip_de"]
    assert ledger_count() - before == 2  # no tip call without prosody


def test_assess_speaking_audio_multipart(client) -> None:  # type: ignore[no-untyped-def]
    before = ledger_count()
    r = client.post(
        "/assess/speaking/audio",
        files={"file": ("aufnahme.wav", silent_wav(6.0), "audio/wav")},
        data={
            "task_text": "Erzählen Sie von Ihrer Prüfung.",
            "learner_id": "default",
            "is_progress_point": "false",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    _check_rubric(out["rubric"], "sprechen_v1")
    assert out["transcript"]
    assert out["prosody"]["n_words"] > 0 and out["prosody"]["tempo_label_de"]
    assert "pronunciation_flags" in out["prosody"]
    assert out["pronunciation_tip"]["tip_de"]
    assert not PCT_RE.search(r.text.replace("%", "% "))  # no "87 %" style pronunciation scores
    rows_after = ledger_count() - before
    assert rows_after == 6  # STT (1) + prosody (1) + assessment (2) + tip (2)


def test_normalisation_fills_missing_and_drops_unknown() -> None:
    scores = [
        CriterionScore(criterion="korrektheit", score=3, comment_de="ok"),
        CriterionScore(criterion="erfunden", score=4, comment_de="?"),
        CriterionScore(criterion="Aufgabenbezug", score=2, comment_de="ok"),
    ]
    out = svc.normalize_scores(scores, "schreiben_v1")
    assert [s.criterion for s in out] == criteria("schreiben_v1")
    assert out[0].score == 2 and out[-1].score == 3
    assert out[1].score == 0 and out[1].comment_de
    tags = ["text/konnektoren", "unfug/tag", "TEXT/KONNEKTOREN"]
    assert svc.filter_error_tags(tags) == ["text/konnektoren"]


def test_rubric_result_total() -> None:
    rr = RubricResult(
        rubric_version="schreiben_v1",
        blueprint_id="x",
        task_type="y",
        scores=[CriterionScore(criterion=c, score=4) for c in criteria("schreiben_v1")],
        summary_de="ok",
    )
    assert rr.total == 28 and rr.total_max == 28
    out = LLMRubricOutput(scores=rr.scores, summary_de="s", better_formulations=[["a", "b"]] * 9)
    assert len(out.better_formulations) == 5
