from __future__ import annotations

import pytest
from app.core.models import ConfigError, check_vendor_difference, resolve_tier, vendor_of


def test_vendor_of() -> None:
    assert vendor_of("openai/gpt-5.6-luna") == "openai"
    assert vendor_of("anthropic/claude-sonnet-5") == "anthropic"
    assert vendor_of("claude-sonnet-5") == "anthropic"
    assert vendor_of("gpt-5.6-terra") == "openai"
    assert vendor_of("mistral/mistral-large-3") == "mistral"


def test_default_config_passes() -> None:
    check_vendor_difference(eu_strict=False)
    check_vendor_difference(eu_strict=True)


def test_same_vendor_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.models as m

    orig = m.resolve_tier

    def fake(tier: str, *, eu_strict: bool | None = None):  # type: ignore[no-untyped-def]
        tc = orig(tier, eu_strict=eu_strict)
        if tier == "validator":
            tc.model = "openai/gpt-5.6-luna"
        return tc

    monkeypatch.setattr(m, "resolve_tier", fake)
    with pytest.raises(ConfigError):
        m.check_vendor_difference()


def test_eu_override_applies() -> None:
    assert resolve_tier("conversation", eu_strict=True).model.startswith("mistral/")
    assert resolve_tier("conversation", eu_strict=False).model.startswith("openai/")
