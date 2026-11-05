"""Repositorio de impuestos — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre la tabla ``impuestos``.
No contiene reglas de negocio (esas viven en :mod:`src.dominio.impuestos`).

La tabla se siembra en el arranque con los tributos peruanos habituales (IGV
18 %, Exonerado e Inafecto), con sus códigos de los catálogos SUNAT, de modo
que el comprobante electrónico no requiera una migración posterior.
"""

from __future__ import annotations

import logging
from typing import Any

from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

#: Orden de columnas que devuelven las consultas.
COLUMNAS = (
    "id",
    "nombre",
    "codigo_sunat",
    "tipo_afectacion",
    "tasa",
    "activo",
    "orden",
)

SQL_LISTAR = (
    f"SELECT {', '.join(COLUMNAS)} FROM impuestos "
    "WHERE activo = 1 ORDER BY orden ASC, id ASC"
)

SQL_LISTAR_TODOS = (
    f"SELECT {', '.join(COLUMNAS)} FROM impuestos ORDER BY orden ASC, id ASC"
)

SQL_OBTENER = f"SELECT {', '.join(COLUMNAS)} FROM impuestos WHERE id = ?"


class ImpuestoRepo:
    """Acceso a datos de la tabla ``impuestos`` (SQLite)."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        """Inicializa el repositorio.

        Args:
            conexion_bd: conexión inyectada (útil para tests). Si se omite, se
                crea una ``ConexionBD`` con la configuración por defecto.
        """
        self._bd = conexion_bd or ConexionBD()

    def listar(self, incluir_inactivos: bool = False) -> list[dict[str, Any]]:
        """Devuelve los impuestos ordenados para mostrar en la interfaz."""
        sql = SQL_LISTAR_TODOS if incluir_inactivos else SQL_LISTAR
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(sql)
            filas: list[dict[str, Any]] = [dict(fila) for fila in cursor.fetchall()]
        return filas

    def obtener(self, impuesto_id: int) -> dict[str, Any] | None:
        """Devuelve un impuesto por su id (o ``None`` si no existe)."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_OBTENER, (int(impuesto_id),))
            fila = cursor.fetchone()
        return dict(fila) if fila is not None else None

    def mapa_por_id(self) -> dict[int, dict[str, Any]]:
        """Devuelve ``{id: impuesto}`` para resolver el desglose sin N consultas."""
        return {int(fila["id"]): fila for fila in self.listar(incluir_inactivos=True)}
