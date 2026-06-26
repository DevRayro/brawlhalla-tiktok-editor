; Inno Setup 6 script for the Brawlhalla TikTok Editor.
; Build with: iscc installers\windows\installer.iss
; Output:     dist\BrawlhallaEditor-Setup-<version>.exe
;
; What this installs:
;   - The repo source code (~5 MB) into %LocalAppData%\BrawlhallaEditor.
;   - Start menu + desktop shortcuts pointing at start.bat.
;   - First-run setup is *not* triggered here. start.bat detects the
;     missing .install-complete marker and runs the first-run GUI.

#define AppName "Brawlhalla TikTok Editor"
#define AppVersion "1.0.16"
#define AppPublisher "Brawlhalla TikTok Editor contributors"
#define AppExeName "start.bat"

[Setup]
AppId={{2C6F8E12-1C9A-4B1F-9B5F-7A52A60A3F11}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\BrawlhallaEditor
DefaultGroupName={#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; %LocalAppData% lets us install per-user without UAC.
OutputDir=..\..\dist
OutputBaseFilename=BrawlhallaEditor-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Replace with your icon file if you ship one:
SetupIconFile=app.ico
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french";  MessagesFile: "compiler:Languages\French.isl"

[Files]
; Ship the dedicated icon so Start menu / Desktop shortcuts use it.
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion
; Ship every text/code asset, but skip the heavy artefacts that get
; populated at first-run (or that don't exist in CI).
Source: "..\..\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; \
  Excludes: "\.git\*,\.venv\*,_local_jobs\*,_cache\*,output\*,work\*,models\*.pt,models\*.pth,remotion\node_modules\*,remotion\public\*-base.mp4,remotion\public\*-source.mp4,remotion\public\*-audio.m4a,dist\*,installers\dist\*,*.pyc,__pycache__\*"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; GroupDescription: "Raccourcis :"; Flags: checkedonce

[Run]
; Launch on finish (optional).
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; Wipe the per-user state on uninstall (the stuff first-run created).
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\_local_jobs"
Type: filesandordirs; Name: "{app}\_cache"
Type: filesandordirs; Name: "{app}\remotion\node_modules"
Type: filesandordirs; Name: "{app}\remotion\public"
Type: files;          Name: "{app}\.install-complete"

; --- Optional: code-signing ----------------------------------------------
; To sign the resulting installer with an EV certificate, configure the
; SignTool macro and uncomment the line below:
;
; SignTool=mysigntool
;
; Then in Inno Setup → Tools → Configure Sign Tools, register a tool named
; "mysigntool" with command:
;   "C:\Program Files (x86)\Windows Kits\10\App Certification Kit\signtool.exe" \
;     sign /tr http://timestamp.digicert.com /td sha256 /fd sha256 \
;     /a $f
