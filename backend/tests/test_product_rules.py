"""Product rules 1, 6 (CLAUDE.md): no TDN grade anywhere; no pronunciation percentages."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import TDN_MAPPING_ENABLED
from app.core.paths import repo_root

TDN_RE = re.compile(r"TDN\s?[3-5]")
PCT_RE = re.compile(r"\d{1,3}\s?%\s?(korrekt|richtig)", re.I)

SCAN_DIRS = ["prompts", "frontend", "backend/app", "scenarios", "blueprints", "config"]


def _files() -> list[Path]:
    root = repo_root()
    out: list[Path] = []
    for d in SCAN_DIRS:
        p = root / d
        if p.exists():
            out += [
                f for f in p.rglob("*") if f.suffix in (".py", ".md", ".yaml", ".yml", ".txt", ".toml") and f.is_file()
            ]
    return out


def test_tdn_mapping_disabled() -> None:
    assert TDN_MAPPING_ENABLED is False


def test_no_tdn_anywhere() -> None:
    hits = []
    for f in _files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if TDN_RE.search(line):
                hits.append(f"{f}:{i}: {line.strip()}")
    assert not hits, "TDN levels must never appear:\n" + "\n".join(hits)


def test_no_pronunciation_percentages_in_prompts_and_ui() -> None:
    hits = []
    for f in _files():
        if f.suffix == ".py" and "tests" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if PCT_RE.search(line):
                hits.append(f"{f}:{i}: {line.strip()}")
    assert not hits, "no percentage pronunciation scores (product rule 6):\n" + "\n".join(hits)


def test_internal_scale_label() -> None:
    from app.core.rubrics import internal_scale

    assert internal_scale(28) == 20
    assert internal_scale(0) == 0
    assert internal_scale(14) == 10
