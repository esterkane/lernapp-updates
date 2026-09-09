"""Cost ledger (ADR-0006, product rule 8): every external call writes rows here.

- LLM calls: ``record_llm_usage`` is invoked synchronously from the single choke point
  ``app.services.llm.complete`` right after LiteLLM returns (LiteLLM's own ``success_callback``
  runs in a background thread, which makes rows non-deterministic in tests; the effect is the
  same — no LLM call can bypass it because all calls go through ``complete``).
- STT/TTS/pron/embedding adapters use ``with meter(...) as m:`` and set ``m.quantity``; the row
  is written in ``finally`` even if the provider raised.
- Local stages write ``cost_usd=0, local=True`` so the cloud counterfactual can be computed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import db_session
from app.core.models import vendor_of
from app.core.pricing import Stage, Unit, load_pricing
from app.db.base import CostLedger, Session

log = logging.getLogger(__name__)

# Counterfactual variants (cost-ledger skill): local stages re-priced with cloud models.
COUNTERFACTUALS: dict[str, dict[str, str]] = {
    "all_cloud_openai": {"stt": "openai/gpt-transcribe", "tts": "openai/gpt-4o-mini-tts", "pron": "azure/pron-batch"},
    "cheapest_cloud": {"stt": "openai/gpt-4o-mini-transcribe", "tts": "google/wavenet-de", "pron": "azure/pron-batch"},
}


@dataclass
class LedgerRow:
    stage: Stage
    provider: str
    model: str
    unit: Unit
    quantity: float
    session_id: str | None = None
    learner_id: str | None = None
    prompt_version: str | None = None
    local: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    unit_price_usd: float | None = None
    cost_usd: float | None = None
    cost_eur: float | None = None
    pricing_version: str = ""
    fx_rate: float = 0.0
    id: int | None = None
    event_id: str | None = None


def price_row(row: LedgerRow) -> LedgerRow:
    table = load_pricing()
    s = get_settings()
    row.pricing_version = table.version
    row.fx_rate = s.fx_usd_eur
    if row.local:
        row.unit_price_usd = 0.0
        row.cost_usd = 0.0
        row.cost_eur = 0.0
        return row
    if row.unit == "requests":
        row.unit_price_usd = None
        row.cost_usd = None
        row.cost_eur = None
        return row
    price = table.unit_price_usd(row.stage, row.model, row.unit)
    if price is None:
        log.error(
            "UNPRICED ledger row: stage=%s model=%s unit=%s — add to config/pricing.yaml",
            row.stage,
            row.model,
            row.unit,
        )
        row.unit_price_usd = None
        row.cost_usd = None
        row.cost_eur = None
        return row
    row.unit_price_usd = price
    row.cost_usd = price * row.quantity
    row.cost_eur = row.cost_usd * row.fx_rate
    return row


def write_row(row: LedgerRow) -> LedgerRow:
    price_row(row)
    with db_session() as db:
        rec = CostLedger(
            session_id=row.session_id,
            learner_id=row.learner_id,
            stage=row.stage,
            provider=row.provider,
            model=row.model,
            prompt_version=row.prompt_version,
            unit=row.unit,
            quantity=float(row.quantity),
            unit_price_usd=row.unit_price_usd,
            cost_usd=row.cost_usd,
            cost_eur=row.cost_eur,
            fx_rate=row.fx_rate,
            pricing_version=row.pricing_version,
            local=row.local,
            meta=row.meta,
            event_id=row.event_id,
        )
        db.add(rec)
        db.flush()
        row.id = rec.id
    return row


# ``m.meta`` keys the meter lifts into the usage event instead of storing them as free-form meta.
USAGE_META_KEYS: frozenset[str] = frozenset(
    {"request_id", "raw_usage", "tier", "total_tokens", "input_tokens", "output_tokens", "operation", "service"}
)


class Meter:
    """Set ``quantity`` (billing unit) inside the block; ``meta["request_id"]`` / ``meta["raw_usage"]`` /
    ``meta["total_tokens"]`` / ``meta["operation"]`` become event fields (provider-reported usage)."""

    def __init__(self, row: LedgerRow) -> None:
        self.row = row
        self.quantity: float = 0.0
        self.meta: dict[str, Any] = dict(row.meta)
        self.written: LedgerRow | None = None
        self.event: Any = None


@contextmanager
def meter(
    stage: Stage,
    provider: str,
    model: str,
    unit: Unit,
    *,
    session_id: str | None = None,
    learner_id: str | None = None,
    prompt_version: str | None = None,
    local: bool = False,
    meta: dict[str, Any] | None = None,
) -> Iterator[Meter]:
    """Context manager: set ``m.quantity`` inside; row written in ``finally``."""
    m = Meter(
        LedgerRow(
            stage=stage,
            provider=provider,
            model=model,
            unit=unit,
            quantity=0.0,
            session_id=session_id,
            learner_id=learner_id,
            prompt_version=prompt_version,
            local=local,
            meta=meta or {},
        )
    )
    ok = False
    try:
        yield m
        ok = True
    finally:
        m.row.quantity = float(m.quantity)
        m.row.meta = {**m.meta, "ok": ok}
        try:
            from app.core.usage import UsageDraft, record_usage

            draft = UsageDraft(
                provider=provider,
                stage=stage,
                model=model,
                learner_id=learner_id,
                session_id=session_id,
                prompt_version=prompt_version,
                local=local,
                outcome="ok" if ok else "error",
                request_id=str(m.meta.get("request_id")) if m.meta.get("request_id") else None,
                raw_usage=dict(m.meta.get("raw_usage") or {}),
                meta={k: v for k, v in m.meta.items() if k not in USAGE_META_KEYS},
                tier_name=str(m.meta.get("tier")) if m.meta.get("tier") else None,
                operation=str(m.meta["operation"]) if m.meta.get("operation") else None,
                service=m.meta.get("service"),
                total_tokens=int(m.meta["total_tokens"]) if m.meta.get("total_tokens") is not None else None,
            )
            if unit == "audio_seconds":
                draft.audio_input_seconds = m.row.quantity
            elif unit == "audio_out_seconds":
                draft.audio_output_seconds = m.row.quantity
            elif unit == "chars":
                draft.characters = int(m.row.quantity)
            elif unit == "tokens_in":
                draft.input_tokens = None if m.meta.get("usage_unavailable") else int(m.row.quantity)
            elif unit == "tokens_out":
                draft.output_tokens = int(m.row.quantity)
            elif unit == "requests":
                draft.meta["requests"] = m.row.quantity
            rec = record_usage(draft)
            m.written = m.row
            m.written.event_id = rec.event_id
            m.event = rec
        except Exception:  # noqa: BLE001
            log.error("failed to record usage: stage=%s provider=%s", stage, provider)


def record_llm_usage(
    model: str,
    *,
    tokens_in: int | None,
    tokens_out: int | None,
    tokens_in_cached: int | None = None,
    session_id: str | None,
    learner_id: str | None,
    prompt_version: str | None,
    tier: str,
    local: bool = False,
    meta: dict[str, Any] | None = None,
    reasoning_tokens: int | None = None,
    total_tokens: int | None = None,
    request_id: str | None = None,
    raw_usage: dict[str, Any] | None = None,
    service: str | None = None,
) -> list[LedgerRow]:
    """One LLM call → one usage event (+ its tokens_in/tokens_out[/cached] ledger rows).

    ``service`` defaults by tier: assessment/validator → ``evaluation`` ("Bewertung"), else ``llm``.
    """
    from app.core.usage import UsageDraft, record_usage

    if service is None:
        service = "evaluation" if tier in ("assessment", "validator") else "llm"
    draft = UsageDraft(
        provider=vendor_of(model),
        stage="llm",
        model=model,
        learner_id=learner_id,
        session_id=session_id,
        service=service,  # type: ignore[arg-type]
        tier_name=tier,
        prompt_version=prompt_version,
        request_id=request_id,
        local=local,
        input_tokens=tokens_in,
        cached_input_tokens=tokens_in_cached or None,
        output_tokens=tokens_out,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        raw_usage=raw_usage or {},
        meta={"tier": tier, **(meta or {})},
    )
    rec = record_usage(draft)
    return [
        LedgerRow(
            stage="llm",
            provider=vendor_of(model),
            model=model,
            unit="requests",
            quantity=1.0,
            id=i,
            event_id=rec.event_id,
        )
        for i in rec.ledger_ids
    ]


# ---------------------------------------------------------------- reporting


def _range(from_: datetime | None, to: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return (from_ or now - timedelta(days=30), to or now + timedelta(minutes=1))


def summary(
    from_: datetime | None = None,
    to: datetime | None = None,
    group_by: str = "stage",
    learner_id: str | None = None,
) -> dict[str, Any]:
    start, end = _range(from_, to)
    if group_by not in ("stage", "model", "learner", "day", "session", "provider"):
        raise ValueError("group_by must be stage|model|learner|day|session|provider")
    col = {
        "stage": CostLedger.stage,
        "model": CostLedger.model,
        "learner": CostLedger.learner_id,
        "session": CostLedger.session_id,
        "provider": CostLedger.provider,
        "day": func.date_trunc("day", CostLedger.ts),
    }[group_by]
    with db_session() as db:
        q: Any = (
            select(
                col.label("key"),
                func.count().label("rows"),
                func.sum(CostLedger.quantity).label("quantity"),
                func.sum(CostLedger.cost_usd).label("cost_usd"),
                func.sum(CostLedger.cost_eur).label("cost_eur"),
                func.sum(
                    func.cast(CostLedger.unit_price_usd.is_(None) & ~CostLedger.local, type_=func.count().type)
                ).label("unpriced"),
            )
            .where(CostLedger.ts >= start, CostLedger.ts < end)
            .group_by(col)
            .order_by(col)
        )
        if learner_id:
            q = q.where(CostLedger.learner_id == learner_id)
        groups = [
            {
                "key": (r.key.date().isoformat() if group_by == "day" and r.key is not None else r.key),
                "rows": int(r.rows),
                "quantity": float(r.quantity or 0),
                "cost_usd": float(r.cost_usd or 0),
                "cost_eur": float(r.cost_eur or 0),
                "unpriced_rows": int(r.unpriced or 0),
            }
            for r in db.execute(q)
        ]
        total_q = select(func.sum(CostLedger.cost_usd), func.sum(CostLedger.cost_eur), func.count()).where(
            CostLedger.ts >= start, CostLedger.ts < end
        )
        unpriced_q = select(func.count()).where(
            CostLedger.ts >= start,
            CostLedger.ts < end,
            CostLedger.unit_price_usd.is_(None),
            CostLedger.local.is_(False),
        )
        if learner_id:
            total_q = total_q.where(CostLedger.learner_id == learner_id)
            unpriced_q = unpriced_q.where(CostLedger.learner_id == learner_id)
        tu, te, n = db.execute(total_q).one()
        unpriced = db.execute(unpriced_q).scalar_one()
        # learning time = sum of session durations in range
        dur_q = select(func.sum(Session.duration_seconds)).where(Session.started_at >= start, Session.started_at < end)
        if learner_id:
            dur_q = dur_q.where(Session.learner_id == learner_id)
        learning_seconds = float(db.execute(dur_q).scalar_one() or 0.0)
    total_eur = float(te or 0)
    per_minute = total_eur / (learning_seconds / 60.0) if learning_seconds > 0 else None
    return {
        "basis": "list_price",  # ledger rows carry provider LIST prices; expected cost lives in /usage/summary
        "from": start.isoformat(),
        "to": end.isoformat(),
        "group_by": group_by,
        "groups": groups,
        "total_usd": float(tu or 0),
        "total_eur": total_eur,
        "rows": int(n),
        "unpriced_rows": int(unpriced),
        "learning_seconds": learning_seconds,
        "eur_per_learning_minute": per_minute,
        "pricing_version": load_pricing().version,
        "fx_rate": get_settings().fx_usd_eur,
    }


def counterfactual(
    variant: str = "all_cloud_openai",
    from_: datetime | None = None,
    to: datetime | None = None,
    learner_id: str | None = None,
) -> dict[str, Any]:
    """Re-price *local* rows with cloud prices to answer "what would this cost all-cloud?"."""
    if variant not in COUNTERFACTUALS:
        raise ValueError(f"variant must be one of {list(COUNTERFACTUALS)}")
    mapping = COUNTERFACTUALS[variant]
    start, end = _range(from_, to)
    table = load_pricing()
    fx = get_settings().fx_usd_eur
    with db_session() as db:
        q: Any = select(CostLedger).where(CostLedger.ts >= start, CostLedger.ts < end)
        if learner_id:
            q = q.where(CostLedger.learner_id == learner_id)
        rows = list(db.execute(q).scalars())
    actual = 0.0
    hypothetical = 0.0
    by_stage: dict[str, dict[str, float]] = {}
    for r in rows:
        actual += r.cost_usd or 0.0
        st = by_stage.setdefault(r.stage, {"actual_usd": 0.0, "counterfactual_usd": 0.0})
        st["actual_usd"] += r.cost_usd or 0.0
        if r.local and r.stage in mapping:
            cloud_model = mapping[r.stage]
            unit: Unit = "audio_seconds" if r.stage in ("stt", "pron") else "chars"
            if r.stage == "tts" and r.unit != "chars":
                unit = r.unit  # type: ignore[assignment]
            price = table.unit_price_usd(r.stage, cloud_model, unit)  # type: ignore[arg-type]
            cost = (price or 0.0) * r.quantity
        else:
            cost = r.cost_usd or 0.0
        hypothetical += cost
        st["counterfactual_usd"] += cost
    return {
        "variant": variant,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "mapping": mapping,
        "actual_usd": actual,
        "actual_eur": actual * fx,
        "counterfactual_usd": hypothetical,
        "counterfactual_eur": hypothetical * fx,
        "saving_eur": (hypothetical - actual) * fx,
        "by_stage": by_stage,
        "rows": len(rows),
    }


def rows_for_session(session_id: str) -> list[dict[str, Any]]:
    with db_session() as db:
        rows = db.execute(
            select(CostLedger).where(CostLedger.session_id == session_id).order_by(CostLedger.id)
        ).scalars()
        return [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "stage": r.stage,
                "provider": r.provider,
                "model": r.model,
                "unit": r.unit,
                "quantity": r.quantity,
                "cost_usd": r.cost_usd,
                "cost_eur": r.cost_eur,
                "local": r.local,
                "prompt_version": r.prompt_version,
            }
            for r in rows
        ]


def session_cost_eur(session_id: str) -> float:
    with db_session() as db:
        v = db.execute(select(func.sum(CostLedger.cost_eur)).where(CostLedger.session_id == session_id)).scalar_one()
    return float(v or 0.0)
