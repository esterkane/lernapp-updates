#!/usr/bin/env python
"""Export human-labelled spot-checks as golden-set entries (ADR-0017 §2, golden-set-curator agent).

Usage:
    uv run python scripts/export_spotchecks.py --learner default --out evals/golden

Writes ``spotchecks_<skill>.jsonl`` per skill in the ``writing_b2c1.jsonl`` shape
``{id, task, text, human_scores, error_tags, model_scores, confidence, …}``. Existing ids are never
modified or duplicated — re-running appends only new labels.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))


def golden_entry(result: dict[str, Any]) -> dict[str, Any] | None:
    """One labelled ``results`` row (``results.result_to_dict`` shape) → golden-set entry."""
    payload = result.get("payload") or {}
    label = payload.get("human_label") or {}
    rubric = payload.get("rubric") or {}
    if not label.get("scores") or not rubric.get("scores"):
        return None
    handoff = payload.get("review_handoff") or {}
    scores = rubric.get("scores") or []
    model_scores = handoff.get("assessment_scores") or {s["criterion"]: int(s["score"]) for s in scores}
    text = handoff.get("learner_text") or payload.get("learner_text") or payload.get("transcript") or ""
    return {
        "id": result["result_id"],
        "skill": result.get("skill"),
        "task_type": result.get("task_type"),
        "rubric_version": result.get("rubric_version"),
        "task": handoff.get("task_text") or payload.get("task_text") or "",
        "text": text,
        "human_scores": {c: int(v) for c, v in label["scores"].items()},
        "error_tags": list(result.get("error_tags") or []),
        "model_scores": {c: int(v) for c, v in model_scores.items()},
        "validator_scores": handoff.get("validator_scores"),
        "confidence": {s["criterion"]: float(s.get("confidence", 0.5)) for s in scores},
        "routing_before": label.get("routing_before"),
        "note": label.get("note", ""),
        "labelled_at": label.get("labelled_at"),
        "model_version": result.get("model_version"),
        "prompt_version": result.get("prompt_version"),
    }


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            try:
                ids.add(str(json.loads(line).get("id")))
            except json.JSONDecodeError:
                continue
    return ids


def export_spotchecks(learner_id: str, out_dir: Path) -> list[Path]:
    """Append new labelled results to ``<out_dir>/spotchecks_<skill>.jsonl``; returns the files touched."""
    from app.services.results import labelled_results

    by_skill: dict[str, list[dict[str, Any]]] = {}
    for r in labelled_results(learner_id):
        entry = golden_entry(r)
        if entry is not None:
            by_skill.setdefault(str(entry["skill"]), []).append(entry)
    written: list[Path] = []
    for skill in sorted(by_skill):
        path = out_dir / f"spotchecks_{skill}.jsonl"
        known = _existing_ids(path)
        new = [e for e in by_skill[skill] if e["id"] not in known]
        out_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for e in new:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Geprüfte Stichproben als Golden-Set-Einträge exportieren")
    parser.add_argument("--learner", default="default", help="learner id (default: 'default')")
    parser.add_argument("--out", default=str(ROOT / "evals" / "golden"), help="output directory")
    args = parser.parse_args(argv)

    from app.core.db import init_db

    init_db()
    written = export_spotchecks(args.learner, Path(args.out))
    if not written:
        print("Keine geprüften Stichproben vorhanden.")
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
