"""Device-local billing credentials, separate from inference keys and portable exports."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core import credentials
from app.core.db import db_session
from app.db.base import ProviderCredential

PROVIDER = "openai_billing_admin"


def saved(learner_id: str) -> bool:
    with db_session() as db:
        return (
            db.scalar(
                select(ProviderCredential.id).where(
                    ProviderCredential.learner_id == learner_id,
                    ProviderCredential.provider == PROVIDER,
                    ProviderCredential.ciphertext.is_not(None),
                )
            )
            is not None
        )


def save(learner_id: str, key: str) -> None:
    key = credentials._validate_key("openai", key)
    if len(key) > 4096:
        raise credentials.CredentialError("Der Schlüssel ist zu lang. Bitte die Eingabe prüfen.")
    ciphertext = credentials._fernet().encrypt(key.encode()).decode()
    with db_session() as db:
        row = db.scalar(
            select(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id,
                ProviderCredential.provider == PROVIDER,
            )
        )
        if row is None:
            row = ProviderCredential(learner_id=learner_id, provider=PROVIDER)
            db.add(row)
        row.ciphertext = ciphertext
        row.key_hint = None
        row.updated_at = datetime.now(UTC)


def remove(learner_id: str) -> None:
    with db_session() as db:
        db.execute(
            delete(ProviderCredential).where(
                ProviderCredential.learner_id == learner_id,
                ProviderCredential.provider == PROVIDER,
            )
        )


def get(learner_id: str) -> str | None:
    return credentials.get_api_key(learner_id, PROVIDER)
