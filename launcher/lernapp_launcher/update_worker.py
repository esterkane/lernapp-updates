"""Standalone standard-library updater, run by base Python outside the app's virtualenv."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath


def unpack(archive: Path, destination: Path, version: str, digest: str) -> None:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise ValueError("Archive integrity mismatch")
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > 3000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
            raise ValueError("Archive limits exceeded")
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries")
        for i in infos:
            p = PurePosixPath(i.filename)
            if (
                p.is_absolute()
                or ".." in p.parts
                or "\\" in i.filename
                or ":" in i.filename
                or str(p) != i.filename
                or stat.S_ISLNK(i.external_attr >> 16)
                or any(x in (".env", ".git", ".venv", "data", "dist") for x in p.parts)
            ):
                raise ValueError("Unsafe archive entry")
        manifest = json.loads(z.read("SOURCE_MANIFEST.json"))
        if manifest["version"] != version or set(names) != set(manifest["files"]) | {"SOURCE_MANIFEST.json"}:
            raise ValueError("Invalid source manifest")
        if tomllib.loads(z.read("pyproject.toml").decode())["project"]["version"] != version:
            raise ValueError("Version mismatch")
        for name, expected in manifest["files"].items():
            content = z.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("File integrity mismatch")
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def environment(root: Path, data: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        LERNAPP_ROOT=str(root),
        LERNAPP_DATA_DIR=str(data),
        PYTHONPATH=os.pathsep.join(str(root / p) for p in ("backend", "frontend", "launcher")),
    )
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    return env


def stop(root: Path, data: Path) -> None:
    subprocess.run(
        [str(python(root)), "-m", "lernapp_launcher.cli", "stop"],
        cwd=data,
        env=environment(root, data),
        check=True,
        timeout=90,
    )
    # No copy of a live database, even if an old launcher reports success too early.
    if (data / "pg/postmaster.pid").exists():
        raise RuntimeError("Database is still running")


def start(root: Path, data: Path) -> None:
    options = (
        {"start_new_session": True}
        if os.name != "nt"
        else {"creationflags": subprocess.__dict__["DETACHED_PROCESS"] | subprocess.__dict__["CREATE_NEW_PROCESS_GROUP"]}
    )
    with (data / "updates/restart.log").open("ab") as log:
        subprocess.Popen(
            [str(python(root)), "-m", "lernapp_launcher.cli", "start"],
            cwd=data,
            env=environment(root, data),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **options,
        )


def healthy(data: Path, version: str, timeout: float = 180) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = json.loads((data / "run/state.json").read_text())
            with urllib.request.urlopen(f"http://127.0.0.1:{int(state['api_port'])}/health", timeout=2) as response:
                body = json.load(response)
            if body.get("status") == "ok" and body.get("version") == version:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{int(state['ui_port'])}/_stcore/health", timeout=2
                ) as response:
                    return bool(response.status == 200)
        except Exception:
            pass
        time.sleep(2)
    return False


def size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink()) if path.exists() else 0


def run(request: Path) -> None:
    cfg = json.loads(request.read_text())
    app, data = Path(cfg["app"]), Path(cfg["data"])
    if app != data / "app" or app.is_symlink():
        raise ValueError("Only standard local installations can be updated")
    home = data / "updates"
    job = home / ("version-" + cfg["version"] + "-" + uuid.uuid4().hex[:8])
    job.mkdir(mode=0o700)
    staged, old, backup = job / "new-app", job / "previous-app", job / "backup-data"
    switched = stopped = backed_up = False

    def status(phase: str, message: str) -> None:
        temp = home / "status.tmp"
        temp.write_text(json.dumps({"phase": phase, "message": message, "version": cfg["version"], "backup": str(job)}))
        temp.replace(home / "status.json")

    try:
        status("installing", "Update-Dateien werden geprüft.")
        unpack(Path(cfg["archive"]), staged, cfg["version"], cfg["sha256"])
        if shutil.disk_usage(data).free < size(app / ".venv") + 2 * size(data / "pg") + 1024**3:
            raise RuntimeError("Not enough free disk space")
        status("installing", "Lernapp wird für die lokale Sicherung kurz geschlossen.")
        stop(app, data)
        stopped = True
        backup.mkdir(mode=0o700)
        if (data / "pg").exists():
            if any(p.is_symlink() for p in (data / "pg").rglob("*")):
                raise RuntimeError("External database files are not supported")
            shutil.copytree(data / "pg", backup / "pg")
        if (data / ".env").exists():
            shutil.copy2(data / ".env", backup / ".env")
        backed_up = True
        status("installing", "Sicherung fertig. Die neue App-Version wird installiert.")
        # Preserve platform-generated icon. Documents, audio, models and keys remain outside app/.
        if (app / "lernapp.ico").exists():
            shutil.copy2(app / "lernapp.ico", staged / "lernapp.ico")
        app.rename(old)
        switched = True
        staged.rename(app)
        args = [cfg["uv"], "sync", "--frozen", "--no-dev", "--python", "3.12"]
        for extra in cfg.get("extras", []):
            if extra not in ("local-tts", "es", "phones"):
                raise ValueError("Unknown extra")
            args += ["--extra", extra]
        subprocess.run(args, cwd=app, env=environment(app, data), check=True, timeout=1800)
        status("installing", "Die aktualisierte App startet. Dein Browser öffnet sich gleich wieder.")
        start(app, data)
        if not healthy(data, cfg["version"]):
            raise RuntimeError("Updated app did not start successfully")
        status("complete", "Update installiert. Deine Lerndaten und Einstellungen sind weiterhin vorhanden.")
    except Exception as exc:
        print("Update failed:", type(exc).__name__, flush=True)
        if switched:
            # A failed new process must be stopped before restoring the database.
            if (data / "run/state.json").exists() or (data / "pg/postmaster.pid").exists():
                stop(app if python(app).exists() else old, data)
            if app.exists():
                app.rename(job / "failed-app")
            old.rename(app)
            if backed_up:
                if (data / "pg").exists():
                    (data / "pg").rename(job / "failed-pg")
                if (backup / "pg").exists():
                    shutil.copytree(backup / "pg", data / "pg")
                if (backup / ".env").exists():
                    shutil.copy2(backup / ".env", data / ".env")
        if stopped:
            start(app, data)
        status(
            "failed",
            "Update nicht installiert. Die bisherige Version wurde beibehalten bzw. wiederhergestellt. Bitte später erneut versuchen.",
        )
    finally:
        (home / "install.lock").unlink(missing_ok=True)


if __name__ == "__main__":
    request = Path(sys.argv[1])
    try:
        run(request)
    except Exception:
        (request.parent / "status.json").write_text(json.dumps({"phase": "failed", "message": "Das Update wurde unterbrochen und konnte nicht vollständig wiederhergestellt werden. Bitte die Betreuung kontaktieren; Sicherung und Lerndaten nicht löschen."}))
        (request.parent / "install.lock").unlink(missing_ok=True)
        raise
