"""Lector de configuración — capa de INFRAESTRUCTURA.

Lee el ``config.ini`` **escribible** de la aplicación usando ``configparser``
(librería nativa de Python). Es el único módulo que conoce el archivo de
configuración: el resto de la aplicación pide valores a través de esta clase.

En modo desarrollo el archivo vive en ``config/config.ini`` del proyecto; en el
``.exe`` empaquetado vive en ``%PROGRAMDATA%\\LaCasaDelPanApp\\config\\config.ini``.
Si no existe en el primer arranque, se crea copiando la plantilla de solo
lectura ``config.example.ini``.

Uso::

    from src.infraestructura.config_reader import ConfigReader

    config = ConfigReader()
    config.obtener("db", "host")          # -> "localhost"
    config.obtener_int("db", "port")      # -> 3306
"""

from __future__ import annotations

import configparser
import logging
import shutil
from pathlib import Path
from typing import Any

from src.infraestructura.rutas import (
    ARCHIVO_CONFIG_EJEMPLO,
    carpeta_config,
    ruta_config,
    ruta_config_ejemplo,
)

logger = logging.getLogger(__name__)

#: Raíz del proyecto = .../LaCasaDelPanApp (se conserva por compatibilidad).
RAIZ_PROYECTO = Path(__file__).resolve().parents[2]

#: Carpeta donde vive el ``config.ini`` activo (datos escribibles).
CARPETA_CONFIG = carpeta_config()

#: Ruta por defecto del ``config.ini`` activo.
RUTA_CONFIG_POR_DEFECTO = ruta_config()

#: Plantilla de configuración de solo lectura que viaja con la aplicación.
RUTA_CONFIG_EJEMPLO = ruta_config_ejemplo()


def _crear_config_inicial(destino: Path) -> Path:
    """Crea ``destino`` a partir de la plantilla si aún no existe.

    Solo aplica al archivo por defecto (primer arranque). Si el usuario pasó
    una ruta explícita, se respeta tal cual y el error se reporta normalmente.
    """
    if destino.exists():
        return destino

    ejemplo = ruta_config_ejemplo()
    if not ejemplo.exists():
        logger.error(
            "No existe la plantilla de configuración '%s'; no se pudo crear '%s'.",
            ejemplo,
            destino,
        )
        return destino

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ejemplo, destino)
    except OSError as exc:
        logger.error("No se pudo crear la configuración inicial '%s': %s", destino, exc)
        return destino

    logger.info("Configuración inicial creada desde '%s' en '%s'.", ejemplo, destino)
    return destino


class ErrorConfiguracion(Exception):
    """Se lanza cuando la configuración no existe, está incompleta o es inválida."""


class ConfigReader:
    """Lectura tipada del ``config.ini`` de la carpeta de datos."""

    def __init__(self, ruta: str | Path | None = None) -> None:
        """Carga el archivo de configuración.

        Args:
            ruta: ruta alternativa al archivo ``.ini`` (útil para tests).
                Si se omite, se usa el ``config.ini`` de la carpeta de datos
                (creándolo desde la plantilla en el primer arranque).

        Raises:
            ErrorConfiguracion: si el archivo no existe o su formato es inválido.
        """
        if ruta is not None:
            self._ruta = Path(ruta)
        else:
            self._ruta = _crear_config_inicial(ruta_config())

        if not self._ruta.exists():
            raise ErrorConfiguracion(
                f"No se encontró el archivo de configuración:\n{self._ruta}\n\n"
                f"Copia '{ARCHIVO_CONFIG_EJEMPLO}' a '{self._ruta.name}' "
                "y completa tus datos de conexión.\n"
                f"Plantilla incluida con la aplicación:\n{ruta_config_ejemplo()}"
            )

        # interpolation=None evita errores si la contraseña contiene el carácter '%'.
        self._parser = configparser.ConfigParser(interpolation=None)
        try:
            leidos = self._parser.read(self._ruta, encoding="utf-8")
        except configparser.Error as exc:
            raise ErrorConfiguracion(
                f"El archivo de configuración '{self._ruta.name}' tiene un formato "
                f"inválido: {exc}"
            ) from exc

        if not leidos:
            raise ErrorConfiguracion(
                f"No se pudo leer el archivo de configuración '{self._ruta}'."
            )

        logger.debug("Configuración cargada desde %s", self._ruta)

    # ------------------------------------------------------------------
    @property
    def ruta(self) -> Path:
        """Ruta del archivo de configuración cargado."""
        return self._ruta

    def tiene_seccion(self, seccion: str) -> bool:
        """Indica si existe la sección indicada."""
        return self._parser.has_section(seccion)

    # ------------------------------------------------------------------
    # Lectura tipada
    # ------------------------------------------------------------------
    def obtener(self, seccion: str, clave: str, por_defecto: str | None = None) -> str | None:
        """Devuelve un valor de texto.

        Raises:
            ErrorConfiguracion: si la sección o la clave no existen y no se
                indicó un valor por defecto.
        """
        if not self._parser.has_section(seccion):
            if por_defecto is not None:
                return por_defecto
            raise ErrorConfiguracion(
                f"Falta la sección [{seccion}] en '{self._ruta.name}'."
            )
        if not self._parser.has_option(seccion, clave):
            if por_defecto is not None:
                return por_defecto
            raise ErrorConfiguracion(
                f"Falta la clave '{clave}' en la sección [{seccion}] de '{self._ruta.name}'."
            )
        return self._parser.get(seccion, clave)

    def obtener_int(self, seccion: str, clave: str, por_defecto: int | None = None) -> int:
        """Devuelve un valor entero."""
        crudo = self.obtener(seccion, clave, str(por_defecto) if por_defecto is not None else None)
        try:
            return int(str(crudo).strip())
        except (TypeError, ValueError) as exc:
            raise ErrorConfiguracion(
                f"El valor de '{clave}' en [{seccion}] debe ser un número entero "
                f"(se encontró '{crudo}')."
            ) from exc

    def obtener_float(self, seccion: str, clave: str, por_defecto: float | None = None) -> float:
        """Devuelve un valor decimal."""
        crudo = self.obtener(seccion, clave, str(por_defecto) if por_defecto is not None else None)
        try:
            return float(str(crudo).strip())
        except (TypeError, ValueError) as exc:
            raise ErrorConfiguracion(
                f"El valor de '{clave}' en [{seccion}] debe ser un número "
                f"(se encontró '{crudo}')."
            ) from exc

    def obtener_bool(self, seccion: str, clave: str, por_defecto: bool = False) -> bool:
        """Devuelve un valor booleano (sí/1/true/on ...)."""
        if not self._parser.has_option(seccion, clave):
            return por_defecto
        try:
            return self._parser.getboolean(seccion, clave)
        except ValueError as exc:
            raise ErrorConfiguracion(
                f"El valor de '{clave}' en [{seccion}] debe ser booleano "
                "(1/0, true/false, si/no)."
            ) from exc

    def seccion(self, seccion: str) -> dict[str, str]:
        """Devuelve una sección completa como diccionario."""
        if not self._parser.has_section(seccion):
            raise ErrorConfiguracion(
                f"Falta la sección [{seccion}] en '{self._ruta.name}'."
            )
        return dict(self._parser.items(seccion))

    def __repr__(self) -> str:  # pragma: no cover - solo depuración
        return f"ConfigReader(ruta={str(self._ruta)!r}, secciones={self._parser.sections()})"
