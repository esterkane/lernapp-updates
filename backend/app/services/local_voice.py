"""Explicit, optional Piper setup. Fixed sources, checksums and bounded downloads."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from app.core.paths import data_dir, repo_root

VOICE = "de_DE-thorsten-medium"
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/1162a9173d0ce503555aed757976b7a9912eae4c/de/de_DE/thorsten/medium/"
FILES = {
    VOICE + ".onnx": ("7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819", 63201294),
    VOICE + ".onnx.json": ("974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85", 4819),
    "MODEL_CARD": ("5196b5ab0794e6056263a1f37c18bec407b61ac187529bee29d1c366871e5c9e", 285),
}
_lock = threading.Lock()
_state = "idle"
_message = ""


def voice_dir() -> Path:
    return data_dir() / "models" / "piper"


@lru_cache(maxsize=8)
def _valid_files(fingerprint: tuple[tuple[str, int, int], ...]) -> bool:
    return all(
        Path(path).stat().st_size == FILES[Path(path).name][1]
        and hashlib.sha256(Path(path).read_bytes()).hexdigest() == FILES[Path(path).name][0]
        for path, _size, _mtime in fingerprint
    )


def status() -> dict[str, Any]:
    files = [voice_dir() / name for name in FILES]
    try:
        fingerprint = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
        installed = importlib.util.find_spec("piper") is not None and _valid_files(fingerprint)
    except OSError:
        installed = False
    with _lock:
        return {"state": _state, "message": _message, "ready": installed and _state != "installing"}


def _progress(message: str) -> None:
    global _message
    with _lock:
        _message = message


def download_file(name: str, target: Path) -> None:
    digest, size = FILES[name]
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        h = hashlib.sha256()
        count = 0
        with httpx.stream("GET", BASE + name, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            with temporary.open("wb") as out:
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > size:
                        raise ValueError("Unexpected voice download size")
                    h.update(chunk)
                    out.write(chunk)
                    if name.endswith(".onnx"):
                        _progress(f"Deutsche Stimme wird heruntergeladen: {count * 100 // size} %")
        if count != size or h.hexdigest() != digest:
            raise ValueError("Voice checksum mismatch")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def install() -> None:
    """No shell, no user-supplied URL/package; no credential-bearing subprocess output returned."""
    uv = shutil.which("uv") or str(
        Path.home() / ".local" / "bin" / ("uv.exe" if __import__("os").name == "nt" else "uv")
    )
    if importlib.util.find_spec("piper") is None:
        _progress("Zusatzkomponenten werden installiert. Das kann einige Minuten dauern.")
        subprocess.run(
            [uv, "sync", "--frozen", "--inexact", "--no-dev", "--extra", "local-tts"],
            cwd=repo_root(),
            check=True,
            timeout=600,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        importlib.invalidate_caches()
    voice_dir().mkdir(parents=True, exist_ok=True)
    for name, (digest, size) in FILES.items():
        target = voice_dir() / name
        if (
            not target.is_file()
            or target.stat().st_size != size
            or hashlib.sha256(target.read_bytes()).hexdigest() != digest
        ):
            download_file(name, target)
    _progress("Die installierte Stimme wird mit einem Probesatz geprüft.")
    # Exercise actual synthesis before reporting success. Never calls a cloud service.
    import io
    import wave

    from piper import PiperVoice

    voice = PiperVoice.load(str(voice_dir() / (VOICE + ".onnx")))
    with wave.open(io.BytesIO(), "wb") as wav:
        voice.synthesize_wav("Die lokale Stimme ist bereit.", wav)


def _worker() -> None:
    global _state, _message
    try:
        install()
    except Exception:
        with _lock:
            _state, _message = (
                "error",
                "Die lokale Stimme konnte nicht eingerichtet werden. Prüfe Internetverbindung und freien Speicher und versuche es erneut.",
            )
    else:
        with _lock:
            _state, _message = "ready", "Lokale Stimme eingerichtet. Du kannst sie jetzt aktivieren."


def start() -> dict[str, Any]:
    global _state, _message
    with _lock:
        if _state != "installing":
            _state, _message = (
                "installing",
                "Stimme und Zusatzkomponenten werden heruntergeladen. Das kann einige Minuten dauern.",
            )
            threading.Thread(target=_worker, daemon=True, name="local-voice-setup").start()
    return status()
