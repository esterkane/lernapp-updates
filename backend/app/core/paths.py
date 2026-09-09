"""Repository and data-directory resolution.

The app runs either from a git checkout (dev) or from an installed copy of the source tree
(desktop installer). In both cases the *repo root* is the directory holding ``config/`` and
``prompts/``; the *data dir* holds the embedded database, uploaded documents and ``.env``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Lernapp"


@lru_cache(maxsize=1)
def repo_root() -> Path:
    env = os.environ.get("LERNAPP_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "pricing.yaml").exists() and (parent / "prompts").is_dir():
            return parent
    raise RuntimeError("Could not locate repo root (config/pricing.yaml); set LERNAPP_ROOT")


@lru_cache(maxsize=1)
def data_dir() -> Path:
    env = os.environ.get("LERNAPP_DATA_DIR")
    path = Path(env).expanduser().resolve() if env else Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_files() -> tuple[Path, ...]:
    """Candidate .env files, lowest precedence first (later files override earlier ones)."""
    return (repo_root() / ".env", data_dir() / ".env")


def reset_caches() -> None:
    repo_root.cache_clear()
    data_dir.cache_clear()
