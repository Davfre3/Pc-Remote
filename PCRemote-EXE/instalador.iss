; Instalador de PC Remote (Inno Setup 6). Instala el programa, el driver ViGEmBus y abre el puerto en el firewall.
[Setup]
; AppId igual al de la 1.0 (el nombre): instalar una version nueva se pone ENCIMA de la anterior (actualiza), sin desinstalar
AppId=PC Remote
AppName=PC Remote
AppVersion=1.1.4
VersionInfoVersion=1.1.3
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
AppPublisher=PC Remote
DefaultDirName={autopf}\PC Remote
DefaultGroupName=PC Remote
OutputDir=dist
OutputBaseFilename=PC-Remote-Instalador
SetupIconFile=app\icon.ico
UninstallDisplayIcon={app}\PC Remote.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked
Name: "autostart"; Description: "Abrir PC Remote al iniciar Windows"; Flags: unchecked

[Files]
Source: "dist\PC Remote\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\PC Remote"; Filename: "{app}\PC Remote.exe"
Name: "{autodesktop}\PC Remote"; Filename: "{app}\PC Remote.exe"; Tasks: desktopicon
Name: "{commonstartup}\PC Remote"; Filename: "{app}\PC Remote.exe"; Tasks: autostart

[Run]
; El driver del mando virtual (viene dentro del programa); si ya esta, msiexec lo deja como esta
Filename: "msiexec.exe"; Parameters: "/i ""{app}\_internal\vgamepad\win\vigem\install\x64\ViGEmBusSetup_x64.msi"" /passive /norestart"; StatusMsg: "Instalando el driver del mando virtual (ViGEmBus)..."; Flags: waituntilterminated
; Los moviles de la red local entran por estos puertos
Filename: "netsh.exe"; Parameters: "advfirewall firewall add rule name=""PC Remote"" dir=in action=allow protocol=TCP localport=8765-8766 profile=private"; Flags: runhidden
Filename: "{app}\PC Remote.exe"; Description: "Abrir PC Remote"; Flags: postinstall nowait

[UninstallRun]
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""PC Remote"""; Flags: runhidden; RunOnceId: "PCRemoteFw"
