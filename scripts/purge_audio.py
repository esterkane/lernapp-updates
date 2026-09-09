#!/usr/bin/env python
"""Purge encrypted audio past retention (ADR-0011). ``--new-key`` prints a fresh Fernet key."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-key", action="store_true")
    args = ap.parse_args()
    if args.new_key:
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
        return 0
    from app.core.db import init_db
    from app.services.audio import purge_expired_audio

    init_db()
    print(f"purged {purge_expired_audio()} expired audio file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
