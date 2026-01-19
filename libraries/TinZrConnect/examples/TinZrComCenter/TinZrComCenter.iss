[Setup]
AppId={{A1B2C3D4-E5F6-47A8-9B10-112233445567}}
UsePreviousAppDir=no
AppName=TinZr Communication Center
AppVersion=1.0.0
AppPublisher=Ludvik Alkhoury
DefaultDirName={pf}\TinZr Communication Center
DefaultGroupName=TinZr Communication Center
OutputBaseFilename=TinZrComCenterSetup
; Optional installer icon if you have one:
SetupIconFile=C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrComCenter\TinZr_small_logo.ico
Compression=lzma
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrComCenter\dist\TinZrComCenter.exe"; DestDir: "{app}"; Flags: ignoreversion
; If you have extra files (e.g., an icon), you can also include:
Source: "C:\Users\lua4006\Desktop\GitRepo\TinZr\libraries\TinZrConnect\examples\TinZrComCenter\TinZr_small_logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\TinZr Communication Center"; Filename: "{app}\TinZrComCenter.exe"
Name: "{commondesktop}\TinZr Communication Center"; Filename: "{app}\TinZrComCenter.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TinZrComCenter.exe"; Description: "Launch TinZr Communication Center"; Flags: nowait postinstall skipifsilent

