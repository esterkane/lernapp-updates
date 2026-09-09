"""learner_profile.profile_block + progress endpoint."""

from __future__ import annotations

from app.core.db import db_session
from app.core.rubrics import LABELS_DE
from app.db.base import Learner, VocabItem
from app.services import learner_profile, progress, tutor
from app.services.results import store_deterministic_result, store_rubric_result
from app.services.schemas import CriterionScore, RubricResult


def _learner(lid: str, **kw: object) -> str:
    with db_session() as db:
        if db.get(Learner, lid) is None:
            db.add(Learner(id=lid, display_name="Testperson", level="C1", **kw))  # type: ignore[arg-type]
    return lid


def _rubric(scores: dict[str, int], tags: list[str]) -> RubricResult:
    return RubricResult(
        rubric_version="schreiben_v1",
        blueprint_id="testdaf_schreiben",
        task_type="schreiben",
        scores=[CriterionScore(criterion=c, score=s) for c, s in scores.items()],
        error_tags=tags,
        summary_de="Testzusammenfassung.",
        model_version="fake",
        prompt_version="1.0.0",
    )


def test_profile_block_default_learner(learner_id: str) -> None:
    block = learner_profile.profile_block(learner_id)
    assert "Niveau:" in block
    assert "Prüfungstermin:" in block and "Wochenfokus:" in block
    assert "Lernende" not in block  # display_name never reaches the prompt


def test_profile_block_fresh_learner_defaults() -> None:
    lid = _learner("profile-fresh")
    assert learner_profile.weekly_focus(lid) == "allgemein"
    assert learner_profile.top_error_tags(lid) == []
    assert learner_profile.activity_status(lid) == "pausiert"
    block = learner_profile.profile_block(lid)
    assert "Niveau: C1" in block
    assert "noch nicht festgelegt" in block
    assert "Status: pausiert" in block


def test_weekly_focus_from_results_and_override() -> None:
    lid = _learner("profile-focus")
    scores = {
        "aufgabenbezug": 4,
        "sprachfunktionen": 3,
        "quellennutzung": 3,
        "eigenformulierung": 3,
        "praezision": 3,
        "variation": 3,
        "korrektheit": 1,
    }
    store_rubric_result(
        lid, _rubric(scores, ["grammatik/kasus", "grammatik/kasus", "text/konnektoren"]), skill="schreiben"
    )
    assert learner_profile.weekly_focus(lid) == LABELS_DE["korrektheit"]
    tags = learner_profile.top_error_tags(lid)
    assert tags[0] == {"tag": "grammatik/kasus", "count": 2}
    assert {"tag": "text/konnektoren", "count": 1} in tags
    with db_session() as db:
        db.get(Learner, lid).weekly_focus = "Konnektoren"  # type: ignore[union-attr]
    assert learner_profile.weekly_focus(lid) == "Konnektoren"
    block = learner_profile.profile_block(lid)
    assert "Wochenfokus: Konnektoren" in block and "grammatik/kasus (2)" in block


def test_recent_vocab_and_activity() -> None:
    lid = _learner("profile-vocab")
    with db_session() as db:
        for i in range(7):
            db.add(VocabItem(owner_id=lid, wort=f"Wort{i}", bedeutung="Bedeutung"))
    vocab = learner_profile.recent_vocab(lid)
    assert len(vocab) == 5 and all(w.startswith("Wort") for w in vocab)
    tutor.create_session(lid, "tutor")
    assert learner_profile.activity_status(lid) == "aktiv"
    assert "Status: aktiv" in learner_profile.profile_block(lid)


def test_progress_endpoint_and_version_breaks(client) -> None:  # type: ignore[no-untyped-def]
    lid = _learner("profile-progress")
    rid = store_deterministic_result(
        lid,
        skill="lesen",
        task_type="lesen_1",
        blueprint_id="testdaf_lesen",
        score=7,
        score_max=10,
        prompt_version="1.0.0",
        model_version="fake",
    )
    store_deterministic_result(
        lid,
        skill="lesen",
        task_type="lesen_1",
        blueprint_id="testdaf_lesen",
        score=8,
        score_max=10,
        prompt_version="1.1.0",
        model_version="fake",
    )
    store_rubric_result(lid, _rubric({"aufgabenbezug": 3, "korrektheit": 2}, []), skill="schreiben")

    r = client.get(f"/learners/{lid}/progress")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [p["result_id"] for p in body["points"]]
    assert rid in ids and len(body["points"]) == 3
    p0 = body["points"][0]
    assert p0["skill"] == "lesen" and p0["score"] == 7 and p0["score_max"] == 10 and p0["internal_score_20"] == 14
    assert set(p0) >= {
        "rubric_version",
        "prompt_version",
        "model_version",
        "needs_review",
        "is_progress_point",
        "created_at",
    }
    assert body["weekly_focus"] and body["activity_status"] in ("aktiv", "pausiert")
    assert isinstance(body["top_error_tags"], list)
    # prompt_version changed for lesen between point 1 and 2 → exactly one break (schreiben is a first point)
    assert body["version_breaks"] == [body["points"][1]["created_at"]]

    r = client.get(f"/learners/{lid}/progress", params={"skill": "schreiben"})
    assert r.status_code == 200 and [p["skill"] for p in r.json()["points"]] == ["schreiben"]
    assert r.json()["version_breaks"] == []
    assert progress.version_breaks([]) == []

    r = client.get(f"/learners/{lid}/results", params={"skill": "lesen"})
    assert r.status_code == 200 and len(r.json()) == 2
    r = client.get(f"/learners/{lid}/results/{rid}")
    assert r.status_code == 200 and r.json()["result_id"] == rid
    assert client.get(f"/learners/{lid}/results/nope").status_code == 404
    assert client.get("/learners/nope/progress").status_code == 404


def test_learner_get_and_patch(client) -> None:  # type: ignore[no-untyped-def]
    lid = _learner("profile-patch")
    r = client.get(f"/learners/{lid}")
    assert r.status_code == 200
    assert set(r.json()) == {
        "id",
        "display_name",
        "level",
        "exam_date",
        "weekly_focus",
        "activity_status",
        "validator_model",
        "economy_mode",
        "learning_goal",
        "created_at",
    }
    r = client.patch(f"/learners/{lid}", json={"level": "b2", "exam_date": "2027-03-15", "weekly_focus": "Register"})
    assert r.status_code == 200, r.text
    assert (
        r.json()["level"] == "B2" and r.json()["exam_date"] == "2027-03-15" and r.json()["weekly_focus"] == "Register"
    )
    assert "Prüfungstermin: 15.03.2027" in learner_profile.profile_block(lid)
    r = client.patch(f"/learners/{lid}", json={"exam_date": None})
    assert r.json()["exam_date"] is None
    assert client.patch(f"/learners/{lid}", json={"level": "Z9"}).status_code == 400
    assert client.get("/learners/nope").status_code == 404
