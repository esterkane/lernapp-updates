"""Shared Pydantic schemas used across services, API and prompts (ADR-0004, 0010, 0013)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, conint, field_validator, model_validator

from app.services.exam_schemas import ExamDraft

# ---------------------------------------------------------------- speech


class Word(BaseModel):
    text: str
    start: float
    end: float
    prob: float = 1.0


class Transcript(BaseModel):
    text: str
    words: list[Word] = []
    duration_s: float = 0.0
    language: str = "de"
    backend: str = "unknown"
    model: str = "unknown"


class AudioBytes(BaseModel):
    data: bytes
    mime: str  # audio/mpeg | audio/wav
    chars: int
    backend: str = "unknown"
    model: str = "unknown"


class PronunciationFlag(BaseModel):
    word: str
    reason: str  # e.g. "möglicherweise undeutlich"
    position: float | None = None  # seconds


class PhoneFlag(BaseModel):
    word: str
    target_phone: str
    heard_like: str
    position: float | None = None


class ProsodyReport(BaseModel):
    """Qualitative prosody MVP output (product rule 6: never a percentage or score)."""

    wpm: float = 0.0
    duration_s: float = 0.0
    n_words: int = 0
    long_pauses: list[dict[str, float]] = []  # {"start":..,"end":..,"seconds":..}
    pause_ratio: float = 0.0
    repetitions: list[str] = []
    self_corrections: list[str] = []
    fillers: dict[str, int] = {}
    n_fillers: int = 0
    low_confidence_words: list[str] = []
    pronunciation_flags: list[PronunciationFlag] = []
    phone_flags: list[PhoneFlag] = []
    tempo_label_de: str = ""  # "eher langsam" | "angemessen" | "eher zügig"

    def summary_de(self) -> str:
        parts = [f"Sprechtempo {self.wpm:.0f} W/min ({self.tempo_label_de})"]
        if self.long_pauses:
            parts.append(f"{len(self.long_pauses)} lange Pause(n)")
        if self.n_fillers:
            parts.append(f"{self.n_fillers} Füllwörter")
        if self.repetitions:
            parts.append(f"{len(self.repetitions)} Wiederholungen")
        if self.pronunciation_flags:
            parts.append("möglicherweise undeutlich: " + ", ".join(f.word for f in self.pronunciation_flags[:3]))
        return "; ".join(parts)


class PronunciationTip(BaseModel):
    tip_de: str
    practice_words: list[str] = Field(default_factory=list, max_length=3)


# ---------------------------------------------------------------- assessment

Routing = Literal["auto_accept", "needs_review", "spot_check"]
ROUTING_VALUES: tuple[str, ...] = ("auto_accept", "needs_review", "spot_check")
OTHER_ERROR_TAG = "other"  # enum + "other" + detail idiom (learnings-udacity §2)
DEFAULT_CONFIDENCE = 0.5  # used when the model omits ``confidence`` (recorded as ``confidence_missing``)
_WS = re.compile(r"\s+")


def normalise_for_match(text: str) -> str:
    """Whitespace-collapsed, case-folded form used for the evidence-in-text check."""
    return _WS.sub(" ", text).strip().casefold()


def evidence_in_text(quote: str, learner_text: str) -> bool:
    q = normalise_for_match(quote)
    return bool(q) and q in normalise_for_match(learner_text)


def _vocabulary() -> set[str]:
    from app.core.rubrics import error_tag_vocabulary  # local import: rubrics reads a skill file

    return set(error_tag_vocabulary())


class CriterionScore(BaseModel):
    criterion: str
    score: conint(ge=0, le=4)  # type: ignore[valid-type]
    evidence: list[str] = []
    comment_de: str = ""
    confidence: float = Field(
        default=DEFAULT_CONFIDENCE,
        ge=0.0,
        le=1.0,
        description="Selbsteinschätzung 0–1, wie sicher diese Bewertung ist (nur ein Routing-Input, nie das Tor).",
    )


class CriterionAgreement(BaseModel):
    criterion: str
    assessment_score: int
    validator_score: int
    agree: bool  # |assessment − validator| < review threshold (2)


class ValidatorResult(BaseModel):
    model_version: str
    prompt_version: str
    scores: list[CriterionScore]
    max_disagreement: int = 0
    disagreeing_criteria: list[str] = []
    agreements: list[CriterionAgreement] = []
    review_confidence: float = Field(default=1.0, ge=0.0, le=1.0)  # mean self-rated confidence of the validator


class ReviewHandoff(BaseModel):
    """Self-contained payload for the human reviewer (ADR-0017 §5) — no chat transcript needed."""

    task_text: str
    learner_text: str
    assessment_scores: dict[str, int]
    validator_scores: dict[str, int] | None = None
    disagreeing_criteria: list[str] = []
    evidence: dict[str, list[str]] = {}
    reason: str  # one German sentence: why this item was selected
    reasons: list[str] = []  # machine-readable routing reasons
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class RubricResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rubric_version: str
    blueprint_id: str
    task_type: str
    scores: list[CriterionScore]
    error_tags: list[str] = []
    other_error_detail: str | None = None
    better_formulations: list[tuple[str, str]] = Field(default_factory=list, max_length=5)
    new_vocabulary: list[str] = []
    summary_de: str
    model_version: str = ""
    prompt_version: str = ""
    validator: ValidatorResult | None = None
    needs_review: bool = False
    routing: Routing = "auto_accept"
    routing_reasons: list[str] = []
    review_handoff: ReviewHandoff | None = None
    confidence_missing: bool = False
    dropped_evidence: list[str] = []
    # Set by the assessment service before validation so evidence can be checked; never dumped/stored.
    learner_text: str | None = Field(default=None, exclude=True)

    @field_validator("better_formulations", mode="before")
    @classmethod
    def _coerce_pairs(cls, v: Any) -> Any:
        out = []
        for item in v or []:
            if isinstance(item, dict):
                out.append((str(item.get("original", "")), str(item.get("improved", item.get("besser", "")))))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append((str(item[0]), str(item[1])))
        return out[:5]

    @field_validator("error_tags")
    @classmethod
    def _tags_in_vocabulary(cls, v: list[str]) -> list[str]:
        vocab = _vocabulary()
        unknown = [t for t in v if t != OTHER_ERROR_TAG and t not in vocab]
        if unknown:
            raise ValueError(f"error_tags outside the controlled vocabulary: {unknown}")
        return v

    @model_validator(mode="after")
    def _other_requires_detail(self) -> RubricResult:
        if OTHER_ERROR_TAG in self.error_tags and not (self.other_error_detail or "").strip():
            raise ValueError("other_error_detail is required when error_tags contains 'other'")
        return self

    @model_validator(mode="after")
    def _sync_needs_review(self) -> RubricResult:
        """``needs_review`` is True iff ``routing == "needs_review"`` (legacy payloads may only carry the bool)."""
        if "routing" not in self.model_fields_set and "needs_review" in self.model_fields_set and self.needs_review:
            self.routing = "needs_review"
        self.needs_review = self.routing == "needs_review"
        return self

    @model_validator(mode="after")
    def evidence_must_appear_in_text(self) -> RubricResult:
        """Deterministic hallucination check: drop (never raise) evidence that is not a normalised substring
        of the learner text and record it in ``dropped_evidence`` (cd15827 pattern, ADR-0017 §1)."""
        if self.learner_text is None:
            return self
        dropped: list[str] = []
        for s in self.scores:
            kept: list[str] = []
            for quote in s.evidence:
                if evidence_in_text(quote, self.learner_text):
                    kept.append(quote)
                else:
                    dropped.append(quote)
            s.evidence = kept
        if dropped:
            self.dropped_evidence = [*self.dropped_evidence, *dropped]
        return self

    @property
    def total(self) -> int:
        return sum(int(s.score) for s in self.scores)

    @property
    def total_max(self) -> int:
        return 4 * len(self.scores)


class LLMRubricOutput(BaseModel):
    """Schema handed to the assessment LLM (subset of RubricResult; versions are added by code)."""

    scores: list[CriterionScore]
    error_tags: list[str] = []
    other_error_detail: str | None = Field(
        default=None, description="Pflicht, wenn error_tags 'other' enthält: kurze Beschreibung des Fehlers."
    )
    better_formulations: list[tuple[str, str]] = Field(default_factory=list)
    new_vocabulary: list[str] = []
    summary_de: str
    total: int | None = Field(default=None, description="Optional; wird in Python neu berechnet und verglichen.")

    @field_validator("better_formulations", mode="before")
    @classmethod
    def _coerce_pairs(cls, v: Any) -> Any:
        return RubricResult._coerce_pairs(v)


# ---------------------------------------------------------------- tasks


class TaskItem(BaseModel):
    """One deterministic item (Lesen/Hören)."""

    id: str
    question: str
    options: list[str] = []  # empty for richtig/falsch/text-sagt-nichts → use fixed set
    answer: str
    explanation_hint: str | None = None


class GeneratedTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    instructions_de: str
    source_text: str | None = None  # reading text / listening script (own, original)
    graphic_description: str | None = None
    items: list[TaskItem] = []
    expected_content: list[str] = []
    expected_answers: dict[str, str] = {}
    rubric_ref: str | None = None
    language_functions: list[str] = []
    preparation_seconds: int | None = None
    response_seconds: int | None = None
    level: str = "C1"


class ValidationIssue(BaseModel):
    severity: Literal["blocker", "warn"]
    message: str


class ValidationReport(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = []
    solved_answers: dict[str, str] = {}
    agreement: float | None = None


# ---------------------------------------------------------------- roleplay


class RoleCard(BaseModel):
    role: str
    goals: list[str]
    constraints: list[str] = []
    batna: str = ""


class CounterpartCard(BaseModel):
    role: str
    personality: str
    hidden_targets: dict[str, str]
    concession_ladder: list[str]
    escalation_triggers: list[str] = []


class Scenario(BaseModel):
    id: str
    title_de: str
    category: Literal["gehalt", "einkauf", "vertrieb", "projektumfang", "beschwerde", "meeting"]
    difficulty: Literal[1, 2, 3]
    description_de: str = ""
    learner: RoleCard
    counterpart: CounterpartCard
    context_doc_tags: list[str] = []
    max_turns: int = 16
    voice: bool = False


class CoachReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    outcome_vs_goals: str
    moves_used: list[str] = []
    moves_missed: list[str] = []
    language_feedback: str = ""
    error_tags: list[str] = []
    missing_redemittel: list[str] = []
    better_formulations: list[tuple[str, str]] = Field(default_factory=list)
    next_focus: str = ""
    rubric_result: RubricResult | None = None

    @field_validator("better_formulations", mode="before")
    @classmethod
    def _coerce_pairs(cls, v: Any) -> Any:
        return RubricResult._coerce_pairs(v)


class LLMCoachOutput(BaseModel):
    outcome_vs_goals: str
    moves_used: list[str] = []
    moves_missed: list[str] = []
    language_feedback: str = ""
    error_tags: list[str] = []
    missing_redemittel: list[str] = []
    better_formulations: list[tuple[str, str]] = Field(default_factory=list)
    next_focus: str = ""
    rubric_scores: list[CriterionScore] = []
    rubric_summary_de: str = ""

    @field_validator("better_formulations", mode="before")
    @classmethod
    def _coerce_pairs(cls, v: Any) -> Any:
        return RubricResult._coerce_pairs(v)


# ---------------------------------------------------------------- misc


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


SCHEMAS: dict[str, type[BaseModel]] = {
    "ExamDraft": ExamDraft,
    "RubricResult": LLMRubricOutput,
    "LLMRubricOutput": LLMRubricOutput,
    "GeneratedTask": GeneratedTask,
    "ValidationReport": ValidationReport,
    "CoachReport": LLMCoachOutput,
    "LLMCoachOutput": LLMCoachOutput,
    "PronunciationTip": PronunciationTip,
}
