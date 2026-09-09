#!/usr/bin/env python3
"""Stage and verify the same source payload for every OS installer (stdlib only)."""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

EXCLUDED = {'.git', '.venv', 'data', 'dist', 'build', '__pycache__', '.mypy_cache',
            '.pytest_cache', '.ruff_cache', '.ci-data', '.env', 'node_modules', '.DS_Store', 'lernapp-kit'}
MANIFEST = 'SOURCE_MANIFEST.json'
PUBLIC_DIRS = {'backend', 'frontend', 'launcher', 'mcp', 'config', 'prompts', 'scenarios', 'blueprints', 'packaging', 'scripts', '.streamlit', 'evals'}
PUBLIC_FILES = {'README.md', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml', 'uv.lock', 'alembic.ini', 'Makefile', '.env.example', '.python-version', 'docker-compose.yml'}
PUBLIC_DOCS = {'docs/User-Manual.md', 'docs/Benutzerhandbuch.md', 'docs/Updates-veroeffentlichen.md'}


def files(root):
    for directory, dirs, names in os.walk(root):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED
                         and (base / d).relative_to(root).as_posix() != 'evals/reports')
        for name in sorted(names):
            if (name not in EXCLUDED and name != MANIFEST
                    and (base / name).relative_to(root).as_posix() != 'packaging/windows/lernapp.ico'):
                relative = (base / name).relative_to(root)
                if ('tests' in relative.parts or relative.parts[0] == 'notebooks'):
                    continue
                if relative.parts[0] in PUBLIC_DIRS or relative.as_posix() in PUBLIC_FILES | PUBLIC_DOCS:
                    yield base / name


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files(root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage', 'verify'])
    parser.add_argument('destination', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--version')
    args = parser.parse_args()
    root = args.root.resolve()
    dest = args.destination.resolve()
    version = re.search(r'^version\s*=\s*"([^"]+)"',
                        (root / 'pyproject.toml').read_text(), re.M)[1]
    if args.version and args.version != version:
        raise SystemExit(f'Installer version {args.version} differs from app version {version}')
    expected = {'version': version, 'files': hashes(root)}
    if args.action == 'stage':
        if dest == root or root.is_relative_to(dest):
            raise SystemExit('Destination must not contain the source root')
        dest.mkdir(parents=True, exist_ok=True)
        if any(dest.iterdir()):
            raise SystemExit(f'Staging destination must be empty: {dest}')
        for relative in expected['files']:
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(root / relative, target)
        (dest / MANIFEST).write_text(json.dumps(expected, indent=2, sort_keys=True) + '\n')
    actual = hashes(dest)
    if actual != expected['files']:
        changed = sorted(k for k in actual.keys() | expected['files'].keys()
                         if actual.get(k) != expected['files'].get(k))
        raise SystemExit('Installer source mismatch: ' + ', '.join(changed))
    if json.loads((dest / MANIFEST).read_text()) != expected:
        raise SystemExit('Installer source manifest mismatch')
    print(f'Verified {len(actual)} source files for Lernapp {version}: {dest}')


if __name__ == '__main__':
    main()
