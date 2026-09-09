"""Database engine + embedded Postgres bootstrap (ADR-0007 / ADR-0018).

``DATABASE_URL`` set → external Postgres (docker compose / server).
Unset → embedded PostgreSQL 16 + pgvector started via ``pgserver`` in ``<data_dir>/pg``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.paths import data_dir, repo_root

log = logging.getLogger(__name__)
_lock = threading.Lock()
_embedded: Any = None


def _socket_dir_without_whitespace(pgdata: Path, runtime_path: Path) -> Path:
    """Replacement for ``pgserver.utils.find_suitable_socket_dir``.

    pgserver forwards the socket dir to ``pg_ctl -o "-k <dir>"`` *unquoted*, so a data dir with
    whitespace (the macOS default ``~/Library/Application Support/Lernapp/pg``) makes Postgres fail
    with ``invalid argument: "Support/Lernapp/pg"``. Keep pgserver's own choice when it has no
    whitespace; otherwise use a short, whitespace-free directory (pgserver's runtime path, then
    the temp dir).
    """
    import hashlib
    import tempfile

    from pgserver.utils import find_suitable_socket_dir, socket_name_length_ok

    chosen: Path = find_suitable_socket_dir(pgdata, runtime_path)
    if not any(ch.isspace() for ch in str(chosen)):
        return chosen
    ident = hashlib.sha256(f"{pgdata}-{pgdata.stat().st_ino}".encode()).hexdigest()[:10]
    for base in (Path(runtime_path), Path(tempfile.gettempdir()), Path("/tmp")):
        candidate = base / f"lernapp-pg-{ident}"
        if any(ch.isspace() for ch in str(candidate)):
            continue
        candidate.mkdir(parents=True, exist_ok=True)
        if socket_name_length_ok(candidate / ".s.PGSQL.5432"):
            log.info("Embedded Postgres socket dir (whitespace-free): %s", candidate)
            return candidate
    raise RuntimeError(f"No whitespace-free unix socket directory available for {pgdata}")


def _embedded_uri() -> str:
    global _embedded
    import pgserver
    import pgserver.postgres_server

    # see _socket_dir_without_whitespace (pgserver bug with spaces in the data path)
    pgserver.postgres_server.find_suitable_socket_dir = _socket_dir_without_whitespace  # type: ignore[attr-defined]

    s = get_settings()
    pgdata = Path(s.embedded_pg_dir) if s.embedded_pg_dir else data_dir() / "pg"
    pgdata.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _embedded is None:
            log.info("Starting embedded PostgreSQL in %s", pgdata)
            _embedded = pgserver.get_server(pgdata)  # type: ignore[attr-defined]
        uri: str = _embedded.get_uri()
    return uri


def database_url() -> str:
    s = get_settings()
    url = s.database_url or _embedded_uri()
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = database_url()
    engine = create_engine(url, pool_pre_ping=True, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    return engine


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def db_session() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with db_session() as s:
        yield s


def run_migrations() -> None:
    """Apply Alembic migrations to head (idempotent)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(repo_root() / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root() / "backend" / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url())
    command.upgrade(cfg, "head")


def ensure_default_learner() -> str:
    from app.db.base import Learner

    s = get_settings()
    with db_session() as db:
        if db.get(Learner, s.default_learner_id) is None:
            db.add(Learner(id=s.default_learner_id, display_name="Lernende", level=s.default_level))
    return s.default_learner_id


def init_db() -> None:
    get_engine()
    run_migrations()
    ensure_default_learner()


def reset_engine() -> None:
    global _embedded
    _session_factory.cache_clear()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    if _embedded is not None:
        try:
            _embedded.cleanup()
        except Exception:  # noqa: BLE001
            pass
        _embedded = None
