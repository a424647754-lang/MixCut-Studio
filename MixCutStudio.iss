; -*- coding: utf-8 -*-
; MixCut Studio · Inno Setup 安装包脚本
; 打包源：PyInstaller 产物 dist\MixCutStudio（整个文件夹，含 ffmpeg/ffprobe）

#define MyAppName "MixCut Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "MixCut"
#define MyAppExeName "MixCutStudio.exe"

[Setup]
; AppId 固定不变，后续升级才能识别为同一程序（双大括号为 Inno 转义）
AppId={{8F2A6C4E-7D3B-4A9E-9C21-5B6F7A8D9E01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\MixCutStudio
DefaultGroupName=MixCut Studio
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=MixCutStudio_Setup_v1.0.0
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=assets\app.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 递归打包整个 PyInstaller 文件夹，保持目录结构与权限
Source: "dist\MixCutStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MixCut Studio"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "MixCut.Studio.Desktop"
Name: "{group}\卸载 MixCut Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MixCut Studio"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "MixCut.Studio.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,MixCut Studio}"; Flags: nowait postinstall skipifsilent
