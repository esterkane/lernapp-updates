"""English system locale must preserve German resources and both packaged manuals."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_english_locale_installer_resources(tmp_path: Path) -> None:
    # Exercise the real installer payload in a Unicode path, without a database or API calls.
    staged = tmp_path / "Lernapp – Übungen"
    env = {
        **os.environ,
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "LERNAPP_ROOT": str(staged),
        "PYTHONPATH": str(ROOT / "backend"),
    }
    subprocess.run(
        [sys.executable, str(ROOT / "packaging/common/source_payload.py"), "stage", str(staged)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    code = """
import locale
from pathlib import Path
from app.core.paths import repo_root
from app.core.rubrics import error_tag_vocabulary, redemittel_path
locale.setlocale(locale.LC_ALL, "")
assert locale.getlocale()[0] == "en_US", locale.getlocale()
root = repo_root()
assert "grammatik/kasus" in error_tag_vocabulary()
assert "Eröffnung" in redemittel_path().read_text(encoding="utf-8")
english = (root / "docs/User-Manual.md").read_text(encoding="utf-8")
german = (root / "docs/Benutzerhandbuch.md").read_text(encoding="utf-8")
assert "[Deutsch](Benutzerhandbuch.md)" in english
assert "[English](User-Manual.md)" in german
assert "Zurück zur Übersicht" in english and "Zurück zur Übersicht" in german
assert "0.85×" in english and "0,85×" in german
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
