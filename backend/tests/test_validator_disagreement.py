"""ADR-0004 §3: independent validator for progress points; ≥ 2 disagreement → needs_review, lower score."""

from __future__ import annotations

from typing import Any

import pytest
from app.core.rubrics import criteria
from app.services import assessment as svc
from app.services import results
from app.services.schemas import CriterionScore, LLMRubricOutput

from tests.conftest import ledger_count

TEXT = "Die Grafik zeigt einen Anstieg. Ich finde, dass das ein Problem ist, weil viele Studierende aufhören."


def _shift(score: int, delta: int) -> int:
    """Shift within 0–4; flip the direction instead of clamping so the disagreement size is kept."""
    new = score + delta
    return new if 0 <= new <= 4 else score - delta


def _patch_validator(
    monkeypatch: pytest.MonkeyPatch, delta: dict[str, int]
) -> tuple[dict[str, int], dict[str, LLMRubricOutput]]:
    """Validator returns the assessment scores shifted by ``delta`` (per criterion)."""
    original = svc.complete
    calls = {"assessment": 0, "validator": 0, "conversation": 0}
    first: dict[str, LLMRubricOutput] = {}

    def patched(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        calls[tier] += 1
        out = original(tier, messages, schema, **kw)
        if tier == "assessment" and isinstance(out, LLMRubricOutput):
            first["out"] = out
        if tier == "validator" and isinstance(out, LLMRubricOutput):
            base = first["out"]
            out = LLMRubricOutput(
                scores=[
                    CriterionScore(
                        criterion=s.criterion,
                        score=_shift(int(s.score), delta.get(s.criterion, 0)),
                        comment_de="Validator",
                    )
                    for s in base.scores
                ],
                summary_de="validator",
            )
        return out

    monkeypatch.setattr(svc, "complete", patched)
    return calls, first


def test_disagreement_flags_review_and_shows_lower_score(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, captured = _patch_validator(monkeypatch, {"korrektheit": -2, "variation": +2})
    before = ledger_count()
    out = svc.assess_writing(TEXT, task_text="Aufgabe", is_progress_point=True, learner_id="default")
    assert calls["assessment"] == 1 and calls["validator"] == 1
    assert ledger_count() - before == 4  # two rater calls, both metered
    rubric = out["rubric"]
    assert out["needs_review"] is True and rubric["needs_review"] is True
    v = rubric["validator"]
    assert v is not None
    assert v["max_disagreement"] == 2
    assert sorted(v["disagreeing_criteria"]) == ["korrektheit", "variation"]
    assert v["model_version"].split("/")[0] != rubric["model_version"].split("/")[0]  # different vendor
    assert v["prompt_version"] == rubric["prompt_version"]
    shown = {s["criterion"]: s["score"] for s in rubric["scores"]}
    validator = {s["criterion"]: s["score"] for s in v["scores"]}
    assessed = {s.criterion: int(s.score) for s in captured["out"].scores}
    # the lower of the two ratings is shown per criterion; both ratings are stored
    assert shown["korrektheit"] == assessed["korrektheit"] - 2 == validator["korrektheit"]
    assert abs(validator["variation"] - assessed["variation"]) == 2
    assert all(shown[c] == min(assessed[c], validator[c]) for c in criteria("schreiben_v1"))
    assert out["total"] == sum(shown.values())
    stored = results.get_result(out["result_id"])
    assert stored is not None
    assert stored["needs_review"] is True and stored["is_progress_point"] is True
    assert stored["score"] == float(out["total"])
    assert stored["payload"]["rubric"]["validator"]["max_disagreement"] == 2


def test_small_disagreement_does_not_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_validator(monkeypatch, {"korrektheit": -1, "praezision": +1})
    out = svc.assess_writing(TEXT, task_text="Aufgabe", is_progress_point=True)
    assert out["needs_review"] is False
    assert out["rubric"]["validator"]["max_disagreement"] == 1
    assert out["rubric"]["validator"]["disagreeing_criteria"] == []


def test_no_validator_without_progress_point(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _ = _patch_validator(monkeypatch, {"korrektheit": -3})
    out = svc.assess_writing(TEXT, task_text="Aufgabe", is_progress_point=False)
    assert calls["validator"] == 0
    assert out["rubric"]["validator"] is None and out["needs_review"] is False


def test_speaking_progress_point_via_api(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _patch_validator(monkeypatch, {"aufgabenbezug": -2})
    r = client.post(
        "/assess/speaking",
        json={"task_text": "Aufgabe", "transcript": TEXT, "is_progress_point": True},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["needs_review"] is True
    assert out["rubric"]["validator"]["disagreeing_criteria"] == ["aufgabenbezug"]
    assert [s["criterion"] for s in out["rubric"]["validator"]["scores"]] == criteria("sprechen_v1")
