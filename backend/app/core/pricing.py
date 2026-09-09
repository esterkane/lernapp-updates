"""Versioned price table loader (ADR-0006). Prices never live in code."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from app.core.paths import repo_root

log = logging.getLogger(__name__)

Unit = Literal[
    "tokens_in",
    "tokens_out",
    "tokens_in_cached",
    "tokens_reasoning",
    "audio_seconds",
    "audio_out_seconds",
    "chars",
    "requests",
]
Stage = Literal["stt", "llm", "tts", "pron", "embed"]


class PriceEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: str
    verified_on: str | None = None
    note: str | None = None
    input: float | None = None  # USD per 1M tokens
    output: float | None = None  # USD per 1M tokens
    cached_input: float | None = None
    per_minute: float | None = None  # USD per audio minute
    per_1m_chars: float | None = None
    per_1m_audio_tokens: float | None = None
    approx_per_minute: float | None = None
    eur_month: float | None = None
    # cost-tracking model (ADR-0019): free-tier eligibility and price history are data, never code
    free_tier: bool = False  # True → a learner on the provider's free tier is expected to pay 0 for this model
    valid_from: str | None = None  # ISO date this entry's prices apply from (None = since the file's version)
    history: list[dict[str, Any]] = []  # older prices: [{valid_from, valid_to, input, output, …}]


UNIT_FIELDS: dict[str, str] = {
    "tokens_in": "input",
    "tokens_in_cached": "cached_input",
    "tokens_out": "output",
    "tokens_reasoning": "output",  # billed as output unless a provider prices it separately
    "audio_seconds": "per_minute",
    "audio_out_seconds": "per_minute_out",
    "chars": "per_1m_chars",
}


class PriceQuote(BaseModel):
    """Result of pricing one usage event (list price vs. expected cost)."""

    cost_status: Literal["free", "estimated", "confirmed", "unknown"]
    pricing_tier: Literal["free", "paid", "unknown"]
    rule_id: str | None
    pricing_version: str
    list_usd: float | None
    expected_usd: float | None
    unit_prices: dict[str, float | None] = {}
    missing_units: list[str] = []
    note: str | None = None


class PricingTable(BaseModel):
    version: str
    fx_usd_eur_default: float
    llm: dict[str, PriceEntry]
    stt: dict[str, PriceEntry]
    tts: dict[str, PriceEntry]
    pronunciation: dict[str, PriceEntry]
    embedding: dict[str, PriceEntry]
    hosting: dict[str, PriceEntry] = {}
    tts_chars_per_minute_assumption: float = 825.0
    content_hash: str = ""

    def section(self, stage: Stage) -> dict[str, PriceEntry]:
        return {
            "llm": self.llm,
            "stt": self.stt,
            "tts": self.tts,
            "pron": self.pronunciation,
            "embed": self.embedding,
        }[stage]

    def entry_at(self, stage: Stage, model: str, at: datetime | None = None) -> tuple[PriceEntry | None, str | None]:
        """Entry valid at ``at`` (default now) and its rule id; older prices come from ``history``."""
        entry = self.section(stage).get(model)
        if entry is None:
            return None, None
        when = (at or datetime.now(UTC)).date().isoformat()
        rule = f"{self.version}:{stage}:{model}"
        if entry.valid_from and when < entry.valid_from:
            for h in entry.history:
                vf, vt = str(h.get("valid_from", "")), h.get("valid_to")
                if vf <= when and (vt is None or when < str(vt)):
                    merged = PriceEntry.model_validate(
                        {
                            "source": entry.source,
                            **{k: v for k, v in h.items() if k != "valid_to"},
                        }
                    )
                    return merged, f"{rule}:hist:{vf}"
            return None, None
        return entry, rule

    def unit_price_usd(self, stage: Stage, model: str, unit: Unit, at: datetime | None = None) -> float | None:
        """USD price for ONE unit (one token, one audio second, one character). None = unpriced."""
        entry, _ = self.entry_at(stage, model, at)
        if entry is None:
            return None
        if unit == "tokens_in":
            return entry.input / 1e6 if entry.input is not None else None
        if unit == "tokens_out":
            return entry.output / 1e6 if entry.output is not None else None
        if unit == "tokens_in_cached":
            base = entry.cached_input if entry.cached_input is not None else entry.input
            return base / 1e6 if base is not None else None
        if unit == "audio_seconds":
            if entry.per_minute is not None:
                return entry.per_minute / 60.0
            if entry.approx_per_minute is not None:
                return entry.approx_per_minute / 60.0
            return None
        if unit == "chars":
            if entry.per_1m_chars is not None:
                return entry.per_1m_chars / 1e6
            if entry.approx_per_minute is not None:
                # OpenAI mini-tts is priced per audio token; approximate via chars/minute assumption.
                return entry.approx_per_minute / self.tts_chars_per_minute_assumption
            return None
        if unit == "tokens_reasoning":
            return entry.output / 1e6 if entry.output is not None else None
        if unit == "audio_out_seconds":
            extra = getattr(entry, "per_minute_out", None)
            return float(extra) / 60.0 if extra is not None else None
        return None

    def quote(
        self,
        stage: Stage,
        model: str,
        units: dict[str, float],
        *,
        pricing_tier: str = "unknown",
        at: datetime | None = None,
        local: bool = False,
    ) -> PriceQuote:
        """Price a whole event. ``units`` = {unit: quantity}. Never raises on unknown models."""
        tier: Literal["free", "paid", "unknown"]
        if pricing_tier == "free":
            tier = "free"
        elif pricing_tier == "paid":
            tier = "paid"
        else:
            tier = "unknown"
        if local:
            return PriceQuote(
                cost_status="free",
                pricing_tier="free",
                rule_id=f"{self.version}:{stage}:{model}",
                pricing_version=self.version,
                list_usd=0.0,
                expected_usd=0.0,
                note="local stage",
            )
        entry, rule = self.entry_at(stage, model, at)
        if entry is None:
            return PriceQuote(
                cost_status="unknown",
                pricing_tier=tier,
                rule_id=None,
                pricing_version=self.version,
                list_usd=None,
                expected_usd=None,
                missing_units=sorted(units),
                note="model not in pricing.yaml",
            )
        if not units:
            return PriceQuote(
                cost_status="free" if tier == "free" and entry.free_tier else "unknown",
                pricing_tier=tier,
                rule_id=rule,
                pricing_version=self.version,
                list_usd=None,
                expected_usd=0.0 if tier == "free" and entry.free_tier else None,
                note="provider usage unavailable",
            )
        prices: dict[str, float | None] = {}
        missing: list[str] = []
        total = 0.0
        for unit, qty in units.items():
            if not qty:
                continue
            p = self.unit_price_usd(stage, model, unit, at)  # type: ignore[arg-type]
            prices[unit] = p
            if p is None:
                missing.append(unit)
            else:
                total += p * qty
        if missing:
            return PriceQuote(
                cost_status="unknown",
                pricing_tier=tier,
                rule_id=rule,
                pricing_version=self.version,
                list_usd=total if total else None,
                expected_usd=None,
                unit_prices=prices,
                missing_units=missing,
                note="no price for unit(s) " + ", ".join(missing),
            )
        if tier == "free" and entry.free_tier:
            return PriceQuote(
                cost_status="free",
                pricing_tier="free",
                rule_id=rule,
                pricing_version=self.version,
                list_usd=total,
                expected_usd=0.0,
                unit_prices=prices,
                note="free tier (list price shown for reference)",
            )
        if tier == "free" and not entry.free_tier:
            return PriceQuote(
                cost_status="estimated",
                pricing_tier="free",
                rule_id=rule,
                pricing_version=self.version,
                list_usd=total,
                expected_usd=total,
                unit_prices=prices,
                note="model not eligible for the free tier in pricing.yaml → list price expected",
            )
        return PriceQuote(
            cost_status="estimated",
            pricing_tier=tier,
            rule_id=rule,
            pricing_version=self.version,
            list_usd=total,
            expected_usd=total,
            unit_prices=prices,
        )


def pricing_path() -> Path:
    return repo_root() / "config" / "pricing.yaml"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=4)
def _load(path_str: str, mtime: float) -> PricingTable:
    text = Path(path_str).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    tts = dict(raw.get("tts", {}))
    cpm = float(tts.pop("chars_per_minute_assumption", 825))
    table = PricingTable(
        version=str(raw["version"]),
        fx_usd_eur_default=float(raw.get("fx_usd_eur_default", 0.86)),
        llm=raw.get("llm", {}),
        stt=raw.get("stt", {}),
        tts=tts,
        pronunciation=raw.get("pronunciation", {}),
        embedding=raw.get("embedding", {}),
        hosting=raw.get("hosting", {}),
        tts_chars_per_minute_assumption=cpm,
        content_hash=_content_hash(text),
    )
    return table


def load_pricing() -> PricingTable:
    p = pricing_path()
    return _load(str(p), p.stat().st_mtime)


def all_model_keys() -> list[tuple[Stage, str]]:
    t = load_pricing()
    out: list[tuple[Stage, str]] = []
    for stage in ("llm", "stt", "tts", "pron", "embed"):
        out.extend((stage, k) for k in t.section(stage))  # type: ignore[arg-type]
    return out
