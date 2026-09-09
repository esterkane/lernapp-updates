"""Test harness: embedded Postgres in a temp dir, all backends fake, ledger on."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="lernapp-test-"))
os.environ.update(
    {
        "LERNAPP_DATA_DIR": str(_TMP),
        "APP_ENV": "test",
        "LLM_BACKEND": "fake",
        "STT_BACKEND": "fake",
        "TTS_BACKEND": "fake",
        "PRON_BACKEND": "prosody_mvp",
        "EMBEDDING_BACKEND": "fake",
        "KEEP_AUDIO": "false",
        "EU_STRICT_MODE": "false",
        "DATABASE_URL": "",
        "LOG_LEVEL": "WARNING",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
    }
)


@pytest.fixture(scope="session", autouse=True)
def _db() -> Iterator[None]:
    from app.core.db import init_db, reset_engine

    init_db()
    yield
    reset_engine()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def client():  # type: ignore[no-untyped-def]
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def learner_id() -> str:
    from app.core.db import ensure_default_learner

    return ensure_default_learner()


def ledger_count() -> int:
    from app.core.db import db_session
    from app.db.base import CostLedger
    from sqlalchemy import func, select

    with db_session() as db:
        return int(db.execute(select(func.count()).select_from(CostLedger)).scalar_one())


def pytest_configure(config: pytest.Config) -> None:
    """Marker registration (pyproject is owned elsewhere): ``live`` tests hit real providers and are opt-in."""
    config.addinivalue_line("markers", "live: hits real provider APIs; opt-in via `-m live` with keys in .env")
