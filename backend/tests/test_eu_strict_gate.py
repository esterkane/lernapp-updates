"""EU_STRICT_MODE must refuse to start while EU model ids are unverified (owner decision 2026-09-08)."""

from __future__ import annotations

import pytest
from app.core.models import ConfigError, check_eu_strict_ready, eu_strict_unverified, load_models_config


def test_unverified_by_default() -> None:
    cfg = load_models_config()
    assert cfg.eu_strict_verified_on is None
    assert eu_strict_unverified()


def test_strict_mode_refused_with_clear_message() -> None:
    check_eu_strict_ready(eu_strict=False)
    with pytest.raises(ConfigError) as exc:
        check_eu_strict_ready(eu_strict=True)
    msg = str(exc.value)
    assert "EU_STRICT_MODE" in msg and "verify_models" in msg and "mistral/" in msg


def test_settings_api_rejects_enabling_eu_mode(client) -> None:  # type: ignore[no-untyped-def]
    r = client.put("/settings", json={"values": {"EU_STRICT_MODE": "true"}})
    assert r.status_code == 400
    assert "verifiziert" in r.json()["detail"]
    assert client.get("/settings").json()["eu_strict_mode"] is False


def test_verified_date_lifts_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.models as m

    cfg = load_models_config().model_copy(deep=True)
    cfg.eu_strict_verified_on = "2026-09-08"
    monkeypatch.setattr(m, "load_models_config", lambda: cfg)
    assert eu_strict_unverified() == []
    check_eu_strict_ready(eu_strict=True)
