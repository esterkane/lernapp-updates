"""Negotiation roleplay engine (ADR-0013, product rule 7; docs/api.md "Roleplay").

Scenarios come from ``scenarios/*.yaml`` (validated with ``schemas.Scenario`` and seeded into the
``scenarios`` table). A roleplay session stores the scenario id, the turn counter and the RAG
context in ``Session.state``; the dialogue itself is a list of ``Turn`` rows. The counterpart
(``conversation`` tier, prompt ``roleplay_counterpart``) never corrects the learner: the
``counterpart_no_correction`` post-hook (ADR-0016) flags a correcting reply, ``_counterpart_call``
regenerates once and the hook replaces a second offence with a neutral in-role line; ``sanitize_reply``
stays as defence in depth. Coach mode (``assessment`` tier, prompt ``roleplay_coach``) runs only after
"ROLLENSPIEL ENDE" or ``max_turns`` — enforced by the ``coach_requires_end_signal`` pre-hook via
``SessionState(roleplay_ended=True)``; only then are the counterpart's hidden targets revealed.
Every LLM/STT/TTS stage goes through the hook engine and the ledger.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from app.core import ledger, rubrics
from app.core.db import db_session
from app.core.hooks import SessionState
from app.core.models import resolve_tier
from app.core.paths import repo_root
from app.core.prompts import load_prompt
from app.db.base import Learner, Session, Turn
from app.db.base import Scenario as ScenarioRow
from app.services import audio, llm, pronunciation, rag, results
from app.services.learning_time import learning_increment, parse_last_turn
from app.services.schemas import (
    CoachReport,
    CriterionScore,
    LLMCoachOutput,
    ProsodyReport,
    RubricResult,
    Scenario,
    Transcript,
)
from app.services.stt import get_stt
from app.services.tts import get_tts

log = logging.getLogger(__name__)

SESSION_KIND = "roleplay"
END_SIGNAL = "ROLLENSPIEL ENDE"
RUBRIC_VERSION = "sprechen_v1"
TASK_TYPE = "verhandlung"
SKILL = "sprechen"
MOVES = frozenset(
    {
        "anker",
        "fragen_stellen",
        "paketloesung",
        "schweigen",
        "batna_nutzen",
        "zusammenfassen",
        "bedenken_aeussern",
        "zeit_gewinnen",
    }
)
# Lines the counterpart must never produce (product rule 7); stripped defensively.
_FORBIDDEN_LINE_PREFIXES = ("korrektur", "tipp:")

OPENING_REQUEST_DE = "Eröffne jetzt das Gespräch in deiner Rolle in ein bis zwei Sätzen."
ENDED_REPLY_DE = "Rollenspiel beendet."
NOTHING_UNDERSTOOD_DE = "Entschuldigen Sie, ich habe Sie akustisch nicht verstanden. Könnten Sie das bitte wiederholen?"
EMPTY_REPLY_FALLBACK_DE = "Verstehe. Wie stellen Sie sich das konkret vor?"


class ScenarioNotFound(KeyError):
    pass


class SessionNotFound(KeyError):
    pass


class SessionEnded(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- scenarios


def scenarios_dir() -> Path:
    return repo_root() / "scenarios"


def load_scenarios() -> list[Scenario]:
    """All ``scenarios/*.yaml`` validated against ``Scenario``; raises on the first invalid file."""
    out: list[Scenario] = []
    seen: set[str] = set()
    for path in sorted(scenarios_dir().glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"scenario file {path.name} must contain a mapping")
        try:
            scenario = Scenario.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"invalid scenario {path.name}: {exc}") from exc
        if scenario.id in seen:
            raise ValueError(f"duplicate scenario id '{scenario.id}' in {path.name}")
        seen.add(scenario.id)
        out.append(scenario)
    return out


def seed_scenarios() -> int:
    """Upsert the YAML scenarios into the ``scenarios`` table (source="seed"). Returns the count."""
    scenarios = load_scenarios()
    with db_session() as db:
        for s in scenarios:
            row = db.get(ScenarioRow, s.id)
            payload = s.model_dump(mode="json")
            if row is None:
                db.add(
                    ScenarioRow(
                        id=s.id,
                        category=s.category,
                        difficulty=int(s.difficulty),
                        title_de=s.title_de,
                        payload=payload,
                        source="seed",
                    )
                )
            else:
                row.category = s.category
                row.difficulty = int(s.difficulty)
                row.title_de = s.title_de
                row.payload = payload
                row.source = "seed"
    return len(scenarios)


def public_scenario(s: Scenario) -> dict[str, Any]:
    """Scenario as shown to the learner: no hidden targets, ladder or escalation triggers."""
    return {
        "id": s.id,
        "title_de": s.title_de,
        "category": s.category,
        "difficulty": s.difficulty,
        "description_de": s.description_de,
        "learner": s.learner.model_dump(mode="json"),
        "counterpart": {"role": s.counterpart.role, "personality": s.counterpart.personality},
        "context_doc_tags": list(s.context_doc_tags),
        "max_turns": s.max_turns,
        "voice": s.voice,
    }


def _ensure_seeded(db: Any) -> None:
    if db.execute(select(ScenarioRow.id).limit(1)).first() is None:
        db.expunge_all()
        seed_scenarios()


def get_scenario(scenario_id: str) -> Scenario:
    with db_session() as db:
        _ensure_seeded(db)
        row = db.get(ScenarioRow, scenario_id)
        if row is None:
            raise ScenarioNotFound(scenario_id)
        return Scenario.model_validate(row.payload)


def list_scenarios(category: str | None = None, difficulty: int | None = None) -> list[dict[str, Any]]:
    with db_session() as db:
        _ensure_seeded(db)
        q = select(ScenarioRow).order_by(ScenarioRow.category, ScenarioRow.difficulty, ScenarioRow.id)
        if category:
            q = q.where(ScenarioRow.category == category)
        if difficulty is not None:
            q = q.where(ScenarioRow.difficulty == int(difficulty))
        rows = list(db.execute(q).scalars())
    return [public_scenario(Scenario.model_validate(r.payload)) for r in rows]


# ---------------------------------------------------------------- prompts


def _counterpart_system(scenario: Scenario, rag_context: str) -> tuple[str, str]:
    prompt = load_prompt("roleplay_counterpart")
    cp = scenario.counterpart
    system = prompt.render(
        counterpart_role=cp.role,
        personality=cp.personality,
        hidden_targets=cp.hidden_targets,
        concession_ladder=cp.concession_ladder,
        escalation_triggers=cp.escalation_triggers,
        rag_context=rag_context,
    )
    return system, prompt.version


def _rag_context(scenario: Scenario, learner_id: str) -> str:
    query = f"{scenario.title_de} {' '.join(scenario.learner.goals)}"
    hits = rag.context_for(learner_id, query, use_in="rollenspiel", tags=list(scenario.context_doc_tags), k=5)
    return rag.format_context(hits)


def sanitize_reply(text: str) -> str:
    """Drop lines that look like language feedback (product rule 7) and log when that happens."""
    kept: list[str] = []
    dropped: list[str] = []
    for line in text.splitlines():
        if line.strip().lower().startswith(_FORBIDDEN_LINE_PREFIXES):
            dropped.append(line.strip())
        else:
            kept.append(line)
    if dropped:
        log.warning("counterpart produced correction lines, stripped: %s", dropped)
    cleaned = "\n".join(kept).strip()
    return cleaned or EMPTY_REPLY_FALLBACK_DE


def _counterpart_call(
    messages: list[dict[str, str]], *, prompt_version: str, session_id: str, learner_id: str, learner_text: str | None
) -> str:
    """Counterpart completion through the hook engine; regenerates once when the hook flags a correction."""
    state = SessionState(learner_text=learner_text)
    reply = llm.complete(
        "conversation",
        messages,
        prompt_version=prompt_version,
        session_id=session_id,
        learner_id=learner_id,
        prompt_name="roleplay_counterpart",
        state=state,
    )
    if state.extra.get("regenerate"):
        log.info("counterpart corrected the learner (session %s) – regenerating once", session_id)
        reply = llm.complete(
            "conversation",
            messages,
            prompt_version=prompt_version,
            session_id=session_id,
            learner_id=learner_id,
            prompt_name="roleplay_counterpart",
            state=state,
        )
    return sanitize_reply(reply)


# ---------------------------------------------------------------- session helpers


def _load(db: Any, session_id: str) -> Session:
    s: Session | None = db.get(Session, session_id)
    if s is None or s.kind != SESSION_KIND:
        raise SessionNotFound(session_id)
    return s


def _next_ord(db: Any, session_id: str) -> int:
    q = select(Turn.ord).where(Turn.session_id == session_id).order_by(Turn.ord.desc()).limit(1)
    last = db.execute(q).scalar()
    return 0 if last is None else int(last) + 1


def _turns(db: Any, session_id: str) -> list[Turn]:
    q = select(Turn).where(Turn.session_id == session_id).order_by(Turn.ord.asc())
    return list(db.execute(q).scalars())


def _accumulated(s: Session, now: datetime, extra_seconds: float = 0.0, *, started_at: datetime | None = None) -> float:
    """Running total + this turn's learning seconds (same rule as the tutor: ``services.learning_time``).

    Gap since the previous turn only when ≤ 5 min, plus audio input seconds, plus request→response
    processing time (``now - started_at``, capped at 120 s).
    """
    inc = learning_increment(parse_last_turn(s.state), started_at or now, now, extra_seconds)
    return float(s.duration_seconds or 0.0) + inc


def _speak(text: str, *, voice: bool, session_id: str, learner_id: str) -> tuple[str, str | None, str]:
    """TTS when ``voice`` is on. Returns (audio_b64, mime, backend)."""
    if not voice or not text:
        return "", None, ""
    spoken = get_tts().synthesize(text, session_id=session_id, learner_id=learner_id)
    b64 = base64.b64encode(spoken.data).decode("ascii") if spoken.data else ""
    return b64, spoken.mime, spoken.backend


def _transcribe(audio_bytes: bytes, *, session_id: str, learner_id: str) -> tuple[Transcript, ProsodyReport, float]:
    """STT + prosody inside ``temp_wav`` so the file is gone before any remote call (ADR-0011)."""
    with audio.temp_wav(audio_bytes, session_id=session_id, learner_id=learner_id) as path:
        transcript = get_stt().transcribe(path, session_id=session_id, learner_id=learner_id)
        prosody = pronunciation.analyze_prosody_metered(transcript, session_id=session_id, learner_id=learner_id)
    seconds = transcript.duration_s or audio.wav_seconds(audio_bytes)
    return transcript, prosody, float(seconds)


def _turn_dict(t: Turn) -> dict[str, Any]:
    return {"ord": t.ord, "role": t.role, "text": t.text, "ts": _iso(t.ts), "meta": dict(t.meta or {})}


# ---------------------------------------------------------------- start


def start(scenario_id: str, learner_id: str, voice: bool = False) -> dict[str, Any]:
    """Create a roleplay session and let the counterpart open the conversation."""
    scenario = get_scenario(scenario_id)
    with db_session() as db:
        if db.get(Learner, learner_id) is None:
            raise SessionNotFound(f"unknown learner {learner_id}")
        rag_context = _rag_context(scenario, learner_id)
        s = Session(
            learner_id=learner_id,
            kind=SESSION_KIND,
            state={
                "scenario_id": scenario.id,
                "turn": 0,
                "max_turns": scenario.max_turns,
                "voice": bool(voice),
                "ended": False,
                "rag_context": rag_context,
                "last_turn_at": None,
            },
        )
        db.add(s)
        db.flush()
        session_id = s.id

    system, prompt_version = _counterpart_system(scenario, rag_context)
    opening = _counterpart_call(
        [{"role": "system", "content": system}, {"role": "user", "content": OPENING_REQUEST_DE}],
        prompt_version=prompt_version,
        session_id=session_id,
        learner_id=learner_id,
        learner_text=None,
    )
    audio_b64, mime, tts_backend = _speak(opening, voice=voice, session_id=session_id, learner_id=learner_id)
    cost = ledger.session_cost_eur(session_id)
    now = _now()
    with db_session() as db:
        s = _load(db, session_id)
        db.add(
            Turn(
                session_id=session_id,
                ord=0,
                role="assistant",
                text=opening,
                ts=now,
                meta={"prompt_version": prompt_version, "opening": True, "tts_backend": tts_backend, "cost_eur": cost},
            )
        )
        s.state = {**(s.state or {}), "last_turn_at": now.isoformat()}
    return {
        "session_id": session_id,
        "scenario": public_scenario(scenario),
        "opening_line": opening,
        "opening_audio_b64": audio_b64,
        "opening_audio_mime": mime,
        "turn": 0,
        "max_turns": scenario.max_turns,
        "cost_eur_session": cost,
    }


# ---------------------------------------------------------------- turns


def _counterpart_reply(
    *, scenario: Scenario, rag_context: str, turns: list[Turn], session_id: str, learner_id: str
) -> tuple[str, str]:
    system, prompt_version = _counterpart_system(scenario, rag_context)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": OPENING_REQUEST_DE},
    ]
    for t in turns:
        if t.role == "learner":
            messages.append({"role": "user", "content": t.text or "…"})
        elif t.role == "assistant":
            messages.append({"role": "assistant", "content": t.text})
    learner_text = next((t.text for t in reversed(turns) if t.role == "learner"), None)
    reply = _counterpart_call(
        messages, prompt_version=prompt_version, session_id=session_id, learner_id=learner_id, learner_text=learner_text
    )
    return reply, prompt_version


def turn(session_id: str, text: str | None = None, audio_bytes: bytes | None = None) -> dict[str, Any]:
    """One learner turn (text or audio). Returns the dict described in docs/api.md."""
    if not text and not audio_bytes:
        raise ValueError("text oder Audio erforderlich")
    started_at = _now()
    with db_session() as db:
        s = _load(db, session_id)
        if s.ended_at is not None or (s.state or {}).get("ended"):
            raise SessionEnded(f"Rollenspiel {session_id} ist bereits beendet")
        learner_id = s.learner_id
        state = dict(s.state or {})
        ord_learner = _next_ord(db, session_id)
    scenario = get_scenario(str(state["scenario_id"]))
    voice = bool(state.get("voice", False))
    max_turns = int(state.get("max_turns", scenario.max_turns))
    cost_before = ledger.session_cost_eur(session_id)

    prosody: ProsodyReport | None = None
    audio_seconds = 0.0
    learner_meta: dict[str, Any] = {"input": "text"}
    if audio_bytes:
        transcript, prosody, audio_seconds = _transcribe(audio_bytes, session_id=session_id, learner_id=learner_id)
        learner_text = transcript.text.strip()
        learner_meta = {
            "input": "audio",
            "audio_seconds": audio_seconds,
            "stt_backend": transcript.backend,
            "stt_model": transcript.model,
            "prosody": prosody.model_dump(mode="json"),
            "flags": pronunciation.flags_for_prompt(prosody),
        }
    else:
        learner_text = (text or "").strip()

    new_turn = int(state.get("turn", 0)) + 1
    ended = END_SIGNAL in learner_text.upper() or new_turn >= max_turns
    now = _now()
    with db_session() as db:
        s = _load(db, session_id)
        db.add(
            Turn(session_id=session_id, ord=ord_learner, role="learner", text=learner_text, ts=now, meta=learner_meta)
        )
        # gap since the previous turn (≤ 5 min) + audio input; processing time is added with the reply
        s.duration_seconds = _accumulated(s, started_at, audio_seconds, started_at=started_at)
        s.state = {**(s.state or {}), "turn": new_turn, "last_turn_at": started_at.isoformat()}
        turns = _turns(db, session_id)

    if ended:
        return _finish(
            session_id=session_id,
            learner_id=learner_id,
            scenario=scenario,
            learner_text=learner_text,
            prosody=prosody,
            cost_before=cost_before,
        )

    if learner_text:
        reply_text, prompt_version = _counterpart_reply(
            scenario=scenario,
            rag_context=str(state.get("rag_context", "—")),
            turns=turns,
            session_id=session_id,
            learner_id=learner_id,
        )
    else:
        reply_text, prompt_version = NOTHING_UNDERSTOOD_DE, ""
    audio_b64, mime, tts_backend = _speak(reply_text, voice=voice, session_id=session_id, learner_id=learner_id)
    cost_after = ledger.session_cost_eur(session_id)
    cost_turn = max(cost_after - cost_before, 0.0)
    replied_at = _now()
    with db_session() as db:
        s = _load(db, session_id)
        db.add(
            Turn(
                session_id=session_id,
                ord=ord_learner + 1,
                role="assistant",
                text=reply_text,
                ts=replied_at,
                meta={"prompt_version": prompt_version, "tts_backend": tts_backend, "cost_eur": cost_turn},
            )
        )
        # processing time of this turn (request → reply, capped); the next gap is measured from the reply
        s.duration_seconds = _accumulated(s, replied_at, started_at=started_at)
        s.state = {**(s.state or {}), "last_turn_at": replied_at.isoformat()}
        duration = s.duration_seconds
        learning_seconds = learning_increment(parse_last_turn(state), started_at, replied_at, audio_seconds)
    return {
        "session_id": session_id,
        "learner_text": learner_text,
        "reply_text": reply_text,
        "reply_audio_b64": audio_b64,
        "reply_audio_mime": mime,
        "turn": new_turn,
        "max_turns": max_turns,
        "ended": False,
        "prosody": prosody.model_dump(mode="json") if prosody else None,
        "coach": None,
        "hidden_targets": None,
        "result_id": None,
        "cost_eur_turn": cost_turn,
        "cost_eur_session": cost_after,
        "duration_seconds": duration,
        "learning_seconds": learning_seconds,
    }


# ---------------------------------------------------------------- coach / end


def _transcript_text(scenario: Scenario, turns: list[Turn]) -> str:
    lines: list[str] = []
    for t in turns:
        if t.role == "learner":
            lines.append(f"Lernende ({scenario.learner.role}): {t.text or '(nichts verstanden)'}")
        elif t.role == "assistant" and not (t.meta or {}).get("end"):
            lines.append(f"Gegenseite ({scenario.counterpart.role}): {t.text}")
    return "\n".join(lines) or "(kein Gesprächsverlauf)"


def _prosody_flags(turns: list[Turn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in turns:
        meta = t.meta or {}
        if t.role == "learner" and meta.get("input") == "audio" and meta.get("prosody"):
            report = ProsodyReport.model_validate(meta["prosody"])
            out.append({"turn": t.ord, **pronunciation.flags_for_prompt(report)})
    return out


def _enforced_scores(scores: list[CriterionScore]) -> list[CriterionScore]:
    """Exactly the sprechen_v1 criteria, in rubric order; missing criteria score 0."""
    by_name = {sc.criterion: sc for sc in scores}
    out: list[CriterionScore] = []
    for c in rubrics.criteria(RUBRIC_VERSION):
        sc = by_name.get(c)
        out.append(sc if sc is not None else CriterionScore(criterion=c, score=0, comment_de="nicht bewertet"))
    return out


def _coach(scenario: Scenario, turns: list[Turn], *, session_id: str, learner_id: str) -> tuple[CoachReport, str]:
    prompt = load_prompt("roleplay_coach")
    vocabulary = rubrics.error_tag_vocabulary()
    rendered = prompt.render(
        learner_role=scenario.learner.role,
        learner_goals=scenario.learner.goals,
        batna=scenario.learner.batna,
        hidden_targets=scenario.counterpart.hidden_targets,
        transcript=_transcript_text(scenario, turns),
        prosody_flags=_prosody_flags(turns) or "—",
        redemittel=rubrics.redemittel_text(),
        error_tag_vocabulary=vocabulary,
    )
    state = SessionState(roleplay_ended=True)  # coach_requires_end_signal: only _finish reaches this point
    out = llm.complete(
        prompt.tier or "assessment",
        [{"role": "user", "content": rendered}],
        LLMCoachOutput,
        prompt_version=prompt.version,
        session_id=session_id,
        learner_id=learner_id,
        temperature=0.0,
        prompt_name="roleplay_coach",
        state=state,
    )
    allowed = set(vocabulary)
    error_tags = [t for t in out.error_tags if t in allowed]
    rubric = RubricResult(
        rubric_version=RUBRIC_VERSION,
        blueprint_id=f"{TASK_TYPE}_{scenario.category}",
        task_type=TASK_TYPE,
        scores=_enforced_scores(out.rubric_scores),
        error_tags=error_tags,
        better_formulations=out.better_formulations,
        summary_de=out.rubric_summary_de or out.outcome_vs_goals,
        model_version=resolve_tier("assessment").model,
        prompt_version=prompt.version,
        needs_review=bool(state.extra.get("needs_review", False)),  # no_tdn_output fired on the coach output
    )
    report = CoachReport(
        outcome_vs_goals=out.outcome_vs_goals,
        moves_used=[m for m in out.moves_used if m in MOVES],
        moves_missed=[m for m in out.moves_missed if m in MOVES],
        language_feedback=out.language_feedback,
        error_tags=error_tags,
        missing_redemittel=out.missing_redemittel,
        better_formulations=out.better_formulations,
        next_focus=out.next_focus,
        rubric_result=rubric,
    )
    return report, prompt.version


def _ended_payload(
    *,
    session_id: str,
    learner_text: str,
    state: dict[str, Any],
    scenario: Scenario,
    prosody: ProsodyReport | None,
    reply_audio_b64: str,
    reply_audio_mime: str | None,
    cost_turn: float,
    cost_session: float,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "learner_text": learner_text,
        "reply_text": ENDED_REPLY_DE,
        "reply_audio_b64": reply_audio_b64,
        "reply_audio_mime": reply_audio_mime,
        "turn": int(state.get("turn", 0)),
        "max_turns": int(state.get("max_turns", scenario.max_turns)),
        "ended": True,
        "prosody": prosody.model_dump(mode="json") if prosody else None,
        "coach": state.get("coach"),
        "hidden_targets": dict(scenario.counterpart.hidden_targets),
        "result_id": state.get("result_id"),
        "cost_eur_turn": cost_turn,
        "cost_eur_session": cost_session,
    }


def _finish(
    *,
    session_id: str,
    learner_id: str,
    scenario: Scenario,
    learner_text: str,
    prosody: ProsodyReport | None,
    cost_before: float,
) -> dict[str, Any]:
    """Coach mode: report + RubricResult stored, hidden targets revealed, session closed."""
    with db_session() as db:
        s = _load(db, session_id)
        turns = _turns(db, session_id)
        voice = bool((s.state or {}).get("voice", False))
        ord_end = _next_ord(db, session_id)
    report, prompt_version = _coach(scenario, turns, session_id=session_id, learner_id=learner_id)
    rubric = report.rubric_result
    assert rubric is not None  # noqa: S101 — set by _coach
    coach_dict = report.model_dump(mode="json")
    result_id = results.store_rubric_result(
        learner_id,
        rubric,
        skill=SKILL,
        session_id=session_id,
        extra={"coach": coach_dict, "scenario_id": scenario.id, "coach_prompt_version": prompt_version},
    )
    audio_b64, mime, tts_backend = _speak(ENDED_REPLY_DE, voice=voice, session_id=session_id, learner_id=learner_id)
    cost_after = ledger.session_cost_eur(session_id)
    cost_turn = max(cost_after - cost_before, 0.0)
    now = _now()
    with db_session() as db:
        s = _load(db, session_id)
        db.add(
            Turn(
                session_id=session_id,
                ord=ord_end,
                role="assistant",
                text=ENDED_REPLY_DE,
                ts=now,
                meta={
                    "end": True,
                    "tts_backend": tts_backend,
                    "cost_eur": cost_turn,
                    "coach_prompt_version": prompt_version,
                },
            )
        )
        s.duration_seconds = _accumulated(s, now)
        s.ended_at = now
        s.state = {
            **(s.state or {}),
            "ended": True,
            "last_turn_at": now.isoformat(),
            "coach": coach_dict,
            "result_id": result_id,
        }
        state = dict(s.state)
    return _ended_payload(
        session_id=session_id,
        learner_text=learner_text,
        state=state,
        scenario=scenario,
        prosody=prosody,
        reply_audio_b64=audio_b64,
        reply_audio_mime=mime,
        cost_turn=cost_turn,
        cost_session=cost_after,
    )


def end(session_id: str) -> dict[str, Any]:
    """Force coach mode. Idempotent: an already ended session returns its stored report."""
    with db_session() as db:
        s = _load(db, session_id)
        learner_id = s.learner_id
        state = dict(s.state or {})
        already = s.ended_at is not None or bool(state.get("ended"))
    scenario = get_scenario(str(state["scenario_id"]))
    if already:
        return _ended_payload(
            session_id=session_id,
            learner_text="",
            state=state,
            scenario=scenario,
            prosody=None,
            reply_audio_b64="",
            reply_audio_mime=None,
            cost_turn=0.0,
            cost_session=ledger.session_cost_eur(session_id),
        )
    return _finish(
        session_id=session_id,
        learner_id=learner_id,
        scenario=scenario,
        learner_text="",
        prosody=None,
        cost_before=ledger.session_cost_eur(session_id),
    )


# ---------------------------------------------------------------- read


def get(session_id: str) -> dict[str, Any]:
    """Session state + turns. Hidden targets only once the roleplay has ended."""
    with db_session() as db:
        s = _load(db, session_id)
        state = dict(s.state or {})
        turns = [_turn_dict(t) for t in _turns(db, session_id)]
        out: dict[str, Any] = {
            "session_id": s.id,
            "learner_id": s.learner_id,
            "kind": s.kind,
            "started_at": _iso(s.started_at),
            "ended_at": _iso(s.ended_at),
            "duration_seconds": s.duration_seconds,
        }
    scenario = get_scenario(str(state["scenario_id"]))
    ended = bool(state.get("ended")) or out["ended_at"] is not None
    out.update(
        {
            "scenario_id": scenario.id,
            "scenario": public_scenario(scenario),
            "turn": int(state.get("turn", 0)),
            "max_turns": int(state.get("max_turns", scenario.max_turns)),
            "voice": bool(state.get("voice", False)),
            "ended": ended,
            "coach": state.get("coach") if ended else None,
            "hidden_targets": dict(scenario.counterpart.hidden_targets) if ended else None,
            "result_id": state.get("result_id") if ended else None,
            "turns": turns,
            "cost_eur": ledger.session_cost_eur(session_id),
        }
    )
    return out
