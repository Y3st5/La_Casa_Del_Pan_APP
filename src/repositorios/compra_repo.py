"""Repositorio de compras / ingresos de proveedores — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre las tablas ``compras`` y
``compra_detalle``. Registra cada ingreso de mercadería con trazabilidad:
proveedor, productos, cantidades, precios de compra y método de pago.

Sus métodos aceptan un ``cursor`` opcional para participar en transacciones
abiertas por el servicio (patrón *unit of work* ligero). El DDL vive aquí para
que el repositorio sea autocontenido (igual que ``TurnoRepo``).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

#: Métodos de pago válidos para una compra (gasto a proveedor).
METODOS_PAGO_COMPRA: tuple[str, ...] = (
    "Efectivo",
    "Yape/Plin",
    "Transferencia",
    "Cuenta corriente",
)

#: Métodos que egresan efectivo físico de la caja (afectan el arqueo).
#: Solo "Efectivo" sale del cajón: "Yape/Plin" y "Transferencia" se pagan por
#: canales digitales y "Cuenta corriente" es a crédito, así que ninguno mueve
#: el efectivo del cajón.
METODOS_COMPRA_AFECTAN_CAJA: frozenset[str] = frozenset({"Efectivo"})

SQL_CREAR_COMPRAS = """
CREATE TABLE IF NOT EXISTS compras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id    INTEGER NOT NULL,
    fecha           TEXT    NOT NULL,
    total           REAL    NOT NULL DEFAULT 0,
    metodo_pago     TEXT    NOT NULL,
    estado          TEXT    NOT NULL DEFAULT 'RECIBIDO',
    observaciones   TEXT,
    creado_en       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proveedor_id) REFERENCES proveedores (id)
);
"""

SQL_CREAR_COMPRA_DETALLE = """
CREATE TABLE IF NOT EXISTS compra_detalle (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id       INTEGER NOT NULL,
    producto_id     INTEGER NOT NULL,
    cantidad        REAL    NOT NULL,
    precio_compra   REAL    NOT NULL,
    subtotal        REAL    NOT NULL,
    FOREIGN KEY (compra_id) REFERENCES compras (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id)
);
"""

SQL_CREAR_INDICE_COMPRAS = (
    "CREATE INDEX IF NOT EXISTS idx_compras_fecha ON compras (fecha)"
)
SQL_CREAR_INDICE_COMPRA_DETALLE = (
    "CREATE INDEX IF NOT EXISTS idx_compra_detalle_compra ON compra_detalle (compra_id)"
)

SQL_INSERT_COMPRA = (
    "INSERT INTO compras "
    "    (proveedor_id, fecha, total, metodo_pago, estado, observaciones, creado_en) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

SQL_INSERT_COMPRA_DETALLE = (
    "INSERT INTO compra_detalle "
    "    (compra_id, producto_id, cantidad, precio_compra, subtotal) "
    "VALUES (?, ?, ?, ?, ?)"
)

SQL_LISTAR_COMPRAS_POR_FECHA = (
    "SELECT c.id, c.proveedor_id, p.nombre AS proveedor_nombre, c.fecha, c.total, "
    "       c.metodo_pago, c.estado, c.observaciones, c.creado_en "
    "FROM compras c "
    "LEFT JOIN proveedores p ON p.id = c.proveedor_id "
    "WHERE date(c.fecha) = date(?) "
    "ORDER BY c.id DESC"
)

SQL_OBTENER_COMPRA = (
    "SELECT c.id, c.proveedor_id, p.nombre AS proveedor_nombre, c.fecha, c.total, "
    "       c.metodo_pago, c.estado, c.observaciones, c.creado_en "
    "FROM compras c "
    "LEFT JOIN proveedores p ON p.id = c.proveedor_id "
    "WHERE c.id = ?"
)

SQL_LISTAR_DETALLE_COMPRA = (
    "SELECT cd.id, cd.producto_id, pr.nombre AS producto_nombre, cd.cantidad, "
    "       cd.precio_compra, cd.subtotal "
    "FROM compra_detalle cd "
    "LEFT JOIN productos pr ON pr.id = cd.producto_id "
    "WHERE cd.compra_id = ? "
    "ORDER BY cd.id ASC"
)


class CompraRepo:
    """Acceso a datos de compras e ingresos de proveedores."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        self._bd = conexion_bd or ConexionBD()

    # ------------------------------------------------------------------
    # Inicialización (idempotente)
    # ------------------------------------------------------------------
    def asegurar_tablas(self, cursor: sqlite3.Cursor | None = None) -> None:
        """Crea las tablas ``compras`` y ``compra_detalle`` e índices si no existen."""
        sentencias = (
            SQL_CREAR_COMPRAS,
            SQL_CREAR_COMPRA_DETALLE,
            SQL_CREAR_INDICE_COMPRAS,
            SQL_CREAR_INDICE_COMPRA_DETALLE,
        )
        if cursor is not None:
            for sql in sentencias:
                cursor.execute(sql)
            return
        with self._bd.cursor() as (_conexion, cur):
            for sql in sentencias:
                cur.execute(sql)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def crear_compra(
        self,
        proveedor_id: int,
        fecha: str,
        total: float,
        metodo_pago: str,
        estado: str = "RECIBIDO",
        observaciones: str | None = None,
        cursor: sqlite3.Cursor | None = None,
    ) -> int:
        """Inserta una compra y devuelve su ``id``."""
        parametros = (
            int(proveedor_id),
            fecha,
            round(float(total), 2),
            metodo_pago,
            estado,
            observaciones,
            fecha if len(fecha) > 10 else f"{fecha} 00:00:00",
        )
        if cursor is not None:
            cursor.execute(SQL_INSERT_COMPRA, parametros)
            return int(cursor.lastrowid)
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_COMPRA, parametros)
            return int(cur.lastrowid)

    def agregar_detalle(
        self,
        compra_id: int,
        producto_id: int,
        cantidad: float,
        precio_compra: float,
        cursor: sqlite3.Cursor | None = None,
    ) -> None:
        """Inserta una línea de detalle de compra."""
        parametros = (
            int(compra_id),
            int(producto_id),
            round(float(cantidad), 3),
            round(float(precio_compra), 2),
            round(float(cantidad) * float(precio_compra), 2),
        )
        if cursor is not None:
            cursor.execute(SQL_INSERT_COMPRA_DETALLE, parametros)
            return
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_COMPRA_DETALLE, parametros)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def listar_por_fecha(self, fecha: str) -> list[dict[str, Any]]:
        """Compras de un día (``fecha`` = ``'YYYY-MM-DD'``), más recientes primero."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_LISTAR_COMPRAS_POR_FECHA, (fecha,))
            return [dict(fila) for fila in cur.fetchall()]

    def obtener_por_id(self, compra_id: int) -> dict[str, Any] | None:
        """Devuelve una compra (cabecera) o ``None`` si no existe."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_OBTENER_COMPRA, (int(compra_id),))
            fila = cur.fetchone()
            return dict(fila) if fila is not None else None

    def listar_detalle(self, compra_id: int) -> list[dict[str, Any]]:
        """Líneas de detalle de una compra."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_LISTAR_DETALLE_COMPRA, (int(compra_id),))
            return [dict(fila) for fila in cur.fetchall()]

    def eliminar_por_fecha(self, fecha: str) -> int:
        """Elimina todas las compras de una fecha (con cascade a su detalle).

        Se usa al **cerrar el día**: las compras ya quedaron archivadas en el PDF
        de cierre, así que se limpian para que, si se reabre un turno el mismo
        día, no se vuelvan a descontar del efectivo esperado.

        Args:
            fecha: ``'YYYY-MM-DD'``.

        Returns:
            Número de compras eliminadas.
        """
        with self._bd.cursor() as (_conexion, cur):
            cur.execute("SELECT id FROM compras WHERE date(fecha) = ?", (fecha,))
            ids = [fila["id"] for fila in cur.fetchall()]
            if not ids:
                return 0
            # El cascade borra compra_detalle.
            placeholders = ",".join("?" for _ in ids)
            cur.execute(f"DELETE FROM compras WHERE id IN ({placeholders})", ids)
            logger.info("Eliminadas %d compras del día %s (cascade a detalle).", len(ids), fecha)
            return len(ids)
