"""Repositorio de ventas — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre las tablas ``ventas`` y
``venta_detalle``.

Sus métodos aceptan un ``cursor`` opcional: si se les pasa uno, participan de
la transacción que abrió el servicio (patrón *unit of work* ligero); si no,
abren y cierran su propia conexión.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

#: NOTA: el correlativo **no** se deduce de esta tabla. Se reserva en
#: ``series_comprobante`` (ver :mod:`src.repositorios.serie_repo`) porque el
#: Cierre del Día borra las ventas y ``MAX(correlativo)+1`` repetiría números.

SQL_INSERT_VENTA = (
    "INSERT INTO ventas "
    "    (serie, correlativo, tipo_comprobante, serie_comprobante, "
    "     correlativo_comprobante, metodo_pago, subtotal, base_gravada, igv, exonerado, "
    "     inafecto, redondeo, total, monto_recibido, vuelto, "
    "     cliente, dni_cliente, creado_en) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

SQL_INSERT_DETALLE = (
    "INSERT INTO venta_detalle "
    "    (venta_id, producto_id, codigo, descripcion, cantidad, precio_unitario, subtotal, "
    "     unidad_sunat, impuesto_id, tipo_afectacion, tasa_impuesto, base_gravada, igv) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: Ventas de una fecha (hora local y reciente primero), con el número de ítems
#: y descripción del primer producto (para mostrar en la lista).
SQL_LISTAR_POR_FECHA = (
    "SELECT v.id, v.serie, v.correlativo, v.tipo_comprobante, v.serie_comprobante, "
    "       v.correlativo_comprobante, v.metodo_pago, v.subtotal, v.total, "
    "       v.base_gravada, v.igv, v.exonerado, v.inafecto, v.redondeo, "
    "       v.monto_recibido, v.vuelto, v.estado, v.creado_en, v.cliente, v.dni_cliente, "
    "       (SELECT COUNT(*) FROM venta_detalle d WHERE d.venta_id = v.id) AS items, "
    "       (SELECT descripcion FROM venta_detalle d WHERE d.venta_id = v.id ORDER BY d.id LIMIT 1) AS primer_producto "
    "FROM ventas v "
    "WHERE date(v.creado_en) = ? "
    "ORDER BY v.id DESC"
)

SQL_OBTENER_POR_ID = (
    "SELECT id, serie, correlativo, tipo_comprobante, serie_comprobante, "
    "       correlativo_comprobante, metodo_pago, subtotal, base_gravada, igv, exonerado, "
    "       inafecto, redondeo, total, monto_recibido, vuelto, estado, creado_en, "
    "       cliente, dni_cliente "
    "FROM ventas WHERE id = ?"
)

SQL_LISTAR_DETALLE = (
    "SELECT id, producto_id, codigo, descripcion, cantidad, precio_unitario, subtotal, "
    "       unidad_sunat, impuesto_id, tipo_afectacion, tasa_impuesto, base_gravada, igv "
    "FROM venta_detalle WHERE venta_id = ? ORDER BY id ASC"
)

#: Ventas del día agrupadas por la **categoría** del producto (Panes, Abarrotes,
#: Cafetería…). La categoría se resuelve en vivo contra ``productos`` (no es un
#: snapshot); los productos sin categoría se agrupan como "Sin categoría".
SQL_TOTALES_POR_CATEGORIA = (
    "SELECT COALESCE(c.id, 0) AS categoria_id, "
    "       COALESCE(c.nombre, 'Sin categoría') AS categoria, "
    "       COALESCE(SUM(d.subtotal), 0) AS subtotal, "
    "       COALESCE(SUM(d.cantidad), 0) AS cantidad "
    "FROM venta_detalle d "
    "JOIN ventas v ON v.id = d.venta_id "
    "LEFT JOIN productos p ON p.id = d.producto_id "
    "LEFT JOIN categorias c ON c.id = p.categoria_id "
    "WHERE date(v.creado_en) = ? "
    "GROUP BY COALESCE(c.id, 0), COALESCE(c.nombre, 'Sin categoría') "
    "ORDER BY subtotal DESC, categoria ASC"
)

# --- Pagos divididos (RF-06) -----------------------------------------
SQL_INSERT_PAGO = (
    "INSERT INTO venta_pagos (venta_id, metodo_pago, monto, vuelto) VALUES (?, ?, ?, ?)"
)

SQL_LISTAR_PAGOS = (
    "SELECT id, metodo_pago, monto, vuelto FROM venta_pagos WHERE venta_id = ? ORDER BY id ASC"
)


class VentaRepo:
    """Acceso a datos de ventas y su detalle."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        self._bd = conexion_bd or ConexionBD()

    # ------------------------------------------------------------------
    def crear_venta(
        self,
        serie: str,
        correlativo: int,
        metodo_pago: str,
        subtotal: float,
        total: float,
        monto_recibido: float,
        vuelto: float,
        cliente: str = "",
        dni_cliente: str = "",
        cursor: sqlite3.Cursor | None = None,
        base_gravada: float = 0.0,
        igv: float = 0.0,
        exonerado: float = 0.0,
        inafecto: float = 0.0,
        redondeo: float = 0.0,
        tipo_comprobante: str = "TICKET",
        serie_comprobante: str = "",
        correlativo_comprobante: int = 0,
        creado_en: str | None = None,
    ) -> int:
        """Inserta una venta y devuelve su ``id``.

        ``serie``/``correlativo`` identifican el **ticket interno** de control.
        ``serie_comprobante``/``correlativo_comprobante`` guardan el comprobante
        fiscal (boleta/factura); van vacíos cuando la venta solo emite ticket.

        ``creado_en`` (``'YYYY-MM-DD HH:MM:SS'``) permite fijar la fecha de la
        venta desde el servicio, de modo que la venta y su comprobante
        electrónico compartan **exactamente** el mismo instante de emisión.
        """
        parametros = (
            serie,
            int(correlativo),
            str(tipo_comprobante or "TICKET"),
            str(serie_comprobante or ""),
            int(correlativo_comprobante or 0),
            metodo_pago,
            round(float(subtotal), 2),
            round(float(base_gravada), 2),
            round(float(igv), 2),
            round(float(exonerado), 2),
            round(float(inafecto), 2),
            round(float(redondeo), 2),
            round(float(total), 2),
            round(float(monto_recibido), 2),
            round(float(vuelto), 2),
            (cliente or "").strip(),
            (dni_cliente or "").strip(),
            # Hora LOCAL del equipo (la columna por defecto es UTC y desviaba
            # los reportes del día en países con huso negativo como Perú).
            creado_en or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        if cursor is not None:
            cursor.execute(SQL_INSERT_VENTA, parametros)
            return int(cursor.lastrowid)
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_VENTA, parametros)
            return int(cur.lastrowid)

    def agregar_detalle(
        self,
        venta_id: int,
        producto: dict[str, Any],
        cantidad: float,
        cursor: sqlite3.Cursor | None = None,
        *,
        importe: float | None = None,
        impuesto: dict[str, Any] | None = None,
        base_gravada: float = 0.0,
        igv: float = 0.0,
        unidad_sunat: str = "",
    ) -> None:
        """Inserta una línea de detalle (con snapshot del producto y del impuesto).

        Args:
            importe: importe ya cuantizado de la línea (el que usó el desglose
                tributario). Si se omite, se recalcula como ``cantidad × precio``.
            impuesto: impuesto aplicado (para el snapshot del comprobante).
            base_gravada: base imponible de la línea (sin IGV).
            igv: IGV de la línea.
            unidad_sunat: código del Catálogo N.º 03 (``NIU``, ``KGM``…), como
                snapshot: si mañana cambia la unidad del producto, el
                comprobante histórico sigue siendo válido.
        """
        precio = float(producto.get("precio_venta") or 0)
        importe_linea = round(float(cantidad) * precio, 2) if importe is None else round(float(importe), 2)
        parametros = (
            int(venta_id),
            producto.get("id"),
            producto.get("codigo_barras") or producto.get("codigo_interno"),
            producto.get("nombre", ""),
            float(cantidad),
            precio,
            importe_linea,
            str(unidad_sunat or ""),
            int(impuesto["id"]) if impuesto and impuesto.get("id") is not None else None,
            str(impuesto.get("tipo_afectacion")) if impuesto else None,
            round(float(impuesto.get("tasa") or 0), 4) if impuesto else 0.0,
            round(float(base_gravada), 2),
            round(float(igv), 2),
        )
        if cursor is not None:
            cursor.execute(SQL_INSERT_DETALLE, parametros)
            return
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_DETALLE, parametros)

    # ------------------------------------------------------------------
    # Reportes (lecturas)
    # ------------------------------------------------------------------
    def listar_por_fecha(self, fecha: str) -> list[dict[str, Any]]:
        """Ventas de un día (``fecha`` = ``'YYYY-MM-DD'``), más recientes primero."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_LISTAR_POR_FECHA, (fecha,))
            return [dict(fila) for fila in cur.fetchall()]

    def obtener_por_id(self, venta_id: int) -> dict[str, Any] | None:
        """Devuelve una venta (cabecera) o ``None`` si no existe."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_OBTENER_POR_ID, (int(venta_id),))
            fila = cur.fetchone()
            return dict(fila) if fila is not None else None

    def listar_detalle(self, venta_id: int) -> list[dict[str, Any]]:
        """Líneas de detalle (snapshot) de una venta."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_LISTAR_DETALLE, (int(venta_id),))
            return [dict(fila) for fila in cur.fetchall()]

    def totales_por_categoria(self, fecha: str) -> list[dict[str, Any]]:
        """Subtotales vendidos por categoría en un día (``'YYYY-MM-DD'``).

        Devuelve una fila por categoría con ``categoria_id``, ``categoria``,
        ``subtotal`` y ``cantidad``, ordenadas de mayor a menor subtotal.
        """
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_TOTALES_POR_CATEGORIA, (fecha,))
            return [dict(fila) for fila in cur.fetchall()]

    # ------------------------------------------------------------------
    # Pagos divididos (RF-06)
    # ------------------------------------------------------------------
    def agregar_pago(
        self,
        venta_id: int,
        metodo_pago: str,
        monto: float,
        vuelto: float = 0.0,
        cursor: sqlite3.Cursor | None = None,
    ) -> None:
        """Registra un pago parcial de una venta (pago dividido)."""
        parametros = (int(venta_id), metodo_pago, round(float(monto), 2), round(float(vuelto), 2))
        if cursor is not None:
            cursor.execute(SQL_INSERT_PAGO, parametros)
            return
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT_PAGO, parametros)

    def listar_pagos(self, venta_id: int) -> list[dict[str, Any]]:
        """Devuelve todos los pagos de una venta (para ticket/reimpresión)."""
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_LISTAR_PAGOS, (int(venta_id),))
            return [dict(fila) for fila in cur.fetchall()]

    # ------------------------------------------------------------------
    # Mantenimiento
    # ------------------------------------------------------------------
    def eliminar_por_fecha(self, fecha: str) -> int:
        """Elimina todas las ventas de una fecha (con cascade a detalle y pagos).

        Args:
            fecha: ``'YYYY-MM-DD'``.

        Returns:
            Número de ventas eliminadas.
        """
        with self._bd.cursor() as (_conexion, cur):
            # Primero obtener los IDs para contar
            cur.execute("SELECT id FROM ventas WHERE date(creado_en) = ?", (fecha,))
            ids = [fila["id"] for fila in cur.fetchall()]
            if not ids:
                return 0
            # Eliminar ventas (cascade borra detalle y pagos)
            placeholders = ",".join("?" for _ in ids)
            cur.execute(f"DELETE FROM ventas WHERE id IN ({placeholders})", ids)
            logger.info("Eliminadas %d ventas del día %s (cascade a detalle/pagos).", len(ids), fecha)
            return len(ids)
