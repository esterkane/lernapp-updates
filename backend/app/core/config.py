"""Application settings (pydantic-settings). Secrets only via environment / .env files.

Product rule 1: ``TDN_MAPPING_ENABLED`` is a hard-coded constant, not a setting.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import data_dir, env_files

TDN_MAPPING_ENABLED: Final[bool] = False

LLMBackend = Literal["litellm", "fake"]
STTBackend = Literal["faster_whisper", "voxtral", "openai", "fake"]
TTSBackend = Literal["google_wavenet", "piper", "openai_mini_tts", "voxtral_tts", "fake", "none"]
PronBackend = Literal["prosody_mvp", "mfa_gop", "azure_batch", "fake"]
EmbeddingBackend = Literal["litellm", "fake"]

# Environment variable names that LiteLLM / provider SDKs read directly.
PROVIDER_ENV_KEYS: Final[dict[str, str]] = {
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "mistral_api_key": "MISTRAL_API_KEY",
    "azure_api_key": "AZURE_API_KEY",
    "azure_api_base": "AZURE_API_BASE",
    "google_application_credentials": "GOOGLE_APPLICATION_CREDENTIALS",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in env_files()),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_pii: bool = False

    # network
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    ui_port: int = 8501
    lernapp_api_url: str | None = None
    # optional bearer token (dev/ops only; see app/api/auth.py) — never shown in the UI
    lernapp_api_token: str | None = None

    # storage
    database_url: str | None = None  # None → embedded Postgres (pgserver) in data_dir/pg
    embedded_pg_dir: Path | None = None

    # backends (None → value from config/models.yaml `speech:`)
    llm_backend: LLMBackend = "litellm"
    stt_backend: STTBackend | None = None
    stt_model: str | None = None
    stt_compute_type: str | None = None  # int8 | float16 | auto
    tts_backend: TTSBackend | None = None
    tts_voice: str | None = None
    openai_tts_voice: str | None = None
    tts_speed: float = Field(default=0.85, ge=0.7, le=1.2)
    pron_backend: PronBackend | None = None
    embedding_backend: EmbeddingBackend = "litellm"
    rag_backend: Literal["pg", "es"] = "pg"

    # privacy (ADR-0011)
    eu_strict_mode: bool = False
    keep_audio: bool = False
    audio_encryption_key: str | None = None
    credential_encryption_key: str | None = None  # Fernet key for BYOK provider credentials (ADR-0019)
    audio_retention_days: int = 30

    # cost (ADR-0006)
    fx_usd_eur: float = 0.86
    fx_source: str = "config:FX_USD_EUR"  # where the rate came from (stored with every usage event)
    cost_rate_min_learning_seconds: int = 300  # cost per learning hour is hidden below this much learning time

    # learner defaults (single-learner desktop install)
    default_learner_id: str = "default"
    default_level: str = "B2"

    # provider secrets (mirrored into os.environ for LiteLLM / SDKs)
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    mistral_api_key: str | None = None
    azure_api_key: str | None = None
    azure_api_base: str | None = None
    google_application_credentials: str | None = None
    elasticsearch_url: str | None = Field(default=None)

    @property
    def data_path(self) -> Path:
        return data_dir()

    def export_provider_env(self) -> None:
        """Push configured secrets into os.environ so LiteLLM and SDKs find them."""
        for attr, env_name in PROVIDER_ENV_KEYS.items():
            value = getattr(self, attr)
            if value and not os.environ.get(env_name):
                os.environ[env_name] = str(value)

    def configured_providers(self) -> dict[str, bool]:
        """Key availability per vendor: env/.env OR the default learner's encrypted credential (ADR-0019)."""
        from app.core.credentials import has_key  # lazy: credentials imports this module
        from app.core.workspaces import current_workspace

        lid = current_workspace()
        return {
            "openai": has_key(lid, "openai"),
            "anthropic": has_key(lid, "anthropic"),
            "gemini": has_key(lid, "gemini"),
            "mistral": has_key(lid, "mistral"),
            "azure": bool(self.azure_api_key or os.environ.get("AZURE_API_KEY")),
            "google": bool(self.google_application_credentials or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.export_provider_env()
    return s


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
