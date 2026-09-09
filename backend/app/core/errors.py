"""Errors as values at boundaries (learnings §2/§3, ADR-0016).

``ToolResult`` is the uniform envelope every adapter/tool returns through the hook engine; only
``transient`` failures are retryable. ``FailureContext`` carries a German message plus concrete
``alternatives`` (config switches) so the session hub can decide fatal vs tolerated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

ErrorCategoryName = Literal["transient", "validation", "business", "permission"]


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    BUSINESS = "business"
    PERMISSION = "permission"


class FailureContext(BaseModel):
    """A failed boundary call as a value: what went wrong and what the user/operator could switch to."""

    category: ErrorCategoryName
    message: str
    alternatives: list[str] = Field(default_factory=list)

    @property
    def is_retryable(self) -> bool:
        return self.category == "transient"


class HookViolation(BaseModel):
    """One post-hook normalization event (the model produced something a product rule forbids)."""

    hook: str
    reason: str
    tier: str
    prompt_name: str | None = None
    session_id: str | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolResult(BaseModel):
    """Uniform envelope (``success`` + ``result`` | ``error``)."""

    success: bool
    result: Any = None
    error: FailureContext | None = None

    @classmethod
    def ok(cls, result: Any = None) -> ToolResult:
        return cls(success=True, result=result)

    @classmethod
    def fail(
        cls, category: ErrorCategoryName | ErrorCategory, message: str, alternatives: list[str] | None = None
    ) -> ToolResult:
        cat: ErrorCategoryName = category.value if isinstance(category, ErrorCategory) else category  # type: ignore[assignment]
        return cls(success=False, error=FailureContext(category=cat, message=message, alternatives=alternatives or []))

    @property
    def error_category(self) -> ErrorCategoryName | None:
        return self.error.category if self.error else None

    @property
    def is_retryable(self) -> bool:
        return bool(self.error and self.error.is_retryable)


class AdapterFailure(RuntimeError):
    """Raised by the session hub when a *fatal* boundary failure must reach the API (→ HTTP 502)."""

    def __init__(self, context: FailureContext) -> None:
        super().__init__(context.message)
        self.context = context
