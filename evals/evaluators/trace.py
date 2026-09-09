"""Trace capture per eval item: tiers called (model, vendor, prompt_version) and cost, from the ledger rows
of the item's session id (``eval-<run_id>-<item_id>``). Evaluators read the trace, not just the output."""

from __future__ import annotations

from typing import Any

TIERS = ("conversation", "assessment", "validator", "generator", "embedding")


def from_rows(rows: list[dict[str, Any]], tier_models: dict[str, str]) -> dict[str, Any]:
    """Pure helper: ledger rows + {model → tier} → trace dict."""
    tiers: list[dict[str, Any]] = []
    for r in rows:
        # the ledger writes one row per unit (tokens_in / tokens_in_cached / tokens_out); tokens_in marks the call
        if r.get("stage") != "llm" or r.get("unit") not in (None, "tokens_in"):
            continue
        model = str(r.get("model") or "")
        tiers.append(
            {
                "tier": tier_models.get(model, "unknown"),
                "model": model,
                "vendor": model.split("/")[0] if "/" in model else model or None,
                "prompt_version": r.get("prompt_version"),
            }
        )
    return {
        "tiers": tiers,
        "violations": [],
        "cost_eur": round(sum(float(r.get("cost_eur") or 0.0) for r in rows), 6),
        "n_rows": len(rows),
    }


def capture(session_id: str) -> dict[str, Any]:
    from app.core import ledger
    from app.core.models import resolve_tier

    tier_models: dict[str, str] = {}
    for t in TIERS:
        try:
            tier_models.setdefault(resolve_tier(t).model, t)
        except Exception:  # noqa: BLE001 - a tier missing from config must not break the eval
            continue
    return from_rows(ledger.rows_for_session(session_id), tier_models)
