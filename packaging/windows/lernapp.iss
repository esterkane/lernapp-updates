; Inno Setup script for Lernapp (built in CI on windows-latest: iscc /DAppVersion=x.y.z packaging\windows\lernapp.iss)
; Bundles the source tree + install.ps1 and runs install.ps1 after copying (uv + Python 3.12 + uv sync,
; needs internet). Per-user install, no admin rights. Data (%LOCALAPPDATA%\Lernapp\pg, .env, logs) is
; never removed by the uninstaller.
#ifndef AppVersion
  #error AppVersion must match pyproject.toml
#endif
#ifndef PayloadRoot
  #error PayloadRoot must point to the verified ZIP staging src directory
#endif
#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

[Setup]
AppId={{7E2C7C1A-5B1B-4F6E-9C2D-LERNAPP00001}
AppName=Lernapp
AppVersion={#AppVersion}
AppVerName=Lernapp {#AppVersion}
AppPublisher=Lernapp
DefaultDirName={localappdata}\Lernapp
DisableDirPage=yes
DefaultGroupName=Lernapp
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#SourceRoot}\dist
OutputBaseFilename=Lernapp-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Lernapp
UninstallFilesDir={app}\uninstall
#ifexist "lernapp.ico"
SetupIconFile=lernapp.ico
UninstallDisplayIcon={app}\app\lernapp.ico
#endif

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
german.WelcomeLabel2=Dieses Programm installiert Lernapp {#AppVersion} auf Ihrem Computer.%n%nNach dem Kopieren werden Python und alle Abhängigkeiten automatisch heruntergeladen (ca. 1–2 GB, Internetverbindung nötig). Das dauert einige Minuten – bitte das schwarze Fenster nicht schließen.
german.FinishedLabelNoIcons=Lernapp wurde eingerichtet. Starten Sie Lernapp über das Startmenü oder die Desktop-Verknüpfung; der Browser öffnet sich automatisch.

[Files]
; source tree → {app}\app (install.ps1 skips the copy when SourceDir == AppDir)
Source: "{#PayloadRoot}\*"; DestDir: "{app}\app"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "install.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "install.cmd"; DestDir: "{app}\installer"; Flags: ignoreversion
#ifexist "lernapp.ico"
Source: "lernapp.ico"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "lernapp.ico"; DestDir: "{app}\app"; Flags: ignoreversion
#endif

[Run]
; Runs the bootstrap in a visible console so the user sees progress. StatusMsg shows in the wizard.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\install.ps1"" -SourceDir ""{app}\app"" -AppDir ""{app}\app"" -NoShortcuts"; \
  StatusMsg: "Python und Abhängigkeiten werden installiert (einige Minuten, Internet nötig) …"; \
  Flags: waituntilterminated

[Icons]
Name: "{group}\Daten uebernehmen"; Filename: "{app}\app\.venv\Scripts\lernapp.exe"; Parameters: "restore"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\lernapp.ico"
Name: "{group}\Lernapp"; Filename: "{app}\app\.venv\Scripts\lernapp.exe"; Parameters: "start"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\lernapp.ico"; Comment: "Lernapp starten"
Name: "{group}\Lernapp beenden"; Filename: "{app}\app\.venv\Scripts\lernapp.exe"; Parameters: "stop"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\lernapp.ico"
Name: "{group}\Lernapp reparieren (erneut einrichten)"; Filename: "{app}\installer\install.cmd"; WorkingDir: "{app}\installer"
Name: "{userdesktop}\Lernapp"; Filename: "{app}\app\.venv\Scripts\lernapp.exe"; Parameters: "start"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\lernapp.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop anlegen"; GroupDescription: "Zusätzliche Symbole:"

[UninstallRun]
Filename: "{app}\app\.venv\Scripts\lernapp.exe"; Parameters: "stop"; Flags: runhidden skipifdoesntexist; RunOnceId: "stoplernapp"

[UninstallDelete]
; program + virtualenv only – the data directory ({app}\pg, {app}\.env, {app}\logs) stays.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\installer"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then begin
    MsgBox('Lernapp benötigt ein 64-Bit-Windows.', mbError, MB_OK);
    Result := False;
  end;
end;
