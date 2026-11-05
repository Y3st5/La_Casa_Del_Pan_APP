"""Resolución de rutas del sistema — capa de INFRAESTRUCTURA.

La aplicación maneja dos mundos distintos que **no deben mezclarse**:

* **Recursos de solo lectura** (``config/tema_trigo.json``,
  ``config/config.example.ini``): viajan dentro del paquete y, cuando la app
  está congelada con PyInstaller, viven en ``sys._MEIPASS``.
* **Datos escribibles** (base SQLite, ``config.ini``, tickets, boletas,
  facturas, XML/CDR, reportes, respaldos y logs): deben vivir en una carpeta
  con permisos de escritura. En el ``.exe`` instalado esa carpeta es
  ``%PROGRAMDATA%\\LaCasaDelPanApp``.

En **modo desarrollo** (``python main.py``) los datos se resuelven contra la
raíz del proyecto para no romper la base ni la configuración existentes. La
variable de entorno ``LACASADELPAN_DATA`` permite forzar otra carpeta (útil
para probar el comportamiento del paquete sin compilar).

Uso::

    from src.infraestructura.rutas import carpeta_logs, ruta_config

    ruta_config()          # .../config/config.ini (datos)
    carpeta_logs()         # .../logs (datos)
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

#: Nombre de la carpeta de datos de la aplicación.
NOMBRE_APP = "LaCasaDelPanApp"

#: Variable de entorno para forzar la carpeta de datos.
VARIABLE_ENTORNO_DATOS = "LACASADELPAN_DATA"

#: Nombres de archivo de configuración.
ARCHIVO_CONFIG = "config.ini"
ARCHIVO_CONFIG_EJEMPLO = "config.example.ini"

#: Nombre del archivo de log rotativo.
ARCHIVO_LOG = "pos.log"


# ======================================================================
#  Detección del entorno de ejecución
# ======================================================================
def es_empaquetado() -> bool:
    """``True`` si la app corre desde un ejecutable generado por PyInstaller."""
    return bool(getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None))


def ruta_recursos() -> Path:
    """Carpeta de recursos **de solo lectura** (tema, plantillas, etc.).

    * Empaquetado: ``sys._MEIPASS`` (en onedir, la carpeta ``_internal``).
    * Desarrollo: raíz del proyecto (``.../LaCasaDelPanApp``).
    """
    if es_empaquetado():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def ruta_recurso(*partes: str) -> Path:
    """Compone una ruta dentro de la carpeta de recursos de solo lectura."""
    return ruta_recursos().joinpath(*partes)


# ======================================================================
#  Carpeta de datos escribibles
# ======================================================================
def _programdata() -> Path:
    """``%PROGRAMDATA%\\LaCasaDelPanApp`` (con respaldo si falta la variable)."""
    base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
    if base:
        return Path(base) / NOMBRE_APP
    return Path.home() / "AppData" / "Roaming" / NOMBRE_APP


def _localappdata() -> Path:
    """``%LOCALAPPDATA%\\LaCasaDelPanApp`` (respaldo si ProgramData no es escribible)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / NOMBRE_APP
    return Path.home() / ".local" / "share" / NOMBRE_APP


def ruta_datos() -> Path:
    """Carpeta raíz de los datos escribibles de la aplicación.

    Orden de resolución:

    1. ``LACASADELPAN_DATA`` si está definida (tests, soporte, pruebas).
    2. Empaquetado: ``%PROGRAMDATA%\\LaCasaDelPanApp``. Si no se puede crear
       o escribir (usuario sin permisos), se usa
       ``%LOCALAPPDATA%\\LaCasaDelPanApp``.
    3. Desarrollo: la raíz del proyecto.
    """
    override = os.environ.get(VARIABLE_ENTORNO_DATOS)
    if override:
        return Path(override).expanduser()

    if not es_empaquetado():
        return ruta_recursos()

    return _resolver_datos_empaquetado()


@lru_cache(maxsize=1)
def _resolver_datos_empaquetado() -> Path:
    """Elige una carpeta de datos escribible (se resuelve una vez por proceso)."""
    for candidata in (_programdata(), _localappdata()):
        try:
            candidata.mkdir(parents=True, exist_ok=True)
            # Sondeo real de escritura: mkdir(exist_ok=True) no detecta una
            # carpeta existente creada por otro usuario sin permiso de escritura.
            sonda = candidata / ".prueba_escritura"
            sonda.write_text("", encoding="utf-8")
            sonda.unlink()
            return candidata
        except OSError as exc:  # noqa: PERF203 - solo hay dos candidatas
            logger.warning("Sin permiso de escritura en %s (%s).", candidata, exc)

    # Último recurso: se devuelve igualmente para que el error sea explícito
    # al intentar escribir, con una ruta comprensible en el mensaje.
    return _localappdata()


# ======================================================================
#  Carpetas y archivos de datos
# ======================================================================
def carpeta_config() -> Path:
    """Carpeta de configuración escribible (``config.ini`` y certificados)."""
    return ruta_datos() / "config"


def ruta_config() -> Path:
    """Ruta del ``config.ini`` activo."""
    return carpeta_config() / ARCHIVO_CONFIG


def ruta_config_ejemplo() -> Path:
    """Ruta de la plantilla ``config.example.ini`` (solo lectura)."""
    return ruta_recurso("config", ARCHIVO_CONFIG_EJEMPLO)


def carpeta_bd() -> Path:
    """Carpeta de la base de datos SQLite."""
    return ruta_datos() / "db"


def ruta_bd_por_defecto() -> Path:
    """Ruta por defecto de la base de datos SQLite."""
    return carpeta_bd() / "lacasadepan.db"


def carpeta_logs() -> Path:
    """Carpeta de logs de la aplicación."""
    return ruta_datos() / "logs"


def ruta_log() -> Path:
    """Ruta del log rotativo principal."""
    return carpeta_logs() / ARCHIVO_LOG


def carpeta_tickets() -> Path:
    """Carpeta de tickets internos (``.txt`` de 80 mm)."""
    return ruta_datos() / "tickets"


def carpeta_boletas() -> Path:
    """Carpeta de boletas emitidas."""
    return ruta_datos() / "boletas"


def carpeta_facturas() -> Path:
    """Carpeta de facturas emitidas."""
    return ruta_datos() / "facturas"


def carpeta_reportes() -> Path:
    """Carpeta de reportes PDF (cierre del día)."""
    return ruta_datos() / "reportes"


def carpeta_xml() -> Path:
    """Carpeta de XML UBL 2.1 firmados."""
    return ruta_datos() / "xml"


def carpeta_cdr() -> Path:
    """Carpeta de CDR (ZIP) devueltos por SUNAT."""
    return ruta_datos() / "cdr"


def carpeta_respaldos() -> Path:
    """Carpeta de respaldos de la base de datos."""
    return carpeta_bd() / "respaldos"


def asegurar_estructura_datos() -> Path:
    """Crea (si faltan) todas las carpetas de datos y devuelve la raíz.

    Es idempotente y se llama al arrancar la aplicación. No copia ni borra
    nada: solo garantiza que exista la estructura mínima.
    """
    for carpeta in (
        ruta_datos(),
        carpeta_config(),
        carpeta_bd(),
        carpeta_logs(),
        carpeta_tickets(),
        carpeta_boletas(),
        carpeta_facturas(),
        carpeta_reportes(),
        carpeta_xml(),
        carpeta_cdr(),
        carpeta_respaldos(),
    ):
        carpeta.mkdir(parents=True, exist_ok=True)
    return ruta_datos()


__all__ = [
    "ARCHIVO_CONFIG",
    "ARCHIVO_CONFIG_EJEMPLO",
    "ARCHIVO_LOG",
    "NOMBRE_APP",
    "VARIABLE_ENTORNO_DATOS",
    "asegurar_estructura_datos",
    "carpeta_bd",
    "carpeta_boletas",
    "carpeta_cdr",
    "carpeta_config",
    "carpeta_facturas",
    "carpeta_logs",
    "carpeta_reportes",
    "carpeta_respaldos",
    "carpeta_tickets",
    "carpeta_xml",
    "es_empaquetado",
    "ruta_bd_por_defecto",
    "ruta_config",
    "ruta_config_ejemplo",
    "ruta_datos",
    "ruta_log",
    "ruta_recurso",
    "ruta_recursos",
]
