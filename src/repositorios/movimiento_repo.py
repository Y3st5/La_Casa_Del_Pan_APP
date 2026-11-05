"""Repositorio de movimientos de stock (kardex) — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre la tabla ``movimientos_stock``.
Registra cada variación de stock (venta, ajuste, merma, ingreso) con
trazabilidad completa: stock anterior y resultante, documento de origen
(``referencia_tipo`` + ``referencia_id``), usuario y motivo.

Sus métodos aceptan un ``cursor`` opcional: si se les pasa uno, participan de
la transacción que abrió el servicio (patrón *unit of work* ligero); si no,
abren y cierran su propia conexión.

Alineado con el modelo canónico del vault: la ``cantidad`` va **con signo**
(negativa en salidas, positiva en ingresos).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

#: Tipos de movimiento admitidos (alineados con el modelo canónico).
TIPOS_VALIDOS: tuple[str, ...] = (
    "ENTRADA",
    "SALIDA_VENTA",
    "ANULACION_VENTA",
    "AJUSTE_POSITIVO",
    "AJUSTE_NEGATIVO",
    "MERMA",
    "INVENTARIO_INICIAL",
)

SQL_INSERT_MOVIMIENTO = (
    "INSERT INTO movimientos_stock "
    "    (producto_id, tipo, cantidad, stock_anterior, stock_resultante, "
    "     referencia_tipo, referencia_id, usuario_id, motivo) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

SQL_MOVIMIENTOS_POR_REFERENCIA = (
    "SELECT id, producto_id, tipo, cantidad, stock_anterior, stock_resultante, "
    "       referencia_tipo, referencia_id, motivo, creado_en "
    "FROM movimientos_stock "
    "WHERE referencia_tipo = ? AND referencia_id = ? "
    "ORDER BY id ASC"
)


class MovimientoRepo:
    """Acceso a datos del kardex ``movimientos_stock``."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        self._bd = conexion_bd or ConexionBD()

    # ------------------------------------------------------------------
    def registrar(
        self,
        producto_id: int,
        tipo: str,
        cantidad: float,
        *,
        stock_anterior: float | None = None,
        stock_resultante: float | None = None,
        referencia_tipo: str | None = None,
        referencia_id: int | None = None,
        usuario_id: int | None = None,
        motivo: str | None = None,
        cursor: sqlite3.Cursor | None = None,
    ) -> int:
        """Inserta un movimiento de stock y devuelve su ``id``.

        Args:
            producto_id: producto afectado.
            tipo: uno de :data:`TIPOS_VALIDOS` (p. ej. ``"SALIDA_VENTA"``).
            cantidad: variación **con signo** (negativa = salida).
            stock_anterior: stock antes del movimiento (trazabilidad).
            stock_resultante: stock después del movimiento.
            referencia_tipo: documento de origen (``"VENTA"``, ``"AJUSTE"``…).
            referencia_id: id del documento de origen (p. ej. ``venta_id``).
            usuario_id: usuario que origina el movimiento (opcional).
            motivo: texto libre para auditoría.
            cursor: cursor de una transacción abierta por el servicio. Si se
                omite, el repositorio abre su propia conexión.

        Raises:
            ValueError: si ``tipo`` no es un movimiento reconocido.
        """
        if tipo not in TIPOS_VALIDOS:
            raise ValueError(f"Tipo de movimiento no válido: {tipo!r}")

        parametros = (
            int(producto_id),
            tipo,
            round(float(cantidad), 3),
            None if stock_anterior is None else round(float(stock_anterior), 3),
            None if stock_resultante is None else round(float(stock_resultante), 3),
            referencia_tipo,
            None if referencia_id is None else int(referencia_id),
            None if usuario_id is None else int(usuario_id),
            motivo,
        )

        if cursor is not None:
            cursor.execute(SQL_INSERT_MOVIMIENTO, parametros)
            return int(cursor.lastrowid)

        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_MOVIMIENTO, parametros)
            return int(cur.lastrowid)

    def listar_por_referencia(
        self,
        referencia_tipo: str,
        referencia_id: int,
    ) -> list[dict[str, Any]]:
        """Devuelve los movimientos de un documento (auditoría / pruebas).

        Args:
            referencia_tipo: p. ej. ``"VENTA"``.
            referencia_id: id del documento de origen.
        """
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_MOVIMIENTOS_POR_REFERENCIA, (referencia_tipo, int(referencia_id)))
            return [dict(fila) for fila in cur.fetchall()]
