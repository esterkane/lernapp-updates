"""Deterministic review routing (ADR-0017 §1–2).

``route()`` is a pure function of (per-criterion confidence, validator agreement, integration checks) →
``auto_accept | needs_review``. Nothing here calls a model. ``stratified_spot_check()`` promotes
``ceil(10 %)`` of auto-accepted results per skill (never zero per stratum) to ``spot_check`` — the
drift net. Thresholds live in ``config/routing.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel

from app.core.paths import repo_root
from app.core.rubrics import SCORE_MAX, SCORE_MIN, criteria
from app.services.schemas import CriterionScore, Routing, RubricResult, ValidatorResult, evidence_in_text

if TYPE_CHECKING:
    from app.core.blueprints import Blueprint


@dataclass(frozen=True)
class RoutingThresholds:
    min_confidence: float = 0.85
    review_disagreement: int = 2
    spot_check_share: float = 0.10

    @property
    def spot_check_every(self) -> int:
        """Promote every n-th auto_accept result of a skill (n = 1 / share, at least 1)."""
        return max(1, round(1.0 / self.spot_check_share)) if self.spot_check_share > 0 else 0


class IntegrationCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class RoutingDecision(BaseModel):
    decision: Routing
    reasons: list[str] = []


def routing_config_path() -> Path:
    return repo_root() / "config" / "routing.yaml"


@lru_cache(maxsize=4)
def _load(path: str) -> RoutingThresholds:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return RoutingThresholds(
        min_confidence=float(raw.get("min_confidence", 0.85)),
        review_disagreement=int(raw.get("review_disagreement", 2)),
        spot_check_share=float(raw.get("spot_check_share", 0.10)),
    )


def load_thresholds(path: Path | None = None) -> RoutingThresholds:
    return _load(str(path or routing_config_path()))


# ---------------------------------------------------------------- integration checks


def integration_checks(
    task_type: str,
    blueprint: Blueprint | None,
    rubric_result: RubricResult,
    learner_text: str | None,
    *,
    stated_total: int | None = None,
    first_pass_scores: list[CriterionScore] | None = None,
) -> list[IntegrationCheck]:
    """Deterministic checks that need no model: evidence in text, score bounds, total, task type.

    ``stated_total`` (what the model claimed) is compared with the sum of ``first_pass_scores`` when
    given (the shown scores may already be the min-merge with the validator), else with the rubric's.
    """
    checks: list[IntegrationCheck] = []

    phantom = list(rubric_result.dropped_evidence)
    if learner_text is not None:
        phantom += [q for s in rubric_result.scores for q in s.evidence if not evidence_in_text(q, learner_text)]
    checks.append(
        IntegrationCheck(
            name="evidence_in_text",
            ok=not phantom,
            detail="" if not phantom else "Belege nicht im Text gefunden: " + "; ".join(phantom[:3]),
        )
    )

    out_of_range = [
        f"{s.criterion}={s.score}" for s in rubric_result.scores if not SCORE_MIN <= int(s.score) <= SCORE_MAX
    ]
    checks.append(
        IntegrationCheck(
            name="scores_in_range",
            ok=not out_of_range,
            detail="" if not out_of_range else "außerhalb 0–4: " + ", ".join(out_of_range),
        )
    )

    recomputed = sum(int(s.score) for s in rubric_result.scores)
    first_pass_total = sum(int(s.score) for s in first_pass_scores) if first_pass_scores else recomputed
    total_ok = rubric_result.total == recomputed and (stated_total is None or stated_total == first_pass_total)
    checks.append(
        IntegrationCheck(
            name="total_recomputed",
            ok=total_ok,
            detail="" if total_ok else f"Modell nennt {stated_total}, neu berechnet {first_pass_total}",
        )
    )

    try:
        expected = criteria(rubric_result.rubric_version)
    except KeyError:
        expected = []
    got = [s.criterion for s in rubric_result.scores]
    complete = bool(expected) and got == expected
    checks.append(
        IntegrationCheck(
            name="criteria_complete",
            ok=complete,
            detail="" if complete else f"erwartet {len(expected)} Kriterien, erhalten {len(got)}",
        )
    )

    if blueprint is not None:
        allowed = blueprint.task_types
        checks.append(
            IntegrationCheck(
                name="task_type_in_blueprint",
                ok=task_type in allowed,
                detail="" if task_type in allowed else f"{task_type!r} nicht in {blueprint.id}: {allowed}",
            )
        )
    return checks


# ---------------------------------------------------------------- route()


def route(
    assessment_scores: list[CriterionScore],
    validator: ValidatorResult | None,
    integration_checks: list[IntegrationCheck],
    thresholds: RoutingThresholds | None = None,
) -> RoutingDecision:
    """Pure function: needs_review if any confidence < min, any disagreement ≥ threshold, or any failed check."""
    t = thresholds or load_thresholds()
    reasons: list[str] = []
    for s in assessment_scores:
        if s.confidence < t.min_confidence:
            reasons.append(f"confidence:{s.criterion}={s.confidence:.2f}<{t.min_confidence:.2f}")
    if validator is not None:
        for a in validator.agreements:
            diff = abs(a.assessment_score - a.validator_score)
            if diff >= t.review_disagreement:
                reasons.append(f"disagreement:{a.criterion}:{a.assessment_score}vs{a.validator_score}")
    for c in integration_checks:
        if not c.ok:
            reasons.append(f"integration:{c.name}" + (f":{c.detail}" if c.detail else ""))
    return RoutingDecision(decision="needs_review" if reasons else "auto_accept", reasons=reasons)


# ---------------------------------------------------------------- stratified spot-check


def should_spot_check(auto_accept_count: int, thresholds: RoutingThresholds | None = None) -> bool:
    """``count`` = auto-accepted results of this skill already stored. The first (count 0) is always
    promoted so no stratum is empty; afterwards every n-th (n = 1 / share) → ceil(share · N) per skill."""
    every = (thresholds or load_thresholds()).spot_check_every
    return every > 0 and auto_accept_count % every == 0


def stratified_spot_check(learner_id: str, skill: str, thresholds: RoutingThresholds | None = None) -> bool:
    from app.services.results import auto_accept_count  # local import: results imports schemas only

    return should_spot_check(auto_accept_count(learner_id, skill), thresholds)


def explain_de(decision: Routing, reasons: list[str], skill: str) -> str:
    """One German sentence for the reviewer: why this item was selected."""
    kinds = {r.split(":")[0] for r in reasons}
    if decision == "spot_check":
        return "Stichprobe: Jede zehnte automatisch akzeptierte Bewertung dieser Fertigkeit wird zur Prüfung vorgelegt."
    parts: list[str] = []
    if "confidence" in kinds:
        crits = sorted(r.split(":")[1].split("=")[0] for r in reasons if r.startswith("confidence"))
        parts.append("das Bewertungsmodell war sich bei " + ", ".join(crits) + " unsicher")
    if "disagreement" in kinds:
        crits = sorted(r.split(":")[1] for r in reasons if r.startswith("disagreement"))
        parts.append("die Zweitmeinung weicht bei " + ", ".join(crits) + " um mindestens zwei Punkte ab")
    if "integration" in kinds:
        names = sorted(r.split(":")[1] for r in reasons if r.startswith("integration"))
        parts.append("eine Konsistenzprüfung ist fehlgeschlagen (" + ", ".join(names) + ")")
    if not parts:
        return "Diese Bewertung wurde zur Prüfung vorgelegt."
    return "Zur Prüfung vorgelegt, weil " + "; ".join(parts) + "."


__all__ = [
    "IntegrationCheck",
    "RoutingDecision",
    "RoutingThresholds",
    "explain_de",
    "integration_checks",
    "load_thresholds",
    "route",
    "should_spot_check",
    "stratified_spot_check",
]
