"""Tutor / Sprechen sessions (docs/api.md "Sessions / tutor"; ADR-0001 cascade) — the session hub.

Hub-and-spoke (learnings §1): this module owns every handoff between the spoke adapters and
decides which failures are fatal and which are tolerated (errors as values, ``partial_failures``).

Audio turn: ``temp_wav`` → STT (``stt.transcribe_safe``, **fatal** on failure → ``AdapterFailure``
→ HTTP 502 with German message + config alternatives) → prosody (**tolerated**: ``prosody=None``,
``"prosody"`` in ``partial_failures``) → tutor LLM (conversation tier, prompt ``tutor_system`` +
``learner_profile_block`` + RAG context; a failure here is fatal — there is no turn without a
reply) → post-steps that only depend on the reply/prosody and are independent of each other:
TTS of the reply (``tts.synthesize_safe``, tolerated: no audio, ``"tts"``) and the pronunciation
tip (tolerated: ``None``, ``"pronunciation_tip"``). Those two run concurrently in a small thread
pool; the earlier stages are sequential because each consumes the previous stage's output
(prosody needs the transcript, the reply needs the text), so "parallel STT + prosody" is not a
real option — the hub implements the *semantics* (fatal vs tolerated) rather than forcing
parallelism where data dependencies forbid it.

The temp WAV is deleted before the LLM is called (ADR-0011). The LLM only ever sees the session
id — never the learner's name, e-mail or id. Every adapter call goes through the hook engine.

Learning time (``Session.duration_seconds``, ADR-0019) counts only genuine learning: per turn the
learner's audio seconds + the request→response time (capped at 120 s) + the gap since the previous
turn when it is ≤ 5 min (reading/thinking); longer gaps are idle and excluded — see
``services.learning_time``. Each turn response reports its own share as ``learning_seconds``.
"""

from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core import ledger
from app.core.db import db_session
from app.core.errors import AdapterFailure, FailureContext
from app.core.hooks import SessionState
from app.core.prompts import load_prompt
from app.db.base import Learner, Session, Turn
from app.services import audio, llm, pronunciation, rag, stt, tts
from app.services.learner_profile import profile_block
from app.services.learning_time import MAX_GAP_SECONDS, learning_increment, parse_last_turn
from app.services.schemas import AudioBytes, PronunciationTip, ProsodyReport, Transcript

log = logging.getLogger(__name__)

SESSION_KINDS = ("tutor", "sprechen")
HISTORY_TURNS = 12
HISTORY_CHAR_BUDGET = 8000
RAG_CHAR_BUDGET = 6000
_ = MAX_GAP_SECONDS  # re-exported for tests/docs: gaps above this are idle time (services.learning_time)
NOTHING_UNDERSTOOD_DE = "Ich konnte leider nichts verstehen. Magst du es noch einmal versuchen?"


class SessionNotFound(KeyError):
    pass


class SessionEnded(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- sessions


def create_session(learner_id: str, kind: str = "tutor", task_id: str | None = None) -> dict[str, Any]:
    if kind not in SESSION_KINDS:
        raise ValueError(f"kind must be one of {SESSION_KINDS}")
    with db_session() as db:
        if db.get(Learner, learner_id) is None:
            raise SessionNotFound(f"unknown learner {learner_id}")
        s = Session(learner_id=learner_id, kind=kind, state={"task_id": task_id, "last_turn_at": None})
        db.add(s)
        db.flush()
        return {
            "session_id": s.id,
            "kind": s.kind,
            "started_at": _iso(s.started_at),
            "learner_id": learner_id,
        }


def _load(db: Any, session_id: str) -> Session:
    s: Session | None = db.get(Session, session_id)
    if s is None:
        raise SessionNotFound(session_id)
    return s


def _session_dict(s: Session, *, with_turns: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "session_id": s.id,
        "learner_id": s.learner_id,
        "kind": s.kind,
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "duration_seconds": s.duration_seconds,
        "task_id": (s.state or {}).get("task_id"),
    }
    if with_turns:
        out["turns"] = [
            {"ord": t.ord, "role": t.role, "text": t.text, "ts": _iso(t.ts), "meta": dict(t.meta or {})}
            for t in sorted(s.turns, key=lambda t: t.ord)
        ]
    return out


def get_session(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        s = _load(db, session_id)
        out = _session_dict(s, with_turns=True)
    out["cost_eur"] = ledger.session_cost_eur(session_id)
    return out


def list_sessions(learner_id: str | None = None, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    with db_session() as db:
        q = select(Session).order_by(Session.started_at.desc()).limit(limit)
        if learner_id:
            q = q.where(Session.learner_id == learner_id)
        if kind:
            q = q.where(Session.kind == kind)
        rows = [_session_dict(s, with_turns=False) for s in db.execute(q).scalars()]
    for r in rows:
        r["cost_eur"] = ledger.session_cost_eur(r["session_id"])
    return rows


def end_session(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        s = _load(db, session_id)
        if s.ended_at is None:
            now = _now()
            s.duration_seconds = _accumulated(s, now, started_at=now)
            s.ended_at = now
            s.state = {**(s.state or {}), "last_turn_at": now.isoformat()}
        duration = s.duration_seconds
    return {
        "session_id": session_id,
        "duration_seconds": duration,
        "cost_eur": ledger.session_cost_eur(session_id),
    }


def _accumulated(s: Session, now: datetime, extra_seconds: float = 0.0, *, started_at: datetime | None = None) -> float:
    """Running total + this turn's learning seconds (``learning_time.learning_increment``).

    ``started_at`` is when the request arrived (processing time = ``now - started_at``, capped);
    ``extra_seconds`` is the learner's audio input. Gaps above ``MAX_GAP_SECONDS`` add nothing.
    """
    inc = learning_increment(parse_last_turn(s.state), started_at or now, now, extra_seconds)
    return float(s.duration_seconds or 0.0) + inc


# ---------------------------------------------------------------- turns


def bounded_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Bound prompt size without rewriting stored turns or estimating billable tokens."""
    remaining = HISTORY_CHAR_BUDGET
    kept: list[dict[str, str]] = []
    for message in reversed(history):
        if len(message["content"]) > remaining:
            break  # retain complete messages, not misleading sentence fragments
        kept.append(dict(message))
        remaining -= len(message["content"])
    return list(reversed(kept))


def _history(db: Any, session_id: str) -> list[dict[str, str]]:
    q = (
        select(Turn)
        .where(Turn.session_id == session_id, Turn.role.in_(("learner", "assistant")))
        .order_by(Turn.ord.desc())
        .limit(HISTORY_TURNS)
    )
    turns = list(db.execute(q).scalars())[::-1]
    return [{"role": "user" if t.role == "learner" else "assistant", "content": t.text} for t in turns]


def _next_ord(db: Any, session_id: str) -> int:
    q = select(Turn.ord).where(Turn.session_id == session_id).order_by(Turn.ord.desc()).limit(1)
    last = db.execute(q).scalar()
    return 0 if last is None else int(last) + 1


def _transcribe(
    audio_bytes: bytes, *, session_id: str, learner_id: str
) -> tuple[Transcript, ProsodyReport | None, float, list[str]]:
    """STT (fatal) + prosody (tolerated) inside ``temp_wav`` so the file is gone before any remote call.

    Returns ``(transcript, prosody | None, audio_seconds, partial_failures)``; raises ``AdapterFailure``
    with the STT ``FailureContext`` (German message + ``STT_BACKEND=…`` alternatives) when STT fails.
    """
    failures: list[str] = []
    with audio.temp_wav(audio_bytes, session_id=session_id, learner_id=learner_id) as path:
        got = stt.transcribe_safe(path, session_id=session_id, learner_id=learner_id)
        if isinstance(got, FailureContext):
            raise AdapterFailure(got)
        transcript = got
        prosody: ProsodyReport | None
        try:
            prosody = pronunciation.analyze_prosody_metered(transcript, session_id=session_id, learner_id=learner_id)
        except Exception:  # noqa: BLE001 — tolerated: the turn continues without prosody
            log.exception("prosody analysis failed for session %s (tolerated)", session_id)
            prosody = None
            failures.append("prosody")
    seconds = transcript.duration_s or audio.wav_seconds(audio_bytes)
    return transcript, prosody, float(seconds), failures


def _tutor_reply(
    *,
    learner_id: str,
    session_id: str,
    learner_text: str,
    history: list[dict[str, str]],
    timings: dict[str, float] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    from time import perf_counter

    prompt = load_prompt("tutor_system")
    started = perf_counter()
    hits = rag.context_for(learner_id, learner_text, use_in="tutor", k=5, session_id=session_id, timings=timings)
    if timings is not None:
        timings["retrieval"] = (perf_counter() - started) * 1000
    system = prompt.render(
        learner_profile_block=profile_block(learner_id), rag_context=rag.format_context(hits)[:RAG_CHAR_BUDGET]
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        *bounded_history(history),
        {"role": "user", "content": learner_text},
    ]
    started = perf_counter()
    reply = llm.complete(
        prompt.tier or "conversation",
        messages,
        prompt_version=prompt.version,
        session_id=session_id,
        learner_id=learner_id,
        prompt_name="tutor_system",
        state=SessionState(learner_text=learner_text),
    )
    if timings is not None:
        timings["answer_generation"] = (perf_counter() - started) * 1000
    citations = [{"title": h.document_title, "ord": h.ord} for h in hits]
    return reply.strip(), prompt.version, citations


def _pronunciation_tip_call(prosody: ProsodyReport, *, session_id: str, learner_id: str) -> PronunciationTip:
    prompt = load_prompt("pronunciation_tip")
    rendered = prompt.render(
        prosody_report_json=json.dumps(pronunciation.flags_for_prompt(prosody), ensure_ascii=False),
        phone_flags_json=json.dumps([f.model_dump() for f in prosody.phone_flags], ensure_ascii=False),
    )
    return llm.complete(
        prompt.tier or "conversation",
        [{"role": "user", "content": rendered}],
        PronunciationTip,
        prompt_version=prompt.version,
        session_id=session_id,
        learner_id=learner_id,
        prompt_name="pronunciation_tip",
    )


def pronunciation_tip(prosody: ProsodyReport, *, session_id: str, learner_id: str) -> PronunciationTip | None:
    """One qualitative tip (``no_numeric_pronunciation`` hook strips any number); ``None`` when the LLM fails."""
    try:
        return _pronunciation_tip_call(prosody, session_id=session_id, learner_id=learner_id)
    except llm.LLMError:
        log.warning("pronunciation tip failed for session %s", session_id)
        return None


def _post_steps(
    reply_text: str, prosody: ProsodyReport | None, *, session_id: str, learner_id: str
) -> tuple[AudioBytes | None, PronunciationTip | None, list[str]]:
    """TTS of the reply and the pronunciation tip are independent → run concurrently; both are tolerated."""
    from app.db.base import Learner

    with db_session() as db:
        learner = db.get(Learner, learner_id)
        economy = bool((learner.profile or {}).get("economy_mode", False)) if learner else False
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="tutor-post") as pool:
        f_tts = pool.submit(
            copy_context().run, tts.synthesize_safe, reply_text, session_id=session_id, learner_id=learner_id
        )
        f_tip = (
            pool.submit(
                copy_context().run, _pronunciation_tip_call, prosody, session_id=session_id, learner_id=learner_id
            )
            if prosody is not None and not economy
            else None
        )
        spoken_or_fail = f_tts.result()
        tip: PronunciationTip | None = None
        if f_tip is not None:
            try:
                tip = f_tip.result()
            except Exception:  # noqa: BLE001 — tolerated: reply without tip
                log.exception("pronunciation tip failed for session %s (tolerated)", session_id)
                failures.append("pronunciation_tip")
    spoken: AudioBytes | None
    if isinstance(spoken_or_fail, FailureContext):
        log.warning("TTS failed for session %s (tolerated): %s", session_id, spoken_or_fail.message)
        spoken = None
        failures.append("tts")
    else:
        spoken = spoken_or_fail
    return spoken, tip, failures


def turn(
    session_id: str, text: str | None = None, audio_bytes: bytes | None = None, *, defer_audio: bool = False
) -> dict[str, Any]:
    """One learner turn (text or audio). Returns the dict described in docs/api.md (+ ``partial_failures``)."""
    if not text and not audio_bytes:
        raise ValueError("text oder Audio erforderlich")
    from time import perf_counter

    turn_clock = perf_counter()
    timings: dict[str, float] = {}
    started_at = _now()
    with db_session() as db:
        s = _load(db, session_id)
        if s.ended_at is not None:
            raise SessionEnded(f"session {session_id} ist bereits beendet")
        learner_id = s.learner_id
        history = _history(db, session_id)
    cost_before = ledger.session_cost_eur(session_id)

    partial_failures: list[str] = []
    prosody: ProsodyReport | None = None
    transcript: Transcript | None = None
    audio_seconds = 0.0
    learner_meta: dict[str, Any] = {"input": "text"}
    phase_clock = perf_counter()
    if audio_bytes:
        transcript, prosody, audio_seconds, failures = _transcribe(
            audio_bytes, session_id=session_id, learner_id=learner_id
        )
        partial_failures.extend(failures)
        learner_text = transcript.text.strip()
        learner_meta = {
            "input": "audio",
            "audio_seconds": audio_seconds,
            "stt_backend": transcript.backend,
            "stt_model": transcript.model,
            "prosody": prosody.model_dump(mode="json") if prosody else None,
            "flags": [f.model_dump(mode="json") for f in prosody.pronunciation_flags] if prosody else [],
            "partial_failures": list(partial_failures),
        }
    else:
        learner_text = (text or "").strip()

    timings["speech_input"] = (perf_counter() - phase_clock) * 1000
    phase_clock = perf_counter()
    citations: list[dict[str, Any]] = []
    prompt_version = ""
    if learner_text:
        reply_text, prompt_version, citations = _tutor_reply(
            learner_id=learner_id, session_id=session_id, learner_text=learner_text, history=history, timings=timings
        )
    else:
        reply_text = NOTHING_UNDERSTOOD_DE

    timings["reply_and_context"] = (perf_counter() - phase_clock) * 1000
    phase_clock = perf_counter()
    post_failures: list[str]
    if defer_audio and audio_bytes is None:
        spoken, tip, post_failures = None, None, []
    else:
        spoken, tip, post_failures = _post_steps(reply_text, prosody, session_id=session_id, learner_id=learner_id)
    timings["voice_and_tip"] = (perf_counter() - phase_clock) * 1000
    timings["total"] = (perf_counter() - turn_clock) * 1000
    partial_failures.extend(post_failures)

    cost_after = ledger.session_cost_eur(session_id)
    cost_turn = max(cost_after - cost_before, 0.0)
    now = _now()
    assistant_meta: dict[str, Any] = {
        "audio_deferred": defer_audio and audio_bytes is None,
        "timings_ms": timings,
        "prompt_version": prompt_version,
        "citations": citations,
        "tts_backend": spoken.backend if spoken else None,
        "cost_eur": cost_turn,
        "pronunciation_tip": tip.model_dump(mode="json") if tip else None,
        "partial_failures": list(partial_failures),
    }
    with db_session() as db:
        # Allocate numbers only when saving, under a session lock. Requests can
        # overlap while the model is responding, so an earlier read is stale.
        locked_session = db.execute(select(Session).where(Session.id == session_id).with_for_update()).scalar_one_or_none()
        if locked_session is None:
            raise SessionNotFound(session_id)
        s = locked_session
        ord_learner = _next_ord(db, session_id)
        db.add(
            Turn(
                session_id=session_id,
                ord=ord_learner,
                role="learner",
                text=learner_text,
                ts=now,
                meta=learner_meta,
            )
        )
        db.add(
            Turn(
                session_id=session_id,
                ord=ord_learner + 1,
                role="assistant",
                text=reply_text,
                ts=now,
                meta=assistant_meta,
            )
        )
        before = float(s.duration_seconds or 0.0)
        s.duration_seconds = _accumulated(s, now, audio_seconds, started_at=started_at)
        s.state = {**(s.state or {}), "last_turn_at": now.isoformat()}
        duration = s.duration_seconds
        learning_seconds = duration - before

    return {
        "session_id": session_id,
        "timings_ms": timings,
        "turn_ord": ord_learner,
        "learner_text": learner_text,
        "reply_text": reply_text,
        "reply_audio_b64": base64.b64encode(spoken.data).decode("ascii") if spoken and spoken.data else "",
        "reply_audio_mime": spoken.mime if spoken else None,
        "prosody": prosody.model_dump(mode="json") if prosody else None,
        "pronunciation_tip": tip.model_dump(mode="json") if tip else None,
        "citations": citations,
        "cost_eur_turn": cost_turn,
        "cost_eur_session": cost_after,
        "duration_seconds": duration,
        "learning_seconds": learning_seconds,
        "partial_failures": partial_failures,
    }


def deferred_audio(session_id: str, ord_: int) -> dict[str, Any]:
    """Serialize same-turn requests; retain generated speech with its owning turn for reuse."""
    from time import perf_counter

    with db_session() as db:
        session = _load(db, session_id)
        row = db.execute(
            select(Turn)
            .where(Turn.session_id == session_id, Turn.ord == ord_, Turn.role == "assistant")
            # Older versions could save duplicate numbers on overlapping turns.
            # Keep all history and use the latest answer for these legacy URLs.
            .order_by(Turn.ts.desc(), Turn.id.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None or not (row.meta or {}).get("audio_deferred"):
            raise ValueError("Für diese Antwort ist kein nachträgliches Vorlesen verfügbar.")
        if "deferred_audio" in (row.meta or {}):
            return dict(row.meta["deferred_audio"])
        started = perf_counter()
        spoken = tts.synthesize_safe(row.text, session_id=session_id, learner_id=session.learner_id)
        if isinstance(spoken, FailureContext):
            result: dict[str, Any] = {
                "reply_audio_b64": "",
                "reply_audio_mime": None,
                "message": "Vorlesen ist derzeit nicht verfügbar. Die Textantwort bleibt erhalten.",
            }
        else:
            result = {
                "reply_audio_b64": base64.b64encode(spoken.data).decode("ascii"),
                "reply_audio_mime": spoken.mime,
                "message": "",
            }
        result["audio_ms"] = (perf_counter() - started) * 1000
        result["cost_eur_session"] = ledger.session_cost_eur(session_id)
        row.meta = {**(row.meta or {}), "deferred_audio": result}
        return result
