#!/usr/bin/env python
"""Calibration report (ADR-0017 §4): per (skill × criterion) mean predicted confidence, observed agreement
with human labels (|model − human| ≤ 1) and Brier score of confidence vs agreement.

Input: ``evals/golden/spotchecks_<skill>.jsonl`` written by ``scripts/export_spotchecks.py``.
Output: ``evals/reports/calibration_<date>.md`` + ``.json``. Published with every eval run; any proposal
to show numeric pronunciation scores (ADR-0010) must cite this report.

Usage: uv run python evals/calibration.py [--golden evals/golden] [--out evals/reports]
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AGREE_WITHIN = 1  # |model − human| ≤ 1 counts as agreement (eval-harness skill metric "within ±1")
MIN_SAMPLES = 5  # cells below this are reported as under-sampled for the golden-set curator


def load_spotcheck_entries(golden_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not golden_dir.exists():
        return entries
    for path in sorted(golden_dir.glob("spotchecks_*.jsonl")):
        skill = path.stem.removeprefix("spotchecks_")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            entry = json.loads(line)
            entry.setdefault("skill", skill)
            entries.append(entry)
    return entries


def calibration_cells(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One cell per (skill × criterion) with n, mean_confidence, observed_agreement, brier."""
    buckets: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for e in entries:
        skill = str(e.get("skill") or "unbekannt")
        human = e.get("human_scores") or {}
        model = e.get("model_scores") or {}
        conf = e.get("confidence") or {}
        for criterion, h in human.items():
            if criterion not in model:
                continue
            agree = int(abs(int(model[criterion]) - int(h)) <= AGREE_WITHIN)
            c = float(conf.get(criterion, 0.5))
            buckets.setdefault((skill, criterion), []).append((c, agree))
    cells: list[dict[str, Any]] = []
    for (skill, criterion), pairs in sorted(buckets.items()):
        confs = [c for c, _ in pairs]
        agrees = [a for _, a in pairs]
        cells.append(
            {
                "skill": skill,
                "criterion": criterion,
                "n": len(pairs),
                "mean_confidence": statistics.mean(confs),
                "observed_agreement": statistics.mean(agrees),
                "brier": statistics.mean((c - a) ** 2 for c, a in pairs),
            }
        )
    return cells


def calibration_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    cells = calibration_cells(entries)
    return {
        "n_items": len(entries),
        "n_cells": len(cells),
        "cells": cells,
        "brier_mean": round(statistics.mean(c["brier"] for c in cells), 4) if cells else None,
        "under_sampled": [f"{c['skill']}×{c['criterion']} (n={c['n']})" for c in cells if c["n"] < MIN_SAMPLES],
    }


def render_markdown(summary: dict[str, Any], date: str) -> str:
    lines = [
        f"# Kalibrierung {date}",
        "",
        f"- Stichproben mit menschlichem Label: {summary['n_items']}",
        f"- Zellen (Fertigkeit × Kriterium): {summary['n_cells']}",
        f"- Brier-Score (Mittel): {summary['brier_mean']}",
        "",
        "Übereinstimmung = |Modell − Mensch| ≤ 1. Brier = Mittel von (Konfidenz − Übereinstimmung)²; 0 ist perfekt.",
        "",
        "| Fertigkeit | Kriterium | n | Ø Konfidenz | beobachtete Übereinstimmung | Brier |",
        "|---|---|---|---|---|---|",
    ]
    for c in summary["cells"]:
        lines.append(
            f"| {c['skill']} | {c['criterion']} | {c['n']} | {c['mean_confidence']:.2f} | "
            f"{c['observed_agreement']:.2f} | {c['brier']:.3f} |"
        )
    if summary["under_sampled"]:
        lines += ["", f"Zellen mit weniger als {MIN_SAMPLES} Labels: " + ", ".join(summary["under_sampled"])]
    return "\n".join(lines) + "\n"


def write_calibration_report(
    entries: list[dict[str, Any]], out_dir: Path, date: str | None = None
) -> tuple[Path, Path]:
    date = date or datetime.now(UTC).strftime("%Y-%m-%d")
    summary = {"date": date, **calibration_summary(entries)}
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"calibration_{date}.md"
    js = out_dir / f"calibration_{date}.json"
    md.write_text(render_markdown(summary, date), encoding="utf-8")
    js.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kalibrierungsbericht aus geprüften Stichproben")
    ap.add_argument("--golden", default=str(ROOT / "evals" / "golden"))
    ap.add_argument("--out", default=str(ROOT / "evals" / "reports"))
    args = ap.parse_args(argv)
    entries = load_spotcheck_entries(Path(args.golden))
    if not entries:
        print("Keine geprüften Stichproben gefunden (evals/golden/spotchecks_*.jsonl) – kein Bericht geschrieben.")
        return 0
    md, js = write_calibration_report(entries, Path(args.out))
    print(md)
    print(js)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
