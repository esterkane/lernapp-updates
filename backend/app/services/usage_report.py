"""Usage/cost reporting on ``usage_events`` (cost-tracking model, ADR-0019; docs/api.md "Cost tracking v2").

Terminology (binding, see docs/api.md):

- **expected cost** — what the learner will most likely pay: the sum of ``expected_cost_eur`` over
  successful events with ``cost_status`` free|estimated|confirmed (0 on a free tier).
- **list cost** — provider list price for the same usage, whenever a price exists.
- **unknown** — a successful call whose model/unit has no price yet (``unknown_models`` names them).
- **failed** — ``outcome=error`` events: the provider may or may not have billed them; they are
  never counted as expected cost and never as "unpriced usage".

Learning time is ``Session.duration_seconds`` (only genuine learning time, see
``services.learning_time``); the cost per learning hour is shown only above
``settings.cost_rate_min_learning_seconds`` so a first 30-second test does not yield an absurd rate.

Everything here is per learner (``learner_id``); ``None`` aggregates all learners for the digest and
the notebook only — the HTTP API always passes a learner id.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, select

from app.core import credentials
from app.core.config import get_settings
from app.core.db import db_session
from app.core.pricing import load_pricing
from app.db.base import Learner, Session, UsageEvent

SERVICE_LABEL_DE: dict[str, str] = {
    "llm": "Sprachmodell",
    "stt": "Spracherkennung",
    "tts": "Stimme",
    "pronunciation": "Aussprache",
    "embedding": "Dokumentensuche",
    "evaluation": "Bewertung",
    "other": "Sonstiges",
}
PROVIDER_LABEL_DE: dict[str, str] = {
    **credentials.PROVIDER_LABEL_DE,
    "local": "Lokal (kostenlos)",
    "google": "Google Cloud",
    "azure": "Microsoft Azure",
    "deepgram": "Deepgram",
    "elevenlabs": "ElevenLabs",
}
FREE_TIER_LABEL_DE = "Kostenlose Stufe"
RATE_HINT_NOT_ENOUGH_DE = "Noch nicht genügend Daten"
WARN_LEVELS: tuple[int, ...] = (80, 95, 100)
MONTHS_DE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
PRICED_STATUSES = frozenset({"free", "estimated", "confirmed"})
BUDGET_CACHE_TTL_SECONDS = 30.0

WarningLevel = Literal["none", "80", "95", "100"]


class BudgetSettings(BaseModel):
    monthly_budget_eur: float | None = None
    stop_on_limit: bool = False
    warn_levels: list[int] = list(WARN_LEVELS)


class BudgetState(BaseModel):
    """The learner's monthly budget vs. the month's expected cost (what the ``budget_guard`` hook reads)."""

    monthly_budget_eur: float | None
    used_eur: float
    share: float | None
    warning_level: WarningLevel
    stop_on_limit: bool

    @property
    def limit_reached(self) -> bool:
        return (
            self.monthly_budget_eur is not None
            and self.monthly_budget_eur > 0
            and self.share is not None
            and self.share >= 1.0
        )


# ---------------------------------------------------------------- periods


def month_bounds(month: str | None = None, *, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """``(from, to, label_de)`` for ``YYYY-MM`` (default: the current UTC month). ``to`` is exclusive."""
    ref = now or datetime.now(UTC)
    if month:
        try:
            year, mon = (int(x) for x in month.split("-", 1))
            if not 1 <= mon <= 12:
                raise ValueError(month)
        except ValueError as exc:
            raise ValueError(f"Ungültiger Monat: {month!r} (erwartet JJJJ-MM)") from exc
    else:
        year, mon = ref.year, ref.month
    start = datetime(year, mon, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC) if mon == 12 else datetime(year, mon + 1, 1, tzinfo=UTC)
    return start, end, f"{MONTHS_DE[mon - 1]} {year}"


def _eur(v: float | None) -> float:
    return round(float(v or 0.0), 6)


def format_eur_de(v: float) -> str:
    """``1234.5`` → ``1.234,50`` (German number format for user-facing strings)."""
    whole, frac = f"{v:,.2f}".split(".")
    return whole.replace(",", ".") + "," + frac


# ---------------------------------------------------------------- events


def event_dict(e: UsageEvent) -> dict[str, Any]:
    """The ``UsageEvent`` wire shape of docs/api.md: numbers, ids and sanitized usage — never learner text or keys."""
    return {
        "id": e.id,
        "ts": e.ts.isoformat(),
        "session_id": e.session_id,
        "request_id": e.request_id,
        "provider": e.provider,
        "service": e.service,
        "stage": e.stage,
        "model": e.model,
        "tier_name": e.tier_name,
        "operation": e.operation,
        "prompt_version": e.prompt_version,
        "outcome": e.outcome,
        "input_tokens": e.input_tokens,
        "cached_input_tokens": e.cached_input_tokens,
        "output_tokens": e.output_tokens,
        "reasoning_tokens": e.reasoning_tokens,
        "total_tokens": e.total_tokens,
        "audio_input_seconds": e.audio_input_seconds,
        "audio_output_seconds": e.audio_output_seconds,
        "characters": e.characters,
        "local": e.local,
        "pricing_tier": e.pricing_tier,
        "cost_status": e.cost_status,
        "expected_cost_eur": e.expected_cost_eur,
        "list_cost_eur": e.list_cost_eur,
        "pricing_rule_id": e.pricing_rule_id,
        "pricing_version": e.pricing_version,
        "fx_rate": e.fx_rate,
        "raw_usage": dict(e.raw_usage or {}),
        "meta": dict(e.meta or {}),
    }


def _load_events(
    learner_id: str | None,
    start: datetime,
    end: datetime,
    *,
    session_id: str | None = None,
    provider: str | None = None,
    service: str | None = None,
    cost_status: str | None = None,
    outcome: str | None = None,
) -> list[UsageEvent]:
    with db_session() as db:
        q = select(UsageEvent).where(UsageEvent.ts >= start, UsageEvent.ts < end)
        if learner_id is not None:
            q = q.where(UsageEvent.learner_id == learner_id)
        if session_id:
            q = q.where(UsageEvent.session_id == session_id)
        if provider:
            q = q.where(UsageEvent.provider == provider)
        if service:
            q = q.where(UsageEvent.service == service)
        if cost_status:
            q = q.where(UsageEvent.cost_status == cost_status)
        if outcome:
            q = q.where(UsageEvent.outcome == outcome)
        return list(db.execute(q.order_by(UsageEvent.ts.desc(), UsageEvent.id.desc())).scalars())


def list_events(
    learner_id: str,
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
    session_id: str | None = None,
    provider: str | None = None,
    service: str | None = None,
    cost_status: str | None = None,
    outcome: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """``GET /usage/events``: newest first, per learner, paginated (default window: last 30 days)."""
    now = datetime.now(UTC)
    start = from_ or now - timedelta(days=30)
    end = to or now + timedelta(minutes=1)
    rows = _load_events(
        learner_id,
        start,
        end,
        session_id=session_id,
        provider=provider,
        service=service,
        cost_status=cost_status,
        outcome=outcome,
    )
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return {"items": [event_dict(e) for e in rows[offset : offset + limit]], "total": len(rows)}


# ---------------------------------------------------------------- learning time / budget


def learning_seconds(learner_id: str | None, start: datetime, end: datetime) -> float:
    with db_session() as db:
        q = select(func.sum(Session.duration_seconds)).where(Session.started_at >= start, Session.started_at < end)
        if learner_id is not None:
            q = q.where(Session.learner_id == learner_id)
        return float(db.execute(q).scalar_one() or 0.0)


def expected_cost_eur(learner_id: str | None, start: datetime, end: datetime) -> float:
    """Sum of expected cost (successful, priced events) in the window — one SQL aggregate, no rows loaded."""
    with db_session() as db:
        q = select(func.sum(UsageEvent.expected_cost_eur)).where(
            UsageEvent.ts >= start,
            UsageEvent.ts < end,
            UsageEvent.outcome == "ok",
            UsageEvent.cost_status.in_(tuple(PRICED_STATUSES)),
        )
        if learner_id is not None:
            q = q.where(UsageEvent.learner_id == learner_id)
        return _eur(db.execute(q).scalar_one())


def get_budget_settings(learner_id: str) -> BudgetSettings:
    with db_session() as db:
        learner = db.get(Learner, learner_id)
        if learner is None:
            raise KeyError(learner_id)
        profile = dict(learner.profile or {})
    raw_budget = profile.get("monthly_budget_eur")
    budget = float(raw_budget) if isinstance(raw_budget, int | float) and not isinstance(raw_budget, bool) else None
    return BudgetSettings(monthly_budget_eur=budget, stop_on_limit=bool(profile.get("stop_on_limit", False)))


def set_budget_settings(learner_id: str, monthly_budget_eur: float | None, stop_on_limit: bool) -> BudgetSettings:
    """``PUT /usage/settings``: stored in ``Learner.profile`` (JSONB); the guard's cache is invalidated."""
    if monthly_budget_eur is not None and (monthly_budget_eur < 0 or monthly_budget_eur != monthly_budget_eur):
        raise ValueError("Das Monatslimit muss 0 oder größer sein.")
    with db_session() as db:
        learner = db.get(Learner, learner_id)
        if learner is None:
            raise KeyError(learner_id)
        profile = dict(learner.profile or {})
        if monthly_budget_eur is None:
            profile.pop("monthly_budget_eur", None)
        else:
            profile["monthly_budget_eur"] = float(monthly_budget_eur)
        profile["stop_on_limit"] = bool(stop_on_limit)
        learner.profile = profile  # new dict → JSONB change is detected
    invalidate_budget_cache(learner_id)
    return get_budget_settings(learner_id)


def _warning_level(share: float | None) -> WarningLevel:
    if share is None:
        return "none"
    pct = share * 100.0
    level: WarningLevel = "none"
    for w in WARN_LEVELS:
        if pct >= w:
            level = "100" if w == 100 else ("95" if w == 95 else "80")
    return level


def budget_block(settings: BudgetSettings, used_eur: float) -> BudgetState:
    budget = settings.monthly_budget_eur
    share = (used_eur / budget) if budget else None
    return BudgetState(
        monthly_budget_eur=budget,
        used_eur=_eur(used_eur),
        share=None if share is None else round(share, 4),
        warning_level=_warning_level(share),
        stop_on_limit=settings.stop_on_limit,
    )


_budget_cache: dict[str, tuple[float, BudgetState]] = {}
_budget_lock = threading.Lock()


def budget_state(
    learner_id: str, *, now: datetime | None = None, max_age: float = BUDGET_CACHE_TTL_SECONDS
) -> BudgetState:
    """Current month's budget state, cached ``max_age`` seconds per learner (one DB round-trip per 30 s, not per call)."""
    with _budget_lock:
        hit = _budget_cache.get(learner_id)
        if hit is not None and time.monotonic() - hit[0] < max_age:
            return hit[1]
    try:
        settings = get_budget_settings(learner_id)
    except KeyError:
        settings = BudgetSettings()
    start, end, _ = month_bounds(now=now)
    state = budget_block(settings, expected_cost_eur(learner_id, start, end))
    with _budget_lock:
        _budget_cache[learner_id] = (time.monotonic(), state)
    return state


def invalidate_budget_cache(learner_id: str | None = None) -> None:
    with _budget_lock:
        if learner_id is None:
            _budget_cache.clear()
        else:
            _budget_cache.pop(learner_id, None)


# ---------------------------------------------------------------- summary


def _empty_group() -> dict[str, Any]:
    return {
        "requests": 0,
        "expected_eur": 0.0,
        "list_eur": 0.0,
        "unknown": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "audio_seconds": 0.0,
        "characters": 0,
    }


def _add(group: dict[str, Any], e: UsageEvent) -> None:
    group["requests"] += 1
    if e.outcome != "ok":
        return
    if e.cost_status in PRICED_STATUSES:
        group["expected_eur"] += float(e.expected_cost_eur or 0.0)
    if e.cost_status == "unknown":
        group["unknown"] += 1
    group["list_eur"] += float(e.list_cost_eur or 0.0)
    group["input_tokens"] += int(e.input_tokens or 0)
    group["output_tokens"] += int(e.output_tokens or 0)
    group["audio_seconds"] += float(e.audio_input_seconds or 0.0)
    group["characters"] += int(e.characters or 0)


def _status_note(total: int, priced: int, unknown: int, failed: int) -> str:
    if total == 0:
        return "Noch keine Anfragen in diesem Zeitraum."
    parts = [f"{priced} von {total} Anfragen vollständig berechnet."]
    if unknown:
        parts.append(
            "1 Anfrage hat noch keine Preisinformation."
            if unknown == 1
            else f"{unknown} Anfragen haben noch keine Preisinformation."
        )
    if failed:
        parts.append(
            "1 Anfrage ist fehlgeschlagen und wird nicht als Kosten gezählt."
            if failed == 1
            else f"{failed} Anfragen sind fehlgeschlagen und werden nicht als Kosten gezählt."
        )
    return " ".join(parts)


def summary(
    learner_id: str | None,
    month: str | None = None,
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``GET /usage/summary`` payload (docs/api.md "Cost tracking v2"). ``from_``/``to`` override the month."""
    if from_ is not None and to is not None:
        start, end = from_, to
        label = f"{start.date().isoformat()} – {end.date().isoformat()}"
    else:
        start, end, label = month_bounds(month, now=now)
    s = get_settings()
    events = _load_events(learner_id, start, end)

    n_total = len(events)
    n_failed = sum(1 for e in events if e.outcome != "ok")
    ok = [e for e in events if e.outcome == "ok"]
    n_free = sum(1 for e in ok if e.cost_status == "free")
    n_priced = sum(1 for e in ok if e.cost_status in PRICED_STATUSES)
    n_unknown = sum(1 for e in ok if e.cost_status == "unknown")
    n_usage_estimated = sum(1 for e in ok if (e.meta or {}).get("usage_estimated"))
    expected = sum(float(e.expected_cost_eur or 0.0) for e in ok if e.cost_status in PRICED_STATUSES)
    list_cost = sum(float(e.list_cost_eur or 0.0) for e in ok)

    by_provider: dict[str, dict[str, Any]] = defaultdict(_empty_group)
    by_service: dict[str, dict[str, Any]] = defaultdict(_empty_group)
    by_model: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(_empty_group)
    unknown_models: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in events:
        _add(by_provider[e.provider], e)
        _add(by_service[e.service], e)
        _add(by_model[(e.provider, e.service, e.model)], e)
        if e.outcome == "ok" and e.cost_status == "unknown":
            key = (e.provider, e.model, e.service)
            um = unknown_models.setdefault(key, {"requests": 0, "missing_units": set()})
            um["requests"] += 1
            um["missing_units"].update(str(u) for u in (e.meta or {}).get("missing_units") or [])

    providers: list[dict[str, Any]] = []
    for provider, g in sorted(by_provider.items(), key=lambda kv: (-kv[1]["expected_eur"], kv[0])):
        tier = "free" if provider == "local" else credentials.pricing_tier_for(learner_id, provider)
        providers.append(
            {
                "provider": provider,
                "label_de": PROVIDER_LABEL_DE.get(provider, provider),
                "expected_eur": _eur(g["expected_eur"]),
                "list_eur": _eur(g["list_eur"]),
                "pricing_tier": tier,
                "free_tier_label_de": (FREE_TIER_LABEL_DE if tier == "free" and provider != "local" else None),
                "requests": g["requests"],
                "unknown": g["unknown"],
            }
        )
    services = [
        {
            "service": service,
            "label_de": SERVICE_LABEL_DE.get(service, service),
            "expected_eur": _eur(g["expected_eur"]),
            "list_eur": _eur(g["list_eur"]),
            "requests": g["requests"],
        }
        for service, g in sorted(by_service.items(), key=lambda kv: (-kv[1]["expected_eur"], kv[0]))
    ]
    models = [
        {
            "provider": provider,
            "service": service,
            "model": model,
            "requests": g["requests"],
            "input_tokens": g["input_tokens"],
            "output_tokens": g["output_tokens"],
            "audio_seconds": round(g["audio_seconds"], 3),
            "characters": g["characters"],
            "expected_eur": _eur(g["expected_eur"]),
            "list_eur": _eur(g["list_eur"]),
            "unknown": g["unknown"],
        }
        for (provider, service, model), g in sorted(
            by_model.items(), key=lambda kv: (-kv[1]["expected_eur"], -kv[1]["requests"], kv[0])
        )
    ]
    unknown_list = [
        {
            "provider": provider,
            "model": model,
            "service": service,
            "requests": um["requests"],
            "missing_units": sorted(um["missing_units"]),
        }
        for (provider, model, service), um in sorted(unknown_models.items())
    ]

    secs = learning_seconds(learner_id, start, end)
    rate_available = secs >= float(s.cost_rate_min_learning_seconds)
    rate = round(expected / (secs / 3600.0), 6) if rate_available and secs > 0 else None
    try:
        budget_settings = get_budget_settings(learner_id) if learner_id is not None else BudgetSettings()
    except KeyError:  # unknown learner: no budget configured
        budget_settings = BudgetSettings()
    budget = budget_block(budget_settings, expected)

    return {
        "learner_id": learner_id,
        "period": {"from": start.isoformat(), "to": end.isoformat(), "label_de": label},
        "expected_cost_eur": _eur(expected),
        "list_cost_eur": _eur(list_cost),
        "learning_seconds": round(secs, 3),
        "cost_per_learning_hour_eur": rate,
        "rate_available": rate_available,
        "rate_hint_de": None if rate_available else RATE_HINT_NOT_ENOUGH_DE,
        "budget": budget.model_dump(mode="json"),
        "requests": {
            "total": n_total,
            "priced": n_priced,
            "free": n_free,
            "unknown": n_unknown,
            "failed": n_failed,
            "usage_estimated": n_usage_estimated,
        },
        "providers": providers,
        "services": services,
        "models": models,
        "unknown_models": unknown_list,
        "status_note_de": _status_note(n_total, n_priced, n_unknown, n_failed),
        "pricing_version": load_pricing().version,
        "fx_rate": s.fx_usd_eur,
    }


# ---------------------------------------------------------------- session


def session_report(session_id: str, learner_id: str) -> dict[str, Any] | None:
    """``GET /usage/session/{id}``: the session's events (learner-scoped) with provider/service breakdowns.

    Returns ``None`` when the session is unknown for this learner. Task-generation "sessions" have no
    ``sessions`` row: they are reported from their events alone (``kind`` null).
    """
    with db_session() as db:
        s = db.get(Session, session_id)
        if s is not None and s.learner_id != learner_id:
            return None
        head = (
            {"kind": s.kind, "started_at": s.started_at.isoformat(), "duration_seconds": float(s.duration_seconds or 0)}
            if s is not None
            else None
        )
        q = (
            select(UsageEvent)
            .where(UsageEvent.session_id == session_id, UsageEvent.learner_id == learner_id)
            .order_by(UsageEvent.ts.asc(), UsageEvent.id.asc())
        )
        events = list(db.execute(q).scalars())
    if head is None:
        if not events:
            return None
        head = {"kind": None, "started_at": events[0].ts.isoformat(), "duration_seconds": 0.0}
    ok = [e for e in events if e.outcome == "ok"]
    by_provider: dict[str, float] = defaultdict(float)
    by_service: dict[str, float] = defaultdict(float)
    for e in ok:
        if e.cost_status in PRICED_STATUSES:
            by_provider[e.provider] += float(e.expected_cost_eur or 0.0)
            by_service[e.service] += float(e.expected_cost_eur or 0.0)
    return {
        "session_id": session_id,
        **head,
        "expected_cost_eur": _eur(sum(by_provider.values())),
        "list_cost_eur": _eur(sum(float(e.list_cost_eur or 0.0) for e in ok)),
        "requests": {
            "total": len(events),
            "failed": len(events) - len(ok),
            "unknown": sum(1 for e in ok if e.cost_status == "unknown"),
        },
        "by_provider": [
            {"provider": p, "label_de": PROVIDER_LABEL_DE.get(p, p), "expected_eur": _eur(v)}
            for p, v in sorted(by_provider.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "by_service": [
            {"service": sv, "label_de": SERVICE_LABEL_DE.get(sv, sv), "expected_eur": _eur(v)}
            for sv, v in sorted(by_service.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "events": [event_dict(e) for e in events],
    }
