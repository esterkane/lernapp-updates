"""Public, pinned GitHub release channel; no user data or credentials leave the laptop."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import httpx
from packaging.version import Version

from app.core.config import get_settings
from app.core.paths import data_dir, repo_root

REPOSITORY = "esterkane/lernapp-updates"
ASSET = "lernapp-update.zip"
LIMIT = 30 * 1024 * 1024
_lock = threading.Lock()
_cache: dict[str, Any] = {}
_checked = 0.0


def current_version() -> str:
    import tomllib

    return str(tomllib.loads((repo_root() / "pyproject.toml").read_text())["project"]["version"])


def supported() -> bool:
    s = get_settings()
    return (
        repo_root() == data_dir() / "app"
        and not s.database_url
        and (not s.embedded_pg_dir or Path(s.embedded_pg_dir).resolve() == data_dir() / "pg")
    )


def check(force: bool = False) -> dict[str, Any]:
    global _cache, _checked
    with _lock:
        if not force and time.monotonic() - _checked < 6 * 3600 and _cache:
            return {**_cache, "job": job_status()}
        result: dict[str, Any] = {
            "current": current_version(),
            "available": False,
            "supported": supported(),
            "release_page": f"https://github.com/{REPOSITORY}/releases",
        }
        try:
            response = httpx.get(
                f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
                timeout=8,
                headers={"Accept": "application/vnd.github+json"},
            )
            if response.status_code == 404:
                result["message"] = "Noch keine neue Version veröffentlicht."
            else:
                response.raise_for_status()
                release = response.json()
                tag = release["tag_name"]
                if not re.fullmatch(r"v\d+\.\d+\.\d+", tag) or release.get("draft") or release.get("prerelease"):
                    raise ValueError("Invalid release")
                asset = next(a for a in release.get("assets", []) if a["name"] == ASSET)
                digest = asset.get("digest") or ""
                url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET}"
                if asset.get("browser_download_url") != url or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                    raise ValueError("Release integrity information missing")
                if not 0 < asset["size"] <= LIMIT:
                    raise ValueError("Release too large")
                result.update(
                    latest=tag[1:],
                    available=Version(tag[1:]) > Version(result["current"]),
                    download=url,
                    sha256=digest[7:],
                    size=asset["size"],
                )
                result["message"] = (
                    "Neue Version verfügbar." if result["available"] else "Du verwendest die aktuelle Version."
                )
            _checked = time.monotonic()
        except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError):
            result["message"] = "Updates konnten gerade nicht geprüft werden. Bitte später erneut versuchen."
            _checked = time.monotonic() - 6 * 3600 + 60
        _cache = result
        return {**result, "job": job_status()}


def job_status() -> dict[str, Any]:
    from lernapp_launcher.cli import pid_alive
    lock = data_dir() / "updates/install.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text())
            if not pid_alive(pid):
                lock.unlink(missing_ok=True)
                _status("failed", "Das letzte Update wurde unterbrochen. Bitte erneut prüfen. Die lokale Sicherung bleibt erhalten.")
        except (OSError, ValueError):
            pass
    try:
        return cast(dict[str, Any], json.loads((data_dir() / "updates" / "status.json").read_text()))
    except (OSError, ValueError):
        return {}


def _status(phase: str, message: str) -> None:
    path = data_dir() / "updates" / "status.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"phase": phase, "message": message}))
    temp.replace(path)


def _download_and_launch(release: dict[str, Any]) -> None:
    home = data_dir() / "updates"
    try:
        _status("downloading", "Update wird heruntergeladen. Du kannst das Fenster geöffnet lassen.")
        archive = home / "download.zip"
        digest = hashlib.sha256()
        total = 0
        with (
            httpx.stream("GET", release["download"], follow_redirects=True, timeout=60) as response,
            archive.open("wb") as f,
        ):
            response.raise_for_status()
            if response.url.scheme != "https":
                raise ValueError("Insecure redirect")
            for block in response.iter_bytes():
                total += len(block)
                if total > LIMIT:
                    raise ValueError("Download too large")
                f.write(block)
                digest.update(block)
        if total != release["size"] or digest.hexdigest() != release["sha256"]:
            raise ValueError("Download checksum mismatch")
        worker = home / "update_worker.py"
        shutil.copy2(repo_root() / "launcher/lernapp_launcher/update_worker.py", worker)
        uv = shutil.which("uv") or str(Path.home() / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv"))
        if not Path(uv).is_file():
            raise ValueError("uv missing")
        config = {
            "app": str(repo_root()),
            "data": str(data_dir()),
            "archive": str(archive),
            "version": release["latest"],
            "sha256": release["sha256"],
            "uv": uv,
            "extras": [
                x
                for x, module in [
                    ("local-tts", "piper"),
                    ("es", "elasticsearch"),
                    ("phones", "montreal_forced_aligner"),
                ]
                if importlib.util.find_spec(module) is not None
            ],
        }
        request = home / "request.json"
        request.write_text(json.dumps(config))
        request.chmod(0o600)
        _status(
            "installing",
            "Lernapp wird gesichert und aktualisiert. Sie öffnet sich anschließend wieder. Das kann einige Minuten dauern.",
        )
        options: dict[str, Any] = (
            {"start_new_session": True}
            if os.name != "nt"
            else {"creationflags": subprocess.__dict__["DETACHED_PROCESS"] | subprocess.__dict__["CREATE_NEW_PROCESS_GROUP"]}
        )
        # Base Python is outside app/.venv: Windows must be able to replace the app directory.
        with (home / "update.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.__dict__["_base_executable"], str(worker), str(request)],
                cwd=str(home),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                **options,
            )
            (home / "install.lock").write_text(str(process.pid))
    except Exception:
        _status(
            "failed",
            "Das Update konnte nicht vorbereitet werden. Deine bisherige Version und Lerndaten bleiben erhalten. Bitte erneut versuchen.",
        )
        (home / "install.lock").unlink(missing_ok=True)


def install() -> dict[str, Any]:
    release = check(force=True)
    if not release["supported"]:
        raise ValueError("Automatische Installation ist für die reguläre lokale Desktop-Installation verfügbar.")
    if not release["available"]:
        raise ValueError(release["message"])
    home = data_dir() / "updates"
    home.mkdir(mode=0o700, exist_ok=True)
    try:
        fd = os.open(home / "install.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError as exc:
        raise ValueError("Ein Update läuft bereits. Bitte warte, bis die App wieder startet.") from exc
    _status("downloading", "Update wird vorbereitet.")
    threading.Thread(target=_download_and_launch, args=(release,), daemon=True).start()
    return job_status()


def cached_status() -> dict[str, Any]:
    if supported() and (not _cache or time.monotonic() - _checked >= 6 * 3600) and not _lock.locked():
        threading.Thread(target=check, daemon=True).start()
    return {
        **({"current": current_version(), "supported": supported(), "available": False} | _cache),
        "job": job_status(),
    }
