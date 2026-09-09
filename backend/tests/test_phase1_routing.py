"""ADR-0017 §1–2: routing is a pure function; stratified spot-checks; validator isolation.

Table-driven cases for ``routing.route()`` (each threshold, integration failure, spot-check promotion
including the first result of a skill) plus the validator-isolation guarantee (the second rater never
sees the first pass).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.core.rubrics import criteria
from app.services import assessment as svc
from app.services import results, routing
from app.services.routing import (
    IntegrationCheck,
    RoutingThresholds,
    integration_checks,
    load_thresholds,
    route,
    should_spot_check,
    stratified_spot_check,
)
from app.services.schemas import (
    CriterionAgreement,
    CriterionScore,
    LLMRubricOutput,
    RubricResult,
    ValidatorResult,
)
from app.services.tasks import ensure_learner

CRITERIA = criteria("schreiben_v1")
TEXT = (
    "Die Grafik zeigt einen deutlichen Anstieg der Abbruchquote. Meiner Meinung nach liegt das an "
    "finanziellen Belastungen. Deshalb schlage ich vor, die Beratung auszubauen."
)


def _scores(confidence: float = 0.95, score: int = 3, **overrides: float) -> list[CriterionScore]:
    return [
        CriterionScore(criterion=c, score=score, evidence=[], comment_de="ok", confidence=overrides.get(c, confidence))
        for c in CRITERIA
    ]


def _validator(diffs: dict[str, int] | None = None, base: int = 2) -> ValidatorResult:
    """Validator scores = ``base`` shifted by ``diffs`` (base 2 keeps ±2 inside 0–4)."""
    diffs = diffs or {}
    v_scores = [CriterionScore(criterion=c, score=base + diffs.get(c, 0), comment_de="v") for c in CRITERIA]
    agreements = [
        CriterionAgreement(
            criterion=c,
            assessment_score=base,
            validator_score=base + diffs.get(c, 0),
            agree=abs(diffs.get(c, 0)) < 2,
        )
        for c in CRITERIA
    ]
    return ValidatorResult(
        model_version="anthropic/x",
        prompt_version="1.0.0",
        scores=v_scores,
        max_disagreement=max((abs(d) for d in diffs.values()), default=0),
        disagreeing_criteria=[c for c, d in diffs.items() if abs(d) >= 2],
        agreements=agreements,
    )


def _ok_checks() -> list[IntegrationCheck]:
    return [IntegrationCheck(name=n, ok=True, detail="") for n in ("evidence_in_text", "scores_in_range")]


def _rubric(scores: list[CriterionScore] | None = None, learner_text: str | None = TEXT) -> RubricResult:
    return RubricResult(
        rubric_version="schreiben_v1",
        blueprint_id="adhoc_schreiben",
        task_type="adhoc",
        scores=scores or _scores(),
        summary_de="ok",
        learner_text=learner_text,
    )


# ---------------------------------------------------------------- thresholds


def test_thresholds_come_from_config_yaml() -> None:
    t = load_thresholds()
    assert t.min_confidence == pytest.approx(0.85)
    assert t.review_disagreement == 2
    assert t.spot_check_share == pytest.approx(0.10)
    assert t.spot_check_every == 10


# ---------------------------------------------------------------- route() table


ROUTE_CASES: list[tuple[str, list[CriterionScore], ValidatorResult | None, list[IntegrationCheck], str, str | None]] = [
    ("all good, no validator", _scores(), None, _ok_checks(), "auto_accept", None),
    ("all good with validator", _scores(), _validator(), _ok_checks(), "auto_accept", None),
    ("confidence exactly at threshold", _scores(confidence=0.85), None, _ok_checks(), "auto_accept", None),
    ("one confidence below threshold", _scores(korrektheit=0.84), None, _ok_checks(), "needs_review", "confidence"),
    ("missing confidence defaults 0.5", _scores(variation=0.5), None, _ok_checks(), "needs_review", "confidence"),
    ("disagreement of 1 is fine", _scores(), _validator({"variation": 1}), _ok_checks(), "auto_accept", None),
    ("disagreement of 2 flags", _scores(), _validator({"variation": -2}), _ok_checks(), "needs_review", "disagreement"),
    (
        "disagreement of 3 flags",
        _scores(),
        _validator({"praezision": +2}, base=1),
        _ok_checks(),
        "needs_review",
        "disagreement",
    ),
    (
        "integration failure flags",
        _scores(),
        None,
        [IntegrationCheck(name="evidence_in_text", ok=False, detail="Beleg fehlt")],
        "needs_review",
        "integration",
    ),
    (
        "several reasons are all recorded",
        _scores(korrektheit=0.3),
        _validator({"variation": 2}),
        [IntegrationCheck(name="task_type_in_blueprint", ok=False, detail="x")],
        "needs_review",
        "confidence",
    ),
]


@pytest.mark.parametrize(
    ("name", "scores", "validator", "checks", "expected", "reason_prefix"),
    ROUTE_CASES,
    ids=[c[0] for c in ROUTE_CASES],
)
def test_route_table(
    name: str,
    scores: list[CriterionScore],
    validator: ValidatorResult | None,
    checks: list[IntegrationCheck],
    expected: str,
    reason_prefix: str | None,
) -> None:
    decision = route(scores, validator, checks)
    assert decision.decision == expected, name
    if reason_prefix is None:
        assert decision.reasons == []
    else:
        assert any(r.startswith(reason_prefix) for r in decision.reasons), decision.reasons
    # route() never promotes to spot_check by itself — that is the stratified sampler's job.
    assert decision.decision != "spot_check"


def test_route_is_pure_and_respects_custom_thresholds() -> None:
    scores = _scores(confidence=0.9)
    strict = RoutingThresholds(min_confidence=0.95, review_disagreement=2, spot_check_share=0.1)
    assert route(scores, None, [], strict).decision == "needs_review"
    assert route(scores, None, [], RoutingThresholds(min_confidence=0.5)).decision == "auto_accept"
    lenient = RoutingThresholds(review_disagreement=3)
    assert route(scores, _validator({"variation": 2}), [], lenient).decision == "auto_accept"
    # same inputs → same output (no hidden state)
    assert route(scores, None, [], strict) == route(scores, None, [], strict)


def test_all_reasons_are_recorded() -> None:
    d = route(
        _scores(korrektheit=0.3, variation=0.2),
        _validator({"praezision": 2}),
        [IntegrationCheck(name="scores_in_range", ok=False, detail="5 > 4")],
    )
    kinds = sorted({r.split(":")[0] for r in d.reasons})
    assert kinds == ["confidence", "disagreement", "integration"]
    assert sum(r.startswith("confidence") for r in d.reasons) == 2


# ---------------------------------------------------------------- integration checks


def test_integration_checks_pass_for_consistent_result() -> None:
    scores = _scores()
    scores[0].evidence = ["Die Grafik zeigt einen deutlichen Anstieg"]
    checks = integration_checks("adhoc", None, _rubric(scores), TEXT)
    names = {c.name for c in checks}
    assert {"evidence_in_text", "scores_in_range", "total_recomputed", "criteria_complete"} <= names
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    # no blueprint known → the task-type check is not run (rather than failing on adhoc tasks)
    assert "task_type_in_blueprint" not in names


def test_integration_check_evidence_not_in_text_fails() -> None:
    scores = _scores()
    scores[2].evidence = ["Dieser Satz steht nirgends im Text"]
    rubric = _rubric(scores)  # the schema validator drops the phantom quote and records it
    assert rubric.dropped_evidence == ["Dieser Satz steht nirgends im Text"]
    checks = {c.name: c for c in integration_checks("adhoc", None, rubric, TEXT)}
    assert checks["evidence_in_text"].ok is False
    assert "nirgends" in checks["evidence_in_text"].detail


def test_integration_check_total_and_criteria() -> None:
    rubric = _rubric()
    checks = {c.name: c for c in integration_checks("adhoc", None, rubric, TEXT, stated_total=99)}
    assert checks["total_recomputed"].ok is False and "99" in checks["total_recomputed"].detail
    short = RubricResult(
        rubric_version="schreiben_v1",
        blueprint_id="x",
        task_type="adhoc",
        scores=_scores()[:5],
        summary_de="s",
    )
    checks = {c.name: c for c in integration_checks("adhoc", None, short, TEXT)}
    assert checks["criteria_complete"].ok is False


def test_integration_check_task_type_in_blueprint() -> None:
    from app.core.blueprints import get_blueprint

    bp = get_blueprint("testdaf_digital_schreiben")
    rubric = _rubric()
    ok = {c.name: c for c in integration_checks(bp.task_types[0], bp, rubric, TEXT)}
    assert ok["task_type_in_blueprint"].ok is True
    bad = {c.name: c for c in integration_checks("erfundener_typ", bp, rubric, TEXT)}
    assert bad["task_type_in_blueprint"].ok is False
    assert route(rubric.scores, None, list(bad.values())).decision == "needs_review"


# ---------------------------------------------------------------- spot-check sampling


@pytest.mark.parametrize(
    ("count", "expected"), [(0, True), (1, False), (9, False), (10, True), (11, False), (20, True)]
)
def test_should_spot_check_every_tenth_and_first(count: int, expected: bool) -> None:
    assert should_spot_check(count, RoutingThresholds()) is expected


def test_spot_check_share_is_ceil_ten_percent() -> None:
    promoted = sum(should_spot_check(n, RoutingThresholds()) for n in range(25))
    assert promoted == 3  # ceil(25 * 0.10)


def test_stratified_spot_check_in_db_first_of_each_skill() -> None:
    learner = ensure_learner("routing-strata")
    assert stratified_spot_check(learner, "schreiben") is True  # nothing stored yet → first of stratum
    assert stratified_spot_check(learner, "sprechen") is True
    rubric = _rubric(learner_text=None)
    for _ in range(3):
        results.store_rubric_result(learner, rubric, skill="schreiben")
    assert stratified_spot_check(learner, "schreiben") is False
    assert stratified_spot_check(learner, "sprechen") is True  # other stratum still empty
    for _ in range(7):
        results.store_rubric_result(learner, rubric, skill="schreiben")
    assert results.auto_accept_count(learner, "schreiben") == 10
    assert stratified_spot_check(learner, "schreiben") is True  # the 11th → every 10th after the first
    flagged = rubric.model_copy(update={"routing": "needs_review", "needs_review": True})
    results.store_rubric_result(learner, flagged, skill="schreiben")
    assert results.auto_accept_count(learner, "schreiben") == 10  # needs_review rows are not in the stratum


def test_assessment_promotes_first_result_of_skill_to_spot_check() -> None:
    learner = ensure_learner("routing-first")
    out = svc.assess_writing(TEXT, task_text="Aufgabe", learner_id=learner)
    assert out["routing"] == "spot_check"
    assert out["needs_review"] is False
    handoff = out["review_handoff"]
    assert handoff is not None
    assert handoff["learner_text"] == TEXT and handoff["task_text"] == "Aufgabe"
    assert any(r.startswith("spot_check") for r in out["rubric"]["routing_reasons"])
    assert handoff["reason"]
    second = svc.assess_writing(TEXT + " Noch ein Satz.", task_text="Aufgabe", learner_id=learner)
    assert second["routing"] == "auto_accept" and second["review_handoff"] is None
    stored = results.get_result(second["result_id"])
    assert stored is not None and stored["payload"]["routing"] == "auto_accept"


# ---------------------------------------------------------------- confidence → needs_review end to end


def test_low_confidence_routes_to_needs_review(monkeypatch: pytest.MonkeyPatch) -> None:
    original = svc.complete

    def patched(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        out = original(tier, messages, schema, **kw)
        if tier == "assessment" and isinstance(out, LLMRubricOutput):
            out.scores[0].confidence = 0.4
        return out

    monkeypatch.setattr(svc, "complete", patched)
    learner = ensure_learner("routing-lowconf")
    out = svc.assess_writing(TEXT, task_text="Aufgabe", learner_id=learner)
    assert out["routing"] == "needs_review" and out["needs_review"] is True
    assert out["rubric"]["needs_review"] is True
    assert any(r.startswith("confidence:aufgabenbezug") for r in out["rubric"]["routing_reasons"])
    assert out["review_handoff"]["validator_scores"] is None
    assert out["review_handoff"]["assessment_scores"]["aufgabenbezug"] == out["rubric"]["scores"][0]["score"]


def test_fake_backend_marker_yields_low_confidence() -> None:
    learner = ensure_learner("routing-marker")
    out = svc.assess_writing(TEXT + " Ich bin mir unsicher.", task_text="Aufgabe", learner_id=learner)
    assert out["routing"] == "needs_review"
    assert min(s["confidence"] for s in out["rubric"]["scores"]) < 0.85


# ---------------------------------------------------------------- validator isolation


def test_validator_prompt_contains_no_first_pass_output(monkeypatch: pytest.MonkeyPatch) -> None:
    original = svc.complete
    seen: dict[str, Any] = {}

    def patched(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        out = original(tier, messages, schema, **kw)
        seen[tier] = {"messages": [dict(m) for m in messages], "out": out}
        return out

    monkeypatch.setattr(svc, "complete", patched)
    learner = ensure_learner("routing-isolation")
    result = svc.assess_writing(TEXT, task_text="Aufgabe X", learner_id=learner, is_progress_point=True)
    first: LLMRubricOutput = seen["assessment"]["out"]
    validator_text = json.dumps(seen["validator"]["messages"], ensure_ascii=False)
    assert first.summary_de not in validator_text
    assert json.dumps({s.criterion: int(s.score) for s in first.scores}) not in validator_text
    for s in first.scores:
        assert s.comment_de not in validator_text
    assert "score" not in validator_text.split("Rubrik")[0].split("Lernertext")[0]  # no scores before the text
    # the validator gets exactly task + text + rubric — the same inputs as the first pass, nothing more
    assert seen["validator"]["messages"] == seen["assessment"]["messages"]
    assert "Aufgabe X" in validator_text and TEXT in validator_text
    v = result["rubric"]["validator"]
    assert len(v["agreements"]) == 7
    assert all(a["agree"] for a in v["agreements"])
    assert 0.0 <= v["review_confidence"] <= 1.0


def test_agreements_reflect_disagreement(monkeypatch: pytest.MonkeyPatch) -> None:
    original = svc.complete
    first: dict[str, LLMRubricOutput] = {}

    def patched(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        out = original(tier, messages, schema, **kw)
        if tier == "assessment":
            first["out"] = out
        if tier == "validator":
            base = first["out"]
            out = LLMRubricOutput(
                scores=[
                    CriterionScore(
                        criterion=s.criterion,
                        score=max(0, int(s.score) - (2 if s.criterion == "variation" else 0)),
                        comment_de="v",
                        confidence=0.9,
                    )
                    for s in base.scores
                ],
                summary_de="v",
            )
        return out

    monkeypatch.setattr(svc, "complete", patched)
    learner = ensure_learner("routing-agree")
    out = svc.assess_writing(TEXT, task_text="Aufgabe", learner_id=learner, is_progress_point=True)
    v = out["rubric"]["validator"]
    by = {a["criterion"]: a for a in v["agreements"]}
    assert by["variation"]["agree"] is False
    assert by["variation"]["assessment_score"] - by["variation"]["validator_score"] == 2
    assert by["korrektheit"]["agree"] is True
    assert out["routing"] == "needs_review"
    assert out["review_handoff"]["disagreeing_criteria"] == ["variation"]
    assert out["review_handoff"]["validator_scores"]["variation"] == by["variation"]["validator_score"]
    assert v["review_confidence"] == pytest.approx(0.9)


def test_routing_module_has_no_llm_dependency() -> None:
    import inspect

    src = inspect.getsource(routing)
    assert "litellm" not in src and "complete(" not in src
