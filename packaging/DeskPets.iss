; Inno Setup script for DeskPets. Built by `pixi run -e build build-installer`, which passes
; /DAppVersion=<src/__init__.py version> so the installer, exe metadata and tag share one number.
#ifndef AppVersion
  #error AppVersion must be passed on the command line: ISCC /DAppVersion=0.1.1 DeskPets.iss
#endif

[Setup]
; Never change AppId: Inno uses it to recognise an older install and upgrade it in place.
AppId={{814EA053-0D23-4ED0-9E4E-7C0DAB0E1B9C}
AppName=DeskPets
AppVersion={#AppVersion}
AppPublisher=Suyash Naik
AppPublisherURL=https://suyashnaik.com/DeskPets
AppSupportURL=https://github.com/Suyash-Naik/DeskPets/issues
; Per-user install: no admin prompt, lands in %LOCALAPPDATA%\Programs\DeskPets.
PrivilegesRequired=lowest
DefaultDirName={autopf}\DeskPets
DefaultGroupName=DeskPets
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; DeskPets sits in the tray, so ask Windows to close it before replacing its files.
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE
SetupIconFile=DeskPets.ico
UninstallDisplayIcon={app}\DeskPets.exe
OutputDir=..\dist
OutputBaseFilename=DeskPets-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Off unless the user ticks it: a program should not start itself without being asked.
Name: "autostart"; Description: "Would you like to start DeskPets when signin into Windows automatically?"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\deskpets.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DeskPets"; Filename: "{app}\DeskPets.exe"

[Registry]
; Per-user Run key; removed on uninstall. Nothing is written unless the task is ticked.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DeskPets"; ValueData: """{app}\DeskPets.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\DeskPets.exe"; Description: "Launch DeskPets"; Flags: nowait postinstall skipifsilent unchecked

; Settings live in %APPDATA%\LC-pet-stat and are deliberately NOT listed under [UninstallDelete],
; so they survive both uninstall and upgrade.
