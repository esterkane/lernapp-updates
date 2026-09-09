#!/usr/bin/env python
"""Eval harness (eval-harness skill). Usage: uv run python evals/run_evals.py --suite all|writing|rag|tasks|hooks

Runs against whatever backends are configured (LLM_BACKEND=fake works offline). Costs are attributed
to session_id ``eval-<run_id>`` (per item: ``eval-<run_id>-<item_id>``) in the ledger. Every writing item
records a trace (tiers, versions, cost) and is scored by the evaluators in ``evals/evaluators/`` with
partial credit; the calibration report (ADR-0017 §4) is attached when spot-check labels exist.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from evals import calibration  # noqa: E402
from evals.evaluators import accuracy, rule_compliance, schema_validity, trace  # noqa: E402
from evals.report import write_report  # noqa: E402


def _jsonl(name: str) -> list[dict[str, Any]]:
    p = ROOT / "evals" / "golden" / name
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def suite_writing(run_id: str) -> dict[str, Any]:
    from app.services.assessment import assess_writing
    from app.services.schemas import LLMRubricOutput

    items = _jsonl("writing_b2c1.jsonl")
    per_criterion: dict[str, list[float]] = {}
    within1: list[int] = []
    totals_h: list[int] = []
    totals_m: list[int] = []
    per_item: list[dict[str, Any]] = []
    for it in items:
        item_sid = f"eval-{run_id}-{it.get('id', len(per_item))}"
        out = assess_writing(
            task_text=it["task"],
            learner_text=it["text"],
            learner_id="default",
            is_progress_point=False,
            session_id=item_sid,
            expected_content=None,
            task_id=None,
        )
        scores = {s["criterion"]: s["score"] for s in out["rubric"]["scores"]}
        for c, h in it["human_scores"].items():
            m = scores.get(c)
            if m is None:
                continue
            per_criterion.setdefault(c, []).append(abs(m - h))
            within1.append(int(abs(m - h) <= 1))
        totals_h.append(sum(it["human_scores"].values()))
        totals_m.append(sum(scores.values()))
        item_trace = trace.capture(item_sid)
        acc = accuracy.evaluate(scores, it["human_scores"])
        per_item.append(
            {
                "id": it.get("id"),
                "schema_validity": schema_validity.score(out["rubric"], LLMRubricOutput),
                "accuracy": acc["score"],
                "mae": acc["mae"],
                "within_1": acc["within_1"],
                "rule_compliance": rule_compliance.score(item_trace),
                "routing": out.get("routing"),
                "dropped_evidence": len(out["rubric"].get("dropped_evidence") or []),
                "cost_eur": item_trace["cost_eur"],
                "tiers": [t["tier"] for t in item_trace["tiers"]],
                "prompt_versions": sorted({str(t["prompt_version"]) for t in item_trace["tiers"]}),
            }
        )
    mae = {c: round(statistics.mean(v), 3) for c, v in per_criterion.items()}
    n = len(items)
    cost_total = round(sum(i["cost_eur"] for i in per_item), 6)
    return {
        "n": n,
        "mae_per_criterion": mae,
        "mae_mean": round(statistics.mean(mae.values()), 3) if mae else None,
        "within_1": round(statistics.mean(within1), 3) if within1 else None,
        "spearman_total": _spearman(totals_h, totals_m),
        "schema_validity": round(statistics.mean(i["schema_validity"] for i in per_item), 3) if per_item else None,
        "accuracy": round(statistics.mean(i["accuracy"] for i in per_item), 3) if per_item else None,
        "rule_compliance": round(statistics.mean(i["rule_compliance"] for i in per_item), 3) if per_item else None,
        "routing_counts": {
            r: sum(i["routing"] == r for i in per_item) for r in sorted({i["routing"] for i in per_item})
        },
        "cost_eur": cost_total,
        "cost_per_item_eur": round(cost_total / n, 6) if n else 0.0,
        "items": per_item,
    }


def suite_hooks(run_id: str) -> dict[str, Any]:
    """Hooks arm vs prompt-only arm (``evals/hooks_comparison.py``, guardrail-hooks skill) — skipped until present."""
    try:
        from evals import hooks_comparison
    except ImportError:
        return {"skipped": "evals/hooks_comparison.py fehlt noch – Vergleich Hooks vs. Prompt-only nicht ausgeführt"}
    runner = getattr(hooks_comparison, "run", None)
    if runner is None:
        return {"skipped": "evals/hooks_comparison.py hat keine run()-Funktion"}
    result: dict[str, Any] = runner()  # default recorded-response fixture; asserts hook_violations == 0
    return {"run_id": run_id, **result}


def suite_rag(run_id: str) -> dict[str, Any]:
    from app.core.rubrics import redemittel_text
    from app.services import rag

    # Ensure the Redemittel bank is indexed for the default learner as a fixture document.
    docs = rag.list_documents("default")
    if not any(d["title"] == "eval-redemittel" for d in docs):
        rag.ingest(
            "default",
            "eval-redemittel.md",
            redemittel_text().encode("utf-8"),
            tags=["verhandlung"],
            use_in={"uebungen": True, "rollenspiel": True, "tutor": True},
        )
    items = _jsonl("rag_golden.jsonl")
    recall_hits = 0
    rr: list[float] = []
    for it in items:
        hits = rag.search(it["query"], "default", k=5)
        found_rank = None
        for i, h in enumerate(hits):
            if all(sub.lower() in h.text.lower() for sub in it["expected_chunk_substrings"]):
                found_rank = i + 1
                break
        if found_rank:
            recall_hits += 1
            rr.append(1.0 / found_rank)
        else:
            rr.append(0.0)
    return {
        "n": len(items),
        "recall_at_5": round(recall_hits / len(items), 3) if items else None,
        "mrr": round(statistics.mean(rr), 3) if rr else None,
    }


def suite_tasks(run_id: str) -> dict[str, Any]:
    """Validator precision/recall on synthetic tasks: does static blueprint compliance agree with should_pass?"""
    from app.core.blueprints import get_blueprint

    items = _jsonl("task_validation.jsonl")
    tp = fp = fn = tn = 0
    for it in items:
        bp = get_blueprint(it["blueprint_id"])
        t = it["task"]
        bt = bp.task(t.get("task_type", ""))
        ok = bt is not None
        if ok and bt is not None:
            for f in ("preparation_seconds", "response_seconds"):
                if t.get(f) is not None and getattr(bt, f) is not None and t[f] != getattr(bt, f):
                    ok = False
        if ok and it["should_pass"]:
            tp += 1
        elif ok and not it["should_pass"]:
            fp += 1
        elif not ok and it["should_pass"]:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"n": len(items), "precision": prec, "recall": rec, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _spearman(a: list[int], b: list[int]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None

    def ranks(x: list[int]) -> list[float]:
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        for rank, i in enumerate(order):
            r[i] = float(rank + 1)
        return r

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    d2 = sum((x - y) ** 2 for x, y in zip(ra, rb, strict=True))
    return round(1 - 6 * d2 / (n * (n * n - 1)), 3)


def run(suite: str = "all") -> dict[str, Any]:
    from app.core import ledger
    from app.core.db import init_db

    init_db()
    run_id = uuid.uuid4().hex[:8]
    suites = {"writing": suite_writing, "rag": suite_rag, "tasks": suite_tasks, "hooks": suite_hooks}
    chosen = list(suites) if suite == "all" else [suite]
    results: dict[str, Any] = {}
    for name in chosen:
        try:
            results[name] = suites[name](run_id)
        except Exception as exc:  # noqa: BLE001
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
    cost = ledger.session_cost_eur(f"eval-{run_id}") + sum(
        float(r.get("cost_eur") or 0.0) for r in results.values() if isinstance(r, dict) and "items" in r
    )
    labelled = calibration.load_spotcheck_entries(ROOT / "evals" / "golden")
    calib: dict[str, Any] | None = None
    if labelled:
        md, _js = calibration.write_calibration_report(labelled, ROOT / "evals" / "reports")
        calib = {**calibration.calibration_summary(labelled), "report": str(md.relative_to(ROOT))}
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=False
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        sha = "nogit"
    from app.core.config import get_settings
    from app.core.models import resolve_tier

    report = {
        "run_id": run_id,
        "ts": datetime.now(UTC).isoformat(),
        "git_sha": sha,
        "llm_backend": get_settings().llm_backend,
        "models": {
            t: resolve_tier(t).model for t in ("conversation", "assessment", "validator", "generator", "embedding")
        },
        "suites": results,
        "cost_eur": cost,
        "calibration": calib,
    }
    write_report(report, ROOT / "evals" / "reports")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all")
    args = ap.parse_args()
    rep = run(args.suite)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
