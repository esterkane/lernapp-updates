#!/usr/bin/env bash
# Start the dev stack: FastAPI with --reload + Streamlit with runOnSave, logs on the console.
# Uses the launcher (`lernapp start --dev`), which also boots the embedded Postgres (or uses
# DATABASE_URL). Ctrl+C stops everything. Extra args are passed through (e.g. --no-browser).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -d .venv ] || { echo "no .venv – run scripts/bootstrap.sh first" >&2; exit 1; }
exec uv run lernapp start --dev "$@"
