#!/usr/bin/env bash
# Build dist/Lernapp-<version>-macos.pkg  (run on macOS; needs pkgbuild/productbuild/sips/iconutil).
#
# Payload:  /Applications/Lernapp.app  — a tiny launcher bundle (shell script) whose Resources
#           carry the source tree (src.tar.gz) and the shared installer (install_core.sh).
# Scripts:  postinstall runs install_core.sh AS THE LOGGED-IN USER (not root) and installs the
#           virtualenv into ~/Library/Application Support/Lernapp/app, then opens the app.
# The pkg is unsigned (Gatekeeper: right-click → Open). See packaging/README.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${VERSION:-$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$ROOT/pyproject.toml" | head -1)}"
[ -n "$VERSION" ] || { echo "version not found in pyproject.toml" >&2; exit 1; }
IDENTIFIER="de.lernapp.desktop"
BUILD="$ROOT/build/macos"
DIST="$ROOT/dist"
PY="${PYTHON:-python3}"
OUT="$DIST/Lernapp-$VERSION-macos.pkg"

echo "==> Building $OUT"
rm -rf "$BUILD"
APP="$BUILD/root/Applications/Lernapp.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$BUILD/scripts" "$BUILD/resources" "$DIST"

# ---------------------------------------------------------------- source tarball -------------
echo "==> Packing source tree"
"$PY" "$ROOT/packaging/common/source_payload.py" stage "$BUILD/src" --root "$ROOT" --version "$VERSION"
COPYFILE_DISABLE=1 tar -C "$BUILD/src" -czf "$APP/Contents/Resources/src.tar.gz" .
cp "$ROOT/packaging/common/install_core.sh" "$APP/Contents/Resources/install_core.sh"
chmod 755 "$APP/Contents/Resources/install_core.sh"

# ---------------------------------------------------------------- icon (best effort) ---------
echo "==> Icon"
ICONSET="$BUILD/Lernapp.iconset"
if "$PY" "$ROOT/packaging/common/make_icon.py" --png "$BUILD/icon-1024.png" --size 1024 >/dev/null 2>&1 \
   && mkdir -p "$ICONSET"; then
  ok=1
  for sz in 16 32 128 256 512; do
    sips -z "$sz" "$sz" "$BUILD/icon-1024.png" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1 || ok=0
    dbl=$((sz * 2))
    sips -z "$dbl" "$dbl" "$BUILD/icon-1024.png" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1 || ok=0
  done
  if [ "$ok" = 1 ] && iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Lernapp.icns" 2>/dev/null; then
    echo "    Lernapp.icns created"
  else
    echo "    WARNING: icon generation failed – building without icon"
    rm -f "$APP/Contents/Resources/Lernapp.icns"
  fi
else
  echo "    WARNING: python3 not available for the icon – building without icon"
fi
ICON_KEY=""
[ -f "$APP/Contents/Resources/Lernapp.icns" ] && ICON_KEY="  <key>CFBundleIconFile</key><string>Lernapp</string>"

# ---------------------------------------------------------------- Info.plist -----------------
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Lernapp</string>
  <key>CFBundleDisplayName</key><string>Lernapp</string>
  <key>CFBundleIdentifier</key><string>$IDENTIFIER</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>lernapp</string>
$ICON_KEY
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>MIT License</string>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------- launcher script ------------
cat > "$APP/Contents/MacOS/lernapp" <<'LAUNCH'
#!/bin/bash
# Lernapp.app entry point: starts the launcher from the per-user virtualenv.
APP_DIR="$HOME/Library/Application Support/Lernapp/app"
BIN="$APP_DIR/.venv/bin/lernapp"
LOGDIR="$HOME/Library/Logs/Lernapp"
mkdir -p "$LOGDIR"

dialog() {  # dialog TEXT BUTTONS DEFAULT  → prints the clicked button
  /usr/bin/osascript -e "display dialog \"$1\" with title \"Lernapp\" buttons {$2} default button \"$3\"" \
    -e 'button returned of result' 2>/dev/null
}

if [ ! -x "$BIN" ]; then
  dialog "Lernapp ist für diesen Benutzer noch nicht eingerichtet.\n\nBitte das Installationspaket (Lernapp-….pkg) erneut ausführen – die Einrichtung dauert einige Minuten und braucht eine Internetverbindung.\n\nErwartet wurde:\n$BIN" '"OK"' "OK" >/dev/null
  exit 1
fi

if "$BIN" status >/dev/null 2>&1; then
  "$BIN" open >/dev/null 2>&1
  choice=$(dialog "Lernapp läuft bereits – der Browser wurde geöffnet.\n\nZum Beenden auf „Lernapp beenden“ klicken." '"Lernapp beenden", "OK"' "OK")
  [ "$choice" = "Lernapp beenden" ] && "$BIN" stop >>"$LOGDIR/launcher.log" 2>&1
  exit 0
fi

nohup "$BIN" start >>"$LOGDIR/launcher.log" 2>&1 &
choice=$(dialog "Lernapp wird gestartet …\n\nDer Browser öffnet sich automatisch, sobald alles bereit ist (beim ersten Start ca. eine Minute – die Datenbank wird angelegt).\n\nDieses Fenster kann geschlossen werden; Lernapp läuft dann im Hintergrund weiter. Zum Beenden Lernapp erneut öffnen und „Lernapp beenden“ wählen." '"Lernapp beenden", "Im Hintergrund weiterlaufen"' "Im Hintergrund weiterlaufen")
if [ "$choice" = "Lernapp beenden" ]; then
  "$BIN" stop >>"$LOGDIR/launcher.log" 2>&1
fi
exit 0
LAUNCH
chmod 755 "$APP/Contents/MacOS/lernapp"

# ---------------------------------------------------------------- postinstall ----------------
cat > "$BUILD/scripts/postinstall" <<'POST'
#!/bin/bash
# Runs as root from Installer.app. Installs the Python environment for the logged-in user.
set -u
APP="/Applications/Lernapp.app"
RES="$APP/Contents/Resources"
LOG="/tmp/lernapp-install.log"

CONSOLE_USER="$(stat -f%Su /dev/console 2>/dev/null || true)"
if [ -z "$CONSOLE_USER" ] || [ "$CONSOLE_USER" = "root" ]; then
  CONSOLE_USER="${USER:-}"
  [ -n "${SUDO_USER:-}" ] && CONSOLE_USER="$SUDO_USER"
fi
if [ -z "$CONSOLE_USER" ] || [ "$CONSOLE_USER" = "root" ]; then
  echo "Kein angemeldeter Benutzer gefunden – bitte install_core.sh manuell ausführen." | tee -a "$LOG" >&2
  exit 1
fi
USER_HOME="$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
[ -n "$USER_HOME" ] || USER_HOME="$(eval echo "~$CONSOLE_USER")"
[ -d "$USER_HOME" ] || { echo "Home-Verzeichnis von $CONSOLE_USER nicht gefunden." | tee -a "$LOG" >&2; exit 1; }

chown -R "$CONSOLE_USER" "$APP" 2>/dev/null || true
{
  echo "=== $(date) Lernapp postinstall für $CONSOLE_USER ($USER_HOME)"
  /usr/bin/sudo -u "$CONSOLE_USER" -H /usr/bin/env HOME="$USER_HOME" USER="$CONSOLE_USER" LOGNAME="$CONSOLE_USER" \
    /bin/bash "$RES/install_user.sh" "$RES" "$USER_HOME"
  rc=$?
  echo "=== $(date) install_user.sh exit $rc"
  exit $rc
} 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  /usr/bin/sudo -u "$CONSOLE_USER" /usr/bin/osascript -e "display dialog \"Die Einrichtung von Lernapp ist fehlgeschlagen. Details stehen in $LOG und in $USER_HOME/Library/Logs/Lernapp/install.log.\n\nBitte das Installationsprotokoll prüfen lassen und anschließend das korrigierte Paket erneut ausführen.\" with title \"Lernapp\" buttons {\"OK\"} default button \"OK\"" >/dev/null 2>&1 || true
  exit "$rc"
fi
/usr/bin/sudo -u "$CONSOLE_USER" -H /usr/bin/open -a "$APP" >/dev/null 2>&1 || true
exit 0
POST
chmod 755 "$BUILD/scripts/postinstall"

# user-level part (runs as the console user; also usable manually for troubleshooting)
cat > "$APP/Contents/Resources/install_user.sh" <<'USERSH'
#!/bin/bash
# install_user.sh RESOURCES_DIR HOME_DIR — unpack src.tar.gz and run install_core.sh (as the user).
set -euo pipefail
RES="$1"; export HOME="$2"
APP_DIR="$HOME/Library/Application Support/Lernapp/app"
LOGDIR="$HOME/Library/Logs/Lernapp"; mkdir -p "$LOGDIR"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lernapp-src.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$RES/src.tar.gz" -C "$TMP"
bash "$RES/install_core.sh" "$APP_DIR" "$TMP" 2>&1 | tee -a "$LOGDIR/install.log"
exit "${PIPESTATUS[0]}"
USERSH
chmod 755 "$APP/Contents/Resources/install_user.sh"

# ---------------------------------------------------------------- component pkg --------------
echo "==> pkgbuild"
# Always install to /Applications. Automatic relocation can otherwise target a development
# build or a copy in Trash, leaving postinstall's fixed resource path missing.
pkgbuild --analyze --root "$BUILD/root" "$BUILD/components.plist"
"$PY" - "$BUILD/components.plist" <<'COMPONENTS'
import plistlib
import sys
from pathlib import Path
path = Path(sys.argv[1])
components = plistlib.loads(path.read_bytes())
assert components, "No app bundle found in package payload"
for component in components:
    component["BundleIsRelocatable"] = False
path.write_bytes(plistlib.dumps(components))
COMPONENTS
pkgbuild --component-plist "$BUILD/components.plist" --root "$BUILD/root" --scripts "$BUILD/scripts" --identifier "$IDENTIFIER" --version "$VERSION" \
  --install-location / "$BUILD/Lernapp-component.pkg" >/dev/null

# ---------------------------------------------------------------- product archive ------------
cat > "$BUILD/resources/welcome.html" <<HTML
<!DOCTYPE html><html lang="de"><meta charset="utf-8"><body style="font-family:-apple-system,Helvetica;font-size:13px">
<h2>Lernapp $VERSION</h2>
<p>Dieses Paket installiert <b>Lernapp</b> – TestDaF-Vorbereitung und Verhandlungsdeutsch mit KI –
als <b>Lernapp.app</b> im Programme-Ordner.</p>
<p>Nach dem Kopieren richtet der Installer automatisch die Python-Umgebung für Ihren Benutzer ein
(<code>~/Library/Application Support/Lernapp</code>). Dafür wird eine <b>Internetverbindung</b> benötigt;
es werden ca. 1–2 GB heruntergeladen. Das dauert <b>einige Minuten</b> – bitte Geduld, auch wenn der
Fortschrittsbalken lange bei „Skripte werden ausgeführt“ steht.</p>
<p>Voraussetzungen: macOS 12 oder neuer, ca. 3 GB freier Speicherplatz.</p>
</body></html>
HTML
cat > "$BUILD/resources/readme.html" <<HTML
<!DOCTYPE html><html lang="de"><meta charset="utf-8"><body style="font-family:-apple-system,Helvetica;font-size:13px">
<h3>Nach der Installation</h3>
<ul>
<li>Lernapp über das Symbol im Programme-Ordner (oder Launchpad) starten. Datenbank, Server und
Oberfläche starten automatisch, der Browser öffnet sich.</li>
<li>API-Schlüssel (z. B. OpenAI, Mistral) in der App unter „Einrichtung“ eintragen.</li>
<li>Für die lokale Spracherkennung einmalig im Terminal ausführen:<br>
<code>"~/Library/Application Support/Lernapp/app/.venv/bin/lernapp" download-models --size small</code></li>
<li>Alle Lerndaten liegen in <code>~/Library/Application Support/Lernapp</code> und bleiben bei Updates erhalten.</li>
<li>Deinstallation: Lernapp.app in den Papierkorb ziehen und – falls gewünscht – den Ordner
<code>~/Library/Application Support/Lernapp</code> löschen.</li>
</ul>
<p>Das Paket ist nicht von Apple signiert: Beim ersten Öffnen ggf. Rechtsklick → „Öffnen“ wählen.</p>
</body></html>
HTML
cat > "$BUILD/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Lernapp $VERSION</title>
  <welcome file="welcome.html" mime-type="text/html"/>
  <readme file="readme.html" mime-type="text/html"/>
  <options customize="never" require-scripts="true" rootVolumeOnly="true" hostArchitectures="arm64,x86_64"/>
  <allowed-os-versions><os-version min="12.0"/></allowed-os-versions>
  <domains enable_localSystem="true"/>
  <choices-outline><line choice="default"><line choice="$IDENTIFIER"/></line></choices-outline>
  <choice id="default"/>
  <choice id="$IDENTIFIER" visible="false" title="Lernapp"><pkg-ref id="$IDENTIFIER"/></choice>
  <pkg-ref id="$IDENTIFIER" version="$VERSION" onConclusion="none">Lernapp-component.pkg</pkg-ref>
</installer-gui-script>
XML
echo "==> productbuild"
productbuild --distribution "$BUILD/distribution.xml" --resources "$BUILD/resources" \
  --package-path "$BUILD" "$OUT" >/dev/null

echo "==> done: $OUT ($(du -h "$OUT" | cut -f1))"
