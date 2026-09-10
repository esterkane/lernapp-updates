# Using Lernapp

[English](User-Manual.md) · [Deutsch](Benutzerhandbuch.md)

Lernapp helps you practise German for exams and work. The interface currently uses German labels. This guide keeps the exact labels in **bold** and explains what they mean in English.

## 1. Install and open the app

Download the installer for your laptop from the [download page](https://github.com/esterkane/lernapp-updates/releases/latest).

- **Windows:** double-click `Lernapp-Setup-<version>.exe`. If you downloaded the Windows ZIP instead, extract it completely and double-click `install.cmd`.
- **macOS:** open the `.pkg` file and follow the instructions.
- **Linux:** run `bash lernapp-<version>-linux-installer.sh` in a terminal.

Wait for installation to finish. The first installation needs internet access and can take several minutes.

Open **Lernapp** from the desktop or Start menu. On Windows, a dedicated app window opens without a command prompt. Close that window to stop Lernapp and its background services. Saved progress remains available next time. Wait for the saved confirmation before closing after an answer. On macOS and Linux, Lernapp opens in your browser. The app runs on your laptop; you do not need to set up a web server.

## 2. Import a prepared learning pack

If someone has given you a `.lernapp` file and a password, import it once into a new installation. On Windows, choose **Daten übernehmen** (Import data) in the Start menu, or open `Daten-uebernehmen.cmd` from the private pack. Close Lernapp first. Select the backup file and enter the password you received separately.

This imports the included materials, learning progress and configured provider connections. Existing learning data is not automatically merged. You do not need to import a learning backup for normal app updates.

## 3. Find your way around

- **Start:** an overview of your learning options.
- **Lernen** (Learn): practise German, ask questions and work with your materials.
- **Modelltests** (Practice tests): take exams and short learning quizzes.
- **Materialien** (Materials): manage files and external links.
- **Fortschritt** (Progress): see your previous results.

Open **Einstellungen & Hilfe** (Settings & help) for your profile, voice, provider connections and cost overview.

## 4. Take a practice test

1. Open **Modelltests → Üben** (Practice tests → Practise).
2. Search for a test, or choose **Prüfungen** (Exams) or **Kurze Übungen** (Short exercises).
3. Click **Test starten** (Start test). To resume an unfinished attempt, choose **Gespeicherte Übung fortsetzen** (Continue saved exercise).
4. Read the displayed text and answer the question. Use **Weiter** (Next) or the question selector to move on.
5. If needed, enable **Originalseiten mit Abbildungen anzeigen** (Show original pages with images). For listening tasks, choose **Hördatei zum Test öffnen → Hördatei abspielen** (Open test audio → Play audio).
6. When finished, click **Test abgeben und auswerten** (Submit and score test).

Answer changes and your current question are saved locally. After typing an answer, click outside the text box to save the change. **Zurück zur Übersicht** (Back to overview) also saves your work. You can continue later, including after restarting the app.

Choose **Fortschritt zurücksetzen** (Reset progress) to clear the answers in an unfinished attempt after confirmation and start again at question 1. Submitted results stay saved; start a new exercise to try again.

**Timed tests:** the clock keeps running when you leave the test. For relaxed practice, start without a time limit.

Solutions appear only after submission. Writing and speaking tasks need a separate assessment. Practice scores are not official exam grades.

## 5. Choose a voice and reading speed

Open **Einstellungen → Stimme** (Settings → Voice). If needed, use the download buttons to set up local speech recognition and the local voice.

You can use the local voice for German texts. For mixed German and English, a connected OpenAI account lets you choose Marin, Cedar or Alloy. Listen to a sample, adjust **Sprechtempo** (Speaking speed), and click **Auswahl verwenden** (Use selection). A speed of 0.85× is slower than 1.00×; the German interface may display these as 0,85× and 1,00×.

Online reading and online voice samples are billed by the connected provider. Local reading does not incur API charges. **Kostenübersicht öffnen** (Open cost overview) lets you check billing directly with the provider. Local cost estimates and hypothetical cloud comparisons are not displayed.

### Save your billing admin key (optional)

Open **Einstellungen → Kosten → Anbieterabrechnung prüfen** (Settings → Costs → Check provider billing), then expand **Anbieterbeträge direkt abrufen** (Retrieve provider amounts). Enter your OpenAI admin API key and click **Schlüssel auf diesem Gerät speichern** (Save key on this device).

The key is encrypted and saved for the current learning area on this laptop. On macOS, Keychain protects the unlocking key; on Windows, DPAPI protects it for your Windows account. Next time, leave the password field empty and click **Abrechnung abrufen** (Retrieve billing). You can replace the saved key using the same save button, or choose **Gespeicherten Admin-Schlüssel entfernen** (Remove saved admin key). Removing it from Lernapp does not revoke it at OpenAI.

If you only want to use a key once, enter it and click **Abrechnung abrufen** without saving. The billing key is separate from the normal OpenAI key used for learning. It survives app updates but is excluded from shared `.lernapp` learning packs; each recipient can save their own billing key.

### Credential protection and recovery

Version 0.2.20 automatically migrates existing saved provider and billing keys to OS-protected storage on Windows and macOS. It removes the old plaintext unlocking key from the app's `.env` file after verifying the migrated credentials. Known local update and app backup configurations are also protected after the update succeeds.

If macOS asks for Keychain access, allow it only when you are starting or using Lernapp. A locked or unavailable OS key store causes an error; the app does not fall back to storing the unlocking key in plaintext.

Use a password-protected `.lernapp` export to move normal provider connections and learning data to another laptop. Billing admin keys must be entered separately. Simply copying the data folder to another OS account is not a supported credential transfer. Do not delete the Keychain entry or the Windows `os-secrets` folder. Older recovery backups need the original OS account and its key store; contact the app maintainer before restoring them.

This protects copied credential files, but does not make a compromised or unlocked user account safe. External copies of old backups cannot be changed by the app. Linux currently retains the previous file-based encryption-key storage and does not have the Windows/macOS protection described above.

## 6. Add materials and links

Add files under **Materialien** (Materials). For exam PDFs, review the questions, solutions and matching audio before making the test available. Not every downloaded PDF contains a complete test.

In **Externe Links** (External links), you can add, edit and remove websites. Videos and external websites need internet access. A local learning summary may be shorter than the original article.

## 7. Update Lernapp

The app checks for new versions in the background. When an update is available, choose **Update ansehen** (View update). You can also open **Einstellungen → App-Updates → Nach Updates suchen** (Settings → App updates → Check for updates).

Click **Update herunterladen und installieren** (Download and install update). Keep your laptop switched on and wait. The app downloads and checks the update, closes, creates a local backup, installs the new version and opens again. You do not need to import another learning pack.

Updates need internet access and free disk space. If installation or startup fails, the updater tries to restore the previous app version and database. If a power failure interrupts the process, run the current normal installer again. Your learning data is stored outside the app folder. Do not delete the Lernapp data folder.

Versions older than 0.2.17 need a one-time installation of a current version to get the updater. Personal learning materials are shared separately from public app updates. Updating the app does not replace your progress with someone else's learning record.

## 8. Troubleshooting

- **Cannot connect to the app:** open Lernapp using its shortcut and wait for the interface to load.
- **Update check unavailable:** continue learning and try again later.
- **English words sound wrong:** try a multilingual online voice or reduce the reading speed.
- **Progress did not save:** read the error and choose **Speichern erneut versuchen** (Try saving again). If another window changed the same attempt, reload the page.

When asking for help, share the displayed error and your app version. Keep API keys and the transfer password private.

## Listening lessons with WEBM and VTT

Under **Materialien → Dateien hinzufügen → Lesung mit VTT-Transkript hinzufügen**, select a local WEBM, MP3 or WAV recording and its matching VTT subtitles. You can also find this import under **Modelltests → Eigene Tests hinzufügen oder bearbeiten**. Audio may be up to 100 MB and 180 minutes; subtitles up to 2 MB. Long transcripts are limited to 40 short learning sections. Adding the same pair again opens the existing import.

Click **Erklärungen und Fragen erstellen** to prepare all sections. This uses your connected AI provider; completed sections stay saved if interrupted. The supplied subtitles provide the text and time marks, so no additional speech-recognition request is needed. Review automatic subtitle errors, explanations and answers against the recording, then click **Zum Üben freigeben**. You can edit explanations and questions in the preparation screen.

For listening lessons without a timer, use **listen → answer → check → continue**. The audio clip ends automatically at the saved stopping point. Choose your answer and click **Antwort prüfen** (Check answer). Only that question's solution and explanation appear. Then choose **Weiter zum nächsten Abschnitt** (Continue to the next section); press play to continue listening. You can replay a clip. Prepared lessons need question-specific time marks for this flow; older imports may still use a whole learning section per question.

**Wörter und Umgangssprache verstehen** offers optional word explanations. Reading questions show their text; listening questions offer **Transkript als Lesehilfe anzeigen** as an optional aid. Answers, checked feedback and the current question are saved on this laptop. Changing an answer clears its previous check; use Check answer again. **Aufgabenübersicht** lets you revisit questions. **Fortschritt zurücksetzen** clears answers and checks for that attempt. Timed tests retain whole-test submission. These are learning exercises, not official exam tasks.

WEBM playback is converted to MP3 locally and cached; the first playback may take a moment. The original recording, VTT, prepared explanations, questions and progress are included in private `.lernapp` exports. Once installed and imported, reading, playback and automatic scoring work offline. Generating or regenerating exercises needs the configured online provider. Public app updates contain program code only, not private recordings or learning packs.


### Windows ZIP installation troubleshooting

Use **Extract All** on the Windows installer ZIP before running `install.cmd`. Run the file directly beside `install.ps1` and the `src` folder. Running inside the ZIP can launch only the script from a temporary folder. If version 0.2.22 reports an empty PowerShell Path, use installer 0.2.23 or later; your existing `.lernapp` materials backup can be reused. Start data migration only after installation finishes successfully.
