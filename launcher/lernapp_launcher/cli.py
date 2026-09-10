"""``lernapp`` — desktop launcher for the Lernapp (API + Streamlit UI + embedded Postgres).

The launcher is what the desktop shortcut / app bundle runs. It starts the FastAPI backend and
the Streamlit UI as child processes, waits for ``GET /health``, opens the browser and keeps
running in the foreground until it receives SIGINT/SIGTERM (or ``lernapp stop``).

Only the standard library and ``httpx`` are used at import time; the app packages (``app``,
``lernapp_ui``) are imported lazily so that ``lernapp doctor`` still works when they are broken.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import httpx

APP_NAME = "Lernapp"
HOST = "127.0.0.1"
DEFAULT_API_PORT = 8000
DEFAULT_UI_PORT = 8501
HEALTH_TIMEOUT_S = 120.0
UI_TIMEOUT_S = 90.0
STOP_GRACE_S = 15.0
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
WHISPER_SIZES = ("small", "medium", "large-v3-turbo")

IS_WINDOWS = sys.platform == "win32"


# --------------------------------------------------------------------------------------------
# Paths & settings
# --------------------------------------------------------------------------------------------


def version() -> str:
    try:
        from importlib.metadata import version as _v

        return _v("lernapp")
    except Exception:  # noqa: BLE001
        return "0.1.0"


def data_dir() -> Path:
    """<data_dir>: ``LERNAPP_DATA_DIR`` or the platformdirs user data dir (same rule as the API)."""
    env = os.environ.get("LERNAPP_DATA_DIR")
    if env:
        path = Path(env).expanduser().resolve()
    else:
        try:
            from platformdirs import user_data_dir

            path = Path(user_data_dir(APP_NAME, appauthor=False))
        except Exception:  # noqa: BLE001
            path = Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def repo_root() -> Path:
    """Directory holding ``config/`` and ``prompts/`` (git checkout or installed source tree)."""
    env = os.environ.get("LERNAPP_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "pricing.yaml").exists() and (parent / "prompts").is_dir():
            return parent
    raise SystemExit("Konnte das Lernapp-Verzeichnis nicht finden (config/pricing.yaml). LERNAPP_ROOT setzen.")


def ui_app_path() -> Path:
    candidate = repo_root() / "frontend" / "lernapp_ui" / "app.py"
    if candidate.exists():
        return candidate
    try:
        import lernapp_ui

        pkg_dir = Path(lernapp_ui.__file__).resolve().parent
        if (pkg_dir / "app.py").exists():
            return pkg_dir / "app.py"
    except Exception:  # noqa: BLE001
        pass
    return candidate


def run_dir() -> Path:
    p = data_dir() / "run"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def whisper_cache_dir() -> Path:
    """Same ``download_root`` as ``backend/app/services/stt.py``."""
    return data_dir() / "models" / "whisper"


def _settings() -> Any | None:
    """Lazily import the app settings; ``None`` if the backend cannot be imported."""
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("lernapp").debug("app settings unavailable: %s", exc)
        return None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def preferred_ports() -> tuple[int, int]:
    """(api_port, ui_port) from env / <data_dir>/.env via the app settings, with fallbacks."""
    api, ui = DEFAULT_API_PORT, DEFAULT_UI_PORT
    s = _settings()
    if s is not None:
        api, ui = int(s.api_port), int(s.ui_port)
    return _env_int("API_PORT", api), _env_int("UI_PORT", ui)


# --------------------------------------------------------------------------------------------
# Ports & processes
# --------------------------------------------------------------------------------------------


def port_free(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def port_open(port: int, host: str = HOST, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pick_port(preferred: int, taken: set[int]) -> int:
    port = preferred
    for _ in range(100):
        if port not in taken and port_free(port):
            return port
        port += 1
    raise SystemExit(f"Kein freier Port ab {preferred} gefunden.")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_pid(pid: int, *, force: bool = False) -> None:
    """Ask a process (started by another launcher instance) to stop."""
    if not pid_alive(pid):
        return
    if IS_WINDOWS:
        if force:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, creationflags=0x08000000 if IS_WINDOWS else 0)
        else:
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            except OSError:
                subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, check=False, creationflags=0x08000000 if IS_WINDOWS else 0)
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass


def _wait_pids_gone(pids: Sequence[int], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(p) for p in pids):
            return True
        time.sleep(0.25)
    return not any(pid_alive(p) for p in pids)


# --------------------------------------------------------------------------------------------
# Run state (<data_dir>/run/state.json + *.pid)
# --------------------------------------------------------------------------------------------


@dataclass
class RunState:
    launcher_pid: int
    api_pid: int
    ui_pid: int
    api_port: int
    ui_port: int
    started_at: float

    @property
    def ui_url(self) -> str:
        return f"http://{HOST}:{self.ui_port}"

    @property
    def api_url(self) -> str:
        return f"http://{HOST}:{self.api_port}"

    def to_json(self) -> dict[str, Any]:
        return {
            "launcher_pid": self.launcher_pid,
            "api_pid": self.api_pid,
            "ui_pid": self.ui_pid,
            "api_port": self.api_port,
            "ui_port": self.ui_port,
            "started_at": self.started_at,
        }


def state_path() -> Path:
    return run_dir() / "state.json"


def write_state(state: RunState) -> None:
    state_path().write_text(json.dumps(state.to_json(), indent=2), encoding="utf-8")
    for name, pid in (("launcher", state.launcher_pid), ("api", state.api_pid), ("ui", state.ui_pid)):
        (run_dir() / f"{name}.pid").write_text(str(pid), encoding="utf-8")


def clear_state() -> None:
    for name in ("state.json", "launcher.pid", "api.pid", "ui.pid", "api-token", "stop-request.json"):
        try:
            (run_dir() / name).unlink()
        except FileNotFoundError:
            pass


def read_state() -> RunState | None:
    path = state_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RunState(
            launcher_pid=int(raw["launcher_pid"]),
            api_pid=int(raw["api_pid"]),
            ui_pid=int(raw["ui_pid"]),
            api_port=int(raw["api_port"]),
            ui_port=int(raw["ui_port"]),
            started_at=float(raw.get("started_at", 0.0)),
        )
    except Exception:  # noqa: BLE001
        return None


def health(api_port: int, timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        r = httpx.get(f"http://{HOST}:{api_port}/health", timeout=timeout)
        if r.status_code == 200:
            data: dict[str, Any] = r.json()
            return data
    except Exception:  # noqa: BLE001
        pass
    return None


def running_state() -> RunState | None:
    """The state of a live instance, or ``None`` (stale state files are removed)."""
    st = read_state()
    if st is None:
        return None
    alive = pid_alive(st.api_pid) or pid_alive(st.ui_pid) or pid_alive(st.launcher_pid)
    if not alive:
        clear_state()
        return None
    return st


# --------------------------------------------------------------------------------------------
# Child processes with rotating logs
# --------------------------------------------------------------------------------------------


def _rotating_logger(name: str, path: Path, echo: bool) -> logging.Logger:
    logger = logging.getLogger(f"lernapp.child.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    if echo:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(f"[{name}] %(message)s"))
        logger.addHandler(console)
    return logger


def _pump(stream: IO[bytes], logger: logging.Logger) -> None:
    try:
        for raw in iter(stream.readline, b""):
            logger.info(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def spawn(name: str, cmd: Sequence[str], env: dict[str, str], cwd: Path, echo: bool) -> subprocess.Popen[bytes]:
    logger = _rotating_logger(name, logs_dir() / f"{name}.log", echo)
    logger.info("=== start: %s", " ".join(cmd))
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | (0x08000000 if not echo else 0)  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = False
    proc = subprocess.Popen(  # noqa: S603
        list(cmd),
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **kwargs,
    )
    assert proc.stdout is not None
    threading.Thread(target=_pump, args=(proc.stdout, logger), name=f"pump-{name}", daemon=True).start()
    return proc


def terminate(proc: subprocess.Popen[bytes], name: str, grace: float = STOP_GRACE_S) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            proc.terminate()
        else:
            proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    print(f"  {name}: reagiert nicht, wird beendet (kill).", file=sys.stderr)
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False, creationflags=0x08000000 if IS_WINDOWS else 0)
        else:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _tail(path: Path, lines: int = 25) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError:
        return "(kein Log)"


def embedded_pg_dir() -> Path:
    s = _settings()
    if s is not None and s.embedded_pg_dir:
        return Path(s.embedded_pg_dir)
    return data_dir() / "pg"


def _postmaster_pid(pg_dir: Path) -> int:
    try:
        first = (pg_dir / "postmaster.pid").read_text(encoding="utf-8").splitlines()[0]
        return int(first.strip())
    except (OSError, ValueError, IndexError):
        return 0


def stop_embedded_postgres(timeout: float = 20.0) -> bool:
    """Stop the embedded PostgreSQL of this data dir if it is still running.

    pgserver only stops the server when its on-disk handle list is empty; a stale PID from an
    earlier crash leaves the ``postgres`` process running forever. Uses pgserver's bundled
    ``pg_ctl -m fast stop`` (checks postmaster.pid itself); falls back to SIGINT (= fast shutdown).
    """
    s = _settings()
    if s is not None and s.database_url:
        return True  # external database: not ours to stop
    pg_dir = embedded_pg_dir()
    pid = _postmaster_pid(pg_dir)
    if pid <= 0 or not pid_alive(pid):
        return True
    pg_ctl: Path | None = None
    try:
        import pgserver

        cand = (
            Path(pgserver.__file__).resolve().parent / "pginstall" / "bin" / ("pg_ctl.exe" if IS_WINDOWS else "pg_ctl")
        )
        if cand.exists():
            pg_ctl = cand
    except Exception:  # noqa: BLE001
        pg_ctl = None
    if pg_ctl is not None:
        try:
            subprocess.run(  # noqa: S603
                [str(pg_ctl), "-D", str(pg_dir), "-m", "fast", "-w", "-t", str(int(timeout)), "stop"],
                capture_output=True,
                timeout=timeout + 5,
                creationflags=0x08000000 if IS_WINDOWS else 0,
                check=False,
            )
        except Exception:  # noqa: BLE001
            pass
    if pid_alive(pid):
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, creationflags=0x08000000 if IS_WINDOWS else 0)
        else:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                return True
    return _wait_pids_gone([pid], timeout)


# --------------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------------


def _child_env(api_port: int, ui_port: int) -> dict[str, str]:
    import secrets

    env = dict(os.environ)
    env["LERNAPP_API_TOKEN"] = env.get("LERNAPP_API_TOKEN") or secrets.token_urlsafe(32)
    env.update(
        {
            "API_HOST": HOST,
            "API_PORT": str(api_port),
            "UI_PORT": str(ui_port),
            "LERNAPP_API_URL": f"http://{HOST}:{api_port}",
            "LERNAPP_DATA_DIR": str(data_dir()),
            "LERNAPP_ROOT": str(repo_root()),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        }
    )
    token_path = run_dir() / "api-token"
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(env["LERNAPP_API_TOKEN"])
    token_path.chmod(0o600)
    return env


def cmd_start(args: argparse.Namespace) -> int:
    dev: bool = bool(args.dev)
    open_browser: bool = not args.no_browser
    if IS_WINDOWS and open_browser and not dev:
        from lernapp_launcher.desktop import main as desktop_main
        return desktop_main()
    ddir = data_dir()
    root = repo_root()

    existing = running_state()
    if existing is not None and health(existing.api_port) is not None:
        print(f"{APP_NAME} läuft bereits: {existing.ui_url}")
        if open_browser:
            webbrowser.open(existing.ui_url)
        return 0
    if existing is not None:
        print("Verwaiste Prozesse einer früheren Sitzung gefunden – werden beendet …")
        _stop_state(existing)

    pref_api, pref_ui = preferred_ports()
    api_port = pick_port(pref_api, set())
    ui_port = pick_port(pref_ui, {api_port})
    if api_port != pref_api:
        print(f"Hinweis: Port {pref_api} ist belegt, die API nutzt stattdessen Port {api_port}.")
    if ui_port != pref_ui:
        print(f"Hinweis: Port {pref_ui} ist belegt, die Oberfläche nutzt stattdessen Port {ui_port}.")

    ui_file = ui_app_path()
    if not ui_file.exists():
        print(f"FEHLER: Oberfläche nicht gefunden: {ui_file}", file=sys.stderr)
        return 2

    env = _child_env(api_port, ui_port)
    py = sys.executable
    api_cmd = [py, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(api_port)]
    if dev:
        api_cmd += ["--reload", "--reload-dir", str(root / "backend")]
    ui_cmd = [
        py,
        "-m",
        "streamlit",
        "run",
        str(ui_file),
        "--server.port",
        str(ui_port),
        "--server.address",
        HOST,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--browser.serverAddress",
        HOST,
        "--browser.serverPort",
        str(ui_port),
    ]
    if dev:
        ui_cmd += ["--server.runOnSave", "true"]
    else:
        ui_cmd += ["--server.fileWatcherType", "none"]

    print(f"{APP_NAME} {version()} startet …")
    print(f"  Daten:  {ddir}")
    print(f"  Logs:   {logs_dir()}")
    print(f"  API:    http://{HOST}:{api_port}")
    print(f"  UI:     http://{HOST}:{ui_port}")

    stop_requested = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        stop_requested.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    if IS_WINDOWS:
        signal.signal(signal.SIGBREAK, _on_signal)  # type: ignore[attr-defined]

    api = spawn("api", api_cmd, env, root, dev)
    ui = spawn("ui", ui_cmd, env, root, dev)
    state = RunState(os.getpid(), api.pid, ui.pid, api_port, ui_port, time.time())
    write_state(state)

    def watch_stop():
        while not stop_requested.wait(.2):
            try:
                request = json.loads((run_dir() / 'stop-request.json').read_text())
                if request == {'launcher_pid': state.launcher_pid, 'started_at': state.started_at}:
                    stop_requested.set()
            except (OSError, ValueError):
                pass

    threading.Thread(target=watch_stop, daemon=True).start()

    def _shutdown(code: int) -> int:
        stop_requested.set()
        print("Beende Lernapp …")
        terminate(ui, "ui")
        terminate(api, "api")
        if not stop_embedded_postgres():
            print("  Datenbank: reagiert nicht, läuft weiter.", file=sys.stderr)
        clear_state()
        print("Lernapp beendet.")
        return code

    # 1) wait for the API (embedded Postgres init on first start takes a few seconds)
    print("  Warte auf die API (Datenbank wird initialisiert) …", end="", flush=True)
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    ok = False
    while time.monotonic() < deadline and not stop_requested.is_set():
        if api.poll() is not None:
            print()
            print(f"FEHLER: Die API wurde unerwartet beendet (Code {api.returncode}).", file=sys.stderr)
            print(_tail(logs_dir() / "api.log"), file=sys.stderr)
            return _shutdown(3)
        if health(api_port) is not None:
            ok = True
            break
        time.sleep(0.5)
        print(".", end="", flush=True)
    print()
    if stop_requested.is_set():
        return _shutdown(130)
    if not ok:
        print(f"FEHLER: Die API antwortet nach {int(HEALTH_TIMEOUT_S)} s nicht.", file=sys.stderr)
        print(_tail(logs_dir() / "api.log"), file=sys.stderr)
        return _shutdown(3)

    # 2) wait for the UI port
    print("  Warte auf die Oberfläche …", end="", flush=True)
    deadline = time.monotonic() + UI_TIMEOUT_S
    ui_ok = False
    while time.monotonic() < deadline and not stop_requested.is_set():
        if ui.poll() is not None:
            print()
            print(f"FEHLER: Die Oberfläche wurde unerwartet beendet (Code {ui.returncode}).", file=sys.stderr)
            print(_tail(logs_dir() / "ui.log"), file=sys.stderr)
            return _shutdown(4)
        if port_open(ui_port):
            ui_ok = True
            break
        time.sleep(0.5)
        print(".", end="", flush=True)
    print()
    if stop_requested.is_set():
        return _shutdown(130)
    if not ui_ok:
        print(f"FEHLER: Die Oberfläche antwortet nach {int(UI_TIMEOUT_S)} s nicht.", file=sys.stderr)
        print(_tail(logs_dir() / "ui.log"), file=sys.stderr)
        return _shutdown(4)

    print(f"Lernapp läuft: {state.ui_url}   (Beenden: Strg+C oder `lernapp stop`)")
    if open_browser:
        try:
            webbrowser.open(state.ui_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  Browser konnte nicht geöffnet werden: {exc}")

    # 3) supervise
    code = 0
    while not stop_requested.is_set():
        if api.poll() is not None:
            print(f"Die API wurde beendet (Code {api.returncode}).", file=sys.stderr)
            code = 3
            break
        if ui.poll() is not None:
            print(f"Die Oberfläche wurde beendet (Code {ui.returncode}).", file=sys.stderr)
            code = 4
            break
        stop_requested.wait(1.0)
    return _shutdown(code)


def _stop_state(st: RunState) -> bool:
    pids = [st.api_pid, st.ui_pid]
    own = os.getpid()
    if st.launcher_pid != own and pid_alive(st.launcher_pid):
        if IS_WINDOWS:
            (run_dir() / 'stop-request.json').write_text(json.dumps(
                {'launcher_pid': st.launcher_pid, 'started_at': st.started_at}))
        else:
            _signal_pid(st.launcher_pid)
        if _wait_pids_gone([st.launcher_pid, *pids], STOP_GRACE_S + 5):
            stop_embedded_postgres()
            clear_state()
            return True
    for pid in pids:
        _signal_pid(pid)
    if not _wait_pids_gone(pids, STOP_GRACE_S):
        for pid in pids:
            _signal_pid(pid, force=True)
        _wait_pids_gone(pids, 5)
    if st.launcher_pid != own and pid_alive(st.launcher_pid):
        _signal_pid(st.launcher_pid, force=True)
    stop_embedded_postgres()
    clear_state()
    return not any(pid_alive(p) for p in pids)


def cmd_stop(_args: argparse.Namespace) -> int:
    st = running_state()
    if st is None:
        print(f"{APP_NAME} läuft nicht.")
        return 0
    print(f"Beende {APP_NAME} (API-PID {st.api_pid}, UI-PID {st.ui_pid}) …")
    if _stop_state(st):
        print("Lernapp beendet.")
        return 0
    print("Einige Prozesse konnten nicht beendet werden.", file=sys.stderr)
    return 1


def cmd_status(_args: argparse.Namespace) -> int:
    st = running_state()
    if st is None:
        print(f"{APP_NAME}: gestoppt")
        return 1
    h = health(st.api_port)
    print(f"{APP_NAME}: {'läuft' if h else 'startet / nicht erreichbar'}")
    print(f"  UI:        {st.ui_url}  (PID {st.ui_pid}, {'aktiv' if pid_alive(st.ui_pid) else 'beendet'})")
    print(f"  API:       {st.api_url}  (PID {st.api_pid}, {'aktiv' if pid_alive(st.api_pid) else 'beendet'})")
    print(f"  Launcher:  PID {st.launcher_pid} ({'aktiv' if pid_alive(st.launcher_pid) else 'beendet'})")
    print(f"  Daten:     {data_dir()}")
    if h:
        print(
            f"  Backends:  llm={h.get('llm_backend')} stt={h.get('stt_backend')}/{h.get('stt_model')} "
            f"tts={h.get('tts_backend')} eu_strict={h.get('eu_strict_mode')}"
        )
    return 0 if h else 1


def cmd_open(_args: argparse.Namespace) -> int:
    st = running_state()
    if st is None:
        print(f"{APP_NAME} läuft nicht. Starten mit: lernapp start")
        return 1
    webbrowser.open(st.ui_url)
    print(st.ui_url)
    return 0


def _whisper_model_present(size: str) -> bool:
    cache = whisper_cache_dir()
    if not cache.exists():
        return False
    return any(p.is_dir() and size in p.name for p in cache.glob("models--*"))


def _uv_version() -> str | None:
    exe = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if IS_WINDOWS else "uv"))
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def cmd_doctor(_args: argparse.Namespace) -> int:
    problems = 0

    def row(label: str, value: object, ok: bool | None = None) -> None:
        mark = "" if ok is None else ("  [ok]" if ok else "  [!!]")
        print(f"  {label:<22} {value}{mark}")

    print(f"{APP_NAME} doctor — Version {version()}")
    py_ok = sys.version_info[:2] == (3, 12)
    row("Python", f"{sys.version.split()[0]} ({sys.executable})", py_ok)
    problems += not py_ok
    uvv = _uv_version()
    row("uv", uvv or "nicht gefunden (nur für Installation/Updates nötig)", uvv is not None)

    try:
        root = repo_root()
        row("App-Verzeichnis", root, True)
    except SystemExit as exc:
        row("App-Verzeichnis", str(exc), False)
        problems += 1
        root = None

    ddir = data_dir()
    row("Datenverzeichnis", ddir, ddir.is_dir() and os.access(ddir, os.W_OK))
    env_file = ddir / ".env"
    row("Konfiguration (.env)", env_file if env_file.exists() else f"{env_file} (fehlt – Standardwerte)", None)

    s = _settings()
    if s is None:
        row("Backend-Import", "FEHLER: app.core.config nicht importierbar (uv sync ausführen?)", False)
        problems += 1
    else:
        db_url = s.database_url
        if db_url:
            row("Datenbank", "extern (DATABASE_URL gesetzt)", True)
        else:
            pg_dir = Path(s.embedded_pg_dir) if s.embedded_pg_dir else ddir / "pg"
            initialised = (pg_dir / "PG_VERSION").exists()
            row(
                "Datenbank (embedded)",
                f"{pg_dir} ({'initialisiert' if initialised else 'wird beim ersten Start angelegt'})",
                None,
            )
            try:
                import pgserver  # noqa: F401

                row("pgserver", "verfügbar", True)
            except Exception as exc:  # noqa: BLE001
                row("pgserver", f"nicht verfügbar ({exc}) → DATABASE_URL setzen", False)
                problems += 1
        providers = s.configured_providers()
        configured = [k for k, v in providers.items() if v]
        row(
            "Provider konfiguriert",
            ", ".join(configured) if configured else "keine (LLM_BACKEND=fake für Demo)",
            bool(configured) or s.llm_backend == "fake",
        )
        try:
            from app.core.models import speech_config

            sp = speech_config()
            row("STT/TTS", f"stt={sp.stt_backend} ({sp.stt_model}), tts={sp.tts_backend}", None)
            if sp.stt_backend == "faster_whisper":
                present = _whisper_model_present(str(sp.stt_model))
                row(
                    "STT-Modell-Cache",
                    f"{whisper_cache_dir()} "
                    f"({'vorhanden' if present else 'fehlt → lernapp download-models --size ' + str(sp.stt_model)})",
                    present,
                )
        except Exception as exc:  # noqa: BLE001
            row("STT/TTS", f"config/models.yaml nicht lesbar: {exc}", False)
            problems += 1

    if root is not None:
        ui_file = ui_app_path()
        row("Oberfläche", ui_file, ui_file.exists())
        problems += not ui_file.exists()

    try:
        usage = shutil.disk_usage(ddir)
        free_gb = usage.free / 1e9
        row("Freier Speicher", f"{free_gb:.1f} GB", free_gb >= 2.0)
        problems += free_gb < 2.0
    except OSError:
        pass

    api_p, ui_p = preferred_ports()
    st = running_state()
    if st is not None:
        row("Status", f"läuft (UI {st.ui_url})", True)
    else:
        row(
            "Ports",
            f"API {api_p} {'frei' if port_free(api_p) else 'belegt'}, "
            f"UI {ui_p} {'frei' if port_free(ui_p) else 'belegt'}",
            None,
        )

    print("Ergebnis:", "alles in Ordnung" if problems == 0 else f"{problems} Problem(e) gefunden")
    return 0 if problems == 0 else 1


def cmd_download_models(args: argparse.Namespace) -> int:
    size: str = args.size
    cache = whisper_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    print(f"Lade faster-whisper-Modell '{size}' nach {cache} … (einmalig, mehrere hundert MB)")
    try:
        from faster_whisper import download_model
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: faster-whisper nicht installiert: {exc}", file=sys.stderr)
        return 2
    try:
        path = download_model(size, cache_dir=str(cache))
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER beim Herunterladen: {exc}", file=sys.stderr)
        return 1
    print(f"Fertig: {path}")
    return 0


def cmd_reset_db(args: argparse.Namespace) -> int:
    s = _settings()
    if s is not None and s.database_url:
        print("DATABASE_URL ist gesetzt (externe Datenbank) – reset-db betrifft nur die eingebettete Datenbank.")
        return 1
    pg_dir = Path(s.embedded_pg_dir) if (s is not None and s.embedded_pg_dir) else data_dir() / "pg"
    if running_state() is not None:
        print("Lernapp läuft noch. Bitte zuerst `lernapp stop` ausführen.")
        return 1
    if not pg_dir.exists():
        print(f"Keine Datenbank vorhanden ({pg_dir}).")
        return 0
    if not args.yes:
        answer = input(f"ALLE Lerndaten in {pg_dir} löschen? Tippe 'ja' zum Bestätigen: ").strip().lower()
        if answer != "ja":
            print("Abgebrochen.")
            return 1
    shutil.rmtree(pg_dir, ignore_errors=False)
    print(f"Datenbank gelöscht: {pg_dir}. Sie wird beim nächsten Start neu angelegt.")
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"{APP_NAME} {version()}")
    return 0


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def cmd_transfer(args: argparse.Namespace) -> int:
    """Offline transfer: credentials never appear in arguments or terminal output."""
    import getpass

    from app.core.db import init_db
    from app.services.transfer import TransferError, export_backup, restore_backup

    if running_state():
        print("Bitte Lernapp zuerst über 'Lernapp beenden' schließen und danach erneut versuchen.")
        return 1
    filename = args.file
    if not filename:
        print("Pfad zur .lernapp-Datei eingeben (oder Datei in dieses Fenster ziehen):")
        filename = input().strip().strip('"').strip("'")
    path = Path(filename).expanduser()
    try:
        password = getpass.getpass("Übertragungspasswort (Eingabe bleibt unsichtbar): ")
        if args.command == "backup" and password != getpass.getpass("Passwort wiederholen: "):
            raise TransferError("Die Passwörter stimmen nicht überein.")
        init_db()
        if args.command == "backup":
            from app.core import credentials
            credentials.ensure_encryption_key()
            content, counts = export_backup(password)
            with path.open("xb") as handle:
                os.chmod(path, 0o600)
                handle.write(content)
            print(f"Sicherung gespeichert: {path}")
        else:
            if path.stat().st_size > 2 * 1024**3:
                raise TransferError("Die Sicherung ist zu groß.")
            counts = restore_backup(path.read_bytes(), password)
            print("Übernahme abgeschlossen. Du kannst Lernapp jetzt starten.")
        print(f"Materialien: {counts.get('exams', 0)}, Dokumente: {counts.get('documents', 0)}, Vokabeln: {counts.get('vocab_items', 0)}")
        return 0
    except TransferError as exc:
        print(str(exc))
        return 1
    except Exception:
        # SQL/provider exceptions may contain row values (including credentials).
        print("Übertragung fehlgeschlagen. Datei, freien Speicher und passende App-Version prüfen. Bestehende Lerndaten werden nicht ersetzt.")
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lernapp",
        description="Lernapp starten und verwalten (ohne Argumente: start).",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("start", help="Datenbank, API und Oberfläche starten, Browser öffnen")
    sp.add_argument("--dev", action="store_true", help="Entwicklermodus: --reload, Logs auf der Konsole")
    sp.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    sp.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="laufende Lernapp beenden").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="Status anzeigen").set_defaults(func=cmd_status)
    sub.add_parser("open", help="Browser mit der laufenden Oberfläche öffnen").set_defaults(func=cmd_open)
    sub.add_parser("doctor", help="Installation prüfen").set_defaults(func=cmd_doctor)

    for name, help_text in (("backup", "Alle Lerndaten und Anbieterzugänge verschlüsselt sichern"),
                            ("restore", "Sicherung in eine neue Installation übernehmen")):
        transfer = sub.add_parser(name, help=help_text)
        transfer.add_argument("file", nargs="?", help="Pfad zur .lernapp-Datei")
        transfer.set_defaults(func=cmd_transfer)

    dm = sub.add_parser("download-models", help="Spracherkennungs-Modell (faster-whisper) vorab laden")
    dm.add_argument("--size", choices=WHISPER_SIZES, default="small")
    dm.set_defaults(func=cmd_download_models)

    rd = sub.add_parser("reset-db", help="eingebettete Datenbank löschen (alle Lerndaten!)")
    rd.add_argument("--yes", action="store_true", help="ohne Rückfrage")
    rd.set_defaults(func=cmd_reset_db)

    sub.add_parser("version", help="Version anzeigen").set_defaults(func=cmd_version)
    return p


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                # line buffering: status lines must show up immediately in launcher.log / a console
                reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except Exception:  # noqa: BLE001
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or args_list[0].startswith("-") and args_list[0] not in ("-h", "--help"):
        args_list.insert(0, "start")
    parser = build_parser()
    args = parser.parse_args(args_list)
    if not getattr(args, "func", None):
        args = parser.parse_args(["start"])
    try:
        code: int = args.func(args)
    except KeyboardInterrupt:
        code = 130
    return code


if __name__ == "__main__":
    sys.exit(main())
