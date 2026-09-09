#!/usr/bin/env bash
# Build dist/lernapp-<version>-windows.zip: source tree + install.cmd + install.ps1 + lernapp.ico.
# The user unpacks the zip and double-clicks install.cmd (no admin rights, internet needed).
# Runs on macOS/Linux (uses zip, falls back to python3 zipfile) and in CI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${VERSION:-$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$ROOT/pyproject.toml" | head -1)}"
[ -n "$VERSION" ] || { echo "version not found in pyproject.toml" >&2; exit 1; }
BUILD="$ROOT/build/windows"
DIST="$ROOT/dist"
PY="${PYTHON:-python3}"
NAME="Lernapp-$VERSION-windows"
OUT="$DIST/lernapp-$VERSION-windows.zip"

echo "==> Building $OUT"
rm -rf "$BUILD"
STAGE="$BUILD/$NAME"
mkdir -p "$STAGE/src" "$DIST"

echo "==> Packing source tree"
"$PY" "$ROOT/packaging/common/source_payload.py" stage "$STAGE/src" --root "$ROOT" --version "$VERSION"
cp "$ROOT/packaging/windows/install.ps1" "$ROOT/packaging/windows/install.cmd" "$ROOT/packaging/windows/Daten-uebernehmen.cmd" "$STAGE/"
"$PY" "$ROOT/packaging/common/make_icon.py" --ico "$STAGE/lernapp.ico" >/dev/null 2>&1 \
  || echo "    WARNING: icon generation failed – zip without lernapp.ico"
printf 'Lernapp %s fuer Windows 10/11 (64 Bit)\r\n\r\n1. Diesen Ordner entpacken (z. B. nach Dokumente).\r\n2. install.cmd doppelklicken. Bei der SmartScreen-Warnung: "Weitere Informationen" -> "Trotzdem ausfuehren".\r\n3. Warten, bis "wurde installiert" erscheint (Internet noetig, einige Minuten).\r\n4. Lernapp ueber die Desktop-/Startmenue-Verknuepfung starten.\r\n\r\nDetails: src\\packaging\\README.md\r\n' "$VERSION" > "$STAGE/LIESMICH.txt"

echo "==> Zipping"
rm -f "$OUT"
if command -v zip >/dev/null 2>&1; then
  (cd "$BUILD" && zip -qr "$OUT" "$NAME")
else
  "$PY" - "$BUILD" "$NAME" "$OUT" <<'PYZ'
import os, sys, zipfile
build, name, out = sys.argv[1:4]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for dirpath, _dirs, files in os.walk(os.path.join(build, name)):
        for f in files:
            full = os.path.join(dirpath, f)
            zf.write(full, os.path.relpath(full, build))
PYZ
fi
echo "==> done: $OUT ($(du -h "$OUT" | cut -f1))"
