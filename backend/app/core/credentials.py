"""BYOK provider credentials, encrypted at rest (cost-tracking model, ADR-0019).

- Ciphertext lives in ``provider_credentials``. macOS Keychain / Windows user-scoped DPAPI protect
  the Fernet key. Linux and explicit test environments retain the legacy environment-key backend.
- Plaintext keys exist only transiently inside the backend when a provider call is made.
- The API never returns a full key: ``CredentialView.masked`` shows the last three characters only.
- Environment keys (``OPENAI_API_KEY`` …) remain a fallback for developers; on startup any key found in
  ``<data_dir>/.env`` is imported into the encrypted store for the default learner and removed from the file.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import PROVIDER_ENV_KEYS, get_settings, reload_settings
from app.core.db import db_session
from app.core.paths import data_dir
from app.db.base import ProviderCredential

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

log = logging.getLogger(__name__)

Provider = Literal["openai", "gemini", "anthropic", "mistral"]
PROVIDERS: tuple[str, ...] = ("openai", "gemini", "anthropic", "mistral")
PricingTier = Literal["free", "paid", "unknown"]
# Providers without a free inference tier default to "paid" as soon as a key exists.
DEFAULT_TIER: dict[str, str] = {"openai": "paid", "anthropic": "paid", "mistral": "unknown", "gemini": "unknown"}
ENV_FOR: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
PROVIDER_LABEL_DE: dict[str, str] = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "anthropic": "Anthropic",
    "mistral": "Mistral",
}


class CredentialError(RuntimeError):
    pass


class CredentialView(BaseModel):
    provider: str
    label_de: str
    connected: bool
    source: Literal["encrypted", "env", "none"]
    masked: str | None  # "••••••••••••H7k"
    pricing_tier: str
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str | None = None


def native_store_enabled() -> bool:
    from lernapp_launcher import credential_store

    return credential_store.supported() and get_settings().app_env != "test"


def _fernet() -> Fernet:
    from cryptography.fernet import Fernet

    if native_store_enabled():
        from lernapp_launcher import credential_store

        try:
            protected = credential_store.read(data_dir())
        except credential_store.StorageError as exc:
            raise CredentialError(str(exc)) from None
        if not protected:
            raise CredentialError(
                "Der Geräteschlüssel fehlt. Bitte Lernapp neu starten oder den OS-Schlüsselspeicher entsperren."
            )
        return Fernet(protected)
    key = get_settings().credential_encryption_key or os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise CredentialError("CREDENTIAL_ENCRYPTION_KEY fehlt – Schlüssel können nicht gespeichert werden.")
    return Fernet(key.encode())


def ensure_encryption_key() -> bool:
    """Migrate/create OS-protected desktop key; legacy file backend on Linux/test only."""
    if native_store_enabled():
        return _migrate_native_key()
    if get_settings().credential_encryption_key or os.environ.get("CREDENTIAL_ENCRYPTION_KEY"):
        return False
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    env_path = data_dir() / ".env"
    with env_path.open("a", encoding="utf-8") as f:
        f.write(f"\nCREDENTIAL_ENCRYPTION_KEY={key}\n")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    os.environ["CREDENTIAL_ENCRYPTION_KEY"] = key
    reload_settings()
    log.info("generated CREDENTIAL_ENCRYPTION_KEY in %s", env_path)
    return True


def _migrate_native_key() -> bool:
    from cryptography.fernet import Fernet, InvalidToken
    from lernapp_launcher import credential_store

    root = data_dir()
    legacy = get_settings().credential_encryption_key or os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    try:
        protected = credential_store.read(root)
        with db_session() as db:
            rows = list(
                db.scalars(
                    select(ProviderCredential).where(ProviderCredential.ciphertext.is_not(None)).with_for_update()
                )
            )
            if protected is None and not legacy and rows:
                raise CredentialError(
                    "Der Schlüssel zu vorhandenen Zugangsdaten fehlt. Es wird kein Ersatzschlüssel erzeugt."
                )
            key = protected or Fernet.generate_key()
            cipher = Fernet(key)
            old_cipher = Fernet(legacy.encode()) if legacy else None
            replacements = []
            for row in rows:
                assert row.ciphertext is not None
                try:
                    cipher.decrypt(row.ciphertext.encode())
                except InvalidToken:
                    if old_cipher is None:
                        raise
                    plaintext = old_cipher.decrypt(row.ciphertext.encode())
                    replacements.append((row, cipher.encrypt(plaintext).decode()))
            # Native write/readback precedes the DB commit. Interrupted migrations can resume
            # using the still-present legacy key; already-migrated rows also decrypt correctly.
            credential_store.write(root, key)
            for row, ciphertext in replacements:
                row.ciphertext = ciphertext
        # Only after native readback and database decryption succeeded.
        credential_store.strip_legacy(root / ".env")
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        reload_settings()
        return protected is None
    except (InvalidToken, ValueError):
        raise CredentialError(
            "Vorhandene Zugangsdaten konnten nicht geprüft werden. Der alte Schlüssel bleibt erhalten."
        ) from None
    except credential_store.StorageError as exc:
        raise CredentialError(str(exc)) from None


def mask(hint: str | None) -> str | None:
    return None if not hint else "•" * 12 + hint


def _hint(api_key: str) -> str:
    return api_key[-3:] if len(api_key) >= 8 else "•••"


def _validate_key(provider: str, api_key: str) -> str:
    k = api_key.strip()
    if not k or len(k) < 8 or re.search(r"\s", k):
        raise CredentialError("Der Schlüssel sieht unvollständig aus. Bitte kopiere ihn komplett und ohne Leerzeichen.")
    if provider not in PROVIDERS:
        raise CredentialError(f"Unbekannter Anbieter: {provider}")
    return k


def set_credential(learner_id: str, provider: str, api_key: str, *, pricing_tier: str | None = None) -> CredentialView:
    k = _validate_key(provider, api_key)
    token = _fernet().encrypt(k.encode()).decode()
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
            )
        ).scalar_one_or_none()
        if row is None:
            row = ProviderCredential(
                learner_id=learner_id, provider=provider, pricing_tier=pricing_tier or DEFAULT_TIER[provider]
            )
            db.add(row)
        row.ciphertext = token
        row.key_hint = _hint(k)
        row.updated_at = datetime.now(UTC)
        row.last_test_at = None
        row.last_test_ok = None
        row.last_test_message = None
        if pricing_tier:
            row.pricing_tier = pricing_tier
        elif row.pricing_tier == "unknown" and DEFAULT_TIER[provider] != "unknown":
            row.pricing_tier = DEFAULT_TIER[provider]
    log.info("credential stored for provider=%s learner=%s (hint only: …%s)", provider, learner_id, _hint(k))
    return get_view(learner_id, provider)


def set_pricing_tier(learner_id: str, provider: str, pricing_tier: str) -> CredentialView:
    if pricing_tier not in ("free", "paid", "unknown"):
        raise CredentialError("Tarif muss free, paid oder unknown sein.")
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
            )
        ).scalar_one_or_none()
        if row is None:
            row = ProviderCredential(learner_id=learner_id, provider=provider, ciphertext=None, key_hint=None)
            db.add(row)
        row.pricing_tier = pricing_tier
        row.updated_at = datetime.now(UTC)
    return get_view(learner_id, provider)


def record_test(learner_id: str, provider: str, ok: bool, message: str) -> None:
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
            )
        ).scalar_one_or_none()
        if row is None:
            row = ProviderCredential(learner_id=learner_id, provider=provider, ciphertext=None, key_hint=None)
            db.add(row)
        row.last_test_at = datetime.now(UTC)
        row.last_test_ok = ok
        row.last_test_message = message[:300]


def delete_credential(learner_id: str, provider: str) -> bool:
    """Remove the encrypted key (and an app-level env key of the same provider from <data_dir>/.env)."""
    removed = False
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
            )
        ).scalar_one_or_none()
        if row is not None:
            db.delete(row)
            removed = True
    if _remove_env_key(ENV_FOR[provider]):
        removed = True
    return removed


def _env_key(provider: str) -> str | None:
    name = ENV_FOR.get(provider)
    if not name:
        return None
    s = get_settings()
    attr = next((a for a, e in PROVIDER_ENV_KEYS.items() if e == name), None)
    val = getattr(s, attr, None) if attr else None
    return str(val) if val else (os.environ.get(name) or None)


def get_api_key(learner_id: str, provider: str) -> str | None:
    """Decrypted key for a provider call — backend use only, never returned by an API."""
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
            )
        ).scalar_one_or_none()
        token = row.ciphertext if row else None
    if token:
        try:
            return str(_fernet().decrypt(token.encode()).decode())
        except Exception as exc:  # noqa: BLE001
            log.error(
                "cannot decrypt credential for %s (%s) — was CREDENTIAL_ENCRYPTION_KEY changed?",
                provider,
                type(exc).__name__,
            )
            raise CredentialError(
                "Der gespeicherte Schlüssel kann nicht entschlüsselt werden. Bitte erneut hinterlegen."
            ) from None
    return None


def api_key_for(learner_id: str | None, provider: str) -> str | None:
    """Key to use for a provider call: the learner's encrypted key, else the app-level environment key."""
    if learner_id:
        k = get_api_key(learner_id, provider)
        if k:
            return k
    return _env_key(provider) if learner_id in (None, get_settings().default_learner_id) else None


def encrypted_providers(learner_id: str) -> set[str]:
    """Providers with a ciphertext for this learner. Empty (env-only) while the database is not connected yet."""
    from app.core.db import get_engine

    if not get_engine.cache_info().currsize:
        return set()
    try:
        with db_session() as db:
            rows = db.execute(
                select(ProviderCredential.provider).where(
                    ProviderCredential.learner_id == learner_id, ProviderCredential.ciphertext.is_not(None)
                )
            ).scalars()
            return {str(r) for r in rows}
    except Exception as exc:  # noqa: BLE001 — e.g. migrations not applied yet
        log.debug("credential lookup unavailable (%s)", type(exc).__name__)
        return set()


def has_key(learner_id: str | None, provider: str) -> bool:
    """True when a usable key exists: the learner's encrypted credential OR the app-level environment key."""
    if learner_id and provider in encrypted_providers(learner_id):
        return True
    return learner_id in (None, get_settings().default_learner_id) and bool(_env_key(provider))


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI / Anthropic (sk-ant-…)
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),  # Google API keys
    re.compile(r"(?i)(?<=bearer )[A-Za-z0-9_\-.]{8,}"),
    re.compile(r"(?i)(?<=api[_-]key=)[^\s'\",;&]+"),
    re.compile(r"(?i)(?<=x-api-key[:=] )[^\s'\",;&]+"),
    re.compile(r"(?i)(?<=[?&]key=)[^\s'\",;&]+"),
)
REDACTED = "***"


def redact(text: str | None, learner_id: str | None = None) -> str:
    """Mask configured key values (env + this learner's encrypted keys) and key-shaped substrings in free text.

    Used for error messages that end up in usage events, ledger meta or developer logs.
    """
    if not text:
        return "" if text is None else text
    out = str(text)
    values = [v for v in (_env_key(p) for p in PROVIDERS) if v]
    if learner_id:
        for p in encrypted_providers(learner_id):
            v = get_api_key(learner_id, p)
            if v:
                values.append(v)
    for v in sorted(set(values), key=len, reverse=True):
        if len(v) >= 8:
            out = out.replace(v, REDACTED)
    for pat in _SECRET_PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def pricing_tier_for(learner_id: str | None, provider: str) -> str:
    if learner_id:
        with db_session() as db:
            row = db.execute(
                select(ProviderCredential.pricing_tier).where(
                    ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
                )
            ).scalar_one_or_none()
        if row:
            return str(row)
    return (
        DEFAULT_TIER.get(provider, "unknown")
        if learner_id in (None, get_settings().default_learner_id) and _env_key(provider)
        else "unknown"
    )


def get_view(learner_id: str, provider: str) -> CredentialView:
    with db_session() as db:
        row = db.execute(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id, ProviderCredential.provider == provider
            )
        ).scalar_one_or_none()
        data = None
        if row is not None:
            data = (
                row.ciphertext,
                row.key_hint,
                row.pricing_tier,
                row.last_test_at,
                row.last_test_ok,
                row.last_test_message,
            )
    env = _env_key(provider) if learner_id == get_settings().default_learner_id else None
    if data and data[0]:
        source: Literal["encrypted", "env", "none"] = "encrypted"
        hint = data[1]
    elif env:
        source = "env"
        hint = _hint(env)
    else:
        source = "none"
        hint = None
    tier = data[2] if data else (DEFAULT_TIER.get(provider, "unknown") if env else "unknown")
    return CredentialView(
        provider=provider,
        label_de=PROVIDER_LABEL_DE.get(provider, provider),
        connected=source != "none",
        source=source,
        masked=mask(hint),
        pricing_tier=tier,
        last_test_at=data[3] if data else None,
        last_test_ok=data[4] if data else None,
        last_test_message=data[5] if data else None,
    )


def list_credentials(learner_id: str) -> list[CredentialView]:
    return [get_view(learner_id, p) for p in PROVIDERS]


def _remove_env_key(name: str) -> bool:
    """Drop ``NAME=…`` from <data_dir>/.env (never from the repo .env) and from the process environment."""
    path = data_dir() / ".env"
    changed = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if not re.match(rf"^\s*{name}\s*=", ln)]
        if len(kept) != len(lines):
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            changed = True
    if os.environ.pop(name, None) is not None:
        changed = True
    if changed:
        reload_settings()
    return changed


def migrate_env_keys(learner_id: str) -> list[str]:
    """One-time import of plaintext keys from <data_dir>/.env into the encrypted store."""
    path = data_dir() / ".env"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    migrated: list[str] = []
    for provider, name in ENV_FOR.items():
        m = re.search(rf"^\s*{name}\s*=\s*['\"]?([^'\"\n]+)['\"]?\s*$", text, re.M)
        if not m or not m.group(1).strip():
            continue
        try:
            set_credential(learner_id, provider, m.group(1).strip())
        except CredentialError as exc:
            log.warning("could not import %s: %s", name, exc)
            continue
        _remove_env_key(name)
        migrated.append(provider)
    if migrated:
        log.info(
            "migrated %s from %s into the encrypted credential store", ", ".join(ENV_FOR[p] for p in migrated), path
        )
    return migrated


def require_workspace_key(learner_id: str | None, provider: str) -> str | None:
    """Never let an SDK silently use environment credentials for another workspace."""
    key = api_key_for(learner_id, provider)
    if key is None and learner_id not in (None, "system", get_settings().default_learner_id):
        raise CredentialError(
            "Bitte unter Einstellungen → AI-Anbieter einen Schlüssel für diesen Lernbereich hinterlegen."
        )
    return key
