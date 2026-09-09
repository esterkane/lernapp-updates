#!/usr/bin/env bash
# Developer bootstrap (end users: see packaging/README.md — the installers do all of this).
#
#   scripts/bootstrap.sh            uv sync (incl. dev group), STT model, model-id check
#   scripts/bootstrap.sh --docker   additionally start Postgres via docker compose and print DATABASE_URL
#   scripts/bootstrap.sh --no-models   skip the faster-whisper download
#
# The embedded PostgreSQL (ADR-0018) needs no Docker; DATABASE_URL in .env switches to compose.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

USE_DOCKER=0
MODELS=1
for arg in "$@"; do
  case "$arg" in
    --docker) USE_DOCKER=1;;
    --no-models) MODELS=0;;
    -h|--help) sed -n '2,9p' "$0"; exit 0;;
    *) echo "unknown option: $arg" >&2; exit 2;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

step "uv"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found – installing to ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

step "Python 3.12 + dependencies (incl. dev group)"
uv python install 3.12
uv sync --python 3.12

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example – add your API keys there"
fi

if [ "$USE_DOCKER" = 1 ]; then
  step "Postgres via docker compose"
  docker compose up -d
  echo "waiting for the database …"
  for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U lernapp -d lernapp >/dev/null 2>&1; then break; fi
    sleep 2
  done
  cat <<'MSG'

Set this in .env (or export it) to use the compose database instead of the embedded one:
  DATABASE_URL=postgresql://lernapp:lernapp@localhost:5432/lernapp
Migrations run automatically at API start (alembic upgrade head).
MSG
fi

if [ "$MODELS" = 1 ]; then
  step "faster-whisper model (small) – ~500 MB, once"
  uv run lernapp download-models --size small || echo "WARNING: model download failed – retry later with: uv run lernapp download-models --size small"
fi

step "model-id check (non-blocking, offline)"
uv run python scripts/verify_models.py --non-blocking --offline || true

step "doctor"
uv run lernapp doctor || true

cat <<'MSG'

Done. Next steps:
  scripts/run_dev.sh          API (--reload) + Streamlit, browser opens
  uv run pytest -q            tests
  make lint                   ruff + mypy
MSG
