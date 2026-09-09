# Lernapp einfach benutzen

[Deutsch](Benutzerhandbuch.md) · [English](User-Manual.md)

## 1. Installieren und öffnen

Lade den Installer für deinen Laptop von der [Downloadseite](https://github.com/esterkane/lernapp-updates/releases/latest). Unter Windows: ZIP-Datei vollständig entpacken und `install.cmd` doppelklicken. Warte, bis die Installation abgeschlossen ist. Die erste Installation benötigt Internet und kann einige Minuten dauern.

Öffne danach **Lernapp** über den Desktop oder das Startmenü. Ein Browserfenster öffnet sich. Die App läuft auf deinem Laptop; du musst keinen Webserver einrichten.

## 2. Ein vorbereitetes Lernpaket übernehmen

Wenn du zusätzlich eine Datei mit der Endung `.lernapp` und ein Passwort erhalten hast, übernimm sie einmalig in eine neue Installation. Unter Windows kannst du **Daten übernehmen** im Startmenü oder `Daten-uebernehmen.cmd` aus dem privaten Paket starten. Die App muss dafür geschlossen sein. Wähle die Sicherung und gib das separat erhaltene Passwort ein.

Damit werden die mitgegebenen Materialien, Lernstände und eingerichteten Anbieterzugänge übernommen. Bereits vorhandene Lerndaten werden nicht automatisch zusammengeführt. Eine Lernstandsicherung wird nicht für normale App-Updates gebraucht.

## 3. Lernen

- **Start:** Überblick über deine Lernmöglichkeiten.
- **Lernen:** Deutsch üben, Fragen stellen und mit deinen Materialien arbeiten.
- **Modelltests:** Prüfungen und kurze Lernquizze bearbeiten.
- **Materialien:** Dateien und externe Links verwalten.
- **Fortschritt:** bisherige Ergebnisse ansehen.

Unter **Einstellungen & Hilfe** findest du dein Profil, Stimme, Anbieterzugänge und Kostenübersicht.

## 4. Einen Modelltest bearbeiten

1. Öffne **Modelltests → Üben**.
2. Suche einen Test oder wähle **Prüfungen** bzw. **Kurze Übungen**.
3. Klicke **Test starten**. Bei einem begonnenen Versuch wählst du **Gespeicherte Übung fortsetzen**.
4. Lies den angezeigten Text und beantworte die Aufgabe. Mit **Weiter** oder der Aufgabenauswahl wechselst du die Frage.
5. Bei Bedarf aktiviere **Originalseiten mit Abbildungen anzeigen**. Hördateien öffnest du über **Hördatei zum Test öffnen → Hördatei abspielen**.
6. Klicke am Ende **Test abgeben und auswerten**.

Antwortänderungen und die aktuelle Aufgabe werden lokal gespeichert. Bei Textantworten verlasse das Eingabefeld, damit die Änderung übernommen wird. **Zurück zur Übersicht** speichert ebenfalls. Du kannst später weiterarbeiten, auch nach einem Neustart.

Unter **Fortschritt zurücksetzen** kannst du die Antworten eines noch offenen Versuchs nach Bestätigung löschen und bei Aufgabe 1 beginnen. Abgegebene Ergebnisse bleiben erhalten; starte für einen neuen Durchlauf eine neue Übung.

**Mit Zeitlimit:** Die Uhr läuft auch weiter, wenn du den Test verlässt. Für entspanntes Lernen kannst du ohne Zeitlimit starten.

Die Lösungen werden erst nach der Abgabe angezeigt. Schreib- und Sprechaufgaben werden getrennt beurteilt. Die Punkte sind keine offizielle Prüfungsnote.

## 5. Stimme und Tempo einstellen

Öffne **Einstellungen → Stimme**. Richte bei Bedarf die lokale Spracherkennung und Stimme über die Downloadknöpfe ein.

Für deutsche Texte kannst du die lokale Stimme verwenden. Bei gemischtem Deutsch und Englisch kannst du bei verbundenem OpenAI-Zugang Marin, Cedar oder Alloy auswählen. Höre eine Probe an, stelle das **Sprechtempo** ein und klicke **Auswahl verwenden**. 0,85× liest langsamer als 1,00×.

Online-Vorlesen und Online-Hörproben werden über den verbundenen Anbieter abgerechnet. Lokales Vorlesen verursacht keine API-Gebühren. Über **Kostenübersicht öffnen** kannst du die Abrechnung direkt beim Anbieter prüfen. Lokale Kostenschätzungen und hypothetische Cloud-Vergleiche werden nicht angezeigt.

### Admin-Schlüssel für die Abrechnung speichern (optional)

Öffne **Einstellungen → Kosten → Anbieterabrechnung prüfen** und danach **Anbieterbeträge direkt abrufen**. Gib deinen OpenAI-Admin-API-Schlüssel ein und klicke **Schlüssel auf diesem Gerät speichern**.

Der Schlüssel wird verschlüsselt für den aktuellen Lernbereich auf diesem Laptop gespeichert. Unter macOS schützt der Schlüsselbund den Entschlüsselungsschlüssel; unter Windows übernimmt DPAPI den Schutz für dein Windows-Konto. Beim nächsten Mal lässt du das Passwortfeld leer und klickst **Abrechnung abrufen**. Über denselben Speicherknopf kannst du den Schlüssel ersetzen. **Gespeicherten Admin-Schlüssel entfernen** löscht ihn aus Lernapp; bei OpenAI wird er dadurch nicht widerrufen.

Für einen einmaligen Abruf gibst du den Schlüssel ein und klickst **Abrechnung abrufen**, ohne ihn zu speichern. Der Abrechnungsschlüssel ist vom normalen OpenAI-Schlüssel fürs Lernen getrennt. Er bleibt bei App-Updates erhalten, wird aber nicht in geteilte `.lernapp`-Lernpakete übernommen. Empfänger können ihren eigenen Abrechnungsschlüssel hinterlegen.

### Schlüsselschutz und Wiederherstellung

Ab Version 0.2.20 werden bestehende Anbieter- und Abrechnungsschlüssel unter Windows und macOS automatisch in den OS-geschützten Speicher überführt. Nach erfolgreicher Prüfung entfernt die App den bisherigen unverschlüsselten Entschlüsselungsschlüssel aus `.env`. Bekannte lokale Update- und App-Sicherungen werden nach erfolgreichem Update ebenfalls geschützt.

Falls macOS nach Schlüsselbundzugriff fragt, erlaube ihn nur beim Starten oder Benutzen von Lernapp. Ist der OS-Speicher gesperrt oder nicht verfügbar, erscheint ein Fehler; es gibt keinen Rückfall auf einen unverschlüsselten Entschlüsselungsschlüssel.

Für einen anderen Laptop nutze eine passwortgeschützte `.lernapp`-Übertragung. Normale Anbieterzugänge können so mitkommen, Admin-Schlüssel müssen separat eingegeben werden. Kopiere den Datenordner nicht einfach in ein anderes OS-Konto. Lösche weder den Schlüsselbundeintrag noch den Windows-Ordner `os-secrets`. Ältere lokale Wiederherstellungssicherungen benötigen das ursprüngliche OS-Konto und dessen Schlüsselspeicher; kontaktiere vor einer Wiederherstellung die Betreuung.

Dieser Schutz hilft gegen kopierte Schlüsseldateien, schützt aber nicht vor einem kompromittierten oder entsperrten Benutzerkonto. Externe Kopien alter Sicherungen kann die App nicht nachträglich ändern. Linux verwendet weiterhin den bisherigen dateibasierten Schlüsselschutz.

## 6. Materialien und Links

Unter **Materialien** kannst du Dateien hinzufügen. Bei Prüfungs-PDFs müssen Aufgaben, Lösungen und passende Hördateien vor der Freigabe geprüft werden. Nicht jedes heruntergeladene PDF enthält einen vollständigen Test.

Im Tab **Externe Links** kannst du Websites hinzufügen, bearbeiten und entfernen. Videos und externe Websites benötigen Internet. Eine lokale Lernzusammenfassung ist nicht zwangsläufig der vollständige Originalartikel.

## 7. App aktualisieren

Die App sucht im Hintergrund nach neuen Versionen. Bei einem Hinweis öffnest du **Update ansehen**. Alternativ gehe zu **Einstellungen → App-Updates → Nach Updates suchen**.

Klicke **Update herunterladen und installieren**. Lass den Laptop eingeschaltet und warte: Das Update wird heruntergeladen und geprüft, die App geschlossen, eine lokale Sicherung angelegt und die neue Version installiert. Danach öffnet sich Lernapp wieder. Du musst kein neues Lernpaket importieren.

Die ersten Updates benötigen Internet und freien Speicherplatz. Bei einem normalen Installations- oder Startfehler versucht der Updater, die vorige App-Version samt Datenbank wiederherzustellen. Wenn ein Stromausfall den Vorgang unterbricht, installiere den aktuellen normalen Installer erneut; deine Lerndaten liegen außerhalb des App-Ordners. Lösche den Lernapp-Datenordner nicht.

Neue persönliche Lernmaterialien werden getrennt von App-Updates verteilt. Bereits gemachte Fortschritte werden durch ein App-Update nicht durch einen fremden Lernstand ersetzt.

## 8. Wenn etwas nicht klappt

- **Keine Verbindung zur App:** Lernapp über die Verknüpfung neu starten und warten, bis die Oberfläche geöffnet ist.
- **Updateprüfung nicht erreichbar:** Du kannst normal weiterlernen. Versuche es später erneut.
- **Stimme spricht englische Wörter falsch:** Eine mehrsprachige Online-Stimme wählen oder das Tempo reduzieren.
- **Fortschritt nicht gespeichert:** Den angezeigten Fehler beachten und **Speichern erneut versuchen** wählen. Bei Änderungen in einem zweiten Fenster die Seite neu laden.

Für die Fehlerklärung genügen die angezeigte Meldung und deine App-Version. API-Schlüssel und Übertragungspasswort nicht öffentlich teilen.

## Lesungen mit WEBM und VTT

Öffne **Materialien → Dateien hinzufügen → Lesung mit VTT-Transkript hinzufügen**. Wähle eine lokale WEBM-, MP3- oder WAV-Aufnahme und die dazugehörigen VTT-Untertitel. Du findest den Import auch unter **Modelltests → Eigene Tests hinzufügen oder bearbeiten**. Hördateien dürfen bis zu 100 MB und 180 Minuten lang sein, Untertitel bis zu 2 MB. Ein Import umfasst höchstens 40 kurze Lernabschnitte. Dieselbe Dateikombination öffnet den vorhandenen Import.

Mit **Erklärungen und Fragen erstellen** bereitest du alle Abschnitte vor. Das nutzt deinen verbundenen KI-Anbieter. Bei Unterbrechungen bleiben fertige Abschnitte gespeichert. Das vorhandene Transkript liefert Text und Zeitmarken; eine zusätzliche Spracherkennung ist nicht nötig. Prüfe Erkennungsfehler, Erklärungen und Lösungen mit der Aufnahme und klicke anschließend auf **Zum Üben freigeben**. Erklärungen und Fragen lassen sich im Vorbereitungsbereich bearbeiten.

In der Übung liest du zuerst unter **Umgangssprache verstehen** die Bedeutung, Hinweise zum Sprachgebrauch und ein neues Alltagsbeispiel. Danach hörst du den passenden Abschnitt und beantwortest Verständnisfragen. Bei Leseaufgaben erscheint das Originaltranskript direkt. Bei Höraufgaben kannst du **Transkript als Lesehilfe anzeigen** aufklappen. Lösungen erscheinen erst nach der Abgabe. Dies sind neu erstellte Lernübungen, keine offiziellen Prüfungsaufgaben.

WEBM wird für die Wiedergabe lokal in MP3 umgewandelt und zwischengespeichert. Beim ersten Abspielen kann das kurz dauern. Originalaufnahme, VTT, vorbereitete Erklärungen, Fragen und Fortschritt werden in privaten `.lernapp`-Exporten mitgenommen. Nach Installation und Datenübernahme funktionieren Lesen, Anhören und automatische Auswertung offline. Neue Übungen erstellen benötigt den verbundenen Online-Anbieter. Öffentliche App-Updates enthalten nur das Programm, keine privaten Aufnahmen oder Lernpakete.
