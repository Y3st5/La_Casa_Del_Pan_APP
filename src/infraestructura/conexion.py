"""Conexión a la base de datos SQLite — capa de INFRAESTRUCTURA.

Responsabilidad única: entregar conexiones configuradas a la base de datos
local (``db/lacasadepan.db`` dentro de la carpeta de datos) y manejar los
errores de conexión. Aquí NO vive ninguna regla de negocio.

Se usa el módulo nativo ``sqlite3`` (sin dependencias externas).

Detalles de robustez:
    * ``PRAGMA journal_mode=WAL``  → permite leer y escribir a la vez
      (mejor concurrencia y resistencia ante cortes).
    * ``conn.row_factory = sqlite3.Row`` → las filas se comportan como
      diccionarios (acceso por nombre de columna).
    * ``PRAGMA foreign_keys=ON`` → respeta las claves foráneas.
    * ``busy_timeout`` → espera en vez de fallar si la BD está ocupada.

Uso::

    conexion_bd = ConexionBD()
    with conexion_bd.cursor() as (conexion, cursor):
        cursor.execute("SELECT ...")
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.infraestructura.config_reader import ConfigReader
from src.infraestructura.rutas import ruta_bd_por_defecto, ruta_datos

logger = logging.getLogger(__name__)

#: Raíz de los datos escribibles de la aplicación.
#:
#: * Empaquetado: ``%PROGRAMDATA%\\LaCasaDelPanApp``.
#: * Desarrollo: la raíz del proyecto.
#:
#: Se conserva el nombre ``RAIZ_PROYECTO`` porque los tests lo parchean para
#: aislar las carpetas de salida en ``tmp_path``.
RAIZ_PROYECTO = ruta_datos()

#: Ruta por defecto del archivo de base de datos SQLite.
RUTA_BD_POR_DEFECTO = ruta_bd_por_defecto()

#: Segundos de espera si la base de datos está bloqueada por otra operación.
TIEMPO_ESPERA_SEG = 5.0


class ErrorConexionBD(Exception):
    """Se lanza cuando no se puede establecer o usar la conexión a SQLite."""


class ConexionBD:
    """Fábrica de conexiones a la base de datos SQLite local.

    Puede usarse de dos formas:

    1) Conexión directa (casos puntuales)::

        conexion = ConexionBD().obtener_conexion()

    2) Context manager, que gestiona transacción y cierre (recomendado)::

        with ConexionBD().cursor() as (conexion, cursor):
            cursor.execute("SELECT ...")
    """

    def __init__(
        self,
        configuracion: ConfigReader | None = None,
        ruta_bd: str | Path | None = None,
    ) -> None:
        """Inicializa la fábrica resolviendo la ruta del archivo SQLite.

        Args:
            configuracion: lector de settings (sección ``[db] -> archivo``).
                Si se omite, se carga el ``config.ini`` de la carpeta de datos.
            ruta_bd: ruta explícita al archivo ``.db`` (tiene prioridad y es
                útil para tests).

        Raises:
            ErrorConfiguracion: si el archivo de configuración no existe
                o es inválido.
        """
        if ruta_bd is not None:
            self._ruta_bd = Path(ruta_bd)
        else:
            self._ruta_bd = self._resolver_ruta(configuracion)

        # Garantiza que exista la carpeta contenedora (db/).
        self._ruta_bd.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("Base de datos SQLite: %s", self._ruta_bd)

    # ------------------------------------------------------------------
    def _resolver_ruta(self, configuracion: ConfigReader | None) -> Path:
        """Determina la ruta de la BD desde config.ini (o la de por defecto)."""
        try:
            config = configuracion or ConfigReader()
            ruta_config = config.obtener("db", "archivo", None)
        except Exception as exc:  # noqa: BLE001 - se usa la ruta por defecto
            logger.warning(
                "No se pudo leer 'db -> archivo' de la configuración (%s). "
                "Se usará la ruta por defecto: %s",
                exc,
                RUTA_BD_POR_DEFECTO,
            )
            ruta_config = None

        if not ruta_config:
            return RUTA_BD_POR_DEFECTO

        ruta = Path(ruta_config)
        return ruta if ruta.is_absolute() else (RAIZ_PROYECTO / ruta)

    @property
    def ruta(self) -> Path:
        """Ruta del archivo de base de datos."""
        return self._ruta_bd

    # ------------------------------------------------------------------
    def obtener_conexion(self) -> sqlite3.Connection:
        """Devuelve una conexión nueva a la base de datos SQLite.

        Returns:
            Conexión lista para usarse, con ``row_factory`` y PRAGMAs aplicados.

        Raises:
            ErrorConexionBD: si el archivo no se puede abrir o configurar.
        """
        try:
            conexion = sqlite3.connect(self._ruta_bd, timeout=TIEMPO_ESPERA_SEG)
            # Filas accesibles por nombre de columna (dict-like).
            conexion.row_factory = sqlite3.Row
            # Concurrencia y robustez.
            conexion.execute("PRAGMA journal_mode=WAL;")
            conexion.execute("PRAGMA foreign_keys=ON;")
            conexion.execute(f"PRAGMA busy_timeout={int(TIEMPO_ESPERA_SEG * 1000)};")
            logger.debug("Conexión SQLite establecida con %s", self._ruta_bd)
            return conexion
        except sqlite3.Error as exc:
            logger.error("No se pudo conectar a SQLite: %s", exc)
            raise ErrorConexionBD(
                f"No se pudo abrir la base de datos SQLite:\n{self._ruta_bd}\n\n"
                f"Detalle: {exc}"
            ) from exc

    @contextmanager
    def cursor(self) -> Iterator[tuple[sqlite3.Connection, sqlite3.Cursor]]:
        """Context manager que entrega ``(conexion, cursor)`` y gestiona la transacción.

        Hace ``COMMIT`` si el bloque termina bien, ``ROLLBACK`` ante cualquier
        excepción y **siempre** cierra cursor y conexión.
        """
        conexion = self.obtener_conexion()
        cursor = conexion.cursor()
        try:
            yield conexion, cursor
            conexion.commit()
        except Exception:
            conexion.rollback()
            raise
        finally:
            try:
                cursor.close()
            finally:
                conexion.close()
