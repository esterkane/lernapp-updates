# Lernapp installieren

Lernapp läuft komplett auf dem eigenen Rechner: eine eingebettete PostgreSQL-Datenbank, ein
Server (API) und die Oberfläche im Browser. Docker oder eine eigene Python-Installation sind
**nicht** nötig – der Installer erledigt alles. Nur die KI-Dienste (Sprachmodelle) laufen in der
Cloud; die Zugangsschlüssel dafür trägt man nach der Installation in der App unter
„Einrichtung“ ein.

## Voraussetzungen

| | |
|---|---|
| Betriebssystem | macOS 12 oder neuer (Apple Silicon oder Intel) · Ubuntu 22.04+/Fedora (x86_64) · Windows 10/11 (64 Bit) |
| Speicherplatz | ca. 2 GB (Python + Abhängigkeiten), plus ca. 0,5–1,5 GB je Spracherkennungs-Modell |
| Arbeitsspeicher | 8 GB empfohlen (lokale Spracherkennung) |
| Internet | **während der Installation zwingend** (Python und Pakete werden heruntergeladen, ca. 1–2 GB) und später für die KI-Dienste |
| Rechte | keine Administratorrechte nötig (Linux/Windows); macOS fragt beim .pkg einmal nach dem Passwort |

Die Installation dauert je nach Verbindung **5–15 Minuten**. Der Fortschritt steht lange bei
„Skripte werden ausgeführt“ / im schwarzen Fenster – das ist normal.

## macOS – `Lernapp-<version>-macos.pkg`

1. Doppelklick auf die `.pkg`-Datei. Das Paket ist **nicht von Apple signiert**; erscheint die
   Meldung „kann nicht geöffnet werden“: **Rechtsklick (Ctrl-Klick) → „Öffnen“ → „Öffnen“**.
   Alternativ: Systemeinstellungen → Datenschutz & Sicherheit → „Trotzdem öffnen“.
2. Dem Assistenten folgen (Passwort eingeben). Der Installer legt `/Programme/Lernapp.app` an
   und richtet danach – für den angemeldeten Benutzer – Python und alle Pakete in
   `~/Library/Application Support/Lernapp/app` ein. Das dauert einige Minuten.
3. Am Ende öffnet sich Lernapp automatisch. Später: **Lernapp** im Programme-Ordner /
   Launchpad starten. Ein kleines Fenster zeigt „Lernapp wird gestartet …“; der Browser öffnet
   sich, sobald alles bereit ist (erster Start ca. eine Minute).
4. Beenden: Lernapp erneut öffnen → „Lernapp beenden“. (Oder im Terminal:
   `"$HOME/Library/Application Support/Lernapp/app/.venv/bin/lernapp" stop`.)

Falls die Einrichtung fehlschlägt (kein Internet, Proxy): Fehlerdialog beachten, Log unter
`~/Library/Logs/Lernapp/install.log` bzw. `/tmp/lernapp-install.log`, dann das `.pkg` erneut
ausführen. Manuell: `bash "/Applications/Lernapp.app/Contents/Resources/install_user.sh" "/Applications/Lernapp.app/Contents/Resources" "$HOME"`.

## Linux – `lernapp-<version>-linux-installer.sh`

```bash
bash lernapp-<version>-linux-installer.sh      # NICHT mit sudo
```

Der Installer entpackt sich selbst, installiert `uv` nach `~/.local/bin`, Python 3.12, kopiert
die App nach `~/.local/share/Lernapp/app` und legt an:

- Menüeintrag **Lernapp** (`~/.local/share/applications/lernapp.desktop`, Kategorie Bildung)
- Befehl `~/.local/bin/lernapp` (`lernapp start | stop | status | doctor`)

Benötigt: `bash`, `tar`, `curl` (oder `wget`). Getestet für Ubuntu 22.04+/Fedora auf x86_64.
**ARM (aarch64, z. B. Raspberry Pi):** Es gibt keine eingebettete Datenbank für diese
Architektur. Eine eigene PostgreSQL 16 mit `pgvector` bereitstellen und in
`~/.local/share/Lernapp/.env` eintragen: `DATABASE_URL=postgresql://user:pass@localhost:5432/lernapp`.

`bash lernapp-<version>-linux-installer.sh --extract-only ORDNER` entpackt nur (zum Nachsehen).

## Windows – `lernapp-<version>-windows.zip` (unterstützt) · `Lernapp-Setup-<version>.exe` (experimentell)

**Unterstützter Weg ist das Zip mit `install.cmd`** (siehe unten). Das **Setup.exe** aus dem
GitHub-Release ist experimentell: es wird nur im CI mit Inno Setup gebaut und wurde noch auf keinem
Windows-Rechner getestet. Wer es trotzdem probiert: Doppelklick. Windows SmartScreen warnt bei unsignierten
Programmen: **„Weitere Informationen“ → „Trotzdem ausführen“**. Der Assistent kopiert die Dateien
nach `%LOCALAPPDATA%\Lernapp` und öffnet dann ein schwarzes Fenster, in dem Python und die
Pakete installiert werden – bitte nicht schließen, bis „wurde installiert“ erscheint.

**Zip**: entpacken (z. B. nach *Dokumente*), dann `install.cmd` doppelklicken (gleiche
SmartScreen-Meldung; PowerShell wird mit `-ExecutionPolicy Bypass` gestartet, es werden keine
Systemeinstellungen geändert).

Danach gibt es **Lernapp** im Startmenü und auf dem Desktop (sowie „Lernapp beenden“). Beim
Start öffnet sich ein Konsolenfenster mit dem Status; wird es geschlossen, wird Lernapp beendet.
Der Browser öffnet sich automatisch.

Windows Defender kann die eingebettete Datenbank beim ersten Start kurz prüfen – das ist normal.

## Nach der Installation

- **API-Schlüssel** (OpenAI, Mistral, Google …) in der App unter „Einrichtung“ eintragen. Sie
  werden in `<Datenordner>/.env` gespeichert.
- **Spracherkennung offline** (empfohlen): einmalig `lernapp download-models --size small`
  (ca. 500 MB; `medium`/`large-v3-turbo` sind genauer, aber größer).
- `lernapp doctor` prüft die Installation.

## Wo liegen meine Daten?

| System | Datenordner (Datenbank `pg/`, `.env`, `logs/`, `models/`, Dokumente) | Programm |
|---|---|---|
| macOS | `~/Library/Application Support/Lernapp` | `…/Lernapp/app` und `/Applications/Lernapp.app` |
| Linux | `~/.local/share/Lernapp` | `…/Lernapp/app` |
| Windows | `%LOCALAPPDATA%\Lernapp` (z. B. `C:\Users\NAME\AppData\Local\Lernapp`) | `…\Lernapp\app` |

Backup = diesen Ordner kopieren (vorher `lernapp stop`). Export der Lerndaten als JSON/CSV
über die App bzw. `scripts/export_learner_data.py`.

## Updates

Einfach den neuen Installer ausführen. Programmdateien werden ersetzt, der Datenordner
(Datenbank, `.env`, Modelle) bleibt unverändert; Datenbank-Migrationen laufen beim nächsten
Start automatisch.

## Deinstallation

- **macOS**: `Lernapp.app` in den Papierkorb; Datenordner `~/Library/Application Support/Lernapp`
  löschen, wenn die Lerndaten nicht mehr gebraucht werden. Optional: `~/.local/bin/uv` und
  `~/.local/share/uv` (Python), `~/Library/Logs/Lernapp`.
- **Linux**: `rm -rf ~/.local/share/Lernapp ~/.local/bin/lernapp ~/.local/share/applications/lernapp.desktop ~/.local/share/icons/hicolor/256x256/apps/lernapp.png`
- **Windows**: Einstellungen → Apps → Lernapp deinstallieren (Setup.exe-Variante) – entfernt
  nur das Programm; der Datenordner `%LOCALAPPDATA%\Lernapp` bleibt und kann von Hand gelöscht
  werden. Zip-Variante: Ordner `%LOCALAPPDATA%\Lernapp\app` und die Verknüpfungen löschen.

## Probleme

| Symptom | Lösung |
|---|---|
| „Port belegt“ | Lernapp wählt automatisch den nächsten freien Port; feste Ports über `API_PORT`/`UI_PORT` in `.env`. |
| Datenbank startet nicht | `lernapp doctor`; Log unter `<Datenordner>/logs/api.log` und `<Datenordner>/pg/log`. Notfalls `lernapp reset-db` (löscht alle Lerndaten!). |
| Installation bricht bei `uv sync` ab | Internet/Proxy prüfen (`HTTPS_PROXY` setzen), Installer erneut ausführen – er setzt dort fort. |
| Oberfläche zeigt Fehler „API nicht erreichbar“ | `lernapp status`; ggf. `lernapp stop` und neu starten. |

---

# Developer notes (English)

## Layout

```
packaging/
├── common/install_core.sh   shared bash installer (uv → Python 3.12 → copy source → uv sync --frozen --no-dev)
├── common/make_icon.py      pure-python icon generator (PNG / ICO), no Pillow needed
├── macos/build_pkg.sh       → dist/Lernapp-<v>-macos.pkg (pkgbuild + productbuild, unsigned)
├── linux/build_installer.sh → dist/lernapp-<v>-linux-installer.sh (self-extracting: header + __ARCHIVE__ + tar.gz)
└── windows/
    ├── install.ps1          per-user installer (uv, Python 3.12, robocopy, uv sync, shortcuts) — UTF-8 BOM + CRLF, PS 5.1 compatible
    ├── install.cmd          double-click wrapper (-ExecutionPolicy Bypass)
    ├── lernapp.iss          Inno Setup script → Lernapp-Setup-<v>.exe (CI only)
    └── build_zip.sh         → dist/lernapp-<v>-windows.zip (src + install.cmd + install.ps1 + lernapp.ico)
```

All three installers ship the **source tree** (everything except `.git .venv data dist build
caches .env`) plus `uv.lock`, and build the virtualenv on the user's machine with
`uv sync --frozen --no-dev`. Nothing is compiled or vendored ahead of time, so an installer is
< 1 MB and always matches the lockfile. Consequence: internet access is required at install time.

Install locations follow `platformdirs.user_data_dir("Lernapp")` so the app dir lives *inside*
the data dir: `<data_dir>/app` (`.venv` inside). The launcher (`launcher/lernapp_launcher/cli.py`,
console script `lernapp`) finds the source root via `config/pricing.yaml` and stores runtime state
in `<data_dir>/run` and logs in `<data_dir>/logs`.

### macOS pkg

- Payload: `/Applications/Lernapp.app` — `Contents/MacOS/lernapp` is a bash script that runs
  `~/Library/Application Support/Lernapp/app/.venv/bin/lernapp start` (nohup, log in
  `~/Library/Logs/Lernapp/launcher.log`) and shows an osascript dialog with a "Lernapp beenden"
  button; if the venv is missing it shows a friendly hint instead. Resources hold `src.tar.gz`,
  `install_core.sh`, `install_user.sh` and the generated `Lernapp.icns`.
- `postinstall` runs as root, determines the console user (`stat -f%Su /dev/console`), and runs
  `install_user.sh` via `sudo -u <user> -H env HOME=…` so the venv is owned by the user. Output is
  logged to `/tmp/lernapp-install.log` and `~/Library/Logs/Lernapp/install.log`; a failure aborts
  the installation with a dialog. On success it `open`s the app.
- Unsigned/un-notarised: users need right-click → Open. Signing would require an Apple Developer
  ID (`productsign --sign "Developer ID Installer: …"` + `xcrun notarytool submit`).
- Build: `make pkg-macos`; verify: `pkgutil --expand-full dist/*.pkg /tmp/x`.

### Linux installer

`build_installer.sh` writes a bash header (`--extract-only DIR`, `--help`) followed by the
`__ARCHIVE__` marker and a tar.gz of `src/`, `install_core.sh`, `setup.sh`, `lernapp.png`.
`setup.sh` refuses to run as root, calls `install_core.sh`, symlinks `~/.local/bin/lernapp`,
installs the icon under `~/.local/share/icons/hicolor/256x256/apps/` and writes the `.desktop`
entry (`Exec=<venv>/bin/lernapp start`, `Terminal=false`). Linux aarch64 has no `pgserver`
wheel — `install_core.sh` prints the `DATABASE_URL` note.

### Windows

`install.ps1` mirrors `install_core.sh`: `irm https://astral.sh/uv/install.ps1 | iex` into
`%USERPROFILE%\.local\bin` (adds it to the *user* PATH), `uv python install 3.12`, `robocopy /MIR`
(keeps `.venv`), `uv sync --frozen --no-dev`, WScript.Shell shortcuts (Desktop + Start Menu,
plus "Lernapp beenden") to `<app>\.venv\Scripts\lernapp.exe start`. When `-SourceDir` equals
`-AppDir` (Inno Setup case) the copy is skipped. `lernapp.iss` installs to
`{localappdata}\Lernapp` with `PrivilegesRequired=lowest`, runs `install.ps1` post-install, and
its uninstaller only deletes `{app}\app` and `{app}\installer` — never the data.
Neither the exe nor the script is code-signed (SmartScreen warning is expected).
**Not testable on macOS** — the PowerShell path is exercised only by the CI `windows-latest` job.

### CI

- `.github/workflows/ci.yml`: ubuntu, `astral-sh/setup-uv`, `uv sync --frozen`, ruff, mypy,
  pytest, `verify_models.py --non-blocking --offline`, `bash -n` on all installer scripts,
  `lernapp --help/version/doctor`.
- `.github/workflows/release.yml` (tag `v*`): builds pkg (macos-latest), Linux installer
  (ubuntu-latest), `Lernapp-Setup-<v>.exe` via Inno Setup + zip (windows-latest), then creates a
  GitHub release with all files and `SHA256SUMS.txt`. Version = tag without `v` (falls back to
  `pyproject.toml` on `workflow_dispatch`). Bump `version` in `pyproject.toml` before tagging.

### Local verification recipe (macOS)

```bash
make release-local                                        # pkg + linux installer + windows zip
pkgutil --expand-full dist/Lernapp-*-macos.pkg /tmp/pkgx  # inspect payload/scripts
# full install into a throw-away HOME (real uv sync, several minutes):
env -i HOME=/tmp/lh PATH=/usr/bin:/bin bash packaging/common/install_core.sh \
    "/tmp/lh/Library/Application Support/Lernapp/app" "$PWD"
"/tmp/lh/Library/Application Support/Lernapp/app/.venv/bin/lernapp" doctor
```

### Source consistency (0.2.8 and later)

All installers stage their app files with `packaging/common/source_payload.py` and include
`SOURCE_MANIFEST.json` with SHA-256 hashes. This includes current uncommitted files in local
builds: backend/frontend changes, migrations, prompts, `pyproject.toml` and `uv.lock`.
Builds fail if the requested installer version differs from the application version.
Windows Setup.exe uses the same staged `src` directory as the Windows ZIP; direct Inno builds
must pass `/DAppVersion=<version> /DPayloadRoot=<absolute staged src path>` as well as SourceRoot.

`make release-local` builds and extracts the current version's installers, then compares every
source file against the checkout. `make verify-installers` repeats only the extraction/check.
Verification requires Python 3.12+. Release CI performs the same artifact checks before upload.
The Windows executable is compiled on Windows CI; local macOS builds produce the Windows ZIP.
These checks verify packaged contents, not a native installation or successful application startup
on Windows/Linux. Local verification does not publish a release.

Version 0.2.8 includes Modelltests (PDF/MP3 import, draft preparation and practice), the TANDEM PDF
font dependency, database migrations, and the retrieval and tutor fixes. Installers contain the
application; personal tests and downloaded media remain in each user's local database.

## Daten auf einen anderen Laptop übernehmen

Ab 0.2.12: geschützte `.lernapp`-Sicherung zusätzlich zum Installer weitergeben. Unter Windows anschließend `Daten-uebernehmen.cmd` oder Startmenü → Lernapp → Daten uebernehmen öffnen. Die Übernahme funktioniert nur in eine noch nicht genutzte Installation. [Anleitung und Umfang](../docs/portable-transfer.md). Persönliche Materialien und Schlüssel gehören nicht in die öffentlichen Installer.
