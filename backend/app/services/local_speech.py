"""Explicit local recognition downloads. Reading status never contacts a provider."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.core.paths import data_dir

MODELS = {
    "small": {"label": "Schnell – für die meisten Laptops", "download": "ca. 500 MB"},
    "medium": {"label": "Gründlicher – braucht mehr Zeit und Speicher", "download": "ca. 1,5 GB"},
    "large-v3-turbo": {"label": "Für leistungsstarke Laptops", "download": "ca. 1,6 GB"},
}
_lock = threading.Lock()
_job: dict[str, Any] = {"state": "idle", "model": None, "message": ""}


def cached_path(model: str) -> Path | None:
    if model not in MODELS:
        return None
    from faster_whisper import download_model

    try:
        path = Path(download_model(model, cache_dir=str(data_dir() / "models" / "whisper"), local_files_only=True))
        if all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
            return path
    except Exception:
        pass
    return None


def status() -> dict[str, Any]:
    with _lock:
        job = dict(_job)
    return {
        "job": job,
        "models": {name: dict(info, installed=cached_path(name) is not None) for name, info in MODELS.items()},
    }


def install(model: str) -> None:
    from faster_whisper import WhisperModel, download_model

    path = download_model(model, cache_dir=str(data_dir() / "models" / "whisper"))
    # Load the actual downloaded weights before declaring setup successful, without a GPU dependency.
    WhisperModel(path, device="cpu", compute_type="int8", local_files_only=True)


def _worker(model: str) -> None:
    try:
        install(model)
    except Exception:
        state, message = (
            "error",
            "Die Spracherkennung konnte nicht eingerichtet werden. Internetverbindung und freien Speicher prüfen und erneut versuchen.",
        )
    else:
        state, message = "ready", "Spracherkennung eingerichtet. Du kannst sie jetzt testen und verwenden."
    with _lock:
        _job.update(state=state, message=message)


def start(model: str) -> dict[str, Any]:
    if model not in MODELS:
        raise ValueError("Unbekannte Spracherkennung.")
    with _lock:
        if _job["state"] != "installing":
            _job.update(
                state="installing",
                model=model,
                message="Spracherkennung wird heruntergeladen und geprüft. Das kann einige Minuten dauern.",
            )
            threading.Thread(target=_worker, args=(model,), daemon=True, name="local-speech-setup").start()
    return status()
