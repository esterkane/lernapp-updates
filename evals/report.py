"""Write eval reports as markdown + json (evals/reports/<date>_<sha>.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report['ts'][:10]}_{report['git_sha']}_{report['run_id']}"
    (out_dir / f"{stem}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        f"# Eval report {stem}",
        "",
        f"- backend: `{report['llm_backend']}`",
        f"- models: `{report['models']}`",
        f"- cost: {report['cost_eur']:.4f} €",
        "",
    ]
    for name, res in report["suites"].items():
        lines += [f"## {name}", "", "| metric | value |", "|---|---|"]
        for k, v in res.items():
            if k == "items":
                continue
            lines.append(f"| {k} | {v} |")
        lines.append("")
        items = res.get("items") if isinstance(res, dict) else None
        if items:
            lines += [
                "| item | schema_validity | accuracy | within_1 | rule_compliance | routing | cost € | tiers |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for it in items:
                lines.append(
                    f"| {it.get('id')} | {it.get('schema_validity')} | {it.get('accuracy')} | {it.get('within_1')} | "
                    f"{it.get('rule_compliance')} | {it.get('routing')} | {it.get('cost_eur')} | "
                    f"{', '.join(it.get('tiers') or [])} |"
                )
            lines.append("")
    calib = report.get("calibration")
    if calib:
        lines += [
            "## calibration (ADR-0017 §4)",
            "",
            f"- labelled spot-checks: {calib.get('n_items')} · Brier mean: {calib.get('brier_mean')} · report: `{calib.get('report')}`",
            "",
            "| skill | criterion | n | mean confidence | observed agreement | Brier |",
            "|---|---|---|---|---|---|",
        ]
        for c in calib.get("cells") or []:
            lines.append(
                f"| {c['skill']} | {c['criterion']} | {c['n']} | {c['mean_confidence']} | {c['observed_agreement']} | {c['brier']} |"
            )
        if calib.get("under_sampled"):
            lines += ["", "Under-sampled cells (< 5 labels): " + ", ".join(calib["under_sampled"])]
        lines.append("")
    md = out_dir / f"{stem}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md
