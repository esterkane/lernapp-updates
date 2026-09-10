param([Parameter(Mandatory=$true)][string]$AppDir)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $AppDir '.venv\Scripts\lernapp-desktop.exe'
if (-not (Test-Path $exe)) { throw 'Lernapp desktop launcher is missing' }
$shell = New-Object -ComObject WScript.Shell
foreach ($location in @('Desktop', 'Programs')) {
    $folder = [Environment]::GetFolderPath($location)
    if ([string]::IsNullOrWhiteSpace($folder)) { continue }
    if ($location -eq 'Programs') { $folder = Join-Path $folder 'Lernapp' }
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $names = @('Lernapp')
    if ($location -eq 'Programs') { $names += 'Lernapp beenden' }
    foreach ($name in $names) {
        $link = $shell.CreateShortcut((Join-Path $folder ($name + '.lnk')))
        $link.TargetPath = $exe
        $link.Arguments = if ($name -eq 'Lernapp') { 'start' } else { 'stop' }
        $link.WorkingDirectory = $AppDir
        $link.IconLocation = (Join-Path $AppDir 'lernapp.ico') + ',0'
        $link.Save()
    }
}
