from __future__ import annotations

from app.core.paths import repo_root
from app.core.pricing import all_model_keys, load_pricing


def test_every_entry_has_source() -> None:
    t = load_pricing()
    for stage in ("llm", "stt", "tts", "pron", "embed"):
        for model, entry in t.section(stage).items():  # type: ignore[arg-type]
            assert entry.source, f"{stage}/{model} has no source"


def test_prices_resolve_for_configured_models() -> None:
    from app.core.models import all_configured_models

    t = load_pricing()
    missing = []
    for tier, model in all_configured_models():
        stage = "embed" if "embed" in tier else "llm"
        unit = "tokens_in"
        if t.unit_price_usd(stage, model, unit) is None:  # type: ignore[arg-type]
            missing.append((tier, model))
    assert not missing, f"configured models without price: {missing}"


def test_version_bumped_when_content_changed() -> None:
    """config/.pricing.hash pins (version, content hash); changing content without a version bump fails."""
    t = load_pricing()
    hash_file = repo_root() / "config" / ".pricing.hash"
    assert hash_file.exists(), "Pin pricing explicitly in config/.pricing.hash before testing"
    pinned_version, pinned_hash = hash_file.read_text().split()
    assert (pinned_version, pinned_hash) == (t.version, t.content_hash), (
        "Pricing changed: bump the version and explicitly update config/.pricing.hash (ADR-0006)"
    )


def test_all_model_keys_listed() -> None:
    keys = all_model_keys()
    assert ("llm", "openai/gpt-5.6-luna") in keys
    assert ("tts", "google/wavenet-de") in keys
