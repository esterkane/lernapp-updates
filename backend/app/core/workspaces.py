"""Request-local workspace selection. The OS account owns all local workspaces."""

from contextvars import ContextVar

selected_workspace: ContextVar[str | None] = ContextVar("selected_workspace", default=None)


def current_workspace() -> str:
    from app.core.config import get_settings

    return selected_workspace.get() or get_settings().default_learner_id


def resolve_learner(learner_id: str | None = None) -> str:
    from fastapi import HTTPException

    selected = selected_workspace.get()
    if selected and learner_id and learner_id != selected:
        raise HTTPException(403, "Diese Daten gehören zu einem anderen Lernbereich.")
    return learner_id or current_workspace()
