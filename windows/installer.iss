; Установщик PC5 Launcher для Windows (Inno Setup 6). Ставит в профиль пользователя, права администратора не нужны.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B6D1F0A2-5C5A-4D0E-9E57-0C5C5A5C5A01}
AppName=PC5 Launcher
AppVersion={#AppVersion}
AppPublisher=PC5 Launcher contributors
DefaultDirName={autopf}\PC5 Launcher
DefaultGroupName=PC5 Launcher
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\PC5Launcher.exe
SetupIconFile=assets\pc5.ico
LicenseFile=..\LICENSE
OutputDir=dist
OutputBaseFilename=PC5Launcher-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\PC5Launcher\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\PC5 Launcher"; Filename: "{app}\PC5Launcher.exe"
Name: "{autodesktop}\PC5 Launcher"; Filename: "{app}\PC5Launcher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PC5Launcher.exe"; Description: "{cm:LaunchProgram,PC5 Launcher}"; Flags: nowait postinstall skipifsilent
