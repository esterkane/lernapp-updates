#!/usr/bin/env python
"""Verify every model id in config/models.yaml (ADR-0003, provider-adapters skill).

Two checks per model:
1. offline: the id (without vendor prefix) exists in LiteLLM's model registry;
2. online (only when the vendor's API key is configured): the provider's model list contains it.
Exit 1 on any UNKNOWN unless --non-blocking.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.models import all_configured_models, load_models_config, vendor_of  # noqa: E402

KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "azure": "AZURE_API_KEY",
}


def registry_has(model: str) -> bool:
    import litellm

    bare = model.split("/", 1)[1] if "/" in model else model
    reg = litellm.model_cost
    return model in reg or bare in reg or any(k.endswith("/" + bare) for k in reg)


def provider_list(vendor: str) -> set[str] | None:
    try:
        if vendor == "openai":
            from openai import OpenAI

            return {m.id for m in OpenAI().models.list()}
        if vendor == "anthropic":
            import httpx

            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                timeout=20,
            )
            r.raise_for_status()
            return {m["id"] for m in r.json().get("data", [])}
        if vendor == "mistral":
            import httpx

            r = httpx.get(
                "https://api.mistral.ai/v1/models",
                headers={"Authorization": "Bearer " + os.environ["MISTRAL_API_KEY"]},
                timeout=20,
            )
            r.raise_for_status()
            return {m["id"] for m in r.json().get("data", [])}
        if vendor == "gemini":
            import httpx

            r = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": os.environ["GEMINI_API_KEY"]},
                timeout=20,
            )
            r.raise_for_status()
            return {m["name"].removeprefix("models/") for m in r.json().get("models", [])}
    except Exception as exc:  # noqa: BLE001
        print(f"  (online check for {vendor} failed: {exc})")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--non-blocking", action="store_true")
    ap.add_argument("--offline", action="store_true", help="skip provider API calls")
    args = ap.parse_args()
    unknown = 0
    lists: dict[str, set[str] | None] = {}
    get_settings()  # loads <data_dir>/.env and exports provider keys into the environment
    models = list(all_configured_models())
    for i, fb in enumerate(load_models_config().tiers["validator"].fallbacks):
        models.append((f"validator:fb{i + 1}", fb))
    print(f"{'tier':16s} {'model':36s} registry  provider")
    for tier, model in models:
        vendor = vendor_of(model)
        bare = model.split("/", 1)[1] if "/" in model else model
        reg = "OK" if registry_has(model) else "UNKNOWN"
        prov = "skipped"
        if not args.offline and os.environ.get(KEY_ENV.get(vendor, "")):
            if vendor not in lists:
                lists[vendor] = provider_list(vendor)
            lst = lists[vendor]
            if lst is not None:
                prov = "OK" if bare in lst else "UNKNOWN"
        if reg == "UNKNOWN" or prov == "UNKNOWN":
            unknown += 1
        print(f"{tier:16s} {model:36s} {reg:8s}  {prov}")
    if unknown:
        print(f"\n{unknown} model id(s) UNKNOWN — mark them # TODO(verify) and check the provider docs")
        return 0 if args.non_blocking else 1
    print("\nall model ids known")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
