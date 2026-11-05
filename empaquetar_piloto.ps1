# ============================================================
#  Empaqueta el piloto portable de LaCasaDelPanApp (Windows).
#
#  Uso:
#     .\empaquetar_piloto.ps1
#     .\empaquetar_piloto.ps1 -Version 0.1.0
#
#  Genera:
#     dist\LaCasaDelPanApp\                 (onedir)
#     dist\LaCasaDelPanApp_piloto_<ver>.zip
# ============================================================
param(
    [string]$Version = (Get-Date -Format 'yyyy-MM-dd')
)

$ErrorActionPreference = 'Stop'
$Raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Raiz

$Python = Join-Path $Raiz '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "No se encontró el entorno virtual: $Python"
}

Write-Host "Compilando con PyInstaller (onedir)..." -ForegroundColor Cyan
& $Python -m PyInstaller --clean --noconfirm LaCasaDelPanApp.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falló con código $LASTEXITCODE."
}

$Carpeta = Join-Path $Raiz 'dist\LaCasaDelPanApp'
$Leeme = Join-Path $Raiz 'LEEME_PILOTO.txt'
if (Test-Path $Leeme) {
    Copy-Item -Path $Leeme -Destination (Join-Path $Carpeta 'LEEME_PILOTO.txt') -Force
}

$Zip = Join-Path $Raiz "dist\LaCasaDelPanApp_piloto_$Version.zip"
if (Test-Path $Zip) {
    Remove-Item $Zip -Force
}
Compress-Archive -Path $Carpeta -DestinationPath $Zip

Write-Host ""
Write-Host "Paquete listo:" -ForegroundColor Green
Write-Host "  Carpeta: $Carpeta"
Write-Host "  ZIP    : $Zip"
