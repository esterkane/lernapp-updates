#!/usr/bin/env python
"""Export a learner's data as JSON + CSV files (ADR-0011, product rule 9).

Usage:
    uv run python scripts/export_learner_data.py --learner default --out ./export/

Writes learner.json, sessions.json, results.json, documents.json, vocab.csv, progress.csv,
costs.csv into ``--out`` (same content as ``GET /learners/{id}/export``, unpacked).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lernende(n)-Daten exportieren (JSON + CSV)")
    parser.add_argument("--learner", default="default", help="learner id (default: 'default')")
    parser.add_argument("--out", default="./export", help="output directory (default: ./export)")
    args = parser.parse_args(argv)

    from app.core.db import init_db
    from app.services.privacy import write_export

    init_db()
    try:
        written = write_export(args.learner, Path(args.out))
    except KeyError:
        print(f"Lernende nicht gefunden: {args.learner}", file=sys.stderr)
        return 1
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
