#!/usr/bin/env bash
# Build dist/lernapp-<version>-linux-installer.sh — a self-extracting bash installer
# (script header + __ARCHIVE__ marker + tar.gz payload). Works on any Linux with bash, tar and
# curl/wget. Payload: src/ (source tree), install_core.sh, lernapp.png, setup.sh.
#
#   lernapp-<v>-linux-installer.sh                 install/update for the current user
#   lernapp-<v>-linux-installer.sh --extract-only DIR   just unpack the payload
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${VERSION:-$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$ROOT/pyproject.toml" | head -1)}"
[ -n "$VERSION" ] || { echo "version not found in pyproject.toml" >&2; exit 1; }
BUILD="$ROOT/build/linux"
DIST="$ROOT/dist"
PY="${PYTHON:-python3}"
OUT="$DIST/lernapp-$VERSION-linux-installer.sh"

echo "==> Building $OUT"
rm -rf "$BUILD"
mkdir -p "$BUILD/payload/src" "$DIST"

echo "==> Packing source tree"
"$PY" "$ROOT/packaging/common/source_payload.py" stage "$BUILD/payload/src" --root "$ROOT" --version "$VERSION"
cp "$ROOT/packaging/common/install_core.sh" "$BUILD/payload/install_core.sh"
chmod 755 "$BUILD/payload/install_core.sh"
"$PY" "$ROOT/packaging/common/make_icon.py" --png "$BUILD/payload/lernapp.png" --size 256 >/dev/null 2>&1 \
  || echo "    WARNING: icon generation failed – installing without icon"

# ---------------------------------------------------------------- per-user setup (in payload)
cat > "$BUILD/payload/setup.sh" <<'SETUP'
#!/usr/bin/env bash
# Runs from the extracted payload directory: install_core.sh + shortcut + .desktop entry.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/Lernapp/app"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="$DATA_HOME/icons/hicolor/256x256/apps"
DESKTOP_DIR="$DATA_HOME/applications"

if [ "$(id -u)" -eq 0 ]; then
  echo "Bitte NICHT mit sudo/als root ausführen – Lernapp wird für den aktuellen Benutzer installiert." >&2
  exit 1
fi

bash "$HERE/install_core.sh" "$APP_DIR" "$HERE/src"

echo
echo "==> Verknüpfungen anlegen"
mkdir -p "$BIN_DIR" "$DESKTOP_DIR"
ln -sfn "$APP_DIR/.venv/bin/lernapp" "$BIN_DIR/lernapp"
ICON_LINE="Icon=lernapp"
if [ -f "$HERE/lernapp.png" ]; then
  mkdir -p "$ICON_DIR"
  cp "$HERE/lernapp.png" "$ICON_DIR/lernapp.png"
  ICON_LINE="Icon=$ICON_DIR/lernapp.png"
fi
cat > "$DESKTOP_DIR/lernapp.desktop" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=Lernapp
Comment=TestDaF-Vorbereitung und Verhandlungsdeutsch mit KI
Exec=$APP_DIR/.venv/bin/lernapp start
Path=$APP_DIR
$ICON_LINE
Terminal=false
Categories=Education;Languages;
Keywords=TestDaF;Deutsch;Lernen;
StartupNotify=false
DESK
chmod 644 "$DESKTOP_DIR/lernapp.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
if command -v xdg-desktop-menu >/dev/null 2>&1; then xdg-desktop-menu forceupdate >/dev/null 2>&1 || true; fi

case ":$PATH:" in *":$BIN_DIR:"*) ;; *)
  echo "Hinweis: $BIN_DIR ist nicht im PATH. Für den Terminal-Befehl 'lernapp' z. B. in ~/.bashrc ergänzen:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"";;
esac
echo "Menüeintrag: $DESKTOP_DIR/lernapp.desktop  (Anwendungsmenü → Bildung → Lernapp)"
echo "Terminal:    lernapp start | stop | status | doctor"
SETUP
chmod 755 "$BUILD/payload/setup.sh"

# ---------------------------------------------------------------- archive + header ----------
echo "==> Creating archive"
COPYFILE_DISABLE=1 tar -C "$BUILD/payload" -czf "$BUILD/payload.tar.gz" .

cat > "$BUILD/header.sh" <<HEADER
#!/usr/bin/env bash
# Lernapp $VERSION – Installer für Linux (selbstentpackend). Aufruf: bash \$0 [--extract-only DIR]
# Installiert für den aktuellen Benutzer nach ~/.local/share/Lernapp (Internetverbindung nötig).
set -euo pipefail
VERSION="$VERSION"
SELF="\$(readlink -f "\$0" 2>/dev/null || echo "\$0")"
EXTRACT_ONLY=""
case "\${1:-}" in
  --extract-only) EXTRACT_ONLY="\${2:?--extract-only braucht ein Zielverzeichnis}";;
  -h|--help)
    echo "Lernapp \$VERSION Installer"
    echo "  bash \$0                      installieren / aktualisieren (Benutzer, kein sudo)"
    echo "  bash \$0 --extract-only DIR   nur entpacken (src/, install_core.sh, setup.sh)"
    exit 0;;
  "") ;;
  *) echo "Unbekannte Option: \$1" >&2; exit 2;;
esac
ARCHIVE_LINE="\$(awk '/^__ARCHIVE__\$/ {print NR + 1; exit 0}' "\$SELF")"
[ -n "\$ARCHIVE_LINE" ] || { echo "Archiv nicht gefunden – Datei beschädigt?" >&2; exit 1; }
if [ -n "\$EXTRACT_ONLY" ]; then
  mkdir -p "\$EXTRACT_ONLY"
  tail -n +"\$ARCHIVE_LINE" "\$SELF" | tar -xz -C "\$EXTRACT_ONLY"
  echo "Entpackt nach: \$EXTRACT_ONLY"
  exit 0
fi
echo "Lernapp \$VERSION wird installiert …"
TMP="\$(mktemp -d "\${TMPDIR:-/tmp}/lernapp-installer.XXXXXX")"
trap 'rm -rf "\$TMP"' EXIT
tail -n +"\$ARCHIVE_LINE" "\$SELF" | tar -xz -C "\$TMP"
bash "\$TMP/setup.sh"
exit \$?
__ARCHIVE__
HEADER
cat "$BUILD/header.sh" "$BUILD/payload.tar.gz" > "$OUT"
chmod 755 "$OUT"
echo "==> done: $OUT ($(du -h "$OUT" | cut -f1))"
