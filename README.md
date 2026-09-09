# Lernapp

Deutsch für TestDaF, Beruf und Verhandlungen üben – auf deinem eigenen Laptop.

Die App bietet Gespräche und Schreibübungen, Vokabelkarten, eigene PDF-Modelltests mit Hördateien und eine Übersicht über deinen Lernfortschritt. Du kannst eigene Materialien und hilfreiche Links hinzufügen.

## Herunterladen und starten

Lade die passende Version unter [Releases](https://github.com/esterkane/lernapp-updates/releases/latest) herunter:

- **Windows 10/11 (64 Bit):** ZIP entpacken, `install.cmd` doppelklicken. Falls ein `Setup.exe` angeboten wird, kannst du stattdessen dieses starten.
- **macOS:** die `.pkg`-Datei öffnen und der Installation folgen.
- **Linux:** den heruntergeladenen Installer mit `bash lernapp-<Version>-linux-installer.sh` ausführen.

Für die erste Installation brauchst du Internet. Python und benötigte Komponenten werden eingerichtet. Danach startest du „Lernapp“ über die angelegte Verknüpfung; die Oberfläche öffnet sich im Browser.

**[Zum einfachen Benutzerhandbuch](docs/Benutzerhandbuch.md)**

## Updates

Die App prüft im Hintergrund auf neue Versionen. Unter **Einstellungen → App-Updates** kannst du jederzeit selbst prüfen und **Update herunterladen und installieren** wählen. Die App wird kurz geschlossen und startet danach wieder. Lerndaten und Einstellungen liegen getrennt von den App-Dateien und werden vor dem Wechsel gesichert.

Versionen vor 0.2.17 benötigen einmalig die Installation einer aktuellen Version, um diese Update-Funktion zu erhalten. Ein Update ist keine Wiederherstellung einer alten Lernstandsicherung.

## Daten und laufende Kosten

Deine Materialien, Antworten und Einstellungen werden lokal gespeichert. Dieses öffentliche Repository enthält keine persönlichen Lernstände, PDFs oder API-Schlüssel. Ein privates Lernpaket wird separat übertragen.

KI-Antworten und Online-Stimmen benötigen einen eigenen oder bereits eingerichteten Anbieterzugang und können Gebühren verursachen. Lokale Spracherkennung und lokale Stimme verarbeiten Audio auf dem Laptop. Details findest du in der App unter **Einstellungen → Kosten**.

App-Punkte sind interne Übungsergebnisse und kein offizielles TestDaF- oder Goethe-Prüfungsergebnis. Freie Schreib- und Sprechantworten benötigen eine inhaltliche Beurteilung.

## Für die Betreuung der App

[Neue Versionen veröffentlichen](docs/Updates-veroeffentlichen.md). Der Quellcode und die plattformübergreifenden Installer liegen in diesem Repository. Betriebssystemspezifische Update-Tests bitte vor jeder Freigabe durchführen.
