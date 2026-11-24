; Inno Setup script: turns build\dist\Sushi into SushiSetup.exe
; Build:  iscc packaging\installer.iss   (after pyinstaller)
#define AppVersion GetEnv("SUSHI_VERSION")
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F1C2A5E-5B1D-4C77-9E0A-5A3B7C2D9E11}
AppName=Sushi
AppVersion={#AppVersion}
AppPublisher=Sushi
DefaultDirName={autopf}\Sushi
DefaultGroupName=Sushi
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\build\installer
OutputBaseFilename=SushiSetup-{#AppVersion}
SetupIconFile=..\sushi\data\logo.ico
UninstallDisplayIcon={app}\Sushi.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\build\dist\Sushi\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Sushi"; Filename: "{app}\Sushi.exe"
Name: "{group}\Uninstall Sushi"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Sushi"; Filename: "{app}\Sushi.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Sushi.exe"; Description: "Open Sushi"; Flags: nowait postinstall skipifsilent
