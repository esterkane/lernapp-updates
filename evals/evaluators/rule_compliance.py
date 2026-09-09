"""Rule compliance from a trace: 0 on any hook violation or a same-vendor validator (product rule 3 /
ADR-0004), otherwise the fraction of tier entries that are complete (partial credit for thin traces).

Trace shape (``evals/evaluators/trace.py``): ``{tiers: [{tier, model, vendor}], violations: [...]}``.
"""

from __future__ import annotations

from typing import Any


def evaluate(trace: dict[str, Any]) -> dict[str, Any]:
    tiers = list(trace.get("tiers") or [])
    violations = list(trace.get("violations") or [])
    problems: list[str] = [f"violation: {v}" for v in violations]
    vendors = {str(t.get("tier")): t.get("vendor") for t in tiers if t.get("vendor")}
    if vendors.get("assessment") and vendors.get("validator") and vendors["assessment"] == vendors["validator"]:
        problems.append(f"validator vendor equals assessment vendor ({vendors['validator']})")
    if problems:
        return {"score": 0.0, "problems": problems, "n_tiers": len(tiers)}
    if not tiers:
        return {"score": 0.0, "problems": ["trace has no tier entries"], "n_tiers": 0}
    complete = sum(bool(t.get("tier") and t.get("model") and t.get("vendor")) for t in tiers)
    return {"score": complete / len(tiers), "problems": [], "n_tiers": len(tiers)}


def score(trace: dict[str, Any]) -> float:
    return float(evaluate(trace)["score"])
