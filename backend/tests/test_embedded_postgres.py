"""ADR-0018 condition 1: the suite runs against the embedded PostgreSQL + pgvector instance."""

from __future__ import annotations

import os

from app.core.db import database_url, get_engine
from app.core.paths import data_dir
from sqlalchemy import text


def test_tests_use_embedded_instance() -> None:
    assert not os.environ.get("DATABASE_URL"), "tests must not point at an external Postgres"
    url = database_url()
    assert url.startswith("postgresql+psycopg://")
    assert "host=" in url or "127.0.0.1" in url  # unix socket (mac/linux) or loopback (windows)
    assert (data_dir() / "pg" / "PG_VERSION").exists()


def test_vector_extension_and_german_fts() -> None:
    with get_engine().connect() as conn:
        ver = conn.execute(text("select extversion from pg_extension where extname='vector'")).scalar_one()
        assert ver
        tsv = conn.execute(text("select to_tsvector('german', 'Verhandlungen über Prüfungen')::text")).scalar_one()
        assert "verhandl" in tsv and "prufung" in tsv
        major = conn.execute(text("show server_version_num")).scalar_one()
        assert int(major) >= 160000
