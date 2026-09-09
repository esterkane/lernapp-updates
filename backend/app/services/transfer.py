"""Offline, password-protected installation transfer. No SQL or pickle in backups.

Only known SQLAlchemy tables/columns are restored, into an unused installation.
The archive is authenticated before opening its ZIP; filenames are never extracted.
"""
from __future__ import annotations

import base64
import io
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import DateTime, LargeBinary, func, select, text

from app.core import credentials
from app.core.config import get_settings, reload_settings
from app.core.db import get_engine
from app.core.paths import data_dir
from app.db import exams as _exams  # noqa: F401 -- register all tables
from app.db.base import Base

MAGIC = b"LERNAPP-TRANSFER-1\n"
MAX_SIZE = 1024 * 1024 * 1024
SETTINGS = (
    "stt_backend", "stt_model", "stt_compute_type", "tts_backend", "tts_voice", "tts_speed", "openai_tts_voice",
    "pron_backend", "llm_backend", "embedding_backend", "keep_audio", "eu_strict_mode",
    "audio_retention_days", "fx_usd_eur", "default_learner_id", "default_level",
)


class TransferError(ValueError):
    pass


def _cipher(password: str, salt: bytes) -> Fernet:
    if len(password) < 12:
        raise TransferError("Bitte ein Passwort mit mindestens 12 Zeichen verwenden.")
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _columns(table: Any) -> list[Any]:
    return [c for c in table.columns if c.computed is None]


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError("Nicht unterstützter Datentyp in der Sicherung.")


def export_backup(password: str, originals: tuple[Path, ...] = ()) -> tuple[bytes, dict[str, int]]:
    """Consistent DB snapshot; all learner spaces, binary exam assets and provider keys."""
    salt = os.urandom(16)
    cipher = _cipher(password, salt)
    settings = get_settings()
    manifest: dict[str, Any] = {"format": 1, "created_at": datetime.now(UTC).isoformat(),
                                "tables": {}, "settings": {}, "originals": []}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        def asset(content: bytes) -> str:
            name = "assets/" + uuid.uuid4().hex
            archive.writestr(name, content)
            return name

        with get_engine().connect().execution_options(isolation_level="REPEATABLE READ") as conn, conn.begin():
            for table in Base.metadata.sorted_tables:
                rows = []
                for result in conn.execute(select(*_columns(table))).mappings():
                    row = dict(result)
                    if table.name == "provider_credentials" and row["provider"] == "openai_billing_admin":
                        continue  # Billing admin access stays on this device.
                    for col in _columns(table):
                        if isinstance(col.type, LargeBinary) and row[col.name] is not None:
                            row[col.name] = asset(bytes(row[col.name]))
                    if table.name == "audio_files":
                        row["path"] = asset(Path(row["path"]).read_bytes())
                    if table.name == "provider_credentials":
                        # Inner credential encryption is replaced with the recipient's key on restore.
                        value = row.pop("ciphertext")
                        row["ciphertext"] = credentials._fernet().decrypt(value.encode()).decode() if value else None
                    rows.append(row)
                manifest["tables"][table.name] = rows
            # Environment fallback keys also become encrypted per-learner credentials at destination.
            provider_rows = manifest["tables"]["provider_credentials"]
            for provider in credentials.PROVIDERS:
                value = getattr(settings, credentials.ENV_FOR[provider].lower(), None)
                if not value:
                    continue
                for learner in manifest["tables"]["learners"]:
                    existing = next((r for r in provider_rows if r["learner_id"] == learner["id"]
                                     and r["provider"] == provider), None)
                    if existing is not None:
                        if not existing["ciphertext"]:
                            existing["ciphertext"] = value
                    else:
                        provider_rows.append(dict(id=uuid.uuid4().hex, learner_id=learner["id"], provider=provider,
                            ciphertext=value, key_hint=value[-3:], pricing_tier="unknown",
                            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                            last_test_at=None, last_test_ok=None, last_test_message=None))
        manifest["settings"] = {key: getattr(settings, key) for key in SETTINGS if getattr(settings, key) is not None}
        if manifest["tables"]["audio_files"]:
            if not settings.audio_encryption_key:
                raise TransferError("Der Schlüssel für gespeicherte Aufnahmen fehlt.")
            manifest["audio_key"] = settings.audio_encryption_key
        if settings.google_application_credentials:
            manifest["google_credentials"] = asset(Path(settings.google_application_credentials).read_bytes())
        if settings.azure_api_key:
            raise TransferError("Azure-Zugänge bitte separat einrichten; diese Übertragung unterstützt OpenAI, Gemini, Anthropic und Mistral.")
        for path in originals:
            manifest["originals"].append({"name": path.name, "asset": asset(path.read_bytes())})
        archive.writestr("manifest.json", json.dumps(manifest, default=_json_default, ensure_ascii=False))
    content = stream.getvalue()
    if len(content) > MAX_SIZE:
        raise TransferError("Die Sicherung ist größer als 1 GB.")
    return MAGIC + salt + cipher.encrypt(content), {k: len(v) for k, v in manifest["tables"].items()}


def _open(content: bytes, password: str) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    if len(content) > MAX_SIZE * 2 or not content.startswith(MAGIC):
        raise TransferError("Keine unterstützte Lernapp-Sicherung.")
    offset = len(MAGIC)
    try:
        payload = _cipher(password, content[offset:offset + 16]).decrypt(content[offset + 16:])
        archive = zipfile.ZipFile(io.BytesIO(payload))
        if sum(i.file_size for i in archive.infolist()) > MAX_SIZE * 4:
            raise TransferError("Die entpackte Sicherung ist zu groß.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["format"] != 1 or set(manifest["tables"]) != set(Base.metadata.tables):
            raise TransferError("Diese Sicherung benötigt eine passende Lernapp-Version.")
        return archive, manifest
    except (InvalidToken, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise TransferError("Falsches Passwort oder beschädigte Sicherung.") from exc


def restore_backup(content: bytes, password: str) -> dict[str, int]:
    """Restore while app is stopped. Never replace existing learning or credential data."""
    archive, manifest = _open(content, password)
    destination = data_dir()
    folder = destination / ("transfer-" + uuid.uuid4().hex)
    previous_env = (destination / ".env").read_bytes() if (destination / ".env").exists() else None
    written_env = False
    try:
        with get_engine().begin() as conn:
            tables = Base.metadata.sorted_tables
            # Block concurrent writers while checking emptiness and inserting the complete snapshot.
            conn.execute(text("LOCK TABLE " + ", ".join('"' + t.name + '"' for t in tables) + " IN ACCESS EXCLUSIVE MODE"))
            for table in tables:
                if table.name == "audit_log":
                    continue
                rows = conn.execute(select(table)).mappings().all()
                if table.name == "scenarios":
                    rows = [r for r in rows if r["source"] != "seed"]
                if table.name == "learners":
                    rows = [r for r in rows if r["id"] != "default" or r["display_name"] != "Lernende"
                            or r["level"] != "B2" or r["profile"] or r["exam_date"] or r["weekly_focus"]]
                if rows:
                    raise TransferError("Hier sind bereits eigene Daten gespeichert. Die Übernahme ist nur in einer neuen Installation möglich.")
            folder.mkdir(mode=0o700)
            new_key = Fernet.generate_key()
            target_cipher = Fernet(new_key)

            def materialize(name: str, filename: str | None = None) -> Path:
                target = folder / (uuid.uuid4().hex + ("-" + Path(filename).name if filename else ""))
                # Both Windows and Unix separators are removed, regardless of source OS.
                target = folder / target.name.replace("\\", "_").replace(":", "_")
                with target.open("xb") as handle:
                    os.chmod(target, 0o600)
                    handle.write(archive.read(name))
                return target

            for table in reversed(tables):
                conn.execute(table.delete())
            for table in tables:
                allowed = {c.name for c in _columns(table)}
                for source in manifest["tables"][table.name]:
                    if set(source) != allowed:
                        raise TransferError("Die Datenstruktur der Sicherung passt nicht zur App-Version.")
                    row = dict(source)
                    if table.name == "provider_credentials" and row["provider"] == "openai_billing_admin":
                        continue  # Do not import billing admin access from a foreign pack.
                    for col in _columns(table):
                        value = row[col.name]
                        if value is None:
                            continue
                        if isinstance(col.type, LargeBinary):
                            row[col.name] = archive.read(value)
                        elif isinstance(col.type, DateTime):
                            row[col.name] = datetime.fromisoformat(value)
                    if table.name == "provider_credentials" and row["ciphertext"]:
                        row["ciphertext"] = target_cipher.encrypt(row["ciphertext"].encode()).decode()
                    if table.name == "audio_files":
                        row["path"] = str(materialize(row["path"]))
                    conn.execute(table.insert().values(**row))
                for col in table.primary_key:
                    if col.autoincrement is True:
                        sequence = conn.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col.name}).scalar()
                        maximum = conn.execute(select(func.max(col))).scalar()
                        if sequence:
                            conn.execute(text("SELECT setval(:seq, :value, :used)"), {"seq": sequence, "value": maximum or 1, "used": maximum is not None})
            for original in manifest.get("originals", []):
                materialize(original["asset"], original["name"])
            values = {key.upper(): value for key, value in manifest["settings"].items() if key in SETTINGS}
            values["CREDENTIAL_ENCRYPTION_KEY"] = new_key.decode()
            if manifest.get("audio_key"):
                values["AUDIO_ENCRYPTION_KEY"] = manifest["audio_key"]
            if manifest.get("google_credentials"):
                values["GOOGLE_APPLICATION_CREDENTIALS"] = str(materialize(manifest["google_credentials"], "google.json"))
            # JSON quoting is compatible with dotenv, including Windows paths and spaces.
            lines = [f"{key}={json.dumps(str(value).lower() if isinstance(value, bool) else str(value))}" for key, value in values.items()]
            env_path = destination / ".env"
            written_env = True
            with env_path.open("w", encoding="utf-8") as handle:
                os.chmod(env_path, 0o600)
                handle.write("\n".join(lines) + "\n")
        reload_settings()
        return {key: len(value) for key, value in manifest["tables"].items()}
    except Exception:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        if written_env:
            if previous_env is None:
                (destination / ".env").unlink(missing_ok=True)
            else:
                (destination / ".env").write_bytes(previous_env)
        raise
    finally:
        archive.close()
