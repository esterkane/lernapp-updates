"""ADR-0017 §2 + §5: spot-check queue, human labels, export to the golden-set shape.

Round trip: assess → first result of a skill is a ``spot_check`` with a self-contained ReviewHandoff →
``GET /learners/{id}/spotchecks`` lists it → ``POST …/label`` stores the human scores (payload only, no
schema change) → the queue is empty, ``pending_spotchecks`` in progress drops → ``scripts/export_spotchecks.py``
writes ``evals/golden/spotchecks_<skill>.jsonl`` in the ``writing_b2c1.jsonl`` shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from app.core.rubrics import criteria
from app.services import results
from app.services.tasks import ensure_learner

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

CRITERIA = criteria("schreiben_v1")
TEXT = (
    "Die Grafik zeigt, dass die Abbruchquote gestiegen ist. Meiner Meinung nach sollten die Universitäten "
    "mehr Beratung anbieten, damit weniger Studierende abbrechen."
)


def _assess(client: Any, learner: str, text: str = TEXT, **kw: Any) -> dict[str, Any]:
    r = client.post(
        "/assess/writing",
        json={"task_text": "Fassen Sie die Grafik zusammen.", "learner_text": text, "learner_id": learner, **kw},
    )
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_spotcheck_round_trip(client: Any) -> None:
    learner = ensure_learner("spot-roundtrip")
    first = _assess(client, learner)
    assert first["routing"] == "spot_check"
    assert first["review_handoff"]["learner_text"] == TEXT
    assert first["rubric"]["routing"] == "spot_check"

    before = client.get(f"/learners/{learner}/progress").json()
    assert before["pending_spotchecks"] == 1

    listed = client.get(f"/learners/{learner}/spotchecks")
    assert listed.status_code == 200, listed.text
    items = listed.json()
    assert [i["result_id"] for i in items] == [first["result_id"]]
    item = items[0]
    assert item["routing"] == "spot_check" and item["skill"] == "schreiben"
    assert item["criteria"] == CRITERIA
    handoff = item["review_handoff"]
    assert set(handoff) >= {
        "task_text",
        "learner_text",
        "assessment_scores",
        "validator_scores",
        "disagreeing_criteria",
        "evidence",
        "reason",
        "created_at",
    }
    assert handoff["reason"] and item["reasons"]

    human = {c: 2 for c in CRITERIA}
    r = client.post(
        f"/learners/{learner}/spotchecks/{first['result_id']}/label",
        json={"scores": human, "note": "Quellen nur angedeutet"},
    )
    assert r.status_code == 200, r.text
    labelled = r.json()
    assert labelled["routing"] == "labelled"
    assert labelled["payload"]["routing"] == "labelled"
    assert labelled["payload"]["human_label"]["scores"] == human
    assert labelled["payload"]["human_label"]["note"] == "Quellen nur angedeutet"
    assert labelled["payload"]["human_label"]["labelled_at"]
    assert labelled["payload"]["spot_check_label"] == labelled["payload"]["human_label"]
    # the shown score stays the model's — a human label is calibration data, not a re-grade
    assert labelled["score"] == first["total"]

    assert client.get(f"/learners/{learner}/spotchecks").json() == []
    assert client.get(f"/learners/{learner}/progress").json()["pending_spotchecks"] == 0
    stored = results.get_result(first["result_id"])
    assert stored is not None and stored["payload"]["review_handoff"]["learner_text"] == TEXT


def test_needs_review_items_are_queued_too(client: Any) -> None:
    learner = ensure_learner("spot-review")
    _assess(client, learner)  # first → spot_check
    flagged = _assess(client, learner, text=TEXT + " Ich bin mir unsicher.")  # fake marker → low confidence
    assert flagged["routing"] == "needs_review" and flagged["needs_review"] is True
    ids = {i["result_id"]: i for i in client.get(f"/learners/{learner}/spotchecks").json()}
    assert flagged["result_id"] in ids
    assert ids[flagged["result_id"]]["routing"] == "needs_review"
    assert "confidence" in " ".join(ids[flagged["result_id"]]["reasons"])
    assert ids[flagged["result_id"]]["review_handoff"]["reason"]


def test_label_validation(client: Any) -> None:
    learner = ensure_learner("spot-validate")
    first = _assess(client, learner)
    rid = first["result_id"]
    url = f"/learners/{learner}/spotchecks/{rid}/label"
    assert client.post(url, json={"scores": {c: 7 for c in CRITERIA}, "note": ""}).status_code in (400, 422)
    assert client.post(url, json={"scores": {"korrektheit": 2}, "note": ""}).status_code in (400, 422)
    assert client.post(url, json={"scores": {**{c: 2 for c in CRITERIA}, "erfunden": 1}}).status_code in (400, 422)
    assert client.post(f"/learners/{learner}/spotchecks/nope/label", json={"scores": {}}).status_code == 404
    other = ensure_learner("spot-other")
    assert client.post(f"/learners/{other}/spotchecks/{rid}/label", json={"scores": {}}).status_code == 404
    assert client.get("/learners/unknown-learner/spotchecks").status_code == 404
    ok = client.post(url, json={"scores": {c: 2 for c in CRITERIA}})  # note optional
    assert ok.status_code == 200, ok.text
    # relabelling is allowed (last label wins) and does not re-queue the item
    again = client.post(url, json={"scores": {c: 3 for c in CRITERIA}, "note": "zweiter Blick"})
    assert again.status_code == 200 and again.json()["payload"]["human_label"]["scores"]["korrektheit"] == 3
    assert client.get(f"/learners/{learner}/spotchecks").json() == []


def test_export_spotchecks_script(client: Any, tmp_path: Path) -> None:
    import export_spotchecks

    learner = ensure_learner("spot-export")
    first = _assess(client, learner)
    _assess(client, learner, text=TEXT + " Zweiter Text.")  # auto_accept, never labelled → not exported
    human = {c: 2 for c in CRITERIA}
    client.post(f"/learners/{learner}/spotchecks/{first['result_id']}/label", json={"scores": human, "note": "n"})

    written = export_spotchecks.export_spotchecks(learner, tmp_path)
    assert [p.name for p in written] == ["spotchecks_schreiben.jsonl"]
    lines = [json.loads(line) for line in written[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    entry = lines[0]
    assert set(entry) >= {"id", "task", "text", "human_scores", "error_tags", "model_scores", "confidence"}
    assert entry["id"] == first["result_id"]
    assert entry["text"] == TEXT and entry["task"] == "Fassen Sie die Grafik zusammen."
    assert entry["human_scores"] == human
    assert entry["model_scores"] == {s["criterion"]: s["score"] for s in first["rubric"]["scores"]}
    assert set(entry["confidence"]) == set(CRITERIA)
    assert all(0.0 <= v <= 1.0 for v in entry["confidence"].values())
    assert entry["skill"] == "schreiben" and entry["rubric_version"] == "schreiben_v1"
    # CLI entry point returns 0 and prints the file
    assert export_spotchecks.main(["--learner", learner, "--out", str(tmp_path / "cli")]) == 0
    assert (tmp_path / "cli" / "spotchecks_schreiben.jsonl").exists()
    # re-running does not duplicate ids (curator rule: never modify existing ids)
    export_spotchecks.export_spotchecks(learner, tmp_path)
    ids = [json.loads(line)["id"] for line in written[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert ids == [first["result_id"]]


def test_pending_helper_and_label_helper_errors() -> None:
    learner = ensure_learner("spot-helpers")
    assert results.pending_spot_checks(learner) == []
    with pytest.raises(KeyError):
        results.label_spot_check("does-not-exist", {c: 1 for c in CRITERIA}, "")
