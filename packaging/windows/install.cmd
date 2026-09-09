@echo off
rem Lernapp installieren / aktualisieren (Doppelklick). Startet install.ps1 ohne Adminrechte.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Die Installation ist fehlgeschlagen (Code %RC%). Bitte die Meldungen oben pruefen.
echo Fenster mit einer beliebigen Taste schliessen ...
pause >nul
exit /b %RC%
