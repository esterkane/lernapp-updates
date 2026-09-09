"""Deterministic hook engine (ADR-0016, guardrail-hooks skill).

Zero-tolerance product rules live here as pure functions, not as prompt sentences:

- **Pre-hooks** ``(call, state) -> HookDecision`` run before any LLM/STT/TTS/embedding/RAG call.
  The first non-``allow`` decision short-circuits; on ``deny`` the engine returns
  ``ToolResult.fail("business", …)`` and the wrapped ``dispatch`` provably never runs.
- **Post-hooks** ``(call, result, state) -> result'`` normalize the output before anyone else
  (UI, DB, next tier) sees it. They record what they changed in ``state.extra["violations"]``;
  the engine copies those into its bounded ``violations`` ring buffer.

Every adapter (``services.llm``, ``stt``, ``tts``, ``embeddings``, ``rag``) calls
``engine.execute``; ``tests/test_phase0_hooks.py`` greps for bypasses.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.errors import HookViolation, ToolResult
from app.core.models import resolve_tier, vendor_of

log = logging.getLogger(__name__)

CallKind = Literal["llm", "stt", "tts", "embed", "rag"]
LEDGERED_KINDS: frozenset[str] = frozenset({"llm", "stt", "tts", "embed"})

INTERNAL_LABEL_DE = "interne Übungsbewertung"
NEUTRAL_COUNTERPART_LINE_DE = "Lassen Sie uns beim Thema bleiben – wie sehen Sie den nächsten Schritt?"

# Product rule 1: no official TestDaF grade, ever.
_TDN_RE = re.compile(r"TDN\s?[3-5]|TestDaF-Niveau|TDN-Stufe(?:\s?\d)?", re.IGNORECASE)
# Product rule 6: no numeric pronunciation feedback (percentages, "Score 7.5", "Score: 8/10").
_PCT_RE = re.compile(
    r"(?:\b(?:zu|etwa|ca\.?|rund)\s+)?\d{1,3}(?:[.,]\d+)?\s?%(?:\s?(?:korrekt|richtig|verständlich))?", re.I
)
_SCORE_RE = re.compile(r"\bScore\s*[:=]?\s*\d+(?:[.,]\d+)?(?:\s?(?:/|von)\s?\d+)?\.?", re.IGNORECASE)
_PRON_PROMPTS: frozenset[str] = frozenset({"pronunciation_tip", "speaking_feedback"})
# Product rule 7: the counterpart never corrects mid-roleplay.
_CORRECTION_RE = re.compile(r"richtig hei(?:ß|ss)t es|Fehler\s*:|korrekt w[äa]re|Korrektur|Tipp\s*:", re.IGNORECASE)

SCORE_MIN, SCORE_MAX = 0, 4


class TierCall(BaseModel):
    """What is about to be called — metadata only, never the learner text itself."""

    kind: CallKind
    tier: str  # LLM tier name or adapter name (stt|tts|embed|rag)
    model: str
    prompt_name: str | None = None
    prompt_version: str | None = None
    session_id: str | None = None
    learner_id: str | None = None
    owner_id: str | None = None
    payload_summary: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    """Per-request state the hooks may read; ``extra`` carries hook outputs back to the caller."""

    roleplay_ended: bool = False
    learner_text: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def note_violation(self, hook: str, reason: str, call: TierCall) -> None:
        self.extra.setdefault("violations", []).append(
            HookViolation(
                hook=hook, reason=reason, tier=call.tier, prompt_name=call.prompt_name, session_id=call.session_id
            ).model_dump(mode="json")
        )


class HookDecision(BaseModel):
    action: Literal["allow", "deny", "redirect"]
    reason: str | None = None
    redirect_to: str | None = None


PreHook = Callable[[TierCall, SessionState], HookDecision]
PostHook = Callable[[TierCall, Any, SessionState], Any]
Dispatch = Callable[[TierCall], Any]


class HookDenied(RuntimeError):
    """A pre-hook denied the call; carries the ``ToolResult`` so the API can answer 400/business."""

    def __init__(self, result: ToolResult) -> None:
        msg = result.error.message if result.error else "hook denied"
        super().__init__(msg)
        self.result = result


class HookEngine:
    def __init__(self, maxlen: int = 1000) -> None:
        self._pre: list[tuple[str, PreHook]] = []
        self._post: list[tuple[str, PostHook]] = []
        self.decisions: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.violations: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    # -- registration
    def register_pre(self, h: PreHook, name: str | None = None) -> None:
        self._pre.append((name or h.__name__, h))

    def register_post(self, h: PostHook, name: str | None = None) -> None:
        self._post.append((name or h.__name__, h))

    def clear(self) -> None:
        self._pre.clear()
        self._post.clear()

    @property
    def pre_hooks(self) -> list[str]:
        return [n for n, _ in self._pre]

    @property
    def post_hooks(self) -> list[str]:
        return [n for n, _ in self._post]

    # -- execution
    def run_pre(self, call: TierCall, state: SessionState) -> HookDecision:
        """First non-allow decision short-circuits; the deciding hook's name is prefixed to ``reason``."""
        for name, hook in self._pre:
            decision = hook(call, state)
            if decision.action != "allow":
                reason = decision.reason or decision.action
                return decision.model_copy(update={"reason": f"hook {name}: {reason}"})
        return HookDecision(action="allow")

    def run_post(self, call: TierCall, result: Any, state: SessionState) -> Any:
        before = len(state.extra.get("violations", []))
        for _, hook in self._post:
            result = hook(call, result, state)
        new = state.extra.get("violations", [])[before:]
        if new:
            with self._lock:
                self.violations.extend(new)
            for v in new:
                log.warning("hook violation %s (%s/%s): %s", v["hook"], call.tier, call.prompt_name, v["reason"])
        return result

    def _record(self, call: TierCall, action: str, hook: str | None, reason: str | None) -> None:
        with self._lock:
            self.decisions.append(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "kind": call.kind,
                    "tier": call.tier,
                    "prompt_name": call.prompt_name,
                    "session_id": call.session_id,
                    "action": action,
                    "hook": hook,
                    "reason": reason,
                }
            )

    def execute(self, call: TierCall, state: SessionState, dispatch: Dispatch) -> ToolResult:
        """Pre-hooks → (deny: never dispatch) → dispatch → post-hooks. Exceptions from ``dispatch`` propagate."""
        decision = self.run_pre(call, state)
        hook_name = decision.reason.split(":", 1)[0].removeprefix("hook ").strip() if decision.reason else None
        if decision.action == "deny":
            self._record(call, "deny", hook_name, decision.reason)
            log.info("hook denied %s call (%s/%s): %s", call.kind, call.tier, call.prompt_name, decision.reason)
            return ToolResult.fail("business", decision.reason or "denied by hook")
        if decision.action == "redirect" and decision.redirect_to:
            call = call.model_copy(update={"tier": decision.redirect_to})
        try:
            result = dispatch(call)
        except BaseException:
            self._record(call, "error", hook_name, decision.reason)
            raise
        result = self.run_post(call, result, state)
        self._record(call, decision.action, hook_name, decision.reason)
        return ToolResult.ok(result)


# ---------------------------------------------------------------- helpers for post-hooks


def _walk_strings(obj: Any, fn: Callable[[str], str]) -> Any:
    """Apply ``fn`` to every string inside str/list/tuple/dict/BaseModel results (in place for models)."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(x, fn) for x in obj]
    if isinstance(obj, tuple):
        return tuple(_walk_strings(x, fn) for x in obj)
    if isinstance(obj, dict):
        return {k: _walk_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            value = getattr(obj, name, None)
            if value is None or isinstance(value, (int, float, bool)):
                continue
            setattr(obj, name, _walk_strings(value, fn))
        return obj
    return obj


def _scores_of(obj: Any) -> list[Any]:
    """The rubric score lists an object carries (``scores`` and/or ``rubric_scores``)."""
    out: list[Any] = []
    for attr in ("scores", "rubric_scores"):
        val = obj.get(attr) if isinstance(obj, dict) else getattr(obj, attr, None)
        if isinstance(val, list):
            out.append(val)
    return out


# ---------------------------------------------------------------- the Phase-1 hooks (pure) + budget_guard (ADR-0019)


def no_tdn_output(call: TierCall, result: Any, state: SessionState) -> Any:
    if call.kind != "llm":
        return result
    hits: list[str] = []

    def scrub(s: str) -> str:
        if _TDN_RE.search(s):
            hits.append(s[:80])
            return _TDN_RE.sub(INTERNAL_LABEL_DE, s)
        return s

    result = _walk_strings(result, scrub)
    if hits:
        state.note_violation("no_tdn_output", f"{len(hits)} TDN mention(s) replaced by '{INTERNAL_LABEL_DE}'", call)
        if _scores_of(result):
            state.extra["needs_review"] = True
    return result


def no_numeric_pronunciation(call: TierCall, result: Any, state: SessionState) -> Any:
    if call.kind != "llm" or call.prompt_name not in _PRON_PROMPTS:
        return result
    n = 0

    def scrub(s: str) -> str:
        nonlocal n
        cleaned, k1 = _PCT_RE.subn("", s)
        cleaned, k2 = _SCORE_RE.subn("", cleaned)
        n += k1 + k2
        return re.sub(r"\s{2,}", " ", cleaned).strip() if (k1 or k2) else s

    result = _walk_strings(result, scrub)
    if n:
        state.extra["pronunciation_flagged"] = True
        state.note_violation("no_numeric_pronunciation", f"{n} numeric pronunciation score(s) stripped", call)
    return result


def coach_requires_end_signal(call: TierCall, state: SessionState) -> HookDecision:
    if call.kind == "llm" and call.prompt_name == "roleplay_coach" and not state.roleplay_ended:
        return HookDecision(action="deny", reason="Coach-Modus erst nach 'ROLLENSPIEL ENDE' oder Rundenlimit (Regel 7)")
    return HookDecision(action="allow")


def counterpart_no_correction(call: TierCall, result: Any, state: SessionState) -> Any:
    if call.kind != "llm" or call.prompt_name != "roleplay_counterpart" or not isinstance(result, str):
        return result
    if not _CORRECTION_RE.search(result):
        return result
    attempts = int(state.extra.get("correction_attempts", 0)) + 1
    state.extra["correction_attempts"] = attempts
    state.extra["regenerate"] = attempts == 1
    state.note_violation(
        "counterpart_no_correction",
        "counterpart corrected the learner; " + ("regenerating once" if attempts == 1 else "replaced by neutral line"),
        call,
    )
    return NEUTRAL_COUNTERPART_LINE_DE


def ledger_required(call: TierCall, state: SessionState) -> HookDecision:
    if call.kind in LEDGERED_KINDS and (not call.session_id or not call.learner_id):
        return HookDecision(
            action="deny",
            reason=f"{call.kind}-Aufruf ohne session_id/learner_id – kein Ledger-Eintrag möglich (Regel 8)",
        )
    return HookDecision(action="allow")


def validator_vendor_differs(call: TierCall, state: SessionState) -> HookDecision:
    if call.kind == "llm" and call.tier == "validator":
        assessment_vendor = vendor_of(resolve_tier("assessment").model)
        if vendor_of(call.model) == assessment_vendor:
            return HookDecision(
                action="deny",
                reason=f"validator ({call.model}) und assessment nutzen denselben Anbieter '{assessment_vendor}' (ADR-0004)",
            )
    return HookDecision(action="allow")


def rag_owner_scope(call: TierCall, state: SessionState) -> HookDecision:
    if call.kind == "rag" and not call.owner_id:
        return HookDecision(action="deny", reason="Retrieval ohne owner_id (Dokumente sind lernenden-gebunden)")
    return HookDecision(action="allow")


# Backends whose calls never cost money: the budget guard lets them through even at the limit.
_LOCAL_BACKENDS: frozenset[str] = frozenset({"faster_whisper", "fake", "piper", "none", "prosody_mvp", "mfa_gop"})
BUDGET_LIMIT_REASON_DE = (
    "Monatslimit erreicht ({used} € von {budget} €). Erhöhe das Limit unter Kosten oder schalte den Stopp aus."
)


def _is_local_call(call: TierCall) -> bool:
    backend = str(call.payload_summary.get("backend") or "")
    return call.model.startswith("local/") or backend in _LOCAL_BACKENDS


def budget_guard(call: TierCall, state: SessionState) -> HookDecision:
    """ADR-0019 budget stop: deny paid llm/stt/tts/embed calls once the month's expected cost reaches the
    learner's ``monthly_budget_eur`` **and** ``stop_on_limit`` is on. Warning-only (the default) never denies.
    The monthly total is cached for 30 s (``usage_report.budget_state``) — no DB query per call."""
    if call.kind not in LEDGERED_KINDS or not call.learner_id or _is_local_call(call):
        return HookDecision(action="allow")
    from app.services import usage_report

    try:
        b = usage_report.budget_state(call.learner_id)
    except Exception:  # noqa: BLE001 — a reporting failure must never block learning
        log.exception("budget_guard: could not read the budget state; allowing the call")
        return HookDecision(action="allow")
    if b.stop_on_limit and b.limit_reached and b.monthly_budget_eur is not None:
        return HookDecision(
            action="deny",
            reason=BUDGET_LIMIT_REASON_DE.format(
                used=usage_report.format_eur_de(b.used_eur), budget=usage_report.format_eur_de(b.monthly_budget_eur)
            ),
        )
    return HookDecision(action="allow")


def clamp_scores(call: TierCall, result: Any, state: SessionState) -> Any:
    lists = _scores_of(result)
    if not lists:
        return result
    changed = 0
    total = 0
    for scores in lists:
        for sc in scores:
            raw = sc.get("score") if isinstance(sc, dict) else getattr(sc, "score", None)
            if raw is None:
                continue
            clamped = max(SCORE_MIN, min(SCORE_MAX, int(raw)))
            if clamped != raw:
                changed += 1
                if isinstance(sc, dict):
                    sc["score"] = clamped
                else:
                    sc.score = clamped
            total += clamped
    if isinstance(result, dict):
        if "total" in result:
            result["total"] = total
    elif "total" in getattr(type(result), "model_fields", {}):
        result.total = total
    if changed:
        state.note_violation("clamp_scores", f"{changed} score(s) clamped to {SCORE_MIN}–{SCORE_MAX}", call)
    return result


# ---------------------------------------------------------------- registry


HOOKS: dict[str, dict[str, Any]] = {
    "no_tdn_output": {
        "hook": no_tdn_output,
        "kind": "post",
        "rule": "1",
        "rationale": "TDN/TestDaF-Niveau mentions are replaced by 'interne Übungsbewertung'; rubric results get needs_review.",
    },
    "no_numeric_pronunciation": {
        "hook": no_numeric_pronunciation,
        "kind": "post",
        "rule": "6",
        "rationale": "Percentages/scores in pronunciation_tip and speaking_feedback are stripped and flagged.",
    },
    "coach_requires_end_signal": {
        "hook": coach_requires_end_signal,
        "kind": "pre",
        "rule": "7",
        "rationale": "roleplay_coach is denied until state.roleplay_ended (end signal or turn limit).",
    },
    "counterpart_no_correction": {
        "hook": counterpart_no_correction,
        "kind": "post",
        "rule": "7",
        "rationale": "Counterpart corrections trigger one regeneration, then a neutral in-role line.",
    },
    "ledger_required": {
        "hook": ledger_required,
        "kind": "pre",
        "rule": "8",
        "rationale": "llm/stt/tts/embed calls without session_id and learner_id cannot be attributed → denied.",
    },
    "validator_vendor_differs": {
        "hook": validator_vendor_differs,
        "kind": "pre",
        "rule": "3 / ADR-0004",
        "rationale": "validator tier denied if its vendor equals the assessment vendor (reads config/models.yaml).",
    },
    "rag_owner_scope": {
        "hook": rag_owner_scope,
        "kind": "pre",
        "rule": "9 / ADR-0007",
        "rationale": "Retrieval without owner_id is denied so a learner never sees another owner's chunks.",
    },
    "budget_guard": {
        "hook": budget_guard,
        "kind": "pre",
        "rule": "8 / ADR-0019",
        "rationale": (
            "With stop_on_limit, paid llm/stt/tts/embed calls are denied once the month's expected cost reaches "
            "monthly_budget_eur (German reason, 400 business); warning-only is the default. Local backends pass."
        ),
    },
    "clamp_scores": {
        "hook": clamp_scores,
        "kind": "post",
        "rule": "5",
        "rationale": "Rubric scores clamped to 0–4 and totals recomputed in Python, never trusted from the model.",
    },
}

engine = HookEngine()


def register_default_hooks(target: HookEngine | None = None) -> HookEngine:
    """(Re)register the Phase-1 hooks in registry order. Idempotent."""
    eng = target or engine
    eng.clear()
    for name, entry in HOOKS.items():
        if entry["kind"] == "pre":
            eng.register_pre(entry["hook"], name=name)
        else:
            eng.register_post(entry["hook"], name=name)
    return eng


def active_hooks() -> list[dict[str, Any]]:
    """Inspectable hook set for ``GET /health``."""
    registered = set(engine.pre_hooks) | set(engine.post_hooks)
    return [
        {
            "name": name,
            "kind": entry["kind"],
            "rule": entry["rule"],
            "rationale": entry["rationale"],
            "active": name in registered,
        }
        for name, entry in HOOKS.items()
    ]


register_default_hooks()
