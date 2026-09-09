"""ADR-0016: deterministic hook engine. Every pre-hook deny proves ``dispatch`` never ran (counter == 0);
every post-hook is tested with a violating and a compliant input; the comparison harness asserts
``hooks_arm_violations == 0`` and never a prompt-arm number."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from app.core import hooks as hooks_mod
from app.core.errors import FailureContext, ToolResult
from app.core.hooks import (
    HOOKS,
    HookDecision,
    HookDenied,
    HookEngine,
    SessionState,
    TierCall,
    active_hooks,
    engine,
)
from app.core.models import resolve_tier
from app.services.schemas import CriterionScore, LLMCoachOutput, LLMRubricOutput, PronunciationTip

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "backend" / "app"
EXPECTED_HOOKS = {
    "no_tdn_output",
    "no_numeric_pronunciation",
    "coach_requires_end_signal",
    "counterpart_no_correction",
    "ledger_required",
    "validator_vendor_differs",
    "rag_owner_scope",
    "clamp_scores",
    "budget_guard",
}
TDN_RE = re.compile(r"TDN\s?[3-5]|TestDaF-Niveau|TDN-Stufe")
PCT_RE = re.compile(r"\d{1,3}\s?%")


class Counter:
    """Wraps a dispatch so a test can assert it was (not) executed."""

    def __init__(self, value: Any = "ok") -> None:
        self.calls = 0
        self.value = value

    def __call__(self, call: TierCall) -> Any:
        self.calls += 1
        return self.value


def _call(**kw: Any) -> TierCall:
    base: dict[str, Any] = {
        "kind": "llm",
        "tier": "conversation",
        "model": "openai/gpt-x",
        "prompt_name": "tutor_system",
        "prompt_version": "1.0.0",
        "session_id": "s1",
        "learner_id": "l1",
    }
    base.update(kw)
    return TierCall(**base)


# ---------------------------------------------------------------- envelope + engine basics


def test_tool_result_envelope() -> None:
    ok = ToolResult.ok({"a": 1})
    assert ok.success and ok.result == {"a": 1} and ok.error is None and ok.is_retryable is False
    bad = ToolResult.fail("business", "nein", alternatives=["x"])
    assert not bad.success and bad.error is not None
    assert bad.error.category == "business" and bad.error.message == "nein" and bad.error.alternatives == ["x"]
    assert bad.error_category == "business" and bad.is_retryable is False
    assert ToolResult.fail("transient", "später").is_retryable is True
    fc = FailureContext(category="transient", message="m", alternatives=["STT_BACKEND=openai"])
    assert fc.alternatives == ["STT_BACKEND=openai"]


def test_engine_deny_never_dispatches_and_records() -> None:
    eng = HookEngine()
    eng.register_pre(lambda c, s: HookDecision(action="deny", reason="test deny"), name="t_deny")
    counter = Counter()
    res = eng.execute(_call(), SessionState(), counter)
    assert counter.calls == 0
    assert not res.success and res.error is not None and res.error.category == "business"
    assert "test deny" in res.error.message
    assert eng.decisions[-1]["action"] == "deny" and eng.decisions[-1]["hook"] == "t_deny"


def test_engine_allow_runs_dispatch_then_post_hooks_in_order() -> None:
    eng = HookEngine()
    order: list[str] = []

    def p1(c: TierCall, r: Any, s: SessionState) -> Any:
        order.append("p1")
        return r + "-1"

    def p2(c: TierCall, r: Any, s: SessionState) -> Any:
        order.append("p2")
        return r + "-2"

    eng.register_post(p1, name="p1")
    eng.register_post(p2, name="p2")
    counter = Counter("x")
    res = eng.execute(_call(), SessionState(), counter)
    assert counter.calls == 1 and res.success and res.result == "x-1-2" and order == ["p1", "p2"]
    assert eng.decisions[-1]["action"] == "allow"


def test_engine_redirect_changes_tier_before_dispatch() -> None:
    eng = HookEngine()
    eng.register_pre(
        lambda c, s: (
            HookDecision(action="redirect", reason="eu", redirect_to="eu_conversation")
            if c.tier == "conversation"
            else HookDecision(action="allow")
        ),
        name="t_redirect",
    )
    seen: list[str] = []

    def dispatch(c: TierCall) -> str:
        seen.append(c.tier)
        return "ok"

    res = eng.execute(_call(), SessionState(), dispatch)
    assert res.success and seen == ["eu_conversation"]


def test_engine_first_non_allow_short_circuits() -> None:
    eng = HookEngine()
    hits: list[str] = []

    def a(c: TierCall, s: SessionState) -> HookDecision:
        hits.append("a")
        return HookDecision(action="deny", reason="a")

    def b(c: TierCall, s: SessionState) -> HookDecision:
        hits.append("b")
        return HookDecision(action="deny", reason="b")

    eng.register_pre(a, name="a")
    eng.register_pre(b, name="b")
    assert eng.run_pre(_call(), SessionState()).reason == "hook a: a" and hits == ["a"]


def test_engine_dispatch_exception_propagates_and_is_recorded() -> None:
    eng = HookEngine()

    def boom(c: TierCall) -> Any:
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        eng.execute(_call(), SessionState(), boom)
    assert eng.decisions[-1]["action"] == "error"


def test_ring_buffers_are_bounded() -> None:
    eng = HookEngine(maxlen=5)
    for _ in range(20):
        eng.execute(_call(), SessionState(), Counter())
    assert len(eng.decisions) == 5


def test_registry_lists_all_phase1_hooks_and_health_exposes_them(client) -> None:  # type: ignore[no-untyped-def]
    assert set(HOOKS) == EXPECTED_HOOKS
    for name, entry in HOOKS.items():
        assert entry["kind"] in ("pre", "post") and entry["rule"] and entry["rationale"], name
    listed = active_hooks()
    assert {h["name"] for h in listed} == EXPECTED_HOOKS
    r = client.get("/health")
    assert r.status_code == 200
    assert {h["name"] for h in r.json()["hooks"]} == EXPECTED_HOOKS


# ---------------------------------------------------------------- pre-hooks: deny → dispatch never runs


def test_coach_requires_end_signal_denies_before_end() -> None:
    call = _call(tier="assessment", prompt_name="roleplay_coach")
    counter = Counter()
    res = engine.execute(call, SessionState(roleplay_ended=False), counter)
    assert counter.calls == 0 and not res.success and res.error is not None
    assert "coach_requires_end_signal" in res.error.message
    counter2 = Counter()
    res2 = engine.execute(call, SessionState(roleplay_ended=True), counter2)
    assert counter2.calls == 1 and res2.success


def test_ledger_required_denies_calls_without_ids() -> None:
    for kind in ("llm", "stt", "tts", "embed"):
        for missing in ("session_id", "learner_id"):
            counter = Counter()
            res = engine.execute(_call(kind=kind, **{missing: None}), SessionState(), counter)
            assert counter.calls == 0, (kind, missing)
            assert not res.success and res.error is not None and "ledger_required" in res.error.message
    ok = Counter()
    assert engine.execute(_call(kind="tts"), SessionState(), ok).success and ok.calls == 1


def test_validator_vendor_differs_denies_same_vendor() -> None:
    assessment_model = resolve_tier("assessment").model
    counter = Counter()
    res = engine.execute(
        _call(tier="validator", model=assessment_model, prompt_name="task_validator"), SessionState(), counter
    )
    assert counter.calls == 0 and not res.success and res.error is not None
    assert "validator_vendor_differs" in res.error.message
    other = Counter()
    res2 = engine.execute(_call(tier="validator", model="anthropic/claude-x"), SessionState(), other)
    assert other.calls == 1 and res2.success


def test_rag_owner_scope_denies_without_owner() -> None:
    counter = Counter([])
    res = engine.execute(_call(kind="rag", tier="rag", model="hybrid", owner_id=None), SessionState(), counter)
    assert counter.calls == 0 and not res.success and res.error is not None
    assert "rag_owner_scope" in res.error.message
    res = engine.execute(_call(kind="rag", tier="rag", model="hybrid", owner_id=""), SessionState(), Counter([]))
    assert not res.success
    ok = Counter([])
    assert engine.execute(_call(kind="rag", tier="rag", model="hybrid", owner_id="l1"), SessionState(), ok).success
    assert ok.calls == 1


def test_rag_search_service_is_denied_without_owner() -> None:
    from app.services import rag

    with pytest.raises(HookDenied) as ei:
        rag.search("Vertrag", "")
    assert ei.value.result.error is not None and ei.value.result.error.category == "business"


# ---------------------------------------------------------------- post-hooks: violating vs compliant


def test_no_tdn_output_replaces_text_and_logs_violation() -> None:
    state = SessionState()
    dirty = "Das entspricht etwa TDN 4, also TestDaF-Niveau fortgeschritten (TDN-Stufe hoch)."
    res = engine.execute(_call(), state, Counter(dirty))
    assert res.success and not TDN_RE.search(res.result)
    assert "interne Übungsbewertung" in res.result
    assert any(v["hook"] == "no_tdn_output" for v in state.extra["violations"])
    assert engine.violations[-1]["hook"] == "no_tdn_output"
    clean_state = SessionState()
    clean = "Sehr gut, weiter so!"
    assert engine.execute(_call(), clean_state, Counter(clean)).result == clean
    assert not clean_state.extra.get("violations")


def test_no_tdn_output_marks_structured_results_needs_review() -> None:
    out = LLMRubricOutput(
        scores=[CriterionScore(criterion="aufgabenbezug", score=3, comment_de="Das wäre TDN 5.")],
        summary_de="Insgesamt TestDaF-Niveau erreicht.",
    )
    state = SessionState()
    res = engine.execute(_call(tier="assessment", prompt_name="writing_feedback"), state, Counter(out))
    got: LLMRubricOutput = res.result
    assert not TDN_RE.search(got.summary_de) and not TDN_RE.search(got.scores[0].comment_de)
    assert state.extra.get("needs_review") is True
    ok_state = SessionState()
    clean = LLMRubricOutput(scores=[CriterionScore(criterion="aufgabenbezug", score=3)], summary_de="Gut.")
    engine.execute(_call(tier="assessment", prompt_name="writing_feedback"), ok_state, Counter(clean))
    assert "needs_review" not in ok_state.extra


def test_no_numeric_pronunciation_strips_percentages_and_scores() -> None:
    tip = PronunciationTip(tip_de="Du sprichst 87 % korrekt, Score: 7.5 – achte auf das ü.", practice_words=["Prüfung"])
    state = SessionState()
    res = engine.execute(_call(prompt_name="pronunciation_tip"), state, Counter(tip))
    got: PronunciationTip = res.result
    assert not PCT_RE.search(got.tip_de) and "Score" not in got.tip_de and "achte auf das ü" in got.tip_de
    assert state.extra.get("pronunciation_flagged") is True
    assert any(v["hook"] == "no_numeric_pronunciation" for v in state.extra["violations"])
    # compliant: untouched; other prompts: not touched at all
    ok = PronunciationTip(tip_de="Runde das ü stärker.", practice_words=["Übung"])
    ok_state = SessionState()
    assert engine.execute(_call(prompt_name="pronunciation_tip"), ok_state, Counter(ok)).result.tip_de == ok.tip_de
    assert "pronunciation_flagged" not in ok_state.extra
    text = "Die Abbruchquote liegt bei 30 %."
    assert engine.execute(_call(prompt_name="tutor_system"), SessionState(), Counter(text)).result == text


def test_counterpart_no_correction_regenerates_once_then_neutralizes() -> None:
    call = _call(prompt_name="roleplay_counterpart")
    state = SessionState()
    bad = "Richtig heißt es: „ich möchte“. Fehler: Dativ. Aber gut, welche Laufzeit?"
    res1 = engine.execute(call, state, Counter(bad))
    assert state.extra.get("regenerate") is True
    assert "Fehler:" not in res1.result and "heißt es" not in res1.result.lower()
    res2 = engine.execute(call, state, Counter("Korrektur: Sie meinen sicher 'die Laufzeit'. Tipp: Konjunktiv."))
    assert state.extra.get("regenerate") is False
    assert res2.result == hooks_mod.NEUTRAL_COUNTERPART_LINE_DE
    assert sum(1 for v in state.extra["violations"] if v["hook"] == "counterpart_no_correction") == 2
    clean = "Das ist ein interessanter Vorschlag. Welche Laufzeit hätten Sie sich vorgestellt?"
    ok_state = SessionState()
    assert engine.execute(call, ok_state, Counter(clean)).result == clean and "regenerate" not in ok_state.extra


def test_clamp_scores_clamps_and_recomputes_total() -> None:
    sc = CriterionScore.model_construct(criterion="korrektheit", score=9, evidence=[], comment_de="")
    sc2 = CriterionScore.model_construct(criterion="variation", score=-3, evidence=[], comment_de="")
    out = LLMRubricOutput.model_construct(
        scores=[sc, sc2], error_tags=[], better_formulations=[], new_vocabulary=[], summary_de="x"
    )
    res = engine.execute(_call(tier="assessment", prompt_name="writing_feedback"), SessionState(), Counter(out))
    assert [s.score for s in res.result.scores] == [4, 0]
    coach = LLMCoachOutput.model_construct(
        outcome_vs_goals="o",
        moves_used=[],
        moves_missed=[],
        language_feedback="",
        error_tags=[],
        missing_redemittel=[],
        better_formulations=[],
        next_focus="",
        rubric_scores=[CriterionScore.model_construct(criterion="praezision", score=6, evidence=[], comment_de="")],
        rubric_summary_de="",
    )
    res = engine.execute(
        _call(tier="assessment", prompt_name="roleplay_coach"), SessionState(roleplay_ended=True), Counter(coach)
    )
    assert res.result.rubric_scores[0].score == 4
    dict_result = {"scores": [{"criterion": "a", "score": 7}, {"criterion": "b", "score": 2}], "total": 99}
    res = engine.execute(_call(), SessionState(), Counter(dict_result))
    assert [s["score"] for s in res.result["scores"]] == [4, 2] and res.result["total"] == 6
    fine = LLMRubricOutput(scores=[CriterionScore(criterion="a", score=3)], summary_de="s")
    assert engine.execute(_call(), SessionState(), Counter(fine)).result.scores[0].score == 3


# ---------------------------------------------------------------- wiring: adapters go through the engine


def test_llm_complete_is_denied_without_learner_id() -> None:
    from app.services import llm

    with pytest.raises(HookDenied) as ei:
        llm.complete(
            "conversation",
            [{"role": "user", "content": "Hallo"}],
            prompt_version="1.0.0",
            session_id="s",
            learner_id=None,
        )
    assert ei.value.result.error is not None and "ledger_required" in ei.value.result.error.message


def test_llm_complete_denied_coach_before_end_writes_no_ledger_row() -> None:
    from app.services import llm

    from tests.conftest import ledger_count

    before = ledger_count()
    with pytest.raises(HookDenied):
        llm.complete(
            "assessment",
            [{"role": "user", "content": "Coach!"}],
            prompt_name="roleplay_coach",
            prompt_version="1.0.0",
            session_id="s-coach",
            learner_id="l",
        )
    assert ledger_count() == before  # the blocked call provably never executed


def test_stt_tts_embed_denied_without_ids(tmp_path: Path) -> None:
    from app.services import embeddings, stt, tts
    from app.services.fakes import silent_wav

    wav = tmp_path / "a.wav"
    wav.write_bytes(silent_wav(1.0))
    with pytest.raises(HookDenied):
        stt.get_stt().transcribe(wav, session_id=None, learner_id="l")
    with pytest.raises(HookDenied):
        tts.get_tts().synthesize("Hallo", session_id="s", learner_id=None)
    with pytest.raises(HookDenied):
        embeddings.get_embeddings().embed(["x"], session_id=None, learner_id=None)


def test_hook_denied_maps_to_http_400_business(client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import tutor

    def denied(*a: Any, **k: Any) -> Any:
        raise HookDenied(ToolResult.fail("business", "hook ledger_required: fehlende Metadaten"))

    monkeypatch.setattr(tutor, "_tutor_reply", denied)
    sid = client.post("/sessions", json={"kind": "tutor"}).json()["session_id"]
    r = client.post(f"/sessions/{sid}/turn", json={"text": "Hallo"})
    assert r.status_code == 400
    body = r.json()
    assert body["error_category"] == "business" and "ledger_required" in body["detail"]


def test_no_bypass_direct_litellm_calls() -> None:
    """No ``litellm.<call>(`` outside the adapter choke points (+ the settings connectivity test)."""
    allowed = {
        APP_DIR / "services" / "llm.py",
        APP_DIR / "services" / "stt.py",
        APP_DIR / "services" / "tts.py",
        APP_DIR / "services" / "embeddings.py",
        APP_DIR / "api" / "settings.py",  # POST /settings/test connectivity ping (metered, no learner data)
        APP_DIR / "services" / "provider_test.py",  # POST /provider-credentials/{p}/test (metered, no learner data)
    }
    pattern = re.compile(r"litellm\.(completion|speech|transcription|embedding|acompletion)\(")
    hits: list[str] = []
    for f in APP_DIR.rglob("*.py"):
        if f in allowed:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "direct provider calls bypass the hook engine:\n" + "\n".join(hits)


# ---------------------------------------------------------------- comparison harness


def _load_harness() -> Any:
    path = ROOT / "evals" / "hooks_comparison.py"
    spec = importlib.util.spec_from_file_location("hooks_comparison", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_comparison_harness_hooks_arm_has_zero_violations() -> None:
    harness = _load_harness()
    report = harness.run(ROOT / "backend" / "tests" / "fixtures" / "recorded_responses.jsonl")
    assert report["scenarios"] >= 10
    assert report["hooks_arm_violations"] == 0
    assert all(s["hooks_arm"] == 0 for s in report["per_scenario"])
    # The prompt-only arm is reported for information only; its rate is never asserted (flaky by nature).
    assert "prompt_arm_violations" in report
