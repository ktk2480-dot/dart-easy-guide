; Inno Setup 설치 파일 설정. build_installer.py로 실행한다.
#define AppName "DART 쉬운 가이드"
#define RepoUrl "https://github.com/ktk2480-dot/dart-easy-guide"

[Setup]
AppId={{7E3C9B14-5A2D-4F6E-9C81-2D0B6A4F71C3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=dart-easy-guide
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
DefaultDirName={autopf}\DartGuide
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=DartGuide_Setup_v{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\DartGuide.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} 설치 프로그램

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Messages]
WelcomeLabel2=[name/ver]을(를) 설치합니다.%n%n금융감독원 OpenDART의 공시를 코딩 없이 조회하는 프로그램입니다. 금융감독원의 공식 프로그램이 아니며, 결과는 투자 권유가 아닌 참고 자료입니다.%n%nAI 에이전트는 이 PC의 Claude Code 로그인을 먼저 쓰고, 로그인이 없을 때만 등록한 Claude API 키를 씁니다.%n%n관리자 권한 없이 현재 Windows 사용자에게만 설치됩니다.

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로 가기 만들기"

[InstallDelete]
; 업데이트할 때 이전 버전의 라이브러리가 남지 않게 한다.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; Excludes: "사용방법.txt"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#GuideFile}"; DestDir: "{app}"; DestName: "사용방법.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\DartGuide.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DartGuide.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DartGuide.exe"; Description: "{#AppName} 실행"; Flags: nowait postinstall skipifsilent

[Code]
function UserDataDir: String;
begin
  Result := GetEnv('DART_GUIDE_HOME');
  if Result = '' then
    Result := ExpandConstant('{localappdata}\DartGuide');
end;

procedure RemoveUserData(Folder: String);
begin
  { 이 프로그램이 만든 파일만 지우고, 다른 파일이 있으면 폴더를 남긴다. }
  DeleteFile(Folder + '\api_key.bin');
  DeleteFile(Folder + '\api_key.tmp');
  DeleteFile(Folder + '\claude_api_key.bin');
  DeleteFile(Folder + '\claude_api_key.tmp');
  DeleteFile(Folder + '\corp_index.sqlite');
  DeleteFile(Folder + '\corp_index.tmp');
  DeleteFile(Folder + '\derivative_scan.sqlite');
  DeleteFile(Folder + '\derivative_scan.sqlite-wal');
  DeleteFile(Folder + '\derivative_scan.sqlite-shm');
  DeleteFile(Folder + '\derivative_scan.sqlite-journal');
  DeleteFile(Folder + '\error.log');
  DelTree(Folder + '\matplotlib', True, True, True);
  RemoveDir(Folder);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Folder: String;
  ShouldRemove: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  Folder := UserDataDir;
  if not DirExists(Folder) then
    Exit;
  if UninstallSilent then
    ShouldRemove := ExpandConstant('{param:DELETEUSERDATA|0}') = '1'
  else
    ShouldRemove := MsgBox('저장된 인증키(OpenDART, Claude API), 회사 목록, 파생 전수조사 결과도 함께 삭제할까요?' + #13#10 + #13#10 +
      '[아니요]를 누르면 다시 설치했을 때 그대로 사용할 수 있습니다.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if ShouldRemove then
    RemoveUserData(Folder);
end;
