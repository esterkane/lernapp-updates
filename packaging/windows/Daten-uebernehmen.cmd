@echo off
setlocal
chcp 65001 >nul
set "LERNAPP_EXE=%LOCALAPPDATA%\Lernapp\app\.venv\Scripts\lernapp.exe"
if not exist "%LERNAPP_EXE%" (
  echo Bitte zuerst install.cmd aus dem Windows-Installer ausfuehren.
  pause
  exit /b 1
)
echo Lernapp - Daten auf diesen Laptop uebernehmen
echo Bitte Lernapp vorher beenden. Vorhandene Lerndaten werden nicht ersetzt.
echo.
"%LERNAPP_EXE%" restore %*
echo.
pause
