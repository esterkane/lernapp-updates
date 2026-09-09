"""Rubric-based assessment for Schreiben / Sprechen (ADR-0004 §2–3, assessment-rubrics skill).

- ``assessment`` tier → ``LLMRubricOutput`` (temperature 0), normalised to exactly the 7 criteria
  of the rubric, error tags restricted to the controlled vocabulary, ≤ 5 better formulations.
- ``is_progress_point`` → the ``validator`` tier (different vendor) re-scores with the same prompt —
  it receives ONLY task + text + rubric, never the first pass's output; the shown score is the
  per-criterion minimum of both raters (the lower score is shown); both are stored.
- Routing (ADR-0017): ``routing.route()`` — a pure function of (per-criterion confidence, validator
  agreement, integration checks) → ``auto_accept | needs_review``; a stratified 10 % of ``auto_accept``
  per skill becomes ``spot_check``. Every non-auto item carries a self-contained ``ReviewHandoff``.
- Scores are always "Rubrik-Score (0–28)" and "interne Übungsbewertung (0–20)" — never an
  official exam level, never a pronunciation percentage (product rules 1 and 6).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core import ledger
from app.core.blueprints import Blueprint, get_blueprint
from app.core.models import resolve_tier
from app.core.prompts import load_prompt
from app.core.rubrics import (
    SCORE_MIN,
    criteria,
    criteria_text,
    error_tag_vocabulary,
    internal_scale,
)
from app.db.base import new_id
from app.services import results, routing
from app.services.audio import temp_wav
from app.services.llm import complete
from app.services.pronunciation import analyze_prosody_metered, flags_for_prompt
from app.services.schemas import (
    DEFAULT_CONFIDENCE,
    OTHER_ERROR_TAG,
    CriterionAgreement,
    CriterionScore,
    LLMRubricOutput,
    PronunciationTip,
    ProsodyReport,
    ReviewHandoff,
    Routing,
    RubricResult,
    ValidatorResult,
)
from app.services.stt import get_stt
from app.services.tasks import TaskNotFound, ensure_learner, load_task

log = logging.getLogger(__name__)

LABEL_DE = "interne Übungsbewertung"
REVIEW_THRESHOLD = 2  # ADR-0004 §3
MAX_BETTER_FORMULATIONS = 5
NEUTRAL_TIP = PronunciationTip(
    tip_de=(
        "Ohne Audioaufnahme gibt es keinen Aussprache-Hinweis. Nimm dich beim nächsten Mal auf, "
        "dann bekommst du einen konkreten Tipp."
    ),
    practice_words=[],
)


class AssessmentError(ValueError):
    """Client error (HTTP 400)."""


# ---------------------------------------------------------------- normalisation


def normalize_scores(scores: list[CriterionScore], rubric_version: str) -> list[CriterionScore]:
    """Exactly the rubric's 7 criteria, in rubric order; missing → 0 with comment, unknown dropped."""
    by_name: dict[str, CriterionScore] = {}
    for s in scores:
        key = s.criterion.strip().lower()
        if key not in by_name:
            by_name[key] = s
    out: list[CriterionScore] = []
    for name in criteria(rubric_version):
        found = by_name.get(name)
        if found is None:
            out.append(
                CriterionScore(
                    criterion=name,
                    score=SCORE_MIN,
                    evidence=[],
                    comment_de="Dieses Kriterium wurde vom Bewertungsmodell nicht bewertet.",
                    confidence=0.0,  # an unrated criterion can never be auto-accepted
                )
            )
        else:
            out.append(
                CriterionScore(
                    criterion=name,
                    score=found.score,
                    evidence=list(found.evidence),
                    comment_de=found.comment_de,
                    confidence=found.confidence,
                )
            )
    return out


def confidence_missing(scores: list[CriterionScore]) -> bool:
    """True when the model omitted ``confidence`` on any criterion (the default 0.5 is then in effect)."""
    return any("confidence" not in s.model_fields_set for s in scores)


def filter_error_tags(tags: list[str], other_detail: str | None = None) -> list[str]:
    """Controlled vocabulary + ``other`` (kept only when a detail text is given)."""
    vocab = set(error_tag_vocabulary())
    seen: list[str] = []
    for t in tags:
        tag = t.strip().lower()
        if tag == OTHER_ERROR_TAG and not (other_detail or "").strip():
            continue
        if (tag in vocab or tag == OTHER_ERROR_TAG) and tag not in seen:
            seen.append(tag)
    return seen


def _rubric_from_output(
    out: LLMRubricOutput,
    *,
    rubric_version: str,
    blueprint_id: str,
    task_type: str,
    prompt_version: str,
    learner_text: str | None = None,
) -> RubricResult:
    other_detail = (out.other_error_detail or "").strip() or None
    tags = filter_error_tags(out.error_tags, other_detail)
    return RubricResult(
        rubric_version=rubric_version,
        blueprint_id=blueprint_id,
        task_type=task_type,
        scores=normalize_scores(out.scores, rubric_version),
        error_tags=tags,
        other_error_detail=other_detail if OTHER_ERROR_TAG in tags else None,
        better_formulations=list(out.better_formulations)[:MAX_BETTER_FORMULATIONS],
        new_vocabulary=[v for v in out.new_vocabulary if v.strip()][:8],
        summary_de=out.summary_de.strip(),
        model_version=resolve_tier("assessment").model,
        prompt_version=prompt_version,
        confidence_missing=confidence_missing(out.scores),
        learner_text=learner_text,  # excluded from dumps; enables evidence_must_appear_in_text
    )


def apply_validator(
    rubric: RubricResult,
    validator_scores: list[CriterionScore],
    *,
    prompt_version: str,
    model_version: str | None = None,
) -> RubricResult:
    """ADR-0004 §3: store both ratings; per-criterion agreement; show the lower score per criterion."""
    v_scores = normalize_scores(validator_scores, rubric.rubric_version)
    pairs = list(zip(rubric.scores, v_scores, strict=True))
    diffs = {a.criterion: abs(int(a.score) - int(b.score)) for a, b in pairs}
    max_diff = max(diffs.values(), default=0)
    rubric.validator = ValidatorResult(
        model_version=model_version or resolve_tier("validator").model,
        prompt_version=prompt_version,
        scores=v_scores,
        max_disagreement=max_diff,
        disagreeing_criteria=[c for c, d in diffs.items() if d >= REVIEW_THRESHOLD],
        agreements=[
            CriterionAgreement(
                criterion=a.criterion,
                assessment_score=int(a.score),
                validator_score=int(b.score),
                agree=diffs[a.criterion] < REVIEW_THRESHOLD,
            )
            for a, b in pairs
        ],
        review_confidence=sum(b.confidence for b in v_scores) / len(v_scores) if v_scores else DEFAULT_CONFIDENCE,
    )
    set_routing(rubric, "needs_review" if max_diff >= REVIEW_THRESHOLD else "auto_accept", [])
    rubric.scores = [
        CriterionScore(
            criterion=a.criterion,
            score=min(int(a.score), int(b.score)),
            evidence=a.evidence,
            comment_de=a.comment_de,
            confidence=a.confidence,
        )
        for a, b in pairs
    ]
    return rubric


# ---------------------------------------------------------------- routing (ADR-0017)


def set_routing(rubric: RubricResult, decision: Routing, reasons: list[str]) -> None:
    """Keep ``needs_review`` in sync: True iff routing == needs_review."""
    rubric.routing = decision
    rubric.routing_reasons = list(reasons)
    rubric.needs_review = decision == "needs_review"


def _blueprint_or_none(blueprint_id: str) -> Blueprint | None:
    try:
        return get_blueprint(blueprint_id)
    except KeyError:
        return None


def build_handoff(
    rubric: RubricResult, *, task_text: str, learner_text: str, skill: str, reasons: list[str]
) -> ReviewHandoff:
    """Self-contained reviewer payload (ADR-0017 §5). ``assessment_scores`` are the first pass (the
    shown scores are the per-criterion minimum when a validator ran)."""
    assessment_scores = {s.criterion: int(s.score) for s in rubric.scores}
    validator_scores: dict[str, int] | None = None
    if rubric.validator is not None:
        assessment_scores = {a.criterion: a.assessment_score for a in rubric.validator.agreements}
        validator_scores = {s.criterion: int(s.score) for s in rubric.validator.scores}
    return ReviewHandoff(
        task_text=task_text,
        learner_text=learner_text,
        assessment_scores=assessment_scores,
        validator_scores=validator_scores,
        disagreeing_criteria=list(rubric.validator.disagreeing_criteria) if rubric.validator else [],
        evidence={s.criterion: list(s.evidence) for s in rubric.scores if s.evidence},
        reason=routing.explain_de(rubric.routing, reasons, skill),
        reasons=list(reasons),
    )


def apply_routing(
    rubric: RubricResult,
    *,
    learner_text: str,
    task_text: str,
    learner_id: str,
    skill: str,
    stated_total: int | None = None,
) -> RubricResult:
    """route() → optional stratified spot-check promotion → ReviewHandoff for every non-auto item."""
    assessment_scores = rubric.scores
    if rubric.validator is not None:  # route on the first pass's confidences, not the min-merged scores
        by_name = {s.criterion: s for s in rubric.scores}
        assessment_scores = [
            CriterionScore(
                criterion=a.criterion,
                score=a.assessment_score,
                confidence=by_name[a.criterion].confidence,
            )
            for a in rubric.validator.agreements
        ]
    checks = routing.integration_checks(
        rubric.task_type,
        _blueprint_or_none(rubric.blueprint_id),
        rubric,
        learner_text,
        stated_total=stated_total,
        first_pass_scores=assessment_scores,
    )
    decision = routing.route(assessment_scores, rubric.validator, checks)
    verdict: Routing = decision.decision
    reasons = list(decision.reasons)
    if verdict == "auto_accept" and routing.stratified_spot_check(learner_id, skill):
        verdict = "spot_check"
        reasons.append(f"spot_check:stratified_sample:{skill}")
    set_routing(rubric, verdict, reasons)
    rubric.review_handoff = (
        build_handoff(rubric, task_text=task_text, learner_text=learner_text, skill=skill, reasons=reasons)
        if verdict != "auto_accept"
        else None
    )
    return rubric


# ---------------------------------------------------------------- task context


def _task_context(task_id: str | None, task_text: str | None, skill: str) -> dict[str, Any]:
    """Resolve prompt inputs from a stored task or an ad-hoc text."""
    if task_id:
        t = load_task(task_id)
        payload = t.payload
        parts = [str(payload.get("instructions_de", ""))]
        if payload.get("source_text"):
            parts.append(f"Quelle:\n{payload['source_text']}")
        if payload.get("graphic_description"):
            parts.append(f"Grafik: {payload['graphic_description']}")
        return {
            "task_text": task_text or "\n\n".join(p for p in parts if p),
            "expected_content": list(payload.get("expected_content", [])),
            "blueprint_id": t.blueprint_id,
            "task_type": t.task_type,
        }
    return {
        "task_text": task_text or "Freie Textproduktion (ohne Aufgabenstellung)",
        "expected_content": [],
        "blueprint_id": f"adhoc_{skill}",
        "task_type": "adhoc",
    }


def _score_with_validator(
    messages: list[dict[str, str]],
    out: LLMRubricOutput,
    *,
    rubric_version: str,
    ctx: dict[str, Any],
    prompt_version: str,
    is_progress_point: bool,
    sid: str,
    learner_id: str,
    learner_text: str,
    skill: str,
) -> RubricResult:
    rubric = _rubric_from_output(
        out,
        rubric_version=rubric_version,
        blueprint_id=str(ctx["blueprint_id"]),
        task_type=str(ctx["task_type"]),
        prompt_version=prompt_version,
        learner_text=learner_text,
    )
    if is_progress_point:
        # Independent second rater: the SAME task + text + rubric messages, nothing from the first pass.
        second = complete(
            "validator",
            messages,
            LLMRubricOutput,
            prompt_version=prompt_version,
            session_id=sid,
            learner_id=learner_id,
            temperature=0.0,
        )
        from app.core.models import recorded_model

        actual_model = recorded_model("validator", learner_id, sid, resolve_tier("validator").model)
        rubric = apply_validator(rubric, second.scores, prompt_version=prompt_version, model_version=actual_model)
    return apply_routing(
        rubric,
        learner_text=learner_text,
        task_text=str(ctx["task_text"]),
        learner_id=learner_id,
        skill=skill,
        stated_total=out.total,
    )


def _response(
    result_id: str, rubric: RubricResult, cost_eur: float, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    total = rubric.total
    return {
        "result_id": result_id,
        "rubric": rubric.model_dump(mode="json"),
        "total": total,
        "total_max": rubric.total_max,
        "internal_score_20": internal_scale(total),
        "needs_review": rubric.needs_review,
        "routing": rubric.routing,
        "review_handoff": rubric.review_handoff.model_dump(mode="json") if rubric.review_handoff else None,
        "label": LABEL_DE,
        "cost_eur": cost_eur,
        **(extra or {}),
    }


# ---------------------------------------------------------------- writing


def assess_writing(
    learner_text: str,
    *,
    task_id: str | None = None,
    task_text: str | None = None,
    expected_content: list[str] | None = None,
    learner_id: str = "default",
    is_progress_point: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    if not learner_text.strip():
        raise AssessmentError("learner_text darf nicht leer sein")
    ensure_learner(learner_id)
    rubric_version = "schreiben_v1"
    ctx = _task_context(task_id, task_text, "schreiben")
    content = expected_content if expected_content else list(ctx["expected_content"])
    p = load_prompt("writing_feedback")
    text = p.render(
        task_text=ctx["task_text"],
        expected_content="\n".join(f"- {c}" for c in content) if content else "—",
        learner_text=learner_text,
        rubric_version=rubric_version,
        rubric_criteria=criteria_text(rubric_version),
        error_tag_vocabulary=", ".join(error_tag_vocabulary()),
    )
    messages = [{"role": "user", "content": text}]
    sid = session_id or f"assess-{new_id()}"
    cost_before = ledger.session_cost_eur(sid)
    out = complete(
        "assessment",
        messages,
        LLMRubricOutput,
        prompt_version=p.version,
        session_id=sid,
        learner_id=learner_id,
        temperature=0.0,
    )
    rubric = _score_with_validator(
        messages,
        out,
        rubric_version=rubric_version,
        ctx=ctx,
        prompt_version=p.version,
        is_progress_point=is_progress_point,
        sid=sid,
        learner_id=learner_id,
        learner_text=learner_text,
        skill="schreiben",
    )
    result_id = results.store_rubric_result(
        learner_id,
        rubric,
        skill="schreiben",
        session_id=session_id,
        task_id=task_id,
        is_progress_point=is_progress_point,
        extra={"learner_text": learner_text, "task_text": ctx["task_text"]},
    )
    return _response(result_id, rubric, ledger.session_cost_eur(sid) - cost_before)


# ---------------------------------------------------------------- speaking


def _prosody_fields(prosody: ProsodyReport | None) -> dict[str, Any]:
    if prosody is None:
        return {
            "wpm": "—",
            "long_pauses": "—",
            "repetitions": "—",
            "fillers": "—",
            "low_confidence_words": "—",
        }
    f = flags_for_prompt(prosody)
    return {
        "wpm": f"{f['wpm']} ({f['tempo']})" if f["tempo"] else str(f["wpm"]),
        "long_pauses": f["long_pauses"],
        "repetitions": f["repetitions"] or "keine",
        "fillers": f["fillers"] or "keine",
        "low_confidence_words": f["low_confidence_words"] or "keine",
    }


def pronunciation_tip(prosody: ProsodyReport | None, *, session_id: str | None, learner_id: str) -> PronunciationTip:
    """One qualitative tip from the prosody report (conversation tier); neutral tip without audio."""
    if prosody is None or prosody.n_words == 0:
        return NEUTRAL_TIP.model_copy()
    p = load_prompt("pronunciation_tip")
    flags = flags_for_prompt(prosody)
    phone_flags = flags.pop("phone_flags", [])
    text = p.render(
        prosody_report_json=json.dumps(flags, ensure_ascii=False),
        phone_flags_json=json.dumps(phone_flags, ensure_ascii=False) if phone_flags else "—",
    )
    tip = complete(
        "conversation",
        [{"role": "user", "content": text}],
        PronunciationTip,
        prompt_version=p.version,
        session_id=session_id,
        learner_id=learner_id,
    )
    tip.practice_words = tip.practice_words[:3]
    return tip


def assess_speaking(
    transcript: str,
    *,
    task_id: str | None = None,
    task_text: str | None = None,
    prosody: ProsodyReport | None = None,
    learner_id: str = "default",
    is_progress_point: bool = False,
    session_id: str | None = None,
    ledger_session_id: str | None = None,
) -> dict[str, Any]:
    if not transcript.strip():
        raise AssessmentError("transcript darf nicht leer sein (keine Sprache erkannt)")
    ensure_learner(learner_id)
    rubric_version = "sprechen_v1"
    ctx = _task_context(task_id, task_text, "sprechen")
    p = load_prompt("speaking_feedback")
    text = p.render(
        rubric_version=rubric_version,
        task_text=ctx["task_text"],
        transcript=transcript,
        rubric_criteria=criteria_text(rubric_version),
        error_tag_vocabulary=", ".join(error_tag_vocabulary()),
        **_prosody_fields(prosody),
    )
    messages = [{"role": "user", "content": text}]
    sid = ledger_session_id or session_id or f"assess-{new_id()}"
    cost_before = ledger.session_cost_eur(sid)
    out = complete(
        "assessment",
        messages,
        LLMRubricOutput,
        prompt_version=p.version,
        session_id=sid,
        learner_id=learner_id,
        temperature=0.0,
    )
    rubric = _score_with_validator(
        messages,
        out,
        rubric_version=rubric_version,
        ctx=ctx,
        prompt_version=p.version,
        is_progress_point=is_progress_point,
        sid=sid,
        learner_id=learner_id,
        learner_text=transcript,
        skill="sprechen",
    )
    tip = pronunciation_tip(prosody, session_id=sid, learner_id=learner_id)
    prosody_json = prosody.model_dump(mode="json") if prosody else None
    result_id = results.store_rubric_result(
        learner_id,
        rubric,
        skill="sprechen",
        session_id=session_id,
        task_id=task_id,
        is_progress_point=is_progress_point,
        extra={
            "transcript": transcript,
            "task_text": ctx["task_text"],
            "prosody": prosody_json,
            "pronunciation_tip": tip.model_dump(mode="json"),
        },
    )
    return _response(
        result_id,
        rubric,
        ledger.session_cost_eur(sid) - cost_before,
        {"pronunciation_tip": tip.model_dump(mode="json")},
    )


def assess_speaking_audio(
    data: bytes,
    *,
    task_id: str | None = None,
    task_text: str | None = None,
    learner_id: str = "default",
    is_progress_point: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Audio → STT (metered) → prosody → rubric assessment. Audio is deleted after STT (rule 9)."""
    if not data:
        raise AssessmentError("Leere Audiodatei")
    sid = session_id or f"assess-{new_id()}"
    cost_before = ledger.session_cost_eur(sid)
    with temp_wav(data, session_id=sid, learner_id=learner_id) as wav:
        transcript = get_stt().transcribe(wav, session_id=sid, learner_id=learner_id)
    prosody = analyze_prosody_metered(transcript, session_id=sid, learner_id=learner_id)
    out = assess_speaking(
        transcript.text,
        task_id=task_id,
        task_text=task_text,
        prosody=prosody,
        learner_id=learner_id,
        is_progress_point=is_progress_point,
        session_id=session_id,
        ledger_session_id=sid,
    )
    out["transcript"] = transcript.text
    out["prosody"] = prosody.model_dump(mode="json")
    out["cost_eur"] = ledger.session_cost_eur(sid) - cost_before
    return out


__all__ = [
    "AssessmentError",
    "TaskNotFound",
    "assess_speaking",
    "assess_speaking_audio",
    "assess_writing",
    "apply_validator",
    "apply_routing",
    "build_handoff",
    "confidence_missing",
    "normalize_scores",
    "filter_error_tags",
    "pronunciation_tip",
    "set_routing",
]
