; LiveTrans Windows 安装器（Inno Setup 6）
;
; 前置：先跑 packaging/build_windows.py --portable 产出 dist/LiveTrans/
; 编译：iscc /DVERSION=1.2.3 packaging/installer.iss
;       （CI 的 release.yml 自动传 /DVERSION；本地不传则用 0.0.0-dev）
;
; 说明：
; - 代码装到 {autopf}\LiveTrans（只读使用）；config/keys/识别模型等用户数据
;   由程序自动放到 %LOCALAPPDATA%\LiveTrans（见 livetrans/paths.py 的
;   DATA_DIR 约定）——Program Files 只读安装天然适配，卸载重装不丢用户数据；
; - 打包时若检出 models/ 会一并装上（离线版）；没有则是首启联网下载版。

#define AppName "LiveTrans"
#define AppPublisher "LiveTrans"
#define AppExe "LiveTrans.exe"

#ifndef VERSION
#define VERSION "0.0.0-dev"
#endif

[Setup]
AppId={{7A1E4C6B-9D2F-4B58-8E3A-C51D0F6A92B4}
AppName={#AppName}
AppVersion={#VERSION}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=LiveTrans-{#VERSION}-win-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\LiveTrans\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} 字幕外挂"; Filename: "{app}\{#AppExe}"; \
    Parameters: "--overlay"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; 无：运行中的外挂/主程序由 Windows 在卸载前提示用户关闭
