# Using Lernapp

[English](User-Manual.md) · [Deutsch](Benutzerhandbuch.md)

Lernapp helps you practise German for exams and work. The interface currently uses German labels. This guide keeps the exact labels in **bold** and explains what they mean in English.

## 1. Install and open the app

Download the installer for your laptop from the [download page](https://github.com/esterkane/lernapp-updates/releases/latest).

- **Windows:** double-click `Lernapp-Setup-<version>.exe`. If you downloaded the Windows ZIP instead, extract it completely and double-click `install.cmd`.
- **macOS:** open the `.pkg` file and follow the instructions.
- **Linux:** run `bash lernapp-<version>-linux-installer.sh` in a terminal.

Wait for installation to finish. The first installation needs internet access and can take several minutes.

Open **Lernapp** from the desktop or Start menu. A browser window opens. The app runs on your laptop; you do not need to set up a web server.

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

The key is encrypted and saved for the current learning area on this laptop. Next time, leave the password field empty and click **Abrechnung abrufen** (Retrieve billing). You can replace the saved key using the same save button, or choose **Gespeicherten Admin-Schlüssel entfernen** (Remove saved admin key). Removing it from Lernapp does not revoke it at OpenAI.

If you only want to use a key once, enter it and click **Abrechnung abrufen** without saving. The billing key is separate from the normal OpenAI key used for learning. It survives app updates but is excluded from shared `.lernapp` learning packs; each recipient can save their own billing key.

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
