# Lernapp

Practise German for TestDaF, work and negotiations on your own laptop.

Lernapp brings together conversation and writing practice, vocabulary cards, PDF-based practice tests with audio, and saved learning progress. Add your own materials and useful links as you learn.

## Download and get started

Download the installer for your computer from [Releases](https://github.com/esterkane/lernapp-updates/releases/latest):

- **Windows 10/11 (64-bit):** open `Lernapp-Setup-<version>.exe`. Alternatively, extract the Windows ZIP completely and double-click `install.cmd`.
- **macOS:** open the `.pkg` file and follow the installer.
- **Linux:** run the downloaded installer with `bash lernapp-<version>-linux-installer.sh`.

The first installation needs an internet connection. Python and the required components are installed for you. Then open **Lernapp** using its shortcut; the app opens in your browser.

**[English user manual](docs/User-Manual.md) · [Deutsches Benutzerhandbuch](docs/Benutzerhandbuch.md)**

The app currently uses German interface labels. The English manual explains those labels so you can find the right controls. Learning materials remain in their original language.

## Updates

Lernapp checks for new versions in the background. Open **Einstellungen → App-Updates** (Settings → App updates) to check manually, then choose **Update herunterladen und installieren** (Download and install update). The app closes briefly and restarts when the update is ready. Learning data and settings are stored separately from app files and backed up before the version changes.

If you use a version older than 0.2.17, install a current version once to get the built-in updater. Do not restore an old learning backup as part of a normal app update.

## Your data and running costs

Your materials, answers and settings are stored locally. This public repository contains no personal learning records, imported PDFs or API keys. A private learning pack is transferred separately.

AI responses and online voices require a configured provider account and may incur charges. Local speech recognition and the local voice process audio on your laptop. Check provider billing under **Einstellungen → Kosten** (Settings → Costs).

Scores are for practice only and are not official TestDaF or Goethe exam results. Written and spoken answers need a content review.

## Maintaining the app

See [Publishing updates (German)](docs/Updates-veroeffentlichen.md) for the release process. This repository contains the app source and installers for Windows, macOS and Linux. Run platform-specific update checks before each release.

### Listening lessons from your own recordings

Import matching WEBM/MP3/WAV audio and WebVTT subtitles to prepare colloquial German explanations followed by listening and reading comprehension questions. Original text, timestamps and private learning-pack portability are preserved. See the [English manual](docs/User-Manual.md#listening-lessons-with-webm-and-vtt) or [German manual](docs/Benutzerhandbuch.md#lesungen-mit-webm-und-vtt).
