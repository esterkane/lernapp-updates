#!/usr/bin/env python
"""Generate THIRD_PARTY_NOTICES.md from the installed environment (ADR-0015)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GPL_MARKERS = ("GPL", "AGPL")


def embedded_db_notices() -> list[str]:
    """pgserver bundles PostgreSQL + pgvector binaries; pip-licenses cannot see their licences."""
    out: list[str] = []
    try:
        import importlib.metadata as im

        dist = im.distribution("pgserver")
        lic_text = dist.read_text("LICENSE") or dist.read_text("licenses/LICENSE") or ""
        first = next((ln.strip() for ln in lic_text.splitlines() if ln.strip()), "see wheel LICENSE")
        out.append(
            f"- pgserver {dist.version} — {first} — https://github.com/orm011/pgserver (wheel bundles the binaries below)"
        )
        pginstall = Path(str(dist.locate_file("pgserver"))) / "pginstall"
        pg_version = "16.x"
        try:
            import subprocess as sp

            pg_version = (
                sp.run(
                    [str(pginstall / "bin" / "postgres"), "--version"], capture_output=True, text=True, check=False
                ).stdout.strip()
                or pg_version
            )
        except OSError:
            pass
        vec = sorted((pginstall / "share" / "postgresql" / "extension").glob("vector--*.sql"))
        vec_version = vec[-1].name.split("--")[-1].removesuffix(".sql") if vec else "?"
        out.append(
            f"- PostgreSQL server binaries ({pg_version}) — PostgreSQL License (permissive, BSD-like) — https://www.postgresql.org/about/licence/"
        )
        out.append(
            f"- pgvector extension (up to {vec_version}) — PostgreSQL License — https://github.com/pgvector/pgvector"
        )
    except Exception as exc:  # noqa: BLE001
        out.append(f"- pgserver not installed in this environment ({exc})")
    return out


def main() -> int:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--format=json",
            "--with-urls",
            "--with-license-file",
            "--no-license-path",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        print("pip-licenses not available: uv sync --group dev", file=sys.stderr)
        print(out.stderr, file=sys.stderr)
        return 1
    pkgs = json.loads(out.stdout)
    lines = [
        "# Third-party notices",
        "",
        f"Generated {datetime.now(UTC).date().isoformat()} by scripts/gen_notices.py from the installed environment.",
        "GPL components are optional extras and are never part of the default build (ADR-0015).",
        "",
        "| Package | Version | Licence | URL |",
        "|---|---|---|---|",
    ]
    gpl = []
    for p in sorted(pkgs, key=lambda x: x["Name"].lower()):
        lic = p.get("License", "UNKNOWN")
        if any(m in lic.upper() for m in GPL_MARKERS) and "LGPL" not in lic.upper():
            gpl.append(p["Name"])
        lines.append(f"| {p['Name']} | {p['Version']} | {lic} | {p.get('URL', '')} |")
    lines += ["", "## Embedded database components (ADR-0018)", ""]
    lines += embedded_db_notices()
    if gpl:
        lines += ["", "## GPL-licensed packages present in this environment (must be optional extras)", ""]
        lines += [f"- {g}" for g in gpl]
    (ROOT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote THIRD_PARTY_NOTICES.md ({len(pkgs)} packages, GPL: {gpl or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
