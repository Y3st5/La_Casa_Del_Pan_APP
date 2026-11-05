# ============================================================
#  Compila el instalador profesional con Inno Setup 6.
#
#  Uso:
#     .\empaquetar_instalador.ps1
#     .\empaquetar_instalador.ps1 -Version 0.1.0
#     .\empaquetar_instalador.ps1 -Version 0.1.0 -SkipAppBuild
#
#  Requisitos:
#     - Paquete portable en dist\LaCasaDelPanApp\ (si falta, lo compila).
#     - Inno Setup 6 instalado (ISCC.exe en el PATH o en Program Files).
#
#  Genera:
#     dist\LaCasaDelPanApp_Setup_<Version>.exe
# ============================================================
param(
    [string]$Version = '0.1.0',
    [switch]$SkipAppBuild
)

$ErrorActionPreference = 'Stop'
$Raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Raiz

$Exe = Join-Path $Raiz 'dist\LaCasaDelPanApp\LaCasaDelPanApp.exe'
$Icono = Join-Path $Raiz 'assets\LaCasaDelPanApp.ico'
$Plantilla = Join-Path $Raiz 'config\config.example.ini'
$Iss = Join-Path $Raiz 'installer.iss'

if (-not (Test-Path $Icono)) {
    throw "Falta el icono: $Icono"
}
if (-not (Test-Path $Plantilla)) {
    throw "Falta la plantilla de configuración: $Plantilla"
}
if (-not (Test-Path $Iss)) {
    throw "Falta el script de Inno Setup: $Iss"
}

if (-not $SkipAppBuild -and -not (Test-Path $Exe)) {
    Write-Host 'No existe el paquete portable; compilándolo primero...' -ForegroundColor Yellow
    & (Join-Path $Raiz 'empaquetar_piloto.ps1') -Version $Version
}
if (-not (Test-Path $Exe)) {
    throw "No se encontró el ejecutable del programa: $Exe"
}

function Buscar-Iscc {
    $candidatos = @()
    if ($env:ProgramFiles) {
        $candidatos += (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        $candidatos += (Join-Path $env:ProgramFiles 'Inno Setup 5\ISCC.exe')
    }
    if (${env:ProgramFiles(x86)}) {
        $candidatos += (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
        $candidatos += (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 5\ISCC.exe')
    }
    if ($env:LOCALAPPDATA) {
        $candidatos += (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    }

    foreach ($candidato in $candidatos) {
        if (Test-Path $candidato) { return $candidato }
    }

    $comando = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }

    $ramas = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($rama in $ramas) {
        $apps = Get-ItemProperty -Path $rama -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -like 'Inno Setup*' }
        foreach ($app in $apps) {
            if ($app.InstallLocation) {
                $ruta = Join-Path $app.InstallLocation 'ISCC.exe'
                if (Test-Path $ruta) { return $ruta }
            }
        }
    }
    return $null
}

$Iscc = Buscar-Iscc
if (-not $Iscc) {
    throw @"
No se encontró ISCC.exe (compilador de Inno Setup).

Instala Inno Setup 6 con:
    winget install --id JRSoftware.InnoSetup -e

O descárgalo de:
    https://jrsoftware.org/isdl.php

Después vuelve a ejecutar:
    .\empaquetar_instalador.ps1 -Version $Version
"@
}

Write-Host "Compilando instalador $Version con Inno Setup..." -ForegroundColor Cyan
Write-Host "  ISCC: $Iscc"
& $Iscc "/DMyAppVersion=$Version" $Iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup falló con código $LASTEXITCODE."
}

$Setup = Join-Path $Raiz "dist\LaCasaDelPanApp_Setup_$Version.exe"
if (-not (Test-Path $Setup)) {
    throw "Inno Setup terminó, pero no se encontró el instalador esperado: $Setup"
}

$hash = (Get-FileHash -Algorithm SHA256 $Setup).Hash
$tamano = (Get-Item $Setup).Length / 1MB

Write-Host ""
Write-Host "Instalador listo:" -ForegroundColor Green
Write-Host "  Archivo: $Setup"
Write-Host ("  Tamaño : {0:N2} MB" -f $tamano)
Write-Host "  SHA256 : $hash"
