"""Model tiering + provider routing (ADR-0003, ADR-0011). Loaded from config/models.yaml."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.paths import repo_root
from app.core.workspaces import current_workspace

log = logging.getLogger(__name__)
requested_validator_provider: ContextVar[str | None] = ContextVar("requested_validator_provider", default=None)

Tier = Literal["conversation", "assessment", "validator", "generator", "embedding", "eu_conversation"]


class ConfigError(RuntimeError):
    pass


class TierConfig(BaseModel):
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    repair_max_tokens: int | None = None
    dimensions: int | None = None
    fallbacks: list[str] = []


class SpeechConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    stt_backend: str = "faster_whisper"
    stt_model: str = "small"
    tts_backend: str = "none"
    tts_voice: str = "de-DE-Wavenet-B"
    pron_backend: str = "prosody_mvp"


class ProviderInfo(BaseModel):
    dpa_url: str = "TODO(verify)"
    eu_residency: str = "TODO(verify)"


class ModelsConfig(BaseModel):
    tiers: dict[str, TierConfig]
    eu_strict_overrides: dict[str, str] = {}
    eu_strict_verified_on: str | None = None
    speech: SpeechConfig = SpeechConfig()
    prompt_pins: dict[str, str] = {}
    providers: dict[str, ProviderInfo] = {}


def models_path() -> Path:
    return repo_root() / "config" / "models.yaml"


@lru_cache(maxsize=4)
def _load(path_str: str, mtime: float) -> ModelsConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))
    return ModelsConfig.model_validate(raw)


def load_models_config() -> ModelsConfig:
    p = models_path()
    return _load(str(p), p.stat().st_mtime)


def vendor_of(model: str) -> str:
    """Vendor prefix of a LiteLLM model string ("openai/gpt-x" → "openai"). Infers when absent."""
    if "/" in model:
        return model.split("/", 1)[0].lower()
    m = model.lower()
    if m.startswith(("gpt-", "o1", "o3", "o4", "text-embedding")):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith(("mistral", "ministral", "voxtral", "magistral")):
        return "mistral"
    return "unknown"


def resolve_tier(tier: str, *, eu_strict: bool | None = None) -> TierConfig:
    cfg = load_models_config()
    if tier not in cfg.tiers:
        if tier == "eu_conversation":
            base = cfg.tiers["conversation"].model_copy()
            base.model = cfg.eu_strict_overrides.get("conversation", base.model)
            return base
        raise ConfigError(f"unknown tier '{tier}'")
    tc = cfg.tiers[tier].model_copy()
    strict = get_settings().eu_strict_mode if eu_strict is None else eu_strict
    if strict and tier in cfg.eu_strict_overrides:
        tc.model = cfg.eu_strict_overrides[tier]
        return tc
    if tier == "validator":
        if requested_validator_provider.get():
            candidates = validator_candidates()
            if not candidates:
                raise ConfigError("Für die Gegenprüfung ist kein passender Anbieter verbunden.")
            tc.model = candidates[0]
        else:
            tc.model = validator_preference() or _validator_with_available_key(tc, cfg)
    return tc


def _has_key(vendor: str) -> bool:
    return bool(get_settings().configured_providers().get(vendor, False))


def validator_candidates() -> list[str]:
    """Validator models usable right now (vendor ≠ assessment, key present), preferred order."""
    cfg = load_models_config()
    tc = cfg.tiers["validator"]
    assessment_vendor = vendor_of(cfg.tiers["assessment"].model)
    preferred = validator_preference()
    ordered = ([preferred] if preferred else []) + [tc.model, *tc.fallbacks]
    seen: list[str] = []
    for c in ordered:
        if c not in seen and vendor_of(c) != assessment_vendor and _has_key(vendor_of(c)):
            seen.append(c)
    ordered = _prefer_free_tier(seen)
    if preferred in ordered:
        ordered.remove(preferred)
        ordered.insert(0, preferred)
    required = requested_validator_provider.get()
    return [model for model in ordered if not required or vendor_of(model) == required]


def _prefer_free_tier(candidates: list[str]) -> list[str]:
    try:
        from app.core.credentials import pricing_tier_for
        from app.core.pricing import load_pricing

        table = load_pricing()
        learner = current_workspace()

        def rank(c: str) -> int:
            if pricing_tier_for(learner, vendor_of(c)) != "free":
                return 0
            entry = table.llm.get(c)
            return 0 if entry is not None and entry.free_tier else 1

        return sorted(candidates, key=rank)
    except Exception:  # noqa: BLE001 — DB not ready during early startup; keep configured order
        return candidates


def _validator_with_available_key(tc: TierConfig, cfg: ModelsConfig) -> str:
    """Keep the configured validator when its vendor has a key; otherwise the first usable fallback.

    A fallback is usable when its vendor has a configured key and differs from the assessment vendor
    (product rule 3). If nothing is usable the configured model is returned unchanged so the error
    surfaces clearly instead of silently degrading.
    """
    assessment_vendor = vendor_of(cfg.tiers["assessment"].model)
    if _has_key(vendor_of(tc.model)) or not any(
        _has_key(v) for v in ("openai", "anthropic", "gemini", "mistral", "azure")
    ):
        return tc.model
    for candidate in tc.fallbacks:
        v = vendor_of(candidate)
        if v != assessment_vendor and _has_key(v):
            if candidate != tc.model:
                log.info("validator tier: %s has no API key → using fallback %s", tc.model, candidate)
            return candidate
    return tc.model


def validator_status() -> dict[str, Any]:
    """For /health and the settings page: which second-opinion model is active and why."""
    cfg = load_models_config()
    configured = cfg.tiers["validator"].model
    active = resolve_tier("validator").model
    v_active = vendor_of(active)
    available = _has_key(v_active)
    options = sorted(
        {vendor_of(m) for m in [configured, *cfg.tiers["validator"].fallbacks]}
        - {vendor_of(cfg.tiers["assessment"].model)}
    )
    return {
        "configured": configured,
        "selected": validator_preference(),
        "choices": validator_choices(),
        "last_used": last_validator_usage(),
        "active": active,
        "vendor": v_active,
        "available": available,
        "fallback_used": active != configured,
        "accepted_vendors": options,
        "hint_de": None
        if available
        else (
            "Für die Zweitmeinung (und die Prüfung erzeugter Aufgaben) fehlt ein Schlüssel. "
            "Trage in den Einstellungen einen Schlüssel für " + " oder ".join(options) + " ein."
        ),
    }


def check_vendor_difference(*, eu_strict: bool | None = None) -> None:
    """Product rule 3 / ADR-0004: assessment and validator must come from different vendors."""
    a = resolve_tier("assessment", eu_strict=eu_strict).model
    v = resolve_tier("validator", eu_strict=eu_strict).model
    if vendor_of(a) == vendor_of(v):
        raise ConfigError(f"assessment tier ({a}) and validator tier ({v}) must use different vendors (ADR-0004)")


def eu_strict_unverified() -> list[str]:
    """EU override model ids that are not yet verified against the provider APIs."""
    cfg = load_models_config()
    if cfg.eu_strict_verified_on:
        return []
    return [f"{tier}: {model}" for tier, model in cfg.eu_strict_overrides.items()]


def check_eu_strict_ready(*, eu_strict: bool | None = None) -> None:
    """Refuse EU strict mode while its model ids are unverified (owner decision 2026-09-08)."""
    strict = get_settings().eu_strict_mode if eu_strict is None else eu_strict
    if not strict:
        return
    missing = eu_strict_unverified()
    if missing:
        raise ConfigError(
            "EU_STRICT_MODE=true ist noch nicht möglich: die EU-Modell-IDs in config/models.yaml sind nicht "
            "verifiziert (eu_strict_verified_on ist null). Unverifiziert: "
            + "; ".join(missing)
            + ". Bitte `uv run python scripts/verify_models.py` mit den EU-Schlüsseln ausführen, dann das Datum "
            "in config/models.yaml eintragen — oder EU_STRICT_MODE=false setzen."
        )


def active_provider_table() -> list[dict[str, str]]:
    """Rows for the startup log: tier, model, vendor, DPA + EU residency bookkeeping."""
    cfg = load_models_config()
    rows: list[dict[str, str]] = []
    for tier in cfg.tiers:
        tc = resolve_tier(tier)
        v = vendor_of(tc.model)
        info = cfg.providers.get(v, ProviderInfo())
        rows.append(
            {
                "tier": tier,
                "model": tc.model,
                "vendor": v,
                "dpa_url": info.dpa_url,
                "eu_residency": info.eu_residency,
            }
        )
    return rows


def speech_config() -> SpeechConfig:
    """Speech backends from models.yaml, overridable via env (STT_BACKEND, TTS_BACKEND, ...)."""
    s = get_settings()
    sp = load_models_config().speech.model_copy()
    if s.stt_backend:
        sp.stt_backend = s.stt_backend
    if s.stt_model:
        sp.stt_model = s.stt_model
    if s.tts_backend:
        sp.tts_backend = s.tts_backend
    if s.tts_voice:
        sp.tts_voice = s.tts_voice
    if s.pron_backend:
        sp.pron_backend = s.pron_backend
    if s.eu_strict_mode:
        # ADR-0011: local STT, EU/local TTS.
        if sp.stt_backend in ("openai",):
            sp.stt_backend = "faster_whisper"
        if sp.tts_backend in ("google_wavenet", "openai_mini_tts"):
            sp.tts_backend = "voxtral_tts"
    return sp


def all_configured_models() -> list[tuple[str, str]]:
    cfg = load_models_config()
    out = [(t, tc.model) for t, tc in cfg.tiers.items()]
    out += [(f"eu:{t}", m) for t, m in cfg.eu_strict_overrides.items()]
    return out


def validator_choices() -> list[str]:
    cfg = load_models_config()
    tier = cfg.tiers["validator"]
    excluded = {vendor_of(cfg.tiers[name].model) for name in ("assessment", "generator")}
    return list(dict.fromkeys(model for model in [tier.model, *tier.fallbacks] if vendor_of(model) not in excluded))


def validator_preference() -> str | None:
    from app.core.db import db_session, get_engine
    from app.db.base import Learner

    if not get_engine.cache_info().currsize:
        return None
    with db_session() as db:
        learner = db.get(Learner, current_workspace())
        selected = (learner.profile or {}).get("validator_model") if learner else None
    return str(selected) if selected in validator_choices() else None


def last_validator_usage() -> dict[str, str] | None:
    from sqlalchemy import select

    from app.core.db import db_session, get_engine
    from app.db.base import UsageEvent

    if not get_engine.cache_info().currsize:
        return None
    with db_session() as db:
        row = db.execute(
            select(UsageEvent)
            .where(
                UsageEvent.learner_id == current_workspace(),
                UsageEvent.tier_name == "validator",
                UsageEvent.outcome == "ok",
            )
            .order_by(UsageEvent.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        return {"model": row.model, "at": row.ts.isoformat()} if row else None


def recorded_model(tier: str, learner_id: str, session_id: str, default: str) -> str:
    """Use persisted call provenance instead of re-resolving a model after fallback."""
    from sqlalchemy import select

    from app.core.db import db_session
    from app.db.base import UsageEvent

    with db_session() as db:
        model = db.execute(
            select(UsageEvent.model)
            .where(
                UsageEvent.tier_name == tier,
                UsageEvent.learner_id == learner_id,
                UsageEvent.session_id == session_id,
                UsageEvent.outcome == "ok",
            )
            .order_by(UsageEvent.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
    return str(model) if model else default
