; ============================================================================
;  LaCasaDelPanApp - Instalador profesional (Inno Setup 6)
;
;  Compilar:
;     .\empaquetar_instalador.ps1
;     .\empaquetar_instalador.ps1 -Version 0.1.0
;
;  Requiere que exista el paquete portable:
;     dist\LaCasaDelPanApp\LaCasaDelPanApp.exe
;
;  Distribución:
;     - Programa (solo lectura) .......... {autopf}\LaCasaDelPanApp
;     - Datos (escritura usuarios) ....... {commonappdata}\LaCasaDelPanApp
;
;  El desinstalador NUNCA borra la carpeta de datos ni la base de datos.
; ============================================================================

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "La Casa del Pan - POS"
#define MyAppExeName "LaCasaDelPanApp.exe"
#define MyAppFolder "LaCasaDelPanApp"
#define MyAppPublisher "La Casa del Pan S.A.C."
#define MyAppCopyright "© 2026 La Casa del Pan S.A.C."
#define MyAppDataDir "{commonappdata}\LaCasaDelPanApp"

[Setup]
; AppId estable: permite actualizar encima sin crear otra entrada en Windows.
AppId={{93940016-9A88-4A73-BEB1-3C7E5EFBE499}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=Punto de venta e inventario para panadería
AppCopyright={#MyAppCopyright}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Instalador de {#MyAppName}
VersionInfoProductName={#MyAppName}
DefaultDirName={autopf}\{#MyAppFolder}
DefaultGroupName=La Casa del Pan
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=LaCasaDelPanApp_Setup_{#MyAppVersion}
SetupIconFile=assets\LaCasaDelPanApp.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
; Se crea por defecto; el usuario puede desmarcarlo en el asistente.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 1) Programa completo (onedir de PyInstaller).
Source: "dist\LaCasaDelPanApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 2) Configuración inicial: solo si no existe (nunca pisa la del negocio).
Source: "config\config.example.ini"; DestDir: "{#MyAppDataDir}\config"; DestName: "config.ini"; Flags: onlyifdoesntexist uninsneveruninstall

[Dirs]
; Carpeta raíz de datos y subcarpetas. Se crean con permiso Modify para
; BUILTIN\Users, de modo que un cajero estándar (sin admin) pueda escribir.
; uninsneveruninstall: el desinstalador jamás las elimina.
Name: "{#MyAppDataDir}";                 Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\config";          Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\db";              Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\db\respaldos";    Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\tickets";         Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\boletas";         Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\facturas";        Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\xml";             Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\cdr";             Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\logs";            Permissions: users-modify; Flags: uninsneveruninstall
Name: "{#MyAppDataDir}\reportes";        Permissions: users-modify; Flags: uninsneveruninstall

[Icons]
; Menú Inicio (todos los usuarios).
Name: "{group}\{#MyAppName}";               Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}";   Filename: "{uninstallexe}"
; Escritorio (según la tarea, marcada por defecto).
Name: "{autodesktop}\{#MyAppName}";         Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { Aviso explícito: los datos del negocio quedan intactos. }
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
    MsgBox(
      'Se desinstaló el programa, pero los datos de la panadería se conservaron en:' +
      Chr(13) + Chr(10) + Chr(13) + Chr(10) +
      ExpandConstant('{#MyAppDataDir}') + Chr(13) + Chr(10) + Chr(13) + Chr(10) +
      'Puedes volver a instalar el POS y seguirás teniendo tu base de datos, ' +
      'configuración, tickets y comprobantes.',
      mbInformation,
      MB_OK
    );
end;
