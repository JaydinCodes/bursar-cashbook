; Build after PyInstaller: ISCC installer\BursarCashbook.iss
#define MyAppName "Ledgerly"
#define MyAppVersion "0.8.0-rc1"
#define MyAppPublisher "Ledgerly"
#define MyAppExeName "BursarCashbook.exe"

[Setup]
AppId={{C7FE7CF9-0957-4C59-8F85-CE9190B231D3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Bursar Cashbook
DefaultGroupName={#MyAppName}
OutputDir=..\release
OutputBaseFilename=BursarCashbook-0.8.0-rc1-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\ledgerly.ico
UninstallDisplayName={#MyAppName}
; Accounting data is in %LOCALAPPDATA%\BursarCashbook and is not installed.
; Uninstall removes the database so a later installation starts fresh.

[Files]
Source: "..\dist\BursarCashbook.exe"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
Type: files; Name: "{localappdata}\BursarCashbook\data\cashbook.db*"

[Icons]
Name: "{autoprograms}\Ledgerly"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Ledgerly"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Bursar Cashbook"; Flags: nowait postinstall skipifsilent
