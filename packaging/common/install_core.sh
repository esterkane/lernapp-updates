#!/usr/bin/env bash
# Shared installer logic for macOS and Linux (called by the .pkg postinstall and the Linux
# self-extracting installer). Runs as the *end user*, never as root.
#
#   install_core.sh APP_DIR SRC_DIR
#
#   APP_DIR  where the app source + virtualenv end up
#            (macOS: ~/Library/Application Support/Lernapp/app, Linux: ~/.local/share/Lernapp/app)
#   SRC_DIR  unpacked source tree (must contain pyproject.toml and uv.lock)
#
# Steps: install uv (if missing) → uv python install 3.12 → copy source → uv sync --frozen --no-dev.
# Re-running updates the app in place; the data directory (<APP_DIR>/..) is never touched.
# Optional env: LERNAPP_DOWNLOAD_MODELS=1 also pre-downloads the small faster-whisper model.
set -euo pipefail

APP_DIR="${1:-}"
SRC_DIR="${2:-}"

die() { printf '\nFEHLER: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

[ -n "$APP_DIR" ] && [ -n "$SRC_DIR" ] || die "Aufruf: install_core.sh APP_DIR SRC_DIR"
[ -f "$SRC_DIR/pyproject.toml" ] || die "Quellverzeichnis ungültig (pyproject.toml fehlt): $SRC_DIR"
[ -f "$SRC_DIR/uv.lock" ] || die "Quellverzeichnis ungültig (uv.lock fehlt): $SRC_DIR"
[ "$(id -u)" -ne 0 ] || printf 'WARNUNG: install_core.sh läuft als root – die App gehört dann root.\n' >&2

HOME="${HOME:-$(eval echo "~$(id -un)")}"
export HOME
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_PREFERENCE="${UV_PYTHON_PREFERENCE:-managed}"

OS="$(uname -s)"
ARCH="$(uname -m)"
printf 'Lernapp-Installation\n  System:  %s %s\n  Ziel:    %s\n  Quelle:  %s\n' "$OS" "$ARCH" "$APP_DIR" "$SRC_DIR"

# ---------------------------------------------------------------- 1. uv ----------------------
step "uv (Python-Paketmanager) prüfen"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv nicht gefunden – wird nach $HOME/.local/bin installiert (Internetverbindung nötig) …"
  mkdir -p "$HOME/.local/bin"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" sh \
      || die "uv konnte nicht installiert werden (Internetverbindung? Proxy?)."
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" sh \
      || die "uv konnte nicht installiert werden (Internetverbindung? Proxy?)."
  else
    die "Weder curl noch wget gefunden. Bitte curl installieren (z. B. 'sudo apt install curl')."
  fi
  hash -r 2>/dev/null || true
fi
command -v uv >/dev/null 2>&1 || die "uv ist nach der Installation nicht auffindbar (PATH: $PATH)."
echo "uv: $(uv --version)"

# ---------------------------------------------------------------- 2. Python 3.12 -------------
step "Python 3.12 bereitstellen"
uv python install 3.12 || die "Python 3.12 konnte nicht heruntergeladen werden (Internetverbindung?)."

# ---------------------------------------------------------------- 3. Quellcode kopieren ------
step "App-Dateien nach $APP_DIR kopieren"
mkdir -p "$APP_DIR" || die "Kann $APP_DIR nicht anlegen (Schreibrechte?)."
EXCLUDES=(.git .venv data dist build __pycache__ .mypy_cache .pytest_cache .ruff_cache .env lernapp-kit
          node_modules .DS_Store)
if command -v rsync >/dev/null 2>&1; then
  RSYNC_ARGS=(-a --delete)
  for e in "${EXCLUDES[@]}"; do RSYNC_ARGS+=(--exclude "$e"); done
  rsync "${RSYNC_ARGS[@]}" "$SRC_DIR"/ "$APP_DIR"/ || die "Kopieren fehlgeschlagen."
else
  # No rsync: wipe everything except the virtualenv, then copy via tar.
  find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +
  TAR_ARGS=()
  for e in "${EXCLUDES[@]}"; do TAR_ARGS+=(--exclude "$e"); done
  (cd "$SRC_DIR" && tar "${TAR_ARGS[@]}" -cf - .) | (cd "$APP_DIR" && tar -xf -) \
    || die "Kopieren fehlgeschlagen."
fi

# ---------------------------------------------------------------- 4. Abhängigkeiten ----------
step "Python-Abhängigkeiten installieren (uv sync – beim ersten Mal einige Minuten, ~1–2 GB)"
cd "$APP_DIR"
if ! uv sync --frozen --inexact --no-dev --python 3.12; then
  die "uv sync fehlgeschlagen. Internetverbindung prüfen und den Installer erneut ausführen."
fi

LERNAPP_BIN="$APP_DIR/.venv/bin/lernapp"
[ -x "$LERNAPP_BIN" ] || die "Startprogramm fehlt nach der Installation: $LERNAPP_BIN"
"$LERNAPP_BIN" version >/dev/null || die "Das Startprogramm lässt sich nicht ausführen."

if [ "$OS" = "Linux" ] && [ "$ARCH" != "x86_64" ]; then
  cat <<'NOTE'

HINWEIS: Für Linux auf ARM (aarch64) gibt es keine eingebettete PostgreSQL-Datenbank (pgserver).
Bitte eine eigene PostgreSQL-16-Datenbank mit pgvector bereitstellen und in
~/.local/share/Lernapp/.env eintragen, z. B.:
  DATABASE_URL=postgresql://lernapp:lernapp@localhost:5432/lernapp
NOTE
fi

# ---------------------------------------------------------------- 5. optional: STT-Modell ----
if [ "${LERNAPP_DOWNLOAD_MODELS:-0}" = "1" ]; then
  step "Spracherkennungs-Modell (faster-whisper small) herunterladen"
  "$LERNAPP_BIN" download-models --size small || echo "WARNUNG: Modell-Download fehlgeschlagen – später mit 'lernapp download-models --size small' nachholen."
fi

# ---------------------------------------------------------------- fertig ---------------------
cat <<DONE

================================================================================
Lernapp $("$LERNAPP_BIN" version | awk '{print $2}') wurde installiert.

  Programm:   $APP_DIR
  Starten:    $LERNAPP_BIN start
              (oder über das App-Symbol / den Menüeintrag „Lernapp“)
  Beenden:    $LERNAPP_BIN stop   – oder Strg+C im Startfenster
  Prüfen:     $LERNAPP_BIN doctor

Beim ersten Start wird die Datenbank angelegt (ca. 30–60 Sekunden), danach öffnet sich der
Browser automatisch. API-Schlüssel und Einstellungen werden in der App unter „Einrichtung“
eingetragen. Für die lokale Spracherkennung einmalig ausführen:
  $LERNAPP_BIN download-models --size small
================================================================================
DONE
