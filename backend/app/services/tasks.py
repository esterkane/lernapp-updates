"""Task generation (generator → independent validator → consistency test) and deterministic scoring.

Binding: ADR-0004 §1, ADR-0005, product rules 2–4. Exam parameters come only from the blueprint;
the LLM produces *content* for one blueprint slot. Lesen/Hören are scored against the stored
answer key; the LLM is only asked to explain wrong items.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from sqlalchemy import select

from app.core import ledger
from app.core.blueprints import Blueprint, BlueprintTask, get_blueprint, load_blueprints
from app.core.db import db_session
from app.core.models import ConfigError, resolve_tier, vendor_of
from app.core.prompts import load_prompt
from app.core.retry import FailureCategory, Rejection, ValidationFailure, complete_validated
from app.db.base import Learner, Task, new_id
from app.services import results
from app.services.llm import complete
from app.services.schemas import GeneratedTask, ValidationIssue, ValidationReport
from app.services.tts import get_tts

log = logging.getLogger(__name__)

DETERMINISTIC_SKILLS = frozenset({"lesen", "hoeren"})
MIN_AGREEMENT = 0.9
MAX_RETRIES = 2  # regenerate at most twice → 3 attempts in total (recoverable categories only, ADR-0017 §3)
_WS_RE = re.compile(r"\s+")
# Validator issue text → retry category. Futile (copyright) rejects immediately; consistency/format retry.
COPYRIGHT_MARKERS = ("kopie", "offiziell", "urheber", "abgeschrieben", "modelltest", "originalaufgabe")
CONSISTENCY_MARKERS = ("item", "lösung", "loesung", "schlüssel", "konsistenz", "antwort")


class TaskError(ValueError):
    """Client error (HTTP 400): unknown task type, wrong skill for the operation, ..."""


class TaskNotFound(LookupError):
    """Unknown task id (HTTP 404)."""


# ---------------------------------------------------------------- helpers


def ensure_learner(learner_id: str) -> str:
    """Create the learner row on first use so result rows satisfy the FK (single-user install)."""
    with db_session() as db:
        if db.get(Learner, learner_id) is None:
            db.add(Learner(id=learner_id))
    return learner_id


def task_session_id(task_id: str) -> str:
    """Ledger session id that groups every provider call made while generating one task."""
    return f"task-{task_id}"


def normalize_answer(value: str | None) -> str:
    return _WS_RE.sub(" ", (value or "").strip().lower())


def agreement_ratio(expected: dict[str, str], solved: dict[str, str]) -> float:
    """Fraction of items where the validator's blind solution matches the answer key."""
    if not expected:
        return 0.0
    hits = sum(1 for k, v in expected.items() if normalize_answer(solved.get(k)) == normalize_answer(v))
    return hits / len(expected)


def public_view(task: GeneratedTask | dict[str, Any]) -> dict[str, Any]:
    """GeneratedTask as shown to the learner: no answer key, no per-item answers or hints."""
    data = task.model_dump(mode="json") if isinstance(task, GeneratedTask) else dict(task)
    data.pop("expected_answers", None)
    hidden = ("answer", "explanation_hint")
    data["items"] = [{k: v for k, v in item.items() if k not in hidden} for item in data.get("items", [])]
    return data


def _blueprint_task(bp: Blueprint, task_type: str) -> BlueprintTask:
    bt = bp.task(task_type)
    if bt is None:
        raise TaskError(
            f"Aufgabentyp '{task_type}' existiert nicht im Blueprint {bp.id} "
            f"(verfügbar: {', '.join(bp.task_types) or 'keine'})"
        )
    return bt


def _topic_context(learner_id: str, topic: str | None) -> str:
    parts: list[str] = []
    if topic:
        parts.append(f"Thema: {topic}")
        try:
            from app.services import rag

            hits = rag.context_for(learner_id, topic, use_in="uebungen", k=4)
            if hits:
                parts.append(rag.format_context(hits))
        except Exception:  # noqa: BLE001 — grounding is optional, never blocks generation
            log.debug("RAG grounding unavailable for task generation", exc_info=True)
    return "\n\n".join(parts) if parts else "—"


def _task_blueprint(bp: Blueprint, bt: BlueprintTask) -> Blueprint:
    """A single slot's requirements, without importing the whole exam's functions."""
    return bp.model_copy(
        update={
            "tasks": [bt],
            "language_functions": list(
                bt.language_functions if bt.language_functions is not None else bp.language_functions
            ),
        }
    )


def _apply_blueprint_parameters(task: GeneratedTask, bp: Blueprint, bt: BlueprintTask, level: str) -> GeneratedTask:
    """Product rule 2: timings, rubric and level come from the blueprint, never from the LLM."""
    task.preparation_seconds = bt.preparation_seconds
    task.response_seconds = bt.response_seconds
    task.level = level
    if bp.scoring == "rubric":
        task.rubric_ref = bp.rubric_ref
    else:
        task.rubric_ref = None
    task.language_functions = _task_blueprint(bp, bt).language_functions
    if task.items and not task.expected_answers:
        task.expected_answers = {it.id: it.answer for it in task.items}
    return task


# ---------------------------------------------------------------- retry classification (ADR-0017 §3)


def classify_issue(issue: ValidationIssue) -> FailureCategory:
    """Map a validator blocker to a retry category: copyright → futile, item/solution → consistency, else format."""
    text = issue.message.lower()
    if any(m in text for m in COPYRIGHT_MARKERS):
        return "copyright_suspected"
    if any(m in text for m in CONSISTENCY_MARKERS):
        return "consistency"
    return "format"


def classify_report(report: ValidationReport) -> ValidationFailure | None:
    """``None`` when the validator accepted; otherwise the strongest category among the blockers."""
    blockers = [i for i in report.issues if i.severity == "blocker"]
    if report.ok and not blockers:
        return None
    issues = blockers or report.issues
    categories = {classify_issue(i) for i in issues}
    category: FailureCategory = (
        "copyright_suspected"
        if "copyright_suspected" in categories
        else "consistency"
        if "consistency" in categories
        else "format"
    )
    message = "; ".join(i.message for i in issues) or "Der Validator hat die Aufgabe abgelehnt."
    return ValidationFailure(category=category, message=message)


# ---------------------------------------------------------------- generation flow


def _generator_prompt(bp: Blueprint, bt: BlueprintTask, level: str, topic_context: str) -> tuple[str, str]:
    p = load_prompt("task_generator")
    text = p.render(
        level=level,
        blueprint_yaml=_task_blueprint(bp, bt).to_yaml(),
        topic_context=topic_context,
        task_type=bt.task_type,
        language_functions=", ".join(_task_blueprint(bp, bt).language_functions) or "—",
        rubric_ref=bp.rubric_ref or "—",
    )
    return text, p.version


def _validate_once(
    bp: Blueprint,
    task: GeneratedTask,
    level: str,
    *,
    session_id: str,
    learner_id: str,
    bt: BlueprintTask | None = None,
) -> ValidationReport:
    p = load_prompt("task_validator")
    text = p.render(
        blueprint_yaml=(_task_blueprint(bp, bt) if bt else bp).to_yaml(),
        task_json=json.dumps(task.model_dump(mode="json"), ensure_ascii=False),
        level=level,
    )
    report = complete(
        "validator",
        [{"role": "user", "content": text}],
        ValidationReport,
        prompt_version=p.version,
        session_id=session_id,
        learner_id=learner_id,
        temperature=0.0,
        prompt_name="task_validator",
    )
    if bt is not None and bt.n_items is not None and len(task.items) != bt.n_items:
        report.ok = False
        report.issues.append(
            ValidationIssue(
                severity="blocker",
                message=f"Blueprint verlangt {bt.n_items} Items, die Aufgabe enthält {len(task.items)}.",
            )
        )
    if bp.skill in DETERMINISTIC_SKILLS:
        report.agreement = agreement_ratio(task.expected_answers, report.solved_answers)
        if not task.items:
            report.ok = False
            report.issues.append(ValidationIssue(severity="blocker", message="Aufgabe enthält keine Items."))
        elif report.agreement < MIN_AGREEMENT:
            report.ok = False
            report.issues.append(
                ValidationIssue(
                    severity="blocker",
                    message=(
                        f"Lösungskonsistenz zu niedrig: Validator reproduziert den Lösungsschlüssel nur zu "
                        f"{report.agreement:.0%} (mindestens {MIN_AGREEMENT:.0%} erforderlich)."
                    ),
                )
            )
    else:
        report.agreement = None
    if any(i.severity == "blocker" for i in report.issues):
        report.ok = False
    return report


def generate_task(
    blueprint_id: str,
    task_type: str,
    *,
    level: str = "C1",
    topic: str | None = None,
    learner_id: str = "default",
) -> dict[str, Any]:
    try:
        bp = get_blueprint(blueprint_id)
    except KeyError as exc:
        raise TaskNotFound(str(exc)) from exc
    bt = _blueprint_task(bp, task_type)
    from app.core.models import recorded_model

    gen_model = resolve_tier("generator").model
    val_model = resolve_tier("validator").model
    if vendor_of(gen_model) == vendor_of(val_model):
        raise ConfigError(
            f"generator tier ({gen_model}) and validator tier ({val_model}) must use different vendors (product rule 3)"
        )
    ensure_learner(learner_id)
    task_id = new_id()
    sid = task_session_id(task_id)
    topic_context = _topic_context(learner_id, topic)

    # Generator → independent validator → consistency test, with retry classification (ADR-0017 §3):
    # format/consistency blockers regenerate (≤ MAX_RETRIES, error text injected), copyright suspicion
    # rejects immediately. Every attempt is a ledger row.
    prompt_text, gen_prompt_version = _generator_prompt(bp, bt, level, topic_context)
    last: dict[str, Any] = {}

    def validate(candidate: GeneratedTask) -> ValidationFailure | None:
        candidate = _apply_blueprint_parameters(candidate, bp, bt, level)
        report_ = _validate_once(bp, candidate, level, session_id=sid, learner_id=learner_id, bt=bt)
        last["task"], last["report"] = candidate, report_
        failure = classify_report(report_)
        if failure is not None:
            log.info("task %s attempt rejected (%s): %s", task_id, failure.category, failure.message[:200])
        return failure

    outcome = complete_validated(
        "generator",
        [{"role": "user", "content": prompt_text}],
        GeneratedTask,
        validate,
        prompt_version=gen_prompt_version,
        session_id=sid,
        learner_id=learner_id,
        max_retries=MAX_RETRIES,
        prompt_name="task_generator",
        complete_fn=complete,
    )
    rejection: Rejection | None = outcome if isinstance(outcome, Rejection) else None
    task: GeneratedTask = last["task"]
    report: ValidationReport = last["report"]
    status = "ok" if rejection is None else "rejected"

    payload = task.model_dump(mode="json")
    if status == "ok" and bp.skill == "hoeren" and task.source_text:
        audio = get_tts().synthesize(task.source_text, session_id=sid, learner_id=learner_id)
        payload["audio"] = {"b64": base64.b64encode(audio.data).decode("ascii"), "mime": audio.mime}

    prompt_versions = {
        "generator": load_prompt("task_generator").version,
        "validator": load_prompt("task_validator").version,
    }
    validation = report.model_dump(mode="json")
    if rejection is not None:
        validation["rejection"] = rejection.to_dict()
    with db_session() as db:
        db.add(
            Task(
                id=task_id,
                learner_id=learner_id,
                blueprint_id=bp.id,
                blueprint_version=bp.version,
                skill=bp.skill,
                task_type=task_type,
                level=level,
                payload=payload,
                generator_model=gen_model,
                validator_model=recorded_model("validator", learner_id, sid, val_model),
                prompt_versions=prompt_versions,
                status=status,
                validation=validation,
            )
        )
    return get_task(task_id)


# ---------------------------------------------------------------- read


def _task_to_response(t: Task) -> dict[str, Any]:
    bp = load_blueprints().get(t.blueprint_id)
    bt = bp.task(t.task_type) if bp else None
    payload = dict(t.payload)
    audio = payload.pop("audio", None)
    view: dict[str, Any] | None = public_view(payload)
    if isinstance(audio, dict) and view is not None:
        view["audio_b64"] = str(audio.get("b64", ""))
        view["audio_mime"] = str(audio.get("mime", "audio/wav"))
    v = t.validation or {}
    if t.status != "ok":
        # Product rule 3: a task that failed validation is never shown to the learner.
        view = None
    return {
        "task_id": t.id,
        "status": t.status,
        "skill": t.skill,
        "task_type": t.task_type,
        "level": t.level,
        "title": payload.get("title", "") if view is not None else "",
        "task": view,
        "validation": {
            "ok": bool(v.get("ok", t.status == "ok")),
            "issues": list(v.get("issues", [])),
            "agreement": v.get("agreement"),
            "rejection": v.get("rejection"),
        },
        "blueprint": {
            "id": t.blueprint_id,
            "version": t.blueprint_version,
            "preparation_seconds": bt.preparation_seconds if bt else payload.get("preparation_seconds"),
            "response_seconds": bt.response_seconds if bt else payload.get("response_seconds"),
            "total_time_seconds": bp.total_time_seconds if bp else None,
        },
        "models": {"generator": t.generator_model, "validator": t.validator_model},
        "prompt_versions": dict(t.prompt_versions or {}),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "cost_eur": ledger.session_cost_eur(task_session_id(t.id)),
    }


def load_task(task_id: str) -> Task:
    with db_session() as db:
        t = db.get(Task, task_id)
        if t is None:
            raise TaskNotFound(f"Aufgabe {task_id} nicht gefunden")
        return t


def get_task(task_id: str) -> dict[str, Any]:
    return _task_to_response(load_task(task_id))


def list_tasks(*, skill: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    with db_session() as db:
        from sqlalchemy import or_

        from app.core.workspaces import selected_workspace

        q = select(Task).order_by(Task.created_at.desc()).limit(limit)
        workspace = selected_workspace.get()
        if workspace:
            from app.core.config import get_settings

            condition = Task.learner_id == workspace
            if workspace == get_settings().default_learner_id:
                condition = or_(condition, Task.learner_id.is_(None))
            q = q.where(condition)
        if skill:
            q = q.where(Task.skill == skill)
        return [
            {
                "task_id": t.id,
                "title": str(t.payload.get("title", "")),
                "skill": t.skill,
                "task_type": t.task_type,
                "level": t.level,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in db.execute(q).scalars()
        ]


# ---------------------------------------------------------------- deterministic scoring


def score_task(
    task_id: str,
    learner_id: str,
    answers: dict[str, str],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """ADR-0004 §1: compare against the stored answer key; the LLM only explains wrong items."""
    t = load_task(task_id)
    if t.skill not in DETERMINISTIC_SKILLS:
        raise TaskError(f"Aufgabe {task_id} ({t.skill}) wird per Rubrik bewertet, nicht deterministisch")
    if t.status != "ok":
        raise TaskError(f"Aufgabe {task_id} wurde vom Validator abgelehnt und kann nicht bewertet werden")
    ensure_learner(learner_id)
    task = GeneratedTask.model_validate(t.payload)
    expected = task.expected_answers or {it.id: it.answer for it in task.items}
    if not expected:
        raise TaskError(f"Aufgabe {task_id} hat keinen Lösungsschlüssel")
    hints = {it.id: it.explanation_hint for it in task.items}
    questions = {it.id: it.question for it in task.items}

    correct: list[str] = []
    wrong: list[dict[str, Any]] = []
    for item_id, exp in expected.items():
        given = answers.get(item_id)
        if normalize_answer(given) == normalize_answer(exp):
            correct.append(item_id)
        else:
            wrong.append(
                {
                    "id": item_id,
                    "question": questions.get(item_id, ""),
                    "given": given,
                    "expected": exp,
                    "explanation_de": hints.get(item_id) or "",
                }
            )

    sid = session_id or f"score-{task_id}-{new_id()[:8]}"
    cost_before = ledger.session_cost_eur(sid)
    explanations: str | None = None
    prompt_version: str | None = None
    model_version: str | None = None
    if wrong:
        p = load_prompt("error_explanation")
        prompt_version = p.version
        model_version = resolve_tier("conversation").model
        text = p.render(
            source_text=task.source_text or "—",
            wrong_items_json=[{k: v for k, v in w.items() if k != "explanation_de"} for w in wrong],
        )
        explanations = complete(
            "conversation",
            [{"role": "user", "content": text}],
            prompt_version=p.version,
            session_id=sid,
            learner_id=learner_id,
            prompt_name="error_explanation",
        )
    n_items = len(expected)
    score = len(correct)
    result_id = results.store_deterministic_result(
        learner_id,
        skill=t.skill,
        task_type=t.task_type,
        blueprint_id=t.blueprint_id,
        score=score,
        score_max=n_items,
        session_id=session_id,
        task_id=task_id,
        prompt_version=prompt_version,
        model_version=model_version,
        payload={
            "answers": answers,
            "correct": correct,
            "wrong": wrong,
            "explanations_de": explanations,
            "blueprint_version": t.blueprint_version,
        },
    )
    return {
        "result_id": result_id,
        "task_id": task_id,
        "skill": t.skill,
        "task_type": t.task_type,
        "score": score,
        "score_max": n_items,
        "internal_score_20": round(score / n_items * 20) if n_items else 0,
        "correct": correct,
        "wrong": wrong,
        "explanations_de": explanations,
        "source_text": task.source_text,
        "label": "interne Übungsbewertung",
        "cost_eur": ledger.session_cost_eur(sid) - cost_before,
    }
