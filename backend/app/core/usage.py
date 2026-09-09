"""Usage collector (cost-tracking model, ADR-0019).

One provider call → one ``usage_events`` row (normalized usage + list cost + expected cost + status)
→ N ``cost_ledger`` rows (the priced units, unchanged ADR-0006 semantics; ``cost_usd`` there is the
LIST price). Every adapter funnels through ``record_usage``; idempotency keys stop double accounting.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import db_session
from app.core.pricing import Stage, load_pricing
from app.db.base import CostLedger, UsageEvent

log = logging.getLogger(__name__)

Service = Literal["llm", "stt", "tts", "pronunciation", "embedding", "evaluation", "other"]
STAGE_TO_SERVICE: dict[str, Service] = {
    "llm": "llm",
    "stt": "stt",
    "tts": "tts",
    "pron": "pronunciation",
    "embed": "embedding",
}


class UsageDraft(BaseModel):
    provider: str
    stage: Stage
    model: str
    learner_id: str | None
    session_id: str | None
    service: Service | None = None
    tier_name: str | None = None
    prompt_version: str | None = None
    operation: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    outcome: Literal["ok", "error"] = "ok"
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    audio_input_seconds: float | None = None
    audio_output_seconds: float | None = None
    characters: int | None = None
    local: bool = False
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    ts: datetime | None = None

    def units(self) -> dict[str, float]:
        u: dict[str, float] = {}
        if self.input_tokens is not None:
            u["tokens_in"] = float(max(self.input_tokens - (self.cached_input_tokens or 0), 0))
        if self.cached_input_tokens is not None:
            u["tokens_in_cached"] = float(self.cached_input_tokens)
        if self.output_tokens is not None:
            u["tokens_out"] = float(self.output_tokens)
        if self.reasoning_tokens is not None and self.meta.get("reasoning_excluded_from_output") is True:
            # Only explicitly normalized, additional reasoning units are billed separately.
            u["tokens_reasoning"] = float(self.reasoning_tokens)
        if self.audio_input_seconds is not None:
            u["audio_seconds"] = float(self.audio_input_seconds)
        if self.audio_output_seconds is not None:
            u["audio_out_seconds"] = float(self.audio_output_seconds)
        if self.characters is not None:
            u["chars"] = float(self.characters)
        return u


class RecordedUsage(BaseModel):
    event_id: str
    duplicate: bool = False
    cost_status: str
    pricing_tier: str
    expected_eur: float | None
    list_eur: float | None
    ledger_ids: list[int] = []


def _idempotency_key(d: UsageDraft, ts: datetime) -> str:
    if d.idempotency_key:
        return d.idempotency_key[:128]
    if d.request_id:
        return "request:" + hashlib.sha256(json.dumps([d.learner_id, d.provider, d.request_id]).encode()).hexdigest()
    payload = json.dumps(
        [
            d.provider,
            d.stage,
            d.model,
            d.session_id,
            d.outcome,
            ts.isoformat(),  # full timestamp: retries are legitimate calls; real dedupe comes from request_id
            sorted(d.units().items()),
            d.tier_name,
        ],
        sort_keys=True,
    )
    return "h:" + hashlib.sha256(payload.encode()).hexdigest()[:60]


def _sanitize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep numeric usage and known billing-type labels; discard arbitrary string values."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        kl = str(k).lower()
        if any(t in kl for t in ("key", "token_str", "authorization", "secret", "password")):
            continue
        if (
            k == "type"
            and isinstance(v, str)
            and v in {"duration", "tokens"}
            or isinstance(v, bool | int | float)
            or v is None
        ):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = _sanitize_raw(v)
    return out


def record_usage(draft: UsageDraft) -> RecordedUsage:
    """Persist one usage event and its priced ledger units. Idempotent on ``idempotency_key``."""
    from app.core import credentials

    s = get_settings()
    table = load_pricing()
    ts = draft.ts or datetime.now(UTC)
    key = _idempotency_key(draft, ts)
    with db_session() as db:
        existing = db.execute(select(UsageEvent).where(UsageEvent.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            log.info("duplicate usage event suppressed (%s %s %s)", draft.provider, draft.model, key[:24])
            return RecordedUsage(
                event_id=existing.id,
                duplicate=True,
                cost_status=existing.cost_status,
                pricing_tier=existing.pricing_tier,
                expected_eur=existing.expected_cost_eur,
                list_eur=existing.list_cost_eur,
            )
    tier = "free" if draft.local else credentials.pricing_tier_for(draft.learner_id, draft.provider)
    units = draft.units()
    meta = dict(draft.meta)
    if "error" in meta and meta["error"] is not None:
        # never persist a key that a provider/SDK echoed back in its error text
        meta["error"] = credentials.redact(str(meta["error"]), draft.learner_id)[:300]
    if draft.outcome == "error":
        # A failed call: we do not know whether the provider billed the attempted units. Keep the list
        # price as a reference (when units were attempted) but never count it as expected cost.
        quote = table.quote(draft.stage, draft.model, units, pricing_tier=tier, at=ts, local=draft.local)
        quote.cost_status, quote.expected_usd = "unknown", None
        quote.note = "call failed — billing unknown" + ("" if units else ", no usage reported")
    elif meta.get("free_call") and not units:
        # unbilled endpoint (e.g. a models listing used as a connection test): known and free
        quote = table.quote(draft.stage, draft.model, {}, pricing_tier=tier, at=ts, local=draft.local)
        quote.cost_status, quote.list_usd, quote.expected_usd = "free", 0.0, 0.0
        quote.missing_units, quote.note = [], "unbilled provider endpoint"
    else:
        quote = table.quote(draft.stage, draft.model, units, pricing_tier=tier, at=ts, local=draft.local)
    if (
        draft.stage == "llm"
        and draft.outcome == "ok"
        and not draft.local
        and not meta.get("free_call")
        and (draft.input_tokens is None or draft.output_tokens is None)
        and quote.cost_status != "free"
    ):
        quote.cost_status = "unknown"
        quote.expected_usd = None
        quote.note = "incomplete provider usage"
    fx = s.fx_usd_eur
    ev = UsageEvent(
        ts=ts,
        learner_id=draft.learner_id,
        session_id=draft.session_id,
        request_id=draft.request_id,
        idempotency_key=key,
        provider=draft.provider,
        service=draft.service or STAGE_TO_SERVICE.get(draft.stage, "other"),
        stage=draft.stage,
        tier_name=draft.tier_name,
        model=draft.model,
        operation=draft.operation,
        prompt_version=draft.prompt_version,
        outcome=draft.outcome,
        input_tokens=draft.input_tokens,
        cached_input_tokens=draft.cached_input_tokens,
        output_tokens=draft.output_tokens,
        reasoning_tokens=draft.reasoning_tokens,
        total_tokens=draft.total_tokens,
        audio_input_seconds=draft.audio_input_seconds,
        audio_output_seconds=draft.audio_output_seconds,
        characters=draft.characters,
        pricing_tier=quote.pricing_tier,
        cost_status=quote.cost_status,
        list_cost_usd=quote.list_usd,
        list_cost_eur=None if quote.list_usd is None else quote.list_usd * fx,
        expected_cost_usd=quote.expected_usd,
        expected_cost_eur=None if quote.expected_usd is None else quote.expected_usd * fx,
        pricing_rule_id=quote.rule_id,
        pricing_version=quote.pricing_version,
        fx_rate=fx,
        fx_source=s.fx_source,
        fx_at=ts,
        local=draft.local,
        raw_usage=_sanitize_raw(draft.raw_usage),
        meta={
            **meta,
            **({"pricing_note": quote.note} if quote.note else {}),
            **({"missing_units": quote.missing_units} if quote.missing_units else {}),
        },
    )
    # Serialize duplicate submissions and commit the event and all units together.
    ledger_ids: list[int] = []
    with db_session() as db:
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        existing = db.execute(select(UsageEvent).where(UsageEvent.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            return RecordedUsage(
                event_id=existing.id,
                duplicate=True,
                cost_status=existing.cost_status,
                pricing_tier=existing.pricing_tier,
                expected_eur=existing.expected_cost_eur,
                list_eur=existing.list_cost_eur,
            )
        db.add(ev)
        db.flush()
        event_id = ev.id
        ledger_units = units or ({"requests": 1.0} if draft.outcome == "error" else {})
        for unit, qty in ledger_units.items():
            price = 0.0 if draft.local else quote.unit_prices.get(unit)
            cost = None if price is None else price * qty
            row = CostLedger(
                ts=ts,
                event_id=event_id,
                session_id=draft.session_id,
                learner_id=draft.learner_id,
                stage=draft.stage,
                provider=draft.provider,
                model=draft.model,
                prompt_version=draft.prompt_version,
                unit=unit,
                quantity=qty,
                unit_price_usd=price,
                cost_usd=cost,
                cost_eur=None if cost is None else cost * fx,
                pricing_version=quote.pricing_version,
                fx_rate=fx,
                local=draft.local,
                meta={**meta, "ok": draft.outcome == "ok"},
            )
            db.add(row)
            db.flush()
            ledger_ids.append(row.id)
    return RecordedUsage(
        event_id=event_id,
        cost_status=quote.cost_status,
        pricing_tier=quote.pricing_tier,
        expected_eur=ev.expected_cost_eur,
        list_eur=ev.list_cost_eur,
        ledger_ids=ledger_ids,
    )


def extract_llm_usage(resp: Any) -> dict[str, Any]:
    """Normalize a LiteLLM completion/embedding response's usage into UsageDraft fields (provider-reported only)."""
    u = getattr(resp, "usage", None)
    out: dict[str, Any] = {"request_id": getattr(resp, "id", None)}
    if u is None:
        return out
    out["input_tokens"] = _int(getattr(u, "prompt_tokens", None))
    out["output_tokens"] = _int(getattr(u, "completion_tokens", None))
    out["total_tokens"] = _int(getattr(u, "total_tokens", None))
    pd = getattr(u, "prompt_tokens_details", None)
    cd = getattr(u, "completion_tokens_details", None)
    out["cached_input_tokens"] = _int(getattr(pd, "cached_tokens", None)) if pd else None
    out["reasoning_tokens"] = _int(getattr(cd, "reasoning_tokens", None)) if cd else None
    raw: dict[str, Any] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        raw[name] = _int(getattr(u, name, None))
    if pd:
        raw["prompt_tokens_details"] = {
            k: _int(getattr(pd, k, None))
            for k in ("cached_tokens", "audio_tokens", "text_tokens", "cache_creation_tokens")
        }
        audio_in = _int(getattr(pd, "audio_tokens", None))
        if audio_in:
            raw["audio_input_tokens"] = audio_in
    if cd:
        raw["completion_tokens_details"] = {
            k: _int(getattr(cd, k, None)) for k in ("reasoning_tokens", "audio_tokens", "text_tokens")
        }
    out["raw_usage"] = {k: v for k, v in raw.items() if v not in (None, {})}
    return out


def _int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None
