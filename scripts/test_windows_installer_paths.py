"""Execute the real installer preflight under native Windows PowerShell 5.1."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
script = (root / 'packaging/windows/install.ps1').read_text(encoding='utf-8-sig')
preflight = script.split('# ---------------------------------------------------------------- 1. uv')[0]
# Stop before any download or install: execute the actual path binding and validation.
preflight += '\n@{source=$SourceDir; destination=$AppDir} | ConvertTo-Json -Compress\nexit 0\n'


def run(path, *args, env=None):
    return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(path), *args],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', env=env, timeout=30)


def fixture(folder):
    folder.mkdir(parents=True)
    for name in ('pyproject.toml', 'uv.lock'):
        (folder / name).write_text('test')


with tempfile.TemporaryDirectory(prefix='Lernapp Gaby Ant ') as temporary:
    base = Path(temporary)
    install = base / 'Unpacked installer'
    fixture(install / 'src')
    path = install / 'install.ps1'
    path.write_text(preflight, encoding='utf-8-sig')
    for env in (None, {**os.environ, 'LOCALAPPDATA': ''}):
        r = run(path, env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        values = json.loads(r.stdout.strip().splitlines()[-1])
        assert Path(values['source']).samefile(install / 'src'), values
        assert Path(values['destination']).is_absolute()
    explicit = base / 'Explicit app'
    fixture(explicit)
    r = run(path, '-SourceDir', str(explicit), '-AppDir', str(explicit), '-NoShortcuts')
    assert r.returncode == 0, r.stdout + r.stderr
    values = json.loads(r.stdout.strip().splitlines()[-1])
    assert Path(values['source']).samefile(explicit) and Path(values['destination']).samefile(explicit), values
    installed = base / 'Installed Lernapp'
    fixture(installed / 'app')
    (installed / 'installer').mkdir()
    repair = installed / 'installer/install.ps1'
    repair.write_text(preflight, encoding='utf-8-sig')
    r = run(repair)
    assert r.returncode == 0, r.stdout + r.stderr
    assert Path(json.loads(r.stdout.strip().splitlines()[-1])['source']).samefile(installed / 'app')
    incomplete = base / 'Temp ZIP folder'
    incomplete.mkdir()
    broken = incomplete / 'install.ps1'
    broken.write_text(preflight, encoding='utf-8-sig')
    r = run(broken)
    assert r.returncode == 1 and 'Alle extrahieren' in r.stdout, r.stdout + r.stderr
    assert 'Cannot bind' not in r.stdout + r.stderr
print('Windows PowerShell installer defaults, explicit paths, repair and incomplete ZIP checks passed.')
