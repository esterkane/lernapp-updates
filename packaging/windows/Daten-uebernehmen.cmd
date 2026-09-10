@echo off
setlocal
chcp 65001 >nul
set "LERNAPP_EXE=%LOCALAPPDATA%\Lernapp\app\.venv\Scripts\lernapp.exe"
if not exist "%LERNAPP_EXE%" (
  echo Die App-Installation ist noch nicht abgeschlossen.
  echo Bitte den Windows-Installer vollstaendig ausfuehren.
  echo Bei ZIP-Installation: Alle extrahieren, dann install.cmd neben dem src-Ordner starten.
  pause
  exit /b 1
)
echo Lernapp - Daten auf diesen Laptop uebernehmen
echo Bitte Lernapp vorher beenden. Vorhandene Lerndaten werden nicht ersetzt.
echo.
"%LERNAPP_EXE%" restore %*
echo.
pause
