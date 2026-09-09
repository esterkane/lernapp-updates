"""ADR-0017 §4 + eval-harness skill: calibration report and evaluators with partial credit.

- ``evals/calibration.py``: per (skill × criterion) n, mean predicted confidence, observed agreement
  (|model − human| ≤ 1) and Brier score; markdown + json report.
- ``evals/evaluators/*``: each returns 0–1 with partial credit; ``rule_compliance`` reads a trace and fails
  a same-vendor validator; ``trace`` captures tiers/versions/cost from the ledger rows of an eval session.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import calibration  # noqa: E402
from evals.evaluators import accuracy, rule_compliance, schema_validity, trace  # noqa: E402

CRITERIA = [
    "aufgabenbezug",
    "sprachfunktionen",
    "quellennutzung",
    "eigenformulierung",
    "praezision",
    "variation",
    "korrektheit",
]


def _entry(item_id: str, model: dict[str, int], human: dict[str, int], conf: dict[str, float]) -> dict[str, Any]:
    return {
        "id": item_id,
        "skill": "schreiben",
        "task": "Aufgabe",
        "text": "Text",
        "human_scores": human,
        "error_tags": [],
        "model_scores": model,
        "confidence": conf,
    }


def _three_labelled() -> list[dict[str, Any]]:
    base = {c: 3 for c in CRITERIA}
    conf = {c: 0.9 for c in CRITERIA}
    return [
        _entry("a", base, base, conf),  # perfect agreement
        _entry("b", base, {**base, "korrektheit": 1}, {**conf, "korrektheit": 0.6}),  # off by 2 on one criterion
        _entry("c", base, {**base, "variation": 2}, conf),  # off by 1 → still "agree"
    ]


# ---------------------------------------------------------------- calibration


def test_calibration_cells_on_three_labelled_items() -> None:
    cells = {(c["skill"], c["criterion"]): c for c in calibration.calibration_cells(_three_labelled())}
    assert len(cells) == 7
    k = cells[("schreiben", "korrektheit")]
    assert k["n"] == 3
    assert k["observed_agreement"] == pytest.approx(2 / 3)
    assert k["mean_confidence"] == pytest.approx((0.9 + 0.6 + 0.9) / 3)
    # Brier = mean((confidence − agree)²): (0.1² + 0.6² + 0.1²) / 3
    assert k["brier"] == pytest.approx((0.01 + 0.36 + 0.01) / 3)
    v = cells[("schreiben", "variation")]
    assert v["observed_agreement"] == pytest.approx(1.0) and v["brier"] == pytest.approx(0.01)
    assert all(0.0 <= c["brier"] <= 1.0 for c in cells.values())


def test_calibration_report_files(tmp_path: Path) -> None:
    md, js = calibration.write_calibration_report(_three_labelled(), tmp_path, date="2026-09-08")
    assert md.name == "calibration_2026-09-08.md" and js.name == "calibration_2026-09-08.json"
    text = md.read_text(encoding="utf-8")
    assert "korrektheit" in text and "Brier" in text
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["n_items"] == 3 and len(data["cells"]) == 7
    assert data["cells"][0]["skill"] == "schreiben"
    assert "under_sampled" in data  # cells with fewer than 5 labels are flagged for the curator
    assert len(data["under_sampled"]) == 7


def test_calibration_loads_spotcheck_files(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "spotchecks_schreiben.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in _three_labelled()) + "\n", encoding="utf-8"
    )
    (golden / "writing_b2c1.jsonl").write_text('{"id":"w1"}\n', encoding="utf-8")  # not a spot-check file
    entries = calibration.load_spotcheck_entries(golden)
    assert [e["id"] for e in entries] == ["a", "b", "c"]
    assert calibration.load_spotcheck_entries(tmp_path / "missing") == []
    assert calibration.main(["--golden", str(golden), "--out", str(tmp_path / "reports")]) == 0
    assert list((tmp_path / "reports").glob("calibration_*.md"))
    assert calibration.main(["--golden", str(tmp_path / "missing"), "--out", str(tmp_path / "r2")]) == 0
    assert not (tmp_path / "r2").exists() or not list((tmp_path / "r2").glob("*.md"))


# ---------------------------------------------------------------- evaluators


def test_accuracy_partial_credit() -> None:
    human = {c: 3 for c in CRITERIA}
    perfect = accuracy.evaluate(human, human)
    assert perfect["score"] == pytest.approx(1.0) and perfect["mae"] == 0.0 and perfect["within_1"] == 1.0
    off = accuracy.evaluate({**human, "korrektheit": 1, "variation": 2}, human)
    assert off["mae"] == pytest.approx(3 / 7)
    assert off["score"] == pytest.approx(1 - (3 / 7) / 4)
    assert off["within_1"] == pytest.approx(6 / 7)
    assert off["n"] == 7
    assert accuracy.score({}, human) == 0.0  # nothing to compare → no credit
    worst = accuracy.evaluate({c: 0 for c in CRITERIA}, {c: 4 for c in CRITERIA})
    assert worst["score"] == 0.0


def test_schema_validity_fraction_of_valid_fields() -> None:
    from app.services.schemas import LLMRubricOutput

    good = {
        "scores": [
            {"criterion": c, "score": 3, "evidence": [], "comment_de": "ok", "confidence": 0.9} for c in CRITERIA
        ],
        "error_tags": ["text/konnektoren"],
        "better_formulations": [["a", "b"]],
        "new_vocabulary": ["abwägen"],
        "summary_de": "Gut.",
    }
    assert schema_validity.score(good, LLMRubricOutput) == pytest.approx(1.0)
    bad = {**good, "scores": "nicht eine Liste", "summary_de": None}
    res = schema_validity.evaluate(bad, LLMRubricOutput)
    assert 0.0 < res["score"] < 1.0
    assert set(res["invalid"]) == {"scores", "summary_de"}
    assert res["score"] == pytest.approx((res["total_fields"] - 2) / res["total_fields"])
    assert schema_validity.score({}, LLMRubricOutput) < 1.0  # required fields missing
    assert schema_validity.score("kein dict", LLMRubricOutput) == 0.0


def test_rule_compliance_reads_trace() -> None:
    ok = {
        "tiers": [
            {"tier": "assessment", "model": "openai/gpt-x", "vendor": "openai"},
            {"tier": "validator", "model": "anthropic/claude-y", "vendor": "anthropic"},
        ],
        "violations": [],
    }
    assert rule_compliance.score(ok) == pytest.approx(1.0)
    same_vendor = {
        "tiers": [
            {"tier": "assessment", "model": "openai/gpt-x", "vendor": "openai"},
            {"tier": "validator", "model": "openai/gpt-z", "vendor": "openai"},
        ],
        "violations": [],
    }
    res = rule_compliance.evaluate(same_vendor)
    assert res["score"] == 0.0 and any("vendor" in p for p in res["problems"])
    violated = {**ok, "violations": [{"hook": "no_tdn", "detail": "TDN"}]}
    assert rule_compliance.score(violated) == 0.0
    partial = {"tiers": [{"tier": "assessment", "model": "openai/gpt-x", "vendor": "openai"}, {"tier": "validator"}]}
    p = rule_compliance.evaluate(partial)
    assert 0.0 < p["score"] < 1.0  # incomplete trace: partial credit, not a violation


def test_trace_from_ledger_rows() -> None:
    rows = [
        {"stage": "llm", "model": "openai/gpt-x", "prompt_version": "1.0.0", "cost_eur": 0.002},
        {"stage": "llm", "model": "anthropic/claude-y", "prompt_version": "1.0.0", "cost_eur": 0.003},
        {"stage": "stt", "model": "local/whisper", "prompt_version": None, "cost_eur": 0.0},
    ]
    t = trace.from_rows(rows, {"openai/gpt-x": "assessment", "anthropic/claude-y": "validator"})
    tiers = {x["tier"]: x for x in t["tiers"]}
    assert tiers["assessment"]["vendor"] == "openai" and tiers["validator"]["vendor"] == "anthropic"
    assert tiers["assessment"]["prompt_version"] == "1.0.0"
    assert t["cost_eur"] == pytest.approx(0.005)
    assert t["n_rows"] == 3 and t["violations"] == []
    assert rule_compliance.score(t) == pytest.approx(1.0)


def test_trace_capture_from_real_session() -> None:
    from app.services.assessment import assess_writing
    from app.services.tasks import ensure_learner

    learner = ensure_learner("calib-trace")
    sid = "eval-test-trace-item"
    assess_writing("Ein kurzer Text zur Probe.", task_text="Aufgabe", learner_id=learner, session_id=sid)
    t = trace.capture(sid)
    assert [x["tier"] for x in t["tiers"]] == ["assessment"]
    from app.core.prompts import load_prompt

    assert t["tiers"][0]["prompt_version"] == load_prompt("writing_feedback").version
    assert t["n_rows"] >= 1 and t["cost_eur"] >= 0.0


def test_run_evals_writing_suite_reports_evaluators(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evals import run_evals

    rep = run_evals.suite_writing("test-" + tmp_path.name[-6:])
    assert rep["n"] == 3
    assert 0.0 <= rep["schema_validity"] <= 1.0
    assert 0.0 <= rep["accuracy"] <= 1.0
    assert rep["cost_per_item_eur"] >= 0.0
    assert len(rep["items"]) == 3
    item = rep["items"][0]
    assert {"id", "schema_validity", "accuracy", "within_1", "routing", "cost_eur", "tiers"} <= set(item)
    assert item["tiers"] == ["assessment"]
    hooks = run_evals.suite_hooks("test-hooks")
    assert isinstance(hooks, dict) and hooks  # either {"skipped": …} or the hooks-vs-prompt comparison
    assert "skipped" in hooks or "run_id" in hooks
