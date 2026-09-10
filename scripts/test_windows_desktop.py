"""Real WebView2 window + hidden supervisor lifecycle smoke test on Windows."""
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'launcher'))


def main():
    import webview
    from lernapp_launcher import cli, desktop

    with tempfile.TemporaryDirectory(prefix='Lernapp desktop ') as folder:
        data = Path(folder)
        os.environ.update(LERNAPP_DATA_DIR=folder, LERNAPP_ROOT=str(ROOT))
        saved = data / 'saved-learning.json'
        saved.write_text('{"answers": [1, 2, 3]}')
        before = saved.read_bytes()
        child = data / 'supervisor.py'
        child.write_text('''import http.server, json, os, pathlib, threading, time
root = pathlib.Path(os.environ['LERNAPP_DATA_DIR']) / 'run'
root.mkdir(exist_ok=True)
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok"}')
 def log_message(self, *args): pass
server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
pid = os.getpid(); started = time.time()
state = dict(launcher_pid=pid,api_pid=pid,ui_pid=pid,api_port=server.server_port,ui_port=server.server_port,started_at=started)
(root / 'state.json').write_text(json.dumps(state))
threading.Thread(target=server.serve_forever, daemon=True).start()
while True:
 try:
  request = json.loads((root / 'stop-request.json').read_text())
  if request == dict(launcher_pid=pid,started_at=started): break
 except (OSError, ValueError): pass
 time.sleep(.1)
server.shutdown()
''')
        session = desktop.Session([sys.executable, str(child)])
        original_create = webview.create_window
        loaded = threading.Event()

        def create(*args, **kwargs):
            window = original_create(*args, **kwargs)
            original_load = window.load_url

            def load(url):
                original_load(url)
                loaded.set()
                threading.Timer(2, window.destroy).start()
            window.load_url = load
            return window

        webview.create_window = create
        # Prevent a hung GUI from consuming the entire Actions job.
        watchdog = threading.Timer(80, lambda: os._exit(2))
        watchdog.start()
        try:
            desktop.show_window(webview, session)
        finally:
            watchdog.cancel()
        assert loaded.is_set(), 'App window never loaded the local service'
        assert session.process.poll() is not None, 'Supervisor left running after window closed'
        assert not cli.read_state(), 'Service registration left behind'
        assert saved.read_bytes() == before, 'Saved learning data changed'
        # Ensure Windows receives a GUI-subsystem entry point (no console allocation).
        exe = Path(sys.executable).parent / 'lernapp-desktop.exe'
        content = exe.read_bytes()
        pe = int.from_bytes(content[60:64], 'little')
        assert int.from_bytes(content[pe + 24 + 68:pe + 24 + 70], 'little') == 2
        print('PASS: real WebView2 window, hidden GUI launcher, close stops services, saved data preserved')


if __name__ == '__main__':
    main()
