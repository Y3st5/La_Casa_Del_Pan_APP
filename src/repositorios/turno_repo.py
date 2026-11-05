"""Repositorio de turnos de caja — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre la tabla ``turnos_caja``.
Gestiona la apertura y cierre de turnos (arranque y arqueo de caja).

Sus métodos aceptan un ``cursor`` opcional para participar en transacciones
abiertas por el servicio (patrón *unit of work* ligero).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

# Estados válidos de un turno
ESTADOS_TURNO = ("ABIERTO", "CERRADO")

SQL_CREAR_TURNOS_CAJA = """
CREATE TABLE IF NOT EXISTS turnos_caja (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    caja_id               INTEGER NOT NULL,
    usuario_apertura_id   INTEGER,
    usuario_cierre_id     INTEGER,
    monto_apertura        REAL    NOT NULL DEFAULT 0,
    monto_cierre_declarado REAL,
    monto_cierre_sistema   REAL,
    diferencia            REAL,
    estado                TEXT    NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO', 'CERRADO')),
    abierto_en            TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cerrado_en            TEXT,
    observaciones         TEXT,
    FOREIGN KEY (caja_id) REFERENCES cajas (id)
);
"""

SQL_CREAR_INDICE_TURNOS = (
    "CREATE INDEX IF NOT EXISTS idx_turno_caja_estado ON turnos_caja (caja_id, estado)"
)

SQL_INSERT_TURNO = (
    "INSERT INTO turnos_caja "
    "    (caja_id, usuario_apertura_id, monto_apertura, estado, abierto_en) "
    "VALUES (?, ?, ?, 'ABIERTO', ?)"
)

SQL_OBTENER_TURNO_ABIERTO = (
    "SELECT id, caja_id, usuario_apertura_id, monto_apertura, estado, abierto_en "
    "FROM turnos_caja "
    "WHERE caja_id = ? AND estado = 'ABIERTO' "
    "ORDER BY id DESC LIMIT 1"
)

SQL_OBTENER_ULTIMO_CERRADO = (
    "SELECT id, caja_id, usuario_apertura_id, usuario_cierre_id, "
    "       monto_apertura, monto_cierre_declarado, monto_cierre_sistema, diferencia, "
    "       estado, abierto_en, cerrado_en, observaciones "
    "FROM turnos_caja "
    "WHERE caja_id = ? AND estado = 'CERRADO' "
    "ORDER BY id DESC LIMIT 1"
)

SQL_CERRAR_TURNO = (
    "UPDATE turnos_caja "
    "   SET estado = 'CERRADO', "
    "       usuario_cierre_id = ?, "
    "       monto_cierre_declarado = ?, "
    "       monto_cierre_sistema = ?, "
    "       diferencia = ?, "
    "       cerrado_en = ?, "
    "       observaciones = ? "
    " WHERE id = ?"
)


class TurnoRepo:
    """Acceso a datos de turnos de caja (apertura / cierre / arqueo)."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        self._bd = conexion_bd or ConexionBD()

    # ------------------------------------------------------------------
    # Inicialización (idempotente)
    # ------------------------------------------------------------------
    def asegurar_tabla(self, cursor: sqlite3.Cursor | None = None) -> None:
        """Crea la tabla ``turnos_caja`` e índice si no existen."""
        if cursor is not None:
            cursor.execute(SQL_CREAR_TURNOS_CAJA)
            cursor.execute(SQL_CREAR_INDICE_TURNOS)
        else:
            with self._bd.cursor() as (_conexion, cur):
                cur.execute(SQL_CREAR_TURNOS_CAJA)
                cur.execute(SQL_CREAR_INDICE_TURNOS)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def obtener_turno_abierto(
        self, caja_id: int, cursor: sqlite3.Cursor | None = None
    ) -> dict[str, Any] | None:
        """Devuelve el turno ABIERTO actual de una caja, o ``None`` si no hay."""
        if cursor is not None:
            cursor.execute(SQL_OBTENER_TURNO_ABIERTO, (int(caja_id),))
            fila = cursor.fetchone()
            return dict(fila) if fila else None
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_OBTENER_TURNO_ABIERTO, (int(caja_id),))
            fila = cur.fetchone()
            return dict(fila) if fila else None

    def obtener_ultimo_cerrado(
        self, caja_id: int, cursor: sqlite3.Cursor | None = None
    ) -> dict[str, Any] | None:
        """Devuelve el último turno CERRADO de una caja (para inicio del día)."""
        if cursor is not None:
            cursor.execute(SQL_OBTENER_ULTIMO_CERRADO, (int(caja_id),))
            fila = cursor.fetchone()
            return dict(fila) if fila else None
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_OBTENER_ULTIMO_CERRADO, (int(caja_id),))
            fila = cur.fetchone()
            return dict(fila) if fila else None

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def abrir_turno(
        self,
        caja_id: int,
        monto_apertura: float,
        usuario_apertura_id: int | None = None,
        cursor: sqlite3.Cursor | None = None,
    ) -> int:
        """Crea un nuevo turno ABIERTO y devuelve su ``id``.

        Args:
            caja_id: identificador de la caja (de ``config.ini``).
            monto_apertura: fondo de caja inicial (efectivo físico).
            usuario_apertura_id: id del usuario que abre (opcional).
            cursor: cursor de transacción externa.

        Returns:
            ``id`` del turno creado.

        Raises:
            ValueError: si ya existe un turno ABIERTO para esa caja.
        """
        # Verificar que no haya turno abierto
        turno_abierto = self.obtener_turno_abierto(caja_id, cursor=cursor)
        if turno_abierto:
            raise ValueError(
                f"Ya existe un turno abierto para la caja {caja_id} "
                f"(turno #{turno_abierto['id']}). Ciérrelo antes de abrir uno nuevo."
            )

        parametros = (
            int(caja_id),
            usuario_apertura_id,
            round(float(monto_apertura), 2),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        if cursor is not None:
            cursor.execute(SQL_INSERT_TURNO, parametros)
            return int(cursor.lastrowid)
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_TURNO, parametros)
            return int(cur.lastrowid)

    def cerrar_turno(
        self,
        turno_id: int,
        usuario_cierre_id: int | None,
        monto_cierre_declarado: float,
        monto_cierre_sistema: float,
        observaciones: str | None = None,
        cursor: sqlite3.Cursor | None = None,
    ) -> None:
        """Cierra un turno ABIERTO registrando el arqueo.

        Args:
            turno_id: id del turno a cerrar.
            usuario_cierre_id: id del usuario que cierra.
            monto_cierre_declarado: efectivo contado físicamente por el cajero.
            monto_cierre_sistema: efectivo esperado según el sistema (apertura + ventas efectivo - retiros).
            observaciones: notas libres (diferencias, novedades).
        """
        diferencia = round(float(monto_cierre_declarado) - float(monto_cierre_sistema), 2)
        parametros = (
            usuario_cierre_id,
            round(float(monto_cierre_declarado), 2),
            round(float(monto_cierre_sistema), 2),
            diferencia,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            observaciones,
            int(turno_id),
        )
        if cursor is not None:
            cursor.execute(SQL_CERRAR_TURNO, parametros)
        else:
            with self._bd.cursor() as (_conexion, cur):
                cur.execute(SQL_CERRAR_TURNO, parametros)