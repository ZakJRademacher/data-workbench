; Data Workbench setup for Windows (Inno Setup 6). Built by tools/build_installer.py, which passes:
;   /DAppVersion=3.0.0   /DStage=<the extracted bundled download>   /DOutputDir=<where setup.exe goes>
;
; Installs for the current user only (no administrator rights), into
; %LOCALAPPDATA%\Programs\Data Workbench. Projects live in %LOCALAPPDATA%\DataWorkbench\projects,
; outside the program folder, so upgrades and uninstalling never touch them.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef Stage
  #error Pass /DStage=<extracted bundled download>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
; Never change AppId: Windows uses it to recognise upgrades of the same program.
AppId={{E52105C2-B5AD-444E-BAE6-D8681D3E26F5}
AppName=Data Workbench
AppVersion={#AppVersion}
AppVerName=Data Workbench {#AppVersion}
AppPublisher=Zak J. Rademacher
AppPublisherURL=https://github.com/ZakJRademacher/data-workbench
AppSupportURL=https://github.com/ZakJRademacher/data-workbench/issues
DefaultDirName={localappdata}\Programs\Data Workbench
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=Data-Workbench-{#AppVersion}-setup
SetupIconFile=workbench.ico
UninstallDisplayIcon={app}\packaging\workbench.ico
UninstallDisplayName=Data Workbench
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; The open app holds this mutex (workbench/desktop.py), so setup and uninstall ask to close it first.
AppMutex=ZakJRademacher.DataWorkbench
CloseApplications=no
LicenseFile={#Stage}\LICENSE

[Tasks]
Name: desktopicon; Description: "Add a desktop shortcut"; Flags: unchecked

[InstallDelete]
; An upgrade replaces the program files completely, so nothing from an older version lingers.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\workbench"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\licenses"
Type: filesandordirs; Name: "{app}\packaging"
Type: filesandordirs; Name: "{app}\tests"
Type: filesandordirs; Name: "{app}\evidence"
Type: filesandordirs; Name: "{app}\deliverables"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Excludes: "projects\*,Start Workbench.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs
; Marks this copy as installed: projects are kept in the user's app data (workbench.common.installed).
Source: "installed.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Data Workbench"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B -m workbench.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\workbench.ico"; AppUserModelID: "ZakJRademacher.DataWorkbench"; Comment: "Prepare and reconcile CSV and Excel files"
Name: "{autodesktop}\Data Workbench"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B -m workbench.desktop"; WorkingDir: "{app}"; IconFilename: "{app}\packaging\workbench.ico"; AppUserModelID: "ZakJRademacher.DataWorkbench"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: "-B -m workbench.desktop"; WorkingDir: "{app}"; Description: "Open Data Workbench"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Program files only: the folders setup created, then the program folder if it is now empty.
; Anything else in it, and projects, logs and working files in %LOCALAPPDATA%\DataWorkbench, stay.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\workbench"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\licenses"
Type: filesandordirs; Name: "{app}\packaging"
Type: filesandordirs; Name: "{app}\tests"
Type: filesandordirs; Name: "{app}\evidence"
Type: filesandordirs; Name: "{app}\deliverables"
Type: dirifempty; Name: "{app}"

[Messages]
FinishedLabel=Data Workbench is installed. Your projects are kept in your user app-data folder, so upgrading or uninstalling the program never removes them.
