from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.hooks import active_hooks
from app.core.models import active_provider_table, speech_config, validator_status
from app.core.pricing import load_pricing
from app.services.tts import resolve_tts_backend_name
from app.services.updates import current_version

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, Any]:
    s = get_settings()
    sp = speech_config()
    return {
        "status": "ok",
        "version": current_version(),
        "env": s.app_env,
        "eu_strict_mode": s.eu_strict_mode,
        "keep_audio": s.keep_audio,
        "api_token_active": bool(s.lernapp_api_token),  # /health itself is always open (app/api/auth.py)
        "llm_backend": s.llm_backend,
        "stt_backend": sp.stt_backend,
        "stt_model": sp.stt_model,
        "tts_backend": resolve_tts_backend_name(),
        "tts_configured": sp.tts_backend,
        "pron_backend": sp.pron_backend,
        "pricing_version": load_pricing().version,
        "providers_configured": s.configured_providers(),
        "tiers": active_provider_table(),
        "validator": validator_status(),
        "hooks": active_hooks(),  # ADR-0016: the active hook set is inspectable
    }
