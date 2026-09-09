# Eine neue App-Version veröffentlichen

Öffentliches Repository: https://github.com/esterkane/lernapp-updates

1. Änderung implementieren; Version in `pyproject.toml`, `uv.lock` und API-Anwendung erhöhen.
2. `uv run pytest -q` und passende Betriebssystemtests ausführen. Insbesondere Update von einer älteren Version, Fortschritt, Audiodaten, Anbieterzugänge und Rückkehr nach einem simulierten Fehler prüfen.
3. `make release-local` baut die verfügbaren Installer. `python packaging/common/build_update.py` baut zusätzlich `dist/lernapp-update.zip` aus demselben geprüften öffentlichen Dateisatz.
4. Nur App-Quellen, allgemeine Dokumentation und synthetische Tests committen. Niemals `data/`, `.env`, lokale Sicherungen, private Übergabe-ZIPs oder importierte PDFs veröffentlichen.
5. Commit und Versions-Tag pushen. Der Release-Workflow baut Windows, macOS und Linux sowie das gemeinsame Updatepaket. Alternativ eine GitHub Release manuell anlegen und ausschließlich die konkreten öffentlichen Dateien hochladen.
6. Zuerst als Entwurf prüfen, dann als stabile Release veröffentlichen. Der Tag lautet z. B. `v0.2.18`. Das Updatepaket muss exakt `lernapp-update.zip` heißen. GitHub liefert dazu den SHA-256-Digest; die App vergleicht ihn vor dem Auspacken und prüft zusätzlich jedes enthaltene Quellfile gegen das Paketmanifest.

Die App verwendet ausschließlich den festgelegten öffentlichen Release-Kanal; Endnutzer benötigen keinen GitHub-Zugang. Vorabversionen und ältere Versionsnummern werden nicht als Update angeboten. Die automatische Prüfung ist zwischengespeichert und benötigt keine KI-Aufrufe.

## Sicherung und Grenzen

Der Updater unterstützt die reguläre lokale Installation mit eingebettetem PostgreSQL im Standard-Datenordner. Externe Datenbanken und abweichende Datenbankpfade werden nicht automatisch aktualisiert. Vor dem Wechsel beendet er die App und PostgreSQL und kopiert die gestoppte Datenbank sowie `.env` in den privaten Ordner `updates/version-…/backup-data`. Die alte App bleibt daneben erhalten. Diese Sicherungen enthalten vertrauliche Daten und dürfen nicht veröffentlicht werden.

Bei einem Installations- oder Startfehler wird die alte App wiederhergestellt; nach einem fehlgeschlagenen Datenbankstart wird auch die Datenbanksicherung zurückkopiert. Audiodateien, Modelle und sonstige unveränderte Daten außerhalb von `app/` werden nicht gelöscht. Backupordner werden nicht automatisch bereinigt. Nach bestätigtem erfolgreichem Update können alte Sicherungen bewusst archiviert werden.

Ein harter Stromausfall kann eine Neuinstallation des normalen Installers nötig machen. Die Daten liegen weiterhin außerhalb der App. Wenn eine Migration schon gestartet war, vor einer manuellen Datenbankwiederherstellung immer die aktuelle Datenbank separat sichern und beide Versionen vergleichen.

App-Updates verteilen Programmänderungen. Persönliche Quellen und Lernstände werden nicht über öffentliche Releases verteilt und nicht überschrieben.
