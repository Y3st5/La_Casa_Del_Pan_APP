"""Repositorio de series de comprobante — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre la tabla ``series_comprobante``.
No contiene reglas de negocio.

Guarda el **último correlativo emitido** por cada serie (``T001``, ``B001``,
``F001``). Es la fuente de verdad de la numeración y, a diferencia de las
ventas, **no se limpia en el Cierre del Día**: SUNAT exige que la numeración de
una serie sea continua y que un número no se reuse nunca.

Si el correlativo se dedujera con ``MAX(correlativo) FROM ventas`` (como se
hacía antes), el cierre del día —que borra las ventas del día— lo reiniciaría y
se emitirían números repetidos.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from src.dominio.catalogos import TIPO_POR_SERIE, TIPO_TICKET
from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

COLUMNAS = ("serie", "tipo_comprobante", "correlativo_actual", "activo")

SQL_LISTAR = (
    f"SELECT {', '.join(COLUMNAS)} FROM series_comprobante ORDER BY serie ASC"
)

SQL_OBTENER = f"SELECT {', '.join(COLUMNAS)} FROM series_comprobante WHERE serie = ?"

#: Crea la fila de la serie si aún no existe (red de seguridad: la tabla se
#: siembra en el arranque, pero el repositorio no debe depender de eso).
SQL_ASEGURAR = (
    "INSERT OR IGNORE INTO series_comprobante "
    "    (serie, tipo_comprobante, correlativo_actual) VALUES (?, ?, 0)"
)

SQL_INCREMENTAR = (
    "UPDATE series_comprobante "
    "    SET correlativo_actual = correlativo_actual + 1, "
    "        actualizado_en = CURRENT_TIMESTAMP "
    "WHERE serie = ?"
)


class SerieRepo:
    """Acceso a los correlativos por serie de comprobante."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        self._bd = conexion_bd or ConexionBD()

    # ------------------------------------------------------------------
    def listar(self) -> list[dict[str, Any]]:
        """Devuelve todas las series con su último correlativo emitido."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_LISTAR)
            return [dict(fila) for fila in cursor.fetchall()]

    def obtener(self, serie: str) -> dict[str, Any] | None:
        """Devuelve una serie por su nombre (``'B001'``) o ``None``."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_OBTENER, (serie,))
            fila = cursor.fetchone()
        return dict(fila) if fila is not None else None

    # ------------------------------------------------------------------
    def siguiente_correlativo(
        self,
        serie: str,
        cursor: sqlite3.Cursor | None = None,
    ) -> int:
        """Reserva y devuelve el siguiente correlativo de ``serie``.

        Incrementa el contador de forma atómica dentro de la transacción de la
        venta: si la venta falla y se revierte, el número no se consume.

        Args:
            serie: p. ej. ``'T001'``, ``'B001'`` o ``'F001'``.
            cursor: cursor de una transacción abierta (patrón *unit of work*).
                Si se omite, abre y cierra su propia conexión.
        """
        tipo = TIPO_POR_SERIE.get(serie, TIPO_TICKET)
        if cursor is not None:
            return self._reservar(cursor, serie, tipo)
        with self._bd.cursor() as (_conexion, cur):
            return self._reservar(cur, serie, tipo)

    @staticmethod
    def _reservar(cursor: sqlite3.Cursor, serie: str, tipo: str) -> int:
        cursor.execute(SQL_ASEGURAR, (serie, tipo))
        cursor.execute(SQL_INCREMENTAR, (serie,))
        cursor.execute(SQL_OBTENER, (serie,))
        fila = cursor.fetchone()
        correlativo = int(fila["correlativo_actual"])
        logger.debug("Correlativo reservado: %s-%06d", serie, correlativo)
        return correlativo

    # ------------------------------------------------------------------
    def fijar_correlativo(
        self,
        serie: str,
        correlativo: int,
        tipo_comprobante: str = "",
        cursor: sqlite3.Cursor | None = None,
    ) -> None:
        """Fija el correlativo de una serie (migraciones y correcciones).

        Solo **sube** el contador: nunca lo baja, para no repetir números ya
        emitidos.
        """
        tipo = tipo_comprobante or TIPO_POR_SERIE.get(serie, TIPO_TICKET)
        sql = (
            "UPDATE series_comprobante SET correlativo_actual = ?, "
            "    actualizado_en = CURRENT_TIMESTAMP "
            "WHERE serie = ? AND correlativo_actual < ?"
        )
        parametros = (int(correlativo), serie, int(correlativo))
        if cursor is not None:
            cursor.execute(SQL_ASEGURAR, (serie, tipo))
            cursor.execute(sql, parametros)
            return
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_ASEGURAR, (serie, tipo))
            cur.execute(sql, parametros)
