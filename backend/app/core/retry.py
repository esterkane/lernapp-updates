"""Retry classification for validated structured output (ADR-0017 §3, guardrail-hooks skill).

``complete_validated`` calls ``llm.complete`` (every attempt is a ledger row) and runs ``validate``
on the parsed result. Recoverable failures (``format``, ``consistency``) are retried at most
``max_retries`` times with the error text appended as a user message; futile categories reject
immediately with a ``Rejection`` record (category + attempt history), never a retry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from app.core.hooks import SessionState
from app.services import llm

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

FailureCategory = Literal[
    "format", "consistency", "copyright_suspected", "unsafe_content", "blueprint_unfixable", "source_missing"
]
RECOVERABLE: frozenset[str] = frozenset({"format", "consistency"})
FUTILE: frozenset[str] = frozenset({"copyright_suspected", "unsafe_content", "blueprint_unfixable", "source_missing"})

RETRY_PREFIX_DE = (
    "Die vorherige Antwort wurde abgelehnt. Behebe genau dieses Problem und antworte erneut NUR mit JSON: "
)


@dataclass(frozen=True)
class ValidationFailure:
    category: FailureCategory
    message: str


@dataclass(frozen=True)
class Attempt:
    n: int
    category: str | None
    message: str | None


@dataclass
class Rejection:
    """Terminal outcome: the last failure's category plus every attempt's classification."""

    category: str
    history: list[Attempt] = field(default_factory=list)

    @property
    def attempts(self) -> int:
        return len(self.history)

    @property
    def futile(self) -> bool:
        return self.category in FUTILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "futile": self.futile,
            "attempts": self.attempts,
            "history": [{"attempt": a.n, "category": a.category, "message": a.message} for a in self.history],
        }


def complete_validated(
    tier: str,
    messages: list[Any],
    schema: type[T],
    validate: Callable[[T], ValidationFailure | None],
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    max_retries: int = 2,
    prompt_name: str | None = None,
    state: SessionState | None = None,
    temperature: float | None = None,
    complete_fn: Callable[..., Any] | None = None,
) -> T | Rejection:
    """Structured completion + validation with recoverable-vs-futile retry classification.

    ``complete_fn`` defaults to ``llm.complete`` (injectable so callers that patch their own
    ``complete`` symbol keep working). Provider errors (``LLMError``) propagate unchanged.
    """
    call = complete_fn or llm.complete
    history: list[Attempt] = []
    msgs: list[Any] = list(messages)
    for n in range(1, max_retries + 2):
        result: T = call(
            tier,
            msgs,
            schema,
            prompt_version=prompt_version,
            session_id=session_id,
            learner_id=learner_id,
            temperature=temperature,
            prompt_name=prompt_name,
            state=state,
        )
        failure = validate(result)
        if failure is None:
            return result
        history.append(Attempt(n=n, category=failure.category, message=failure.message))
        if failure.category in FUTILE:
            log.info("%s attempt %d rejected (futile: %s): %s", tier, n, failure.category, failure.message)
            return Rejection(category=failure.category, history=history)
        if n > max_retries:
            break
        log.info("%s attempt %d rejected (%s), retrying with error text", tier, n, failure.category)
        msgs = msgs + [{"role": "user", "content": RETRY_PREFIX_DE + failure.message}]
    return Rejection(category=history[-1].category or "format", history=history)
