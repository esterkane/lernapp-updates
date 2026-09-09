"""Validator tier falls back to another vendor with a configured key (ADR-0003 lists Gemini and Anthropic)."""

from __future__ import annotations

import pytest
from app.core import models as m


def _providers(monkeypatch: pytest.MonkeyPatch, **flags: bool) -> None:
    base = {"openai": False, "anthropic": False, "gemini": False, "mistral": False, "azure": False, "google": False}
    base.update(flags)
    monkeypatch.setattr(m, "_has_key", lambda vendor: base.get(vendor, False))


def test_no_keys_at_all_keeps_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch)
    assert m.resolve_tier("validator").model == m.load_models_config().tiers["validator"].model


def test_gemini_only_uses_gemini_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, openai=True, gemini=True)
    active = m.resolve_tier("validator").model
    assert active.startswith("gemini/")
    st = m.validator_status()
    assert st["fallback_used"] is True and st["available"] is True and st["hint_de"] is None
    m.check_vendor_difference()


def test_anthropic_key_keeps_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, openai=True, anthropic=True)
    assert m.resolve_tier("validator").model.startswith("anthropic/")


def test_openai_only_reports_missing_second_opinion(monkeypatch: pytest.MonkeyPatch) -> None:
    _providers(monkeypatch, openai=True)
    st = m.validator_status()
    assert st["available"] is False
    assert "Gemini" in st["hint_de"] or "gemini" in st["hint_de"]
    assert "anthropic" in st["hint_de"]


def test_fallback_never_same_vendor_as_assessment(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = m.load_models_config().model_copy(deep=True)
    cfg.tiers["validator"].model = "anthropic/claude-sonnet-5"
    cfg.tiers["validator"].fallbacks = ["openai/gpt-5.6-luna", "gemini/gemini-3.1-pro-preview"]
    monkeypatch.setattr(m, "load_models_config", lambda: cfg)
    _providers(monkeypatch, openai=True, gemini=True)
    assert m.resolve_tier("validator").model.startswith("gemini/")


def test_health_and_settings_expose_validator(client) -> None:  # type: ignore[no-untyped-def]
    assert "validator" in client.get("/health").json()
    assert "validator" in client.get("/settings").json()
