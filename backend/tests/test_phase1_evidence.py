"""assessment-rubrics skill (ADR-0017 consequences): schema extensions.

- ``evidence_must_appear_in_text``: evidence not in the learner text is dropped (never raised) and
  recorded in ``dropped_evidence``; matching is whitespace-normalised and case-insensitive.
- ``error_tags`` = controlled vocabulary + ``other`` (which requires ``other_error_detail``).
- ``confidence`` in [0, 1], default 0.5 with ``confidence_missing`` recorded.
- ``needs_review`` is True iff ``routing == "needs_review"``.
"""

from __future__ import annotations

import pytest
from app.core.rubrics import criteria, error_tag_vocabulary
from app.services import assessment as svc
from app.services.schemas import (
    OTHER_ERROR_TAG,
    CriterionScore,
    LLMRubricOutput,
    ReviewHandoff,
    RubricResult,
)
from pydantic import ValidationError

CRITERIA = criteria("schreiben_v1")
TEXT = (
    "Die   Grafik zeigt, dass die Abbruchquote\n gestiegen ist.  Meiner Meinung nach ist das ein Problem, "
    "weil viele Studierende Zeit verlieren."
)


def _scores(evidence: dict[str, list[str]] | None = None, **kw: float) -> list[CriterionScore]:
    evidence = evidence or {}
    return [CriterionScore(criterion=c, score=3, evidence=evidence.get(c, []), comment_de="ok", **kw) for c in CRITERIA]


def _rubric(**overrides: object) -> RubricResult:
    base: dict[str, object] = {
        "rubric_version": "schreiben_v1",
        "blueprint_id": "adhoc_schreiben",
        "task_type": "adhoc",
        "scores": _scores(),
        "summary_de": "ok",
    }
    base.update(overrides)
    return RubricResult.model_validate(base)


# ---------------------------------------------------------------- evidence in text


def test_evidence_substring_normalisation_keeps_matching_quotes() -> None:
    quotes = {
        "aufgabenbezug": ["die grafik zeigt, dass die abbruchquote gestiegen ist."],  # case + whitespace
        "korrektheit": ["Meiner  Meinung\nnach"],  # extra whitespace inside the quote
    }
    r = _rubric(scores=_scores(quotes), learner_text=TEXT)
    assert r.scores[0].evidence == quotes["aufgabenbezug"]
    assert r.scores[-1].evidence == quotes["korrektheit"]
    assert r.dropped_evidence == []


def test_evidence_not_in_text_is_dropped_and_recorded_not_raised() -> None:
    quotes = {
        "aufgabenbezug": ["Die Grafik zeigt", "Dieser Beleg wurde erfunden"],
        "variation": ["Ganz anderer Satz"],
    }
    r = _rubric(scores=_scores(quotes), learner_text=TEXT)
    assert r.scores[0].evidence == ["Die Grafik zeigt"]
    assert r.scores[5].evidence == []
    assert r.dropped_evidence == ["Dieser Beleg wurde erfunden", "Ganz anderer Satz"]


def test_evidence_check_skipped_without_learner_text() -> None:
    r = _rubric(scores=_scores({"aufgabenbezug": ["nicht prüfbar"]}))
    assert r.scores[0].evidence == ["nicht prüfbar"]
    assert r.dropped_evidence == []


def test_learner_text_is_not_dumped() -> None:
    r = _rubric(learner_text=TEXT)
    dumped = r.model_dump(mode="json")
    assert "learner_text" not in dumped
    assert "learner_text" not in r.model_dump_json()
    # a round trip through the stored payload keeps the evidence untouched (no text → no check)
    again = RubricResult.model_validate(dumped)
    assert again.learner_text is None and again.dropped_evidence == r.dropped_evidence


def test_assessment_sets_learner_text_and_drops_phantom_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    original = svc.complete

    def patched(tier: str, messages: list[object], schema: object = None, **kw: object) -> object:
        out = original(tier, messages, schema, **kw)  # type: ignore[arg-type]
        if tier == "assessment" and isinstance(out, LLMRubricOutput):
            out.scores[0].evidence = ["Meiner Meinung nach", "Diesen Satz gibt es nicht"]
        return out

    monkeypatch.setattr(svc, "complete", patched)
    out = svc.assess_writing(TEXT, task_text="Aufgabe", learner_id="default")
    assert out["rubric"]["scores"][0]["evidence"] == ["Meiner Meinung nach"]
    assert out["rubric"]["dropped_evidence"] == ["Diesen Satz gibt es nicht"]
    assert "learner_text" not in out["rubric"]
    assert out["routing"] == "needs_review"  # a phantom quote is an integration failure (ADR-0017 §1)
    assert any(r.startswith("integration:evidence_in_text") for r in out["rubric"]["routing_reasons"])


# ---------------------------------------------------------------- error tags: enum + other


def test_error_tags_must_be_in_vocabulary_or_other() -> None:
    r = _rubric(error_tags=["text/konnektoren", "other"], other_error_detail="Zeichensetzung bei Aufzählungen")
    assert r.error_tags == ["text/konnektoren", "other"]
    with pytest.raises(ValidationError):
        _rubric(error_tags=["unfug/tag"])


def test_other_requires_detail() -> None:
    with pytest.raises(ValidationError, match="other_error_detail"):
        _rubric(error_tags=["other"])
    with pytest.raises(ValidationError, match="other_error_detail"):
        _rubric(error_tags=["other"], other_error_detail="   ")
    assert OTHER_ERROR_TAG == "other"
    assert "other" not in error_tag_vocabulary()  # vocabulary stays the pure a/b list; "other" is the spill-over


def test_filter_error_tags_keeps_other_only_with_detail() -> None:
    assert svc.filter_error_tags(["other", "text/konnektoren"], other_detail="Sonstiges") == [
        "other",
        "text/konnektoren",
    ]
    assert svc.filter_error_tags(["other", "text/konnektoren"], other_detail=None) == ["text/konnektoren"]
    assert svc.filter_error_tags(["OTHER"], other_detail="x") == ["other"]


def test_assessment_passes_other_detail_through(monkeypatch: pytest.MonkeyPatch) -> None:
    original = svc.complete

    def patched(tier: str, messages: list[object], schema: object = None, **kw: object) -> object:
        out = original(tier, messages, schema, **kw)  # type: ignore[arg-type]
        if tier == "assessment" and isinstance(out, LLMRubricOutput):
            out.error_tags = ["other", "text/konnektoren"]
            out.other_error_detail = "Anführungszeichen falsch gesetzt"
        return out

    monkeypatch.setattr(svc, "complete", patched)
    out = svc.assess_writing(TEXT, task_text="Aufgabe", learner_id="default")
    assert "other" in out["rubric"]["error_tags"]
    assert out["rubric"]["other_error_detail"] == "Anführungszeichen falsch gesetzt"


# ---------------------------------------------------------------- confidence


def test_confidence_bounds_and_default() -> None:
    s = CriterionScore(criterion="korrektheit", score=3)
    assert s.confidence == 0.5 and "confidence" not in s.model_fields_set
    assert CriterionScore(criterion="korrektheit", score=3, confidence=0.9).confidence == 0.9
    with pytest.raises(ValidationError):
        CriterionScore(criterion="korrektheit", score=3, confidence=1.2)
    with pytest.raises(ValidationError):
        CriterionScore(criterion="korrektheit", score=3, confidence=-0.1)


def test_confidence_missing_is_recorded() -> None:
    with_conf = LLMRubricOutput(scores=_scores(confidence=0.9), summary_de="s")
    without = LLMRubricOutput.model_validate_json(
        LLMRubricOutput(scores=_scores(), summary_de="s").model_dump_json(
            exclude={"scores": {"__all__": {"confidence"}}}
        )
    )
    assert svc.confidence_missing(with_conf.scores) is False
    assert svc.confidence_missing(without.scores) is True
    rubric = svc.normalize_scores(without.scores, "schreiben_v1")
    assert all(s.confidence == 0.5 for s in rubric)


def test_llm_schema_asks_for_confidence() -> None:
    schema = LLMRubricOutput.model_json_schema()
    crit = schema["$defs"]["CriterionScore"]["properties"]
    assert "confidence" in crit
    assert crit["confidence"]["maximum"] == 1 and crit["confidence"]["minimum"] == 0
    assert "other_error_detail" in schema["properties"]


# ---------------------------------------------------------------- routing ↔ needs_review


def test_needs_review_follows_routing() -> None:
    assert _rubric().routing == "auto_accept" and _rubric().needs_review is False
    assert _rubric(routing="needs_review").needs_review is True
    assert _rubric(routing="spot_check").needs_review is False
    # legacy payloads that only carry needs_review stay consistent
    assert _rubric(needs_review=True).routing == "needs_review"
    with pytest.raises(ValidationError):
        _rubric(routing="irgendwas")


def test_review_handoff_shape() -> None:
    h = ReviewHandoff(
        task_text="Aufgabe",
        learner_text=TEXT,
        assessment_scores={c: 3 for c in CRITERIA},
        validator_scores=None,
        disagreeing_criteria=[],
        evidence={"korrektheit": ["Meiner Meinung nach"]},
        reason="Stichprobe",
        reasons=["spot_check:stratified_sample:schreiben"],
    )
    assert h.created_at  # ISO timestamp filled by default
    r = _rubric(routing="spot_check", review_handoff=h)
    assert r.model_dump(mode="json")["review_handoff"]["reason"] == "Stichprobe"
