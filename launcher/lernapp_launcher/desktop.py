"""Windows app window whose lifetime owns the local Lernapp services."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from lernapp_launcher import cli

TITLE = "Lernapp – Deutsch lernen"
START_HTML = """<!doctype html><html lang="de"><meta charset="utf-8"><style>
body{font:20px system-ui;background:#f6f8fb;color:#20354a;margin:12%}h1{font-size:32px}
</style><h1>Lernapp startet …</h1><p>Deine Materialien werden geöffnet.</p>
<p>Beim ersten Start kann das etwas dauern.</p></html>"""
ERROR_HTML = """<!doctype html><html lang="de"><meta charset="utf-8"><style>
body{font:20px system-ui;margin:10%;color:#20354a}</style><h1>Lernapp konnte nicht starten</h1>
<p>Bitte schließe dieses Fenster und starte Lernapp erneut. Deine gespeicherten Lerndaten bleiben erhalten.</p>
<p>Falls der Fehler bleibt, findest du technische Details im Lernapp-Datenordner unter logs.</p></html>"""


def hidden_options():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def console_python():
    return Path(sys.executable).with_name("python.exe") if os.name == "nt" else Path(sys.executable)


def refresh_shortcuts():
    """Also runs after updates, so old console shortcuts become windowed shortcuts."""
    if os.name != "nt":
        return
    script = cli.repo_root() / "packaging/windows/desktop_shortcuts.ps1"
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-AppDir",
            str(cli.repo_root()),
        ],
        capture_output=True,
        timeout=30,
        check=True,
        **hidden_options(),
    )


class Session:
    def __init__(self, command=None):
        self.command = command or [str(console_python()), "-m", "lernapp_launcher.cli", "start", "--no-browser"]
        self.process = None
        self.owner = None
        self.finished = threading.Event()
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.finished.is_set():
                return
            state = cli.running_state()
            if state and cli.health(state.api_port):
                self.owner = state.launcher_pid
                return
            with (cli.logs_dir() / "desktop-services.log").open("ab") as log:
                self.process = subprocess.Popen(
                    self.command,
                    cwd=cli.repo_root(),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    **hidden_options(),
                )
            self.owner = self.process.pid

    def state(self):
        state = cli.read_state()
        return state if state and state.launcher_pid == self.owner else None

    def stop(self):
        self.finished.set()
        with self.lock:
            # Closing during first startup must not leave services launched a moment later.
            deadline = time.monotonic() + 10
            while self.process and self.process.poll() is None and not self.state() and time.monotonic() < deadline:
                time.sleep(0.1)
            state = self.state()
            if state:
                cli._stop_state(state)
            if self.process and self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                            **hidden_options(),
                        )
                    else:
                        self.process.terminate()


def show_window(webview, session):
    window = webview.create_window(TITLE, html=START_HTML, width=1180, height=850, min_size=(760, 560))
    closing = threading.Event()
    allow_close = threading.Event()

    def close_sequence():
        try:
            # Blur text fields while the UI and API are still alive (Streamlit on_change).
            window.evaluate_js("if(document.activeElement){document.activeElement.blur();}")
            time.sleep(1)
        except Exception:
            pass
        session.stop()
        allow_close.set()
        window.destroy()

    def on_closing():
        if allow_close.is_set():
            return True
        if not closing.is_set():
            closing.set()
            threading.Thread(target=close_sequence, daemon=True).start()
        return False

    def run():
        try:
            session.start()
            deadline = time.monotonic() + cli.HEALTH_TIMEOUT_S + cli.UI_TIMEOUT_S
            while not closing.is_set() and time.monotonic() < deadline:
                state = session.state()
                if state and cli.health(state.api_port) and cli.port_open(state.ui_port):
                    window.load_url(state.ui_url)
                    break
                if session.process and session.process.poll() is not None:
                    raise RuntimeError("Launcher exited during startup")
                time.sleep(0.25)
            else:
                if closing.is_set():
                    return
                raise TimeoutError("Startup timed out")
            while not closing.wait(0.25):
                state = session.state()
                if not state or not cli.pid_alive(session.owner):
                    # An update or the Stop shortcut shut down this instance. Do not stop a replacement.
                    allow_close.set()
                    window.destroy()
                    return
        except Exception:
            if not closing.is_set():
                window.load_html(ERROR_HTML)
                session.stop()

    window.events.closing += on_closing
    window.events.closed += session.finished.set
    try:
        webview.start(run, gui="edgechromium", private_mode=False, storage_path=str(cli.data_dir() / "webview"))
    finally:
        session.stop()


def single_instance():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = "Local\\LernappDesktop-" + hashlib.sha256(str(cli.data_dir()).casefold().encode()).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("Could not create desktop mutex")
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        user = ctypes.WinDLL("user32")
        user.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user.FindWindowW.restype = ctypes.c_void_p
        user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        window = user.FindWindowW(None, TITLE)
        if window:
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
        return None
    return kernel, handle


def main():
    if os.name != "nt":
        return cli.main()
    with (
        (cli.logs_dir() / "desktop.log").open("a", encoding="utf-8") as log,
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        if "stop" in sys.argv[1:]:
            return cli.cmd_stop(None)
        mutex = single_instance()
        if mutex is None:
            return 0
        registration = cli.run_dir() / "desktop.json"
        registration.write_text(json.dumps({"pid": os.getpid()}))
        try:
            import webview

            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.settings["ALLOW_FILE_URLS"] = False
            refresh_shortcuts()
            show_window(webview, Session())
            return 0
        except Exception:
            import traceback

            traceback.print_exc()
            ctypes.windll.user32.MessageBoxW(
                None,
                "Lernapp konnte das App-Fenster nicht öffnen. Bitte die Installation reparieren und Microsoft Edge WebView2 Runtime prüfen. Deine Lerndaten bleiben erhalten.",
                "Lernapp",
                0x10,
            )
            return 1
        finally:
            registration.unlink(missing_ok=True)
            mutex[0].CloseHandle(mutex[1])


if __name__ == "__main__":
    raise SystemExit(main())
