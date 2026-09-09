"""Settings endpoints for the non-technical Einstellungen page.

Non-secret settings are written to ``<data_dir>/.env``. Provider keys are NOT (ADR-0019): they live
encrypted in the database (``/provider-credentials``); this module only ever shows the masked form.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import credentials
from app.core.config import get_settings, reload_settings
from app.core.models import active_provider_table, speech_config, validator_status
from app.core.paths import data_dir
from app.core.workspaces import current_workspace
from app.services import provider_test
from app.services.tts import resolve_tts_backend_name

router = APIRouter(tags=["settings"])

KEYS_MOVED_MSG = "Schlüssel werden jetzt verschlüsselt gespeichert: Einstellungen → AI-Anbieter"

EDITABLE_ENV = {
    "AZURE_API_BASE",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "STT_BACKEND",
    "STT_MODEL",
    "TTS_BACKEND",
    "TTS_VOICE",
    "TTS_SPEED",
    "OPENAI_TTS_VOICE",
    "KEEP_AUDIO",
    "EU_STRICT_MODE",
    "FX_USD_EUR",
    "DEFAULT_LEVEL",
    "LLM_BACKEND",
    "STT_COMPUTE_TYPE",
}
SECRET_KEYS = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "AZURE_API_KEY"}


def is_provider_key_name(name: str) -> bool:
    """``*_API_KEY`` names are credentials — never accepted or written by the settings endpoints."""
    return name.upper().endswith("_API_KEY")


def _env_path() -> Path:
    return data_dir() / ".env"


def read_env_file() -> dict[str, str]:
    p = _env_path()
    out: dict[str, str] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def write_env_file(values: dict[str, str | None]) -> None:
    secrets = [k for k in values if is_provider_key_name(k)]
    if secrets:  # guard: plaintext provider keys never reach <data_dir>/.env (ADR-0019)
        raise ValueError(KEYS_MOVED_MSG)
    current = read_env_file()
    for k, v in values.items():
        if v is None or v == "":
            current.pop(k, None)
            os.environ.pop(k, None)
        else:
            current[k] = v
            os.environ[k] = v
    lines = [f"{k}={v}" for k, v in sorted(current.items())]
    _env_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(_env_path(), 0o600)
    except OSError:
        pass
    reload_settings()


def _mask(v: str | None) -> str | None:
    """Same masked form as the credential store: 12 dots + the last three characters."""
    return credentials.mask(credentials._hint(v)) if v else None


def _masked_secrets() -> dict[str, str | None]:
    lid = current_workspace()
    out: dict[str, str | None] = {credentials.ENV_FOR[v.provider]: v.masked for v in credentials.list_credentials(lid)}
    out["AZURE_API_KEY"] = _mask(get_settings().azure_api_key or os.environ.get("AZURE_API_KEY"))
    return out


class SettingsUpdate(BaseModel):
    values: dict[str, str | None]


@router.get("/settings")
def get_current() -> dict[str, Any]:
    s = get_settings()
    env = read_env_file()
    sp = speech_config()
    return {
        "env_file": str(_env_path()),
        "data_dir": str(data_dir()),
        "secrets": _masked_secrets(),  # masked form only; full keys never leave the backend
        "credentials": [v.model_dump(mode="json") for v in credentials.list_credentials(current_workspace())],
        "google_credentials_file": env.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        "providers_configured": s.configured_providers(),
        "speech": {
            "stt_backend": sp.stt_backend,
            "stt_model": sp.stt_model,
            "tts_backend_configured": sp.tts_backend,
            "tts_backend_active": resolve_tts_backend_name(),
            "tts_voice": sp.tts_voice,
            "tts_speed": s.tts_speed,
            "openai_tts_voice": s.openai_tts_voice,
            "pron_backend": sp.pron_backend,
        },
        "keep_audio": s.keep_audio,
        "eu_strict_mode": s.eu_strict_mode,
        "api_token_active": bool(s.lernapp_api_token),  # dev/ops only: the token itself is never returned
        "fx_usd_eur": s.fx_usd_eur,
        "llm_backend": s.llm_backend,
        "default_level": s.default_level,
        "tiers": active_provider_table(),
        "validator": validator_status(),
    }


@router.put("/settings")
def update(body: SettingsUpdate) -> dict[str, Any]:
    if any(is_provider_key_name(k) for k in body.values):
        raise HTTPException(400, KEYS_MOVED_MSG)
    bad = [k for k in body.values if k not in EDITABLE_ENV]
    if bad:
        raise HTTPException(400, f"not editable: {bad}")
    if str(body.values.get("EU_STRICT_MODE", "")).lower() in ("true", "1", "yes"):
        from app.core.models import ConfigError, check_eu_strict_ready

        try:
            check_eu_strict_ready(eu_strict=True)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
    if body.values.get("TTS_SPEED") is not None:
        try:
            if not 0.7 <= float(str(body.values["TTS_SPEED"])) <= 1.2:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Bitte ein Sprechtempo zwischen 0,7 und 1,2 wählen.") from exc
    write_env_file(body.values)
    return get_current()


class ProviderTest(BaseModel):
    provider: str  # openai|anthropic|gemini|mistral|google_tts


@router.post("/settings/test")
def test_provider(body: ProviderTest) -> dict[str, Any]:
    """Legacy path for the current UI: same test (and exactly one usage event) as /provider-credentials/{p}/test."""
    if body.provider == "google_tts":
        from app.services.tts import GoogleWavenetTTS, HookedTTS

        try:
            # goes through the hook engine (ledger_required) like every other TTS call
            HookedTTS(GoogleWavenetTTS()).synthesize("Hallo", session_id="settings-test", learner_id="system")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": credentials.redact(str(exc))[:300]}
        return {"ok": True, "detail": "Google TTS antwortet."}
    try:
        out = provider_test.run_test(current_workspace(), body.provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": out.ok, "detail": out.message_de, "message_de": out.message_de}


@router.get("/settings/local-voice")
def local_voice_status() -> dict[str, Any]:
    from app.services.local_voice import status

    return status()


class LocalVoiceSetup(BaseModel):
    accept_optional_install: bool = False


@router.post("/settings/local-voice")
def setup_local_voice(body: LocalVoiceSetup) -> dict[str, Any]:
    from app.services.local_voice import start

    if not body.accept_optional_install:
        raise HTTPException(400, "Bitte die Installation der optionalen lokalen Stimme bestätigen.")
    return start()
