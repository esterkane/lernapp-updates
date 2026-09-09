#!/usr/bin/env python
"""Hooks-vs-prompt comparison harness (ADR-0016 consequences, guardrail-hooks skill).

Replays recorded model outputs (``backend/tests/fixtures/recorded_responses.jsonl``) through two arms:

- **prompt-only arm**: the raw response is checked by deterministic violation detectors — this is
  what a prompt instruction alone would have let through (reported for information only).
- **hooks arm**: the same response is served by a stub ``dispatch`` through ``engine.execute`` and
  the detectors run on the *normalized* output.

Tests assert ``hooks_arm_violations == 0`` and never a specific prompt-arm rate.

Usage: ``uv run python evals/hooks_comparison.py [fixture.jsonl]``
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.core.hooks import SessionState, TierCall, engine, register_default_hooks  # noqa: E402
from app.services.schemas import SCHEMAS  # noqa: E402

DEFAULT_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "recorded_responses.jsonl"

# Detectors mirror the product rules, not the hook implementations (independent oracle).
_TDN = re.compile(r"TDN\s?[3-5]|TestDaF-Niveau|TDN-Stufe", re.IGNORECASE)
_PCT = re.compile(r"\d{1,3}(?:[.,]\d+)?\s?%")
_SCORE = re.compile(r"\bScore\s*[:=]?\s*\d", re.IGNORECASE)
_CORRECTION = re.compile(r"richtig hei(?:ß|ss)t es|Fehler\s*:|korrekt w[äa]re|Korrektur|Tipp\s*:", re.IGNORECASE)


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings(v)]
    if hasattr(obj, "model_dump"):
        return _strings(obj.model_dump(mode="json"))
    return []


def _scores(obj: Any) -> list[Any]:
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    if not isinstance(data, dict):
        return []
    out: list[Any] = []
    for key in ("scores", "rubric_scores"):
        for sc in data.get(key) or []:
            if isinstance(sc, dict) and "score" in sc:
                out.append(sc["score"])
    return out


def violations(output: Any, rules: list[str]) -> list[str]:
    """Names of the product rules ``output`` violates (subset of ``rules``)."""
    texts = _strings(output)
    found: list[str] = []
    if "no_tdn_output" in rules and any(_TDN.search(t) for t in texts):
        found.append("no_tdn_output")
    if "no_numeric_pronunciation" in rules and any(_PCT.search(t) or _SCORE.search(t) for t in texts):
        found.append("no_numeric_pronunciation")
    if "counterpart_no_correction" in rules and any(_CORRECTION.search(t) for t in texts):
        found.append("counterpart_no_correction")
    if "clamp_scores" in rules and any(not (0 <= int(s) <= 4) for s in _scores(output)):
        found.append("clamp_scores")
    return found


def _materialize(scenario: dict[str, Any]) -> Any:
    """Recorded responses are stored as JSON; structured ones are rebuilt as the schema object the
    adapter would have produced (``model_construct`` so out-of-range scores survive for clamp_scores)."""
    response = scenario["response"]
    schema_name = scenario.get("schema")
    if not schema_name:
        return response
    schema = SCHEMAS[schema_name]
    try:
        return schema.model_validate(response)
    except Exception:  # noqa: BLE001 — e.g. score 7 fails conint(le=4); mimic an unvalidated model
        data = dict(response)
        for key in ("scores", "rubric_scores"):
            if key in data:
                from app.services.schemas import CriterionScore

                data[key] = [CriterionScore.model_construct(**sc) for sc in data[key]]
        return schema.model_construct(**data)


def run(fixture: Path | str = DEFAULT_FIXTURE) -> dict[str, Any]:
    register_default_hooks()
    scenarios = [
        json.loads(line)
        for line in Path(fixture).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    per_scenario: list[dict[str, Any]] = []
    prompt_total = hooks_total = 0
    for sc in scenarios:
        rules = list(sc.get("rules", []))
        raw = _materialize(sc)
        prompt_arm = violations(raw, rules)
        call = TierCall(
            kind=sc.get("kind", "llm"),
            tier=sc.get("tier", "conversation"),
            model="recorded/fixture",
            prompt_name=sc.get("prompt_name"),
            prompt_version="fixture",
            session_id=f"harness-{sc['id']}",
            learner_id="harness",
        )
        state = SessionState(roleplay_ended=bool(sc.get("roleplay_ended", False)))

        # A fresh copy for the hooks arm so the prompt arm's object is not normalized in place.
        def dispatch(_c: Any, sc: dict[str, Any] = sc) -> Any:
            return _materialize(sc)

        res = engine.execute(call, state, dispatch)
        hooked = res.result if res.success else None
        hooks_arm = violations(hooked, rules) if res.success else []
        if res.success and state.extra.get("regenerate"):
            # counterpart_no_correction asks the caller to regenerate once; the harness replays the same
            # recorded output, so the second pass exercises the neutral-line replacement.
            res = engine.execute(call, state, dispatch)
            hooks_arm = violations(res.result, rules)
        per_scenario.append(
            {
                "id": sc["id"],
                "rules": rules,
                "prompt_arm": len(prompt_arm),
                "prompt_arm_rules": prompt_arm,
                "hooks_arm": len(hooks_arm),
                "hooks_arm_rules": hooks_arm,
                "denied": not res.success,
                "hook_violations_logged": len(state.extra.get("violations", [])),
            }
        )
        prompt_total += len(prompt_arm)
        hooks_total += len(hooks_arm)
    return {
        "fixture": str(fixture),
        "scenarios": len(scenarios),
        "prompt_arm_violations": prompt_total,
        "hooks_arm_violations": hooks_total,
        "per_scenario": per_scenario,
    }


def main(argv: list[str]) -> int:
    fixture = Path(argv[1]) if len(argv) > 1 else DEFAULT_FIXTURE
    report = run(fixture)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["hooks_arm_violations"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
