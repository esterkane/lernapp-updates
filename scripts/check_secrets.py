#!/usr/bin/env python3
"""Literal-secret gate (cd15828 secret-leak pattern): fails CI when a config/code file carries a real key.

Same heuristics as ``.claude/hooks/check_secrets_on_write.py``: a ``token|key|secret|password|…``
assignment whose value looks like a literal credential (known prefix such as ``sk-``/``AIza``/``ghp_``
or ≥ 32 chars) instead of ``${VAR}`` / a placeholder. Scans ``.mcp.json``, ``.env.example``,
``config/``, ``frontend/``, ``backend/`` (and any extra paths given). Exit 1 on the first hit.

Usage: ``uv run python scripts/check_secrets.py [paths…]``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = (
    ".mcp.json",
    ".env.example",
    "config",
    "frontend",
    "backend/app",
    "launcher",
    "mcp",
)  # tests use synthetic keys on purpose
SCAN_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".example", ".cfg", ".ini", ".env"}
SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}

PREFIXES = ("sk-", "sk-ant-", "ghp_", "gho_", "github_pat_", "AKIA", "xoxb-", "xoxp-", "AIza")
KEY = re.compile(r"(token|key|secret|password|passwd|credential)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{12,})", re.I)
PLACEHOLDERS = {"CHANGEME", "YOUR_KEY_HERE", "REPLACE_ME", "PLACEHOLDER"}


def is_literal_secret(value: str) -> bool:
    if value.startswith("${") or value.upper() in PLACEHOLDERS:
        return False
    return value.startswith(PREFIXES) or len(value) >= 32


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """``(line_no, key_name, masked_value)`` for every suspicious assignment."""
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in KEY.finditer(line):
            val = m.group(2)
            if is_literal_secret(val):
                hits.append((i, m.group(1), val[:4] + "…" + val[-2:]))
    return hits


def iter_files(targets: list[Path]) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        if t.is_file():
            out.append(t)
        elif t.is_dir():
            for f in t.rglob("*"):
                if f.is_file() and f.suffix in SCAN_SUFFIXES and not (set(f.parts) & SKIP_DIRS):
                    out.append(f)
    return out


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [ROOT / t for t in DEFAULT_TARGETS]
    problems = 0
    for f in iter_files(targets):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, key, masked in scan_text(text):
            problems += 1
            print(
                f"SECRET LEAK SUSPECTED {f}:{line_no}: '{key}' has a literal value ({masked}). Use ${{VAR}} and .env."
            )
    if problems:
        print(f"{problems} suspicious literal(s) found.", file=sys.stderr)
        return 1
    print("check_secrets: no literal secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
