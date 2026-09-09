"""Accuracy vs human labels: score = 1 − MAE/4, plus the within-±1 fraction (partial credit)."""

from __future__ import annotations

from typing import Any

SCORE_RANGE = 4.0


def evaluate(model_scores: dict[str, Any], human_scores: dict[str, Any]) -> dict[str, Any]:
    pairs = [(int(model_scores[c]), int(h)) for c, h in human_scores.items() if c in model_scores]
    if not pairs:
        return {"score": 0.0, "mae": None, "within_1": None, "n": 0}
    errors = [abs(m - h) for m, h in pairs]
    mae = sum(errors) / len(errors)
    return {
        "score": max(0.0, 1.0 - mae / SCORE_RANGE),
        "mae": mae,
        "within_1": sum(e <= 1 for e in errors) / len(errors),
        "n": len(pairs),
    }


def score(model_scores: dict[str, Any], human_scores: dict[str, Any]) -> float:
    return float(evaluate(model_scores, human_scores)["score"])
