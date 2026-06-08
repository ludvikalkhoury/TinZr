[Setup]
AppId={{A1B2C3D4-E5F6-47A8-9B10-112233445566}
AppName=TinZr Wearable
AppVersion=1.0.0
AppPublisher=Ludvik Alkhoury
DefaultDirName={pf}\TinZr Wearable
DefaultGroupName=TinZr Wearable
OutputBaseFilename=TinZrWearableSetup
; Optional installer icon if you have one:
SetupIconFile=C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrWearable\TinZr_small_logo.ico
Compression=lzma
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrWearable\dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\TinZr Wearable"; Filename: "{app}\TinZrWearable.exe"
Name: "{commondesktop}\TinZr Wearable"; Filename: "{app}\TinZrWearable.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TinZrWearable.exe"; Description: "Launch TinZr Wearable"; Flags: nowait postinstall skipifsilent

