<#
.SYNOPSIS
  Lernapp – Installation/Update für Windows 10/11 (x64), ohne Administratorrechte.

.DESCRIPTION
  1. installiert uv (Python-Paketmanager) nach %USERPROFILE%\.local\bin, falls es fehlt
  2. lädt Python 3.12 über uv
  3. kopiert den Quellcode nach %LOCALAPPDATA%\Lernapp\app (bestehende .venv bleibt erhalten)
  4. uv sync --frozen --no-dev   (Internetverbindung nötig, beim ersten Mal einige Minuten, ~1-2 GB)
  5. legt Verknüpfungen "Lernapp" im Startmenü und auf dem Desktop an
  Lerndaten liegen in %LOCALAPPDATA%\Lernapp (pg\, .env, logs\) und werden bei Updates nie angefasst.

.PARAMETER SourceDir   Quellverzeichnis (Standard: <Ordner dieses Skripts>\src)
.PARAMETER AppDir      Zielverzeichnis  (Standard: %LOCALAPPDATA%\Lernapp\app)
.PARAMETER NoShortcuts keine Verknüpfungen anlegen
.PARAMETER DownloadModels  zusätzlich das kleine faster-whisper-Modell herunterladen
#>
[CmdletBinding()]
param(
    [string]$SourceDir = '',
    [string]$AppDir = '',
    [switch]$NoShortcuts,
    [switch]$DownloadModels
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host "FEHLER: $msg" -ForegroundColor Red
    Write-Host "Internetverbindung prüfen und das Skript erneut ausführen."
    exit 1
}

# Resolve script-dependent defaults only after parameter binding. Windows PowerShell
# can expose an empty PSScriptRoot while evaluating a param() default expression.
$installerRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($installerRoot) -and $PSCommandPath) {
    $installerRoot = Split-Path -Parent $PSCommandPath
}
if ([string]::IsNullOrWhiteSpace($installerRoot)) {
    Fail "Skriptordner fehlt. ZIP vollstaendig entpacken und install.cmd doppelklicken."
}
if ([string]::IsNullOrWhiteSpace($SourceDir)) {
    $SourceDir = Join-Path $installerRoot 'src'
    # The installed repair shortcut lives in installer/, alongside app/.
    $installedSource = Join-Path (Split-Path -Parent $installerRoot) 'app'
    if (-not (Test-Path -LiteralPath $SourceDir) -and (Test-Path -LiteralPath (Join-Path $installedSource 'pyproject.toml'))) {
        $SourceDir = $installedSource
    }
}
if ([string]::IsNullOrWhiteSpace($AppDir)) {
    $localData = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($localData)) {
        Fail "Windows konnte den lokalen Benutzerordner nicht bestimmen. Bitte unter deinem normalen Windows-Konto starten."
    }
    $AppDir = Join-Path $localData 'Lernapp\app'
}

Write-Host "Lernapp-Installation für Windows"
Write-Host "  Quelle: $SourceDir"
Write-Host "  Ziel:   $AppDir"

if (-not (Test-Path (Join-Path $SourceDir 'pyproject.toml'))) { Fail "App-Dateien fehlen: $SourceDir. Bitte die ZIP mit Alle extrahieren vollstaendig entpacken und install.cmd neben dem src-Ordner starten." }
if (-not (Test-Path (Join-Path $SourceDir 'uv.lock'))) { Fail "Quellverzeichnis ungültig (uv.lock fehlt): $SourceDir" }
if (-not [Environment]::Is64BitOperatingSystem) { Fail "Lernapp benötigt ein 64-Bit-Windows." }

# ---------------------------------------------------------------- 1. uv ---------------------
Step "uv (Python-Paketmanager) prüfen"
$uvLocal = Join-Path $env:USERPROFILE '.local\bin'
$env:Path = "$uvLocal;$env:Path"
$uv = Get-Command uv.exe -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "uv nicht gefunden – wird nach $uvLocal installiert (Internetverbindung nötig) ..."
    $env:UV_INSTALL_DIR = $uvLocal
    $env:UV_NO_MODIFY_PATH = '1'
    try {
        Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -UseBasicParsing)
    } catch {
        Fail "uv konnte nicht installiert werden: $($_.Exception.Message)"
    }
    $env:Path = "$uvLocal;$env:Path"
    $uv = Get-Command uv.exe -ErrorAction SilentlyContinue
    if (-not $uv) { Fail "uv ist nach der Installation nicht auffindbar ($uvLocal\uv.exe)." }
    # make uv available in future terminals (user PATH only)
    try {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not $userPath) { $userPath = '' }
        if (($userPath -split ';') -notcontains $uvLocal) {
            [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $uvLocal).TrimStart(';'), 'User')
        }
    } catch { Write-Host "Hinweis: PATH konnte nicht dauerhaft ergänzt werden ($($_.Exception.Message))." }
}
$uvExe = $uv.Source
Write-Host "uv: $(& $uvExe --version)"

# ---------------------------------------------------------------- 2. Python 3.12 ------------
Step "Python 3.12 bereitstellen"
& $uvExe python install 3.12
if ($LASTEXITCODE -ne 0) { Fail "Python 3.12 konnte nicht heruntergeladen werden." }

# ---------------------------------------------------------------- 3. Quellcode kopieren -----
Step "App-Dateien nach $AppDir kopieren"
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$srcFull = (Resolve-Path $SourceDir).Path.TrimEnd('\')
$dstFull = (Resolve-Path $AppDir).Path.TrimEnd('\')
if ($srcFull -ieq $dstFull) {
    Write-Host "Quelle und Ziel sind identisch – Kopieren übersprungen."
} else {
    $excludeDirs = @('.git', '.venv', 'data', 'dist', 'build', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache', 'node_modules', 'lernapp-kit')
    $args = @($srcFull, $dstFull, '/MIR', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:2', '/W:2', '/XF', '.env', '.DS_Store', '/XD') + $excludeDirs
    & robocopy.exe @args | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "Kopieren fehlgeschlagen (robocopy Code $LASTEXITCODE)." }
}
$iconSrc = Join-Path $installerRoot 'lernapp.ico'
if (Test-Path $iconSrc) { Copy-Item $iconSrc (Join-Path $AppDir 'lernapp.ico') -Force }

# ---------------------------------------------------------------- 4. Abhängigkeiten ---------
Step "Python-Abhängigkeiten installieren (uv sync – beim ersten Mal einige Minuten, ~1-2 GB)"
Push-Location $AppDir
try {
    $env:UV_PYTHON_PREFERENCE = 'managed'
    & $uvExe sync --frozen --inexact --no-dev --python 3.12
    if ($LASTEXITCODE -ne 0) { Fail "uv sync fehlgeschlagen." }
} finally { Pop-Location }

$lernappExe = Join-Path $AppDir '.venv\Scripts\lernapp.exe'
if (-not (Test-Path $lernappExe)) { Fail "Startprogramm fehlt nach der Installation: $lernappExe" }
$versionLine = & $lernappExe version
if ($LASTEXITCODE -ne 0) { Fail "Das Startprogramm lässt sich nicht ausführen." }

# ---------------------------------------------------------------- 5. Verknüpfungen ----------
if (-not $NoShortcuts) {
    Step "Verknüpfungen anlegen"
    $shell = New-Object -ComObject WScript.Shell
    $iconPath = Join-Path $AppDir 'lernapp.ico'
    $targets = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Lernapp.lnk'),
        (Join-Path (Join-Path ([Environment]::GetFolderPath('Programs')) 'Lernapp') 'Lernapp.lnk')
    )
    foreach ($lnkPath in $targets) {
        New-Item -ItemType Directory -Force -Path (Split-Path $lnkPath) | Out-Null
        $lnk = $shell.CreateShortcut($lnkPath)
        $lnk.TargetPath = $lernappExe
        $lnk.Arguments = 'start'
        $lnk.WorkingDirectory = $AppDir
        $lnk.Description = 'Lernapp starten (Datenbank, Server, Oberfläche)'
        if (Test-Path $iconPath) { $lnk.IconLocation = "$iconPath,0" }
        $lnk.Save()
        Write-Host "  $lnkPath"
    }
    $restoreLnk = $shell.CreateShortcut((Join-Path (Join-Path ([Environment]::GetFolderPath('Programs')) 'Lernapp') 'Daten uebernehmen.lnk'))
    $restoreLnk.TargetPath = $lernappExe
    $restoreLnk.Arguments = 'restore'
    $restoreLnk.WorkingDirectory = $AppDir
    $restoreLnk.Description = 'Verschluesselte Sicherung auf diesem Laptop uebernehmen'
    $restoreLnk.Save()
    # "Lernapp beenden" in the Start Menu
    $stopLnk = $shell.CreateShortcut((Join-Path (Join-Path ([Environment]::GetFolderPath('Programs')) 'Lernapp') 'Lernapp beenden.lnk'))
    $stopLnk.TargetPath = $lernappExe
    $stopLnk.Arguments = 'stop'
    $stopLnk.WorkingDirectory = $AppDir
    $stopLnk.Description = 'Laufende Lernapp beenden'
    if (Test-Path $iconPath) { $stopLnk.IconLocation = "$iconPath,0" }
    $stopLnk.Save()
}

# ---------------------------------------------------------------- 6. optional: STT-Modell ---
if ($DownloadModels) {
    Step "Spracherkennungs-Modell (faster-whisper small) herunterladen"
    & $lernappExe download-models --size small
    if ($LASTEXITCODE -ne 0) { Write-Host "WARNUNG: Modell-Download fehlgeschlagen – später mit 'lernapp download-models --size small' nachholen." -ForegroundColor Yellow }
}

# ---------------------------------------------------------------- fertig --------------------
Write-Host ""
Write-Host "================================================================================" -ForegroundColor Green
Write-Host "$versionLine wurde installiert." -ForegroundColor Green
Write-Host ""
Write-Host "  Programm:  $AppDir"
Write-Host "  Daten:     $(Split-Path $AppDir)"
Write-Host "  Starten:   Verknüpfung 'Lernapp' auf dem Desktop / im Startmenü"
Write-Host "             oder:  `"$lernappExe`" start"
Write-Host "  Beenden:   Startmenü → Lernapp → 'Lernapp beenden'  (oder das Startfenster schließen)"
Write-Host "  Prüfen:    `"$lernappExe`" doctor"
Write-Host ""
Write-Host "Beim ersten Start wird die Datenbank angelegt (ca. 30-60 Sekunden), danach öffnet sich der"
Write-Host "Browser automatisch. API-Schlüssel werden in der App unter 'Einrichtung' eingetragen."
Write-Host "Für die lokale Spracherkennung einmalig:  `"$lernappExe`" download-models --size small"
Write-Host "================================================================================" -ForegroundColor Green
exit 0
