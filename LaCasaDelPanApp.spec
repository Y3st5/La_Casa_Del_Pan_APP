# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — LaCasaDelPanApp (paquete portable ``onedir``).

Genera una carpeta autocontenida (sin instalador) pensada para el piloto:

    .venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm LaCasaDelPanApp.spec

Resultado::

    dist\\LaCasaDelPanApp\\
        LaCasaDelPanApp.exe
        _internal\\...

Se usa **onedir** (no onefile) por tres motivos: arranca más rápido, es más
fácil de auditar/depurar y genera menos falsos positivos de antivirus.

Los datos escribibles **no** se empaquetan: el ejecutable los crea en
``%PROGRAMDATA%\\LaCasaDelPanApp`` (ver ``src/infraestructura/rutas.py``).
Solo se incluyen los recursos de solo lectura (tema y plantilla de config).
"""

from pathlib import Path

RAIZ = Path(SPECPATH)

#: Icono de la aplicación (ventana, accesos directos e instalador).
ICONO = RAIZ / "assets" / "LaCasaDelPanApp.ico"

# Recursos de solo lectura que la app busca en sys._MEIPASS/config/.
datas = [
    (str(RAIZ / "config" / "tema_trigo.json"), "config"),
    (str(RAIZ / "config" / "config.example.ini"), "config"),
]

# Carpetas/módulos que no forman parte del ejecutable.
excludes = [
    "tests",
    "scripts",
    "_pruebas",
    "_obsoletos",
    "pytest",
    "_pytest",
    "coverage",
    "PyInstaller",
    "tkinter.test",
    "pydoc",
    "doctest",
]

a = Analysis(
    [str(RAIZ / "main.py")],
    pathex=[str(RAIZ)],
    binaries=[],
    datas=datas,
    # ``openpyxl`` se importa de forma perezosa dentro de
    # ``importador_productos._leer_excel``; PyInstaller no lo detecta solo.
    # Sin esto, "Importar productos" desde Excel falla en el .exe.
    hiddenimports=["openpyxl"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LaCasaDelPanApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICONO) if ICONO.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LaCasaDelPanApp",
)
