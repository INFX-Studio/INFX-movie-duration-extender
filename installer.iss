; INFX Movie Duration Extender Windows 설치 파일(Inno Setup 6)
; scripts\build_app.ps1 이 PyInstaller 빌드 후 ISCC /DAppVersion=<버전> 으로 실행합니다.
; 관리자 권한 없이 사용자별로 %LOCALAPPDATA%\Programs 아래에 설치하므로 자동 업데이트도 권한 확인 없이 덮어 설치합니다.

#ifndef AppVersion
  #error AppVersion 을 지정해 주세요. 예: ISCC /DAppVersion=1.0.3 installer.iss
#endif

#define AppName "INFX Movie Duration Extender"
#define AppExe AppName + ".exe"

[Setup]
; AppId 는 업그레이드·제거 기준입니다. 바꾸지 마십시오.
AppId={{33805D5A-2135-4C0E-9AF3-81E9D813E3A5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=INFX Studio
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist
OutputBaseFilename=INFX-Movie-Duration-Extender-{#AppVersion}-win64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; 이전 버전의 라이브러리가 남지 않도록 PyInstaller 런타임 폴더를 비우고 새로 설치합니다.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
