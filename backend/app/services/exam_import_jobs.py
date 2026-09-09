"""Browser-independent PDF imports; page commits make restarts resumable."""
from contextvars import copy_context
from threading import Lock, Thread
from typing import Any

import httpx

from app.services import exams, llm

_lock = Lock()
_jobs: dict[tuple[str, str], dict[str, Any]] = {}


def status(identifier: str, owner: str) -> dict[str, Any]:
    data = exams.editor(identifier, owner)  # Always enforce ownership, including cached jobs.
    with _lock:
        job = dict(_jobs.get((owner, identifier), {"running": False, "error": None}))
    return {**job, "completed": len(data.get("extracted_pages", [])), "total": len(data["pages"])}


def _work(identifier: str, owner: str) -> None:
    key = (owner, identifier)
    error = None
    try:
        data = exams.editor(identifier, owner)
        for page in range(1, len(data["pages"]) + 1):
            if page in data.get("extracted_pages", []):
                continue
            for attempt in range(2):
                try:
                    data = exams.extract(identifier, owner, page, page)
                    break
                except (llm.LLMError, httpx.HTTPError):
                    if attempt:
                        raise
    except Exception:
        error = "Ein Abschnitt konnte nicht verarbeitet werden. Fertige Seiten sind gespeichert; bitte fortsetzen."
    finally:
        with _lock:
            _jobs[key] = {"running": False, "error": error}


def start(identifier: str, owner: str) -> dict[str, Any]:
    exams.editor(identifier, owner)
    key = (owner, identifier)
    with _lock:
        if not _jobs.get(key, {}).get("running"):
            _jobs[key] = {"running": True, "error": None}
            context = copy_context()
            Thread(target=context.run, args=(_work, identifier, owner), daemon=True).start()
    return status(identifier, owner)
