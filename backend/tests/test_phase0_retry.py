"""ADR-0017 §3: retry classification — recoverable (format, consistency) retries ≤ 2 with the error text
injected; futile categories reject immediately; every attempt is a ledger row."""

from __future__ import annotations

from typing import Any

import pytest
from app.core import retry
from app.core.retry import FUTILE, RECOVERABLE, Rejection, ValidationFailure, complete_validated
from app.services import tasks as svc
from app.services.schemas import PronunciationTip, ValidationIssue, ValidationReport

from tests.conftest import ledger_count

IDS: dict[str, Any] = {"prompt_version": "1.0.0", "session_id": "t-retry", "learner_id": "default"}


def test_category_sets_are_disjoint_and_complete() -> None:
    assert frozenset({"format", "consistency"}) == RECOVERABLE
    assert frozenset({"copyright_suspected", "unsafe_content", "blueprint_unfixable", "source_missing"}) == FUTILE
    assert not (RECOVERABLE & FUTILE)


@pytest.mark.parametrize(
    ("category", "expected_attempts"),
    [
        ("format", 3),
        ("consistency", 3),
        ("copyright_suspected", 1),
        ("unsafe_content", 1),
        ("blueprint_unfixable", 1),
        ("source_missing", 1),
    ],
)
def test_classification_table(category: str, expected_attempts: int) -> None:
    calls = {"n": 0}

    def always_fail(_: PronunciationTip) -> ValidationFailure | None:
        calls["n"] += 1
        return ValidationFailure(category=category, message=f"Problem {category}")  # type: ignore[arg-type]

    before = ledger_count()
    out = complete_validated(
        "conversation", [{"role": "user", "content": "Tipp"}], PronunciationTip, always_fail, max_retries=2, **IDS
    )
    assert isinstance(out, Rejection)
    assert out.category == category and out.attempts == expected_attempts and calls["n"] == expected_attempts
    assert len(out.history) == expected_attempts and out.history[-1].message == f"Problem {category}"
    assert ledger_count() - before >= 2 * expected_attempts  # tokens_in + tokens_out per attempt


def test_success_after_one_recoverable_retry_injects_error_text() -> None:
    seen_messages: list[list[dict[str, str]]] = []
    original = retry.llm.complete

    def spy(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        seen_messages.append(list(messages))
        return original(tier, messages, schema, **kw)

    attempts = {"n": 0}

    def validate(_: PronunciationTip) -> ValidationFailure | None:
        attempts["n"] += 1
        return ValidationFailure(category="format", message="Feld tip_de fehlt") if attempts["n"] == 1 else None

    out = complete_validated(
        "conversation", [{"role": "user", "content": "Tipp"}], PronunciationTip, validate, complete_fn=spy, **IDS
    )
    assert isinstance(out, PronunciationTip)
    assert len(seen_messages) == 2
    assert seen_messages[1][-1]["role"] == "user" and "Feld tip_de fehlt" in seen_messages[1][-1]["content"]
    assert seen_messages[1][0] == seen_messages[0][0]


def test_happy_path_no_retry() -> None:
    out = complete_validated(
        "conversation", [{"role": "user", "content": "Tipp"}], PronunciationTip, lambda _: None, **IDS
    )
    assert isinstance(out, PronunciationTip) and out.tip_de


def test_max_retries_zero_means_single_attempt() -> None:
    out = complete_validated(
        "conversation",
        [{"role": "user", "content": "Tipp"}],
        PronunciationTip,
        lambda _: ValidationFailure(category="format", message="x"),
        max_retries=0,
        **IDS,
    )
    assert isinstance(out, Rejection) and out.attempts == 1


# ---------------------------------------------------------------- tasks.py uses the classifier


def test_classify_validation_issue_texts() -> None:
    blocker = ValidationIssue(severity="blocker", message="Der Text ist eine Kopie eines offiziellen Modelltests.")
    assert svc.classify_issue(blocker) == "copyright_suspected"
    assert (
        svc.classify_issue(ValidationIssue(severity="blocker", message="Lösungskonsistenz zu niedrig")) == "consistency"
    )
    assert svc.classify_issue(ValidationIssue(severity="blocker", message="Item 3 hat keine Optionen")) == "consistency"
    assert (
        svc.classify_issue(ValidationIssue(severity="blocker", message="Blueprint verlangt 5 Items, 3 gefunden"))
        == "consistency"
    )
    assert (
        svc.classify_issue(ValidationIssue(severity="blocker", message="Anleitung (instructions_de) fehlt."))
        == "format"
    )


def test_copyright_blocker_rejects_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"generator": 0, "validator": 0}
    original = svc.complete

    def copyright_validator(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        calls[tier] += 1
        out = original(tier, messages, schema, **kw)
        if isinstance(out, ValidationReport):
            out.ok = False
            out.issues = [ValidationIssue(severity="blocker", message="Verdacht auf Kopie aus offiziellem Material.")]
        return out

    monkeypatch.setattr(svc, "complete", copyright_validator)
    body = svc.generate_task("testdaf_digital_schreiben", "textproduktion_mit_quellen")
    assert body["status"] == "rejected"
    assert calls == {"generator": 1, "validator": 1}  # futile → no regeneration
    assert body["validation"]["ok"] is False
    assert body["validation"]["rejection"]["category"] == "copyright_suspected"
    assert body["validation"]["rejection"]["attempts"] == 1


def test_consistency_blocker_retries_then_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"generator": 0, "validator": 0}
    original = svc.complete

    def flipping(tier: str, messages: list[Any], schema: Any = None, **kw: Any) -> Any:
        calls[tier] += 1
        out = original(tier, messages, schema, **kw)
        if isinstance(out, ValidationReport):
            out.solved_answers = {k: "falsch" if v != "falsch" else "richtig" for k, v in out.solved_answers.items()}
        return out

    monkeypatch.setattr(svc, "complete", flipping)
    body = svc.generate_task("testdaf_digital_lesen", "richtig_falsch_textsagtnichts")
    assert body["status"] == "rejected"
    assert calls == {"generator": 3, "validator": 3}
    assert body["validation"]["rejection"]["category"] == "consistency"
    assert body["validation"]["rejection"]["attempts"] == 3
