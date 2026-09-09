#!/usr/bin/env python3
"""Credential-handling audit for the BYOK feature (ADR-0019) — greps the repo for leak patterns.

Checks (each hit is a *finding* unless it is on the documented allowlist):
1. ``localStorage`` / ``sessionStorage`` anywhere under ``frontend/`` (keys must never live in the browser).
2. Provider env-name literals (``OPENAI_API_KEY`` …) under ``frontend/``: allowed only as quoted strings that
   name a settings field (listed as *info*); reading them from the process environment is a finding.
3. ``print(`` / ``log.<level>(`` calls under ``backend/app`` whose arguments contain a credential-like variable
   (``api_key``, ``key``, ``token``, ``secret``, ``password``, ``authorization``) outside string literals.
4. ``Authorization`` / ``x-api-key`` / ``x-goog-api-key`` header values in log calls, and provider URLs that
   carry the key as a query parameter (``?key=`` — httpx logs full URLs).
5. Error text that reaches usage events / ledger meta must pass ``credentials.redact`` — structural check on
   ``app/core/usage.py`` (the central choke point for ``meta["error"]``).
6. Plaintext provider keys must never be written to ``<data_dir>/.env`` by the settings API — structural check
   that ``write_env_file`` guards ``*_API_KEY`` names.

Usage: ``uv run python scripts/security_audit.py [--json]`` → exit 1 on any finding. ``run()`` is importable
for the pytest gate (``backend/tests/test_phase3_security.py``).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "lernapp-kit", "dist", "build", "node_modules", "__pycache__", ".git", ".mypy_cache"}
FRONTEND = ROOT / "frontend"
BACKEND_APP = ROOT / "backend" / "app"
SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".toml", ".yaml", ".yml", ".json"}

ENV_KEY_NAMES = ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "AZURE_API_KEY")
CRED_IDENT = re.compile(
    r"(?<![\w.])(api_key|key|token|secret|password|authorization|headers)(?![\w(])"
    r"|\.(api_key|headers|token|secret|password)\b"
)
LOG_CALL = re.compile(r"\b(?:print|(?:log|logger|logging)\.(?:debug|info|warning|warn|error|exception|critical))\(")
HEADER_WORDS = re.compile(r"(?i)authorization|x-api-key|x-goog-api-key")
URL_KEY_PARAM = re.compile(r"https?://[^\s\"']*[?&]key=")
STRING_LITERAL = re.compile(r"(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')")

# (relative path, substring that identifies the line, why it is not a credential)
ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    ("backend/app/core/usage.py", "key[:24]", "idempotency hash of a usage event, not a credential"),
)


@dataclass(frozen=True)
class Hit:
    check: str
    path: str
    line: int
    text: str
    severity: str = "finding"  # finding | info | allowed


def _files(base: Path, suffixes: set[str] = SUFFIXES) -> list[Path]:
    out: list[Path] = []
    if not base.exists():
        return out
    for f in sorted(base.rglob("*")):
        if f.is_file() and f.suffix in suffixes and not (set(f.parts) & SKIP_DIRS):
            out.append(f)
    return out


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _allowed(rel: str, line: str) -> str | None:
    for path, needle, reason in ALLOWLIST:
        if rel == path and needle in line:
            return reason
    return None


def _strip_strings(code: str) -> str:
    return STRING_LITERAL.sub('""', code)


def _call_span(text: str, start: int) -> str:
    """Source of a call from its opening paren to the matching close (handles nesting; strings stripped later)."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return text[start:]


def check_browser_storage() -> list[Hit]:
    hits: list[Hit] = []
    for f in _files(FRONTEND):
        for i, line in enumerate(_read(f).splitlines(), 1):
            if "localStorage" in line or "sessionStorage" in line:
                hits.append(Hit("browser_storage", _rel(f), i, line.strip()))
    return hits


def check_frontend_env_names() -> list[Hit]:
    hits: list[Hit] = []
    for f in _files(FRONTEND):
        for i, line in enumerate(_read(f).splitlines(), 1):
            if not any(n in line for n in ENV_KEY_NAMES):
                continue
            reads_env = re.search(r"os\.environ|getenv|environ\[", line) is not None
            quoted = any(f'"{n}"' in line or f"'{n}'" in line for n in ENV_KEY_NAMES)
            if reads_env or not quoted:
                hits.append(Hit("frontend_env_key", _rel(f), i, line.strip()))
            else:
                hits.append(Hit("frontend_env_key", _rel(f), i, line.strip(), severity="info"))
    return hits


def log_hits(text: str, rel: str) -> list[Hit]:
    """Log/print calls in ``text`` whose arguments (string literals removed) name a credential-like variable."""
    hits: list[Hit] = []
    lines = text.splitlines()
    for m in LOG_CALL.finditer(text):
        span = _call_span(text, m.end() - 1)
        line_no = text.count("\n", 0, m.start()) + 1
        first_line = lines[line_no - 1]
        code = _strip_strings(span)
        if CRED_IDENT.search(code) is None and HEADER_WORDS.search(code) is None:
            continue
        reason = _allowed(rel, first_line)
        shown = first_line.strip() + (f"  # allowed: {reason}" if reason else "")
        hits.append(Hit("log_credential_arg", rel, line_no, shown, "allowed" if reason else "finding"))
    return hits


def check_backend_log_calls() -> list[Hit]:
    hits: list[Hit] = []
    for f in _files(BACKEND_APP, {".py"}):
        hits.extend(log_hits(_read(f), _rel(f)))
    return hits


def check_key_in_url() -> list[Hit]:
    hits: list[Hit] = []
    for f in _files(BACKEND_APP, {".py"}) + _files(FRONTEND, {".py"}):
        for i, line in enumerate(_read(f).splitlines(), 1):
            if URL_KEY_PARAM.search(line):
                hits.append(Hit("key_in_url", _rel(f), i, line.strip()))
    return hits


def check_structural() -> list[Hit]:
    hits: list[Hit] = []
    usage = _read(BACKEND_APP / "core" / "usage.py")
    if "credentials.redact(" not in usage:
        hits.append(Hit("redact_missing", "backend/app/core/usage.py", 0, "record_usage does not redact meta['error']"))
    settings = _read(BACKEND_APP / "api" / "settings.py")
    guard_ok = (
        "def write_env_file" in settings and "is_provider_key_name" in settings.split("def write_env_file", 1)[-1]
    )
    if not guard_ok:
        hits.append(
            Hit("env_write_guard_missing", "backend/app/api/settings.py", 0, "write_env_file accepts *_API_KEY")
        )
    for f in _files(BACKEND_APP, {".py"}):
        text = _read(f)
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"open\(.*\.env|write_text\(", line) and "API_KEY" in line:
                hits.append(Hit("plaintext_key_write", _rel(f), i, line.strip()))
    return hits


def run() -> dict[str, object]:
    hits = [
        *check_browser_storage(),
        *check_frontend_env_names(),
        *check_backend_log_calls(),
        *check_key_in_url(),
        *check_structural(),
    ]
    findings = [h for h in hits if h.severity == "finding"]
    return {
        "findings": [asdict(h) for h in findings],
        "info": [asdict(h) for h in hits if h.severity != "finding"],
        "checks": ["browser_storage", "frontend_env_key", "log_credential_arg", "key_in_url", "structural"],
    }


def main(argv: list[str]) -> int:
    report = run()
    if "--json" in argv:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for h in report["info"]:  # type: ignore[union-attr]
            print(f"[{h['severity']}] {h['check']} {h['path']}:{h['line']}: {h['text']}")
        for h in report["findings"]:  # type: ignore[union-attr]
            print(f"[FINDING] {h['check']} {h['path']}:{h['line']}: {h['text']}")
    n = len(report["findings"])  # type: ignore[arg-type]
    print(f"security_audit: {n} finding(s).", file=sys.stderr if n else sys.stdout)
    return 1 if n else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
