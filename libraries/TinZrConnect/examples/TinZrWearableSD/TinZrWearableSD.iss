[Setup]
AppId={{A1B2C3D4-E5F6-47A8-9B10-112233445566}}
UsePreviousAppDir=no
AppName=TinZr Wearable SD
AppVersion=1.0.0
AppPublisher=Ludvik Alkhoury
DefaultDirName={pf}\TinZr Wearable SD
DefaultGroupName=TinZr Wearable SD
OutputBaseFilename=TinZrWearableSDSetup
; Optional installer icon if you have one:
SetupIconFile=C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrWearableSD\TinZr_small_logo.ico
Compression=lzma
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrWearableSD\dist\TinZrWearableSD.exe"; DestDir: "{app}"; Flags: ignoreversion
; If you have extra files (e.g., an icon), you can also include:
Source: "C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrWearableSD\TinZr_small_logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\TinZr Wearable SD"; Filename: "{app}\TinZrWearableSD.exe"
Name: "{commondesktop}\TinZr Wearable SD"; Filename: "{app}\TinZrWearableSD.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TinZrWearableSD.exe"; Description: "Launch TinZr Wearable SD"; Flags: nowait postinstall skipifsilent

