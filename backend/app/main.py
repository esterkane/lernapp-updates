"""FastAPI application (ADR-0009: the UI talks to this over HTTP only)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.audit_middleware import AuditMiddleware
from app.api.errors import register_exception_handlers
from app.api.workspaces import enforce_workspace
from app.core.blueprints import load_blueprints, warn_unverified
from app.core.config import get_settings
from app.core.db import init_db
from app.core.hooks import register_default_hooks
from app.core.models import active_provider_table, check_eu_strict_ready, check_vendor_difference

log = logging.getLogger("app")

ROUTER_MODULES = [
    "health",
    "updates",
    "workspaces",
    "settings",
    "speech_setup",
    "credentials",
    "blueprints",
    "tasks",
    "assess",
    "sessions",
    "costs",
    "usage",
    "documents",
    "exams",
    "vocab",
    "links",
    "roleplay",
    "learners",
    "evals",
    "audit",
]


def _log_startup() -> None:
    s = get_settings()
    log.info(
        "Lernapp API starting (env=%s, data_dir=%s, eu_strict=%s, keep_audio=%s)",
        s.app_env,
        s.data_path,
        s.eu_strict_mode,
        s.keep_audio,
    )
    for row in active_provider_table():
        log.info(
            "tier %-13s → %-32s vendor=%-9s eu_residency=%s dpa=%s",
            row["tier"],
            row["model"],
            row["vendor"],
            row["eu_residency"],
            row["dpa_url"],
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=get_settings().log_level)
    logging.getLogger("pgserver").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    load_blueprints()  # refuses to start on invalid blueprints (ADR-0005)
    warn_unverified()
    check_vendor_difference()  # ADR-0004
    check_eu_strict_ready()  # refuses EU mode with unverified model ids
    register_default_hooks()  # ADR-0016: the active hook set is listed by GET /health
    init_db()
    try:
        from app.core import credentials

        credentials.ensure_encryption_key()
        credentials.migrate_env_keys(get_settings().default_learner_id)
        if credentials.native_store_enabled():
            import threading

            from lernapp_launcher.credential_store import finish_backup_migration

            from app.core.paths import data_dir

            threading.Thread(target=finish_backup_migration, args=(data_dir(),), daemon=True).start()
    except Exception:  # noqa: BLE001
        log.exception("credential store initialisation failed")
    try:
        from app.services.roleplay import seed_scenarios

        seed_scenarios()
    except Exception as exc:  # noqa: BLE001
        log.warning("scenario seeding skipped: %s", exc)
    if get_settings().keep_audio:
        try:
            from app.services.audio import purge_expired_audio

            n = purge_expired_audio()
            if n:
                log.info("purged %d expired audio files (ADR-0011 retention)", n)
        except Exception:  # noqa: BLE001
            log.exception("audio purge failed")
    _log_startup()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Lernapp API", version="0.2.23", lifespan=lifespan, dependencies=[Depends(enforce_workspace)])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditMiddleware)  # one audit_log row per governed request (learnings §3)
    register_exception_handlers(app)  # HookDenied → 400 business, AdapterFailure → 502 transient
    for name in ROUTER_MODULES:
        mod = import_module(f"app.api.{name}")
        app.include_router(mod.router)
    import_module("app.api.auth").install_auth(app)  # optional LERNAPP_API_TOKEN bearer check
    return app


app = create_app()


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.api_host, port=s.api_port, log_level=s.log_level.lower())


if __name__ == "__main__":
    run()
