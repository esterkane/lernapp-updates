# scripts/ — developer tooling

All setup lives here (user preference: executable scripts over manual command lists).
End users never run these — they use the installers in `packaging/` (see `packaging/README.md`).

| Script | Purpose |
|---|---|
| `bootstrap.sh [--docker] [--no-models]` | dev setup: installs `uv` if missing, Python 3.12, `uv sync` (with dev group), copies `.env.example` → `.env`, downloads the small faster-whisper model, runs `verify_models.py --offline` and `lernapp doctor`. `--docker` starts Postgres via `docker compose` and prints the `DATABASE_URL` to use (optional per ADR-0018; the default is the embedded Postgres). |
| `run_dev.sh` | `uv run lernapp start --dev`: API with `--reload`, Streamlit with `runOnSave`, logs on the console, browser opens. Ctrl+C stops API, UI and the embedded DB. |
| `verify_models.py [--non-blocking] [--offline]` | checks every model id in `config/models.yaml` against the LiteLLM registry (and the provider APIs when keys are set). CI runs it non-blocking. |
| `gen_notices.py` | generates `THIRD_PARTY_NOTICES.md` (ADR-0015). |
| `export_learner_data.py` | learner data export as JSON/CSV (ADR-0011). |
| `purge_audio.py` | deletes retained audio / rotates the audio encryption key. |
| `weekly_digest.py [--demo]` | writes `reports/digest-<YYYY-WW>.md` (learner progress + exact ledger figures; summary only, never sends). `--demo` seeds fake data (requires `LLM_BACKEND=fake`). |
| `check_secrets.py` | literal-secret gate (CI, blocking) on `.mcp.json`, `.env.example`, `config/`, code. |
| `export_spotchecks.py` | exports human-labelled spot-checks as golden-set JSONL (ADR-0017). |

Launcher commands (`uv run lernapp …` in a checkout, `lernapp …` in an installed app):
`start [--dev] [--no-browser]`, `stop`, `status`, `open`, `doctor`, `download-models --size small|medium|large-v3-turbo`, `reset-db`, `version`.

Installer builds: `make pkg-macos`, `make pkg-linux`, `make pkg-windows-zip` (or `make release-local`), outputs in `dist/`.
The Windows `Setup.exe` is built only in CI (`.github/workflows/release.yml`, Inno Setup).
