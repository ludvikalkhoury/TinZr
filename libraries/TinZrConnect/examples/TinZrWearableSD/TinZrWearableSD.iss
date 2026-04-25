[Setup]
AppId={{A1B2C3D4-E5F6-47A8-9B10-112233446655}
AppName=TinZr Wearable SD
AppVersion=1.0.0
AppPublisher=Ludvik Alkhoury
DefaultDirName={pf}\TinZr Wearable SD
DefaultGroupName=TinZr Wearable SD
OutputBaseFilename=TinZrWearableSDSetup
; Optional installer icon if you have one:
SetupIconFile=TinZr_small_logo.ico
Compression=lzma
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\TinZr Wearable SD"; Filename: "{app}\TinZrWearableSD.exe"
Name: "{commondesktop}\TinZr Wearable SD"; Filename: "{app}\TinZrWearableSD.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TinZrWearableSD.exe"; Description: "Launch TinZr Wearable SD"; Flags: nowait postinstall skipifsilent

