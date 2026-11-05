"""Repositorio de proveedores — capa de REPOSITORIOS.

Acceso a datos de las tablas ``proveedores`` y ``proveedor_dias`` (SQLite).

El calendario de visitas se modela de forma **normalizada**: cada día en que un
proveedor visita la panadería es una fila de ``proveedor_dias`` (``dia`` de 1 a 7,
donde 1 = lunes y 7 = domingo). Así se pueden responder consultas del tipo
"¿qué proveedores vienen el miércoles?".

Aquí NO hay reglas de negocio: la validación vive en ``ProveedorService``.
"""

from __future__ import annotations

import logging
from typing import Any

from src.infraestructura.conexion import ConexionBD
from src.dominio.excepciones import ProveedorNoEncontrado

logger = logging.getLogger(__name__)

#: Columnas devueltas al leer proveedores.
COLUMNAS_PROVEEDOR = (
    "id",
    "nombre",
    "contacto",
    "telefono",
    "email",
    "notas",
    "activo",
    "creado_en",
    "actualizado_en",
)

SQL_INSERT_PROVEEDOR = (
    "INSERT INTO proveedores (nombre, contacto, telefono, email, notas, activo) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

SQL_ACTUALIZAR_PROVEEDOR = (
    "UPDATE proveedores SET nombre = ?, contacto = ?, telefono = ?, email = ?, "
    "    notas = ?, activo = ?, actualizado_en = CURRENT_TIMESTAMP "
    "WHERE id = ?"
)

SQL_CAMBIAR_ACTIVO = (
    "UPDATE proveedores SET activo = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?"
)

SQL_BORRAR_DIAS = "DELETE FROM proveedor_dias WHERE proveedor_id = ?"
SQL_INSERTAR_DIA = "INSERT OR IGNORE INTO proveedor_dias (proveedor_id, dia) VALUES (?, ?)"
SQL_BORRAR_DIA = "DELETE FROM proveedor_dias WHERE proveedor_id = ? AND dia = ?"
SQL_LISTAR_DIAS = "SELECT proveedor_id, dia FROM proveedor_dias ORDER BY proveedor_id, dia"

# --- Catálogo interno de productos por proveedor (muchos-a-muchos) ---
SQL_LISTAR_PRODUCTOS_PROVEEDOR = (
    "SELECT pp.producto_id, pp.precio_compra AS precio_sugerido, "
    "       p.nombre, p.codigo_interno, p.unidad_medida, p.precio_venta, "
    "       p.costo, p.categoria_id, p.activo "
    "FROM proveedor_productos pp "
    "JOIN productos p ON p.id = pp.producto_id "
    "WHERE pp.proveedor_id = ? "
    "ORDER BY p.nombre COLLATE NOCASE ASC"
)

#: Inserta el vínculo; si ya existe, solo actualiza el precio (si se envía uno).
SQL_VINCULAR_PRODUCTO = (
    "INSERT INTO proveedor_productos (proveedor_id, producto_id, precio_compra) "
    "VALUES (?, ?, ?) "
    "ON CONFLICT (proveedor_id, producto_id) DO UPDATE SET "
    "    precio_compra = COALESCE(excluded.precio_compra, proveedor_productos.precio_compra)"
)

SQL_DESVINCULAR_PRODUCTO = (
    "DELETE FROM proveedor_productos WHERE proveedor_id = ? AND producto_id = ?"
)

SQL_LISTAR_IDS_PRODUCTOS_PROVEEDOR = (
    "SELECT producto_id FROM proveedor_productos WHERE proveedor_id = ?"
)


class ProveedorRepo:
    """Acceso a datos de proveedores y sus días de visita."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        """Inicializa el repositorio.

        Args:
            conexion_bd: conexión inyectada (útil para tests). Si se omite,
                se crea una ``ConexionBD`` con la configuración por defecto.
        """
        self._bd = conexion_bd or ConexionBD()

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def listar(self, incluir_inactivos: bool = False) -> list[dict[str, Any]]:
        """Devuelve los proveedores (con sus días de visita) ordenados por nombre."""
        columnas = ", ".join(COLUMNAS_PROVEEDOR)
        sql = f"SELECT {columnas} FROM proveedores"
        if not incluir_inactivos:
            sql += " WHERE activo = 1"
        sql += " ORDER BY nombre ASC"

        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(sql)
            proveedores = [dict(fila) for fila in cursor.fetchall()]
            cursor.execute(SQL_LISTAR_DIAS)
            dias = cursor.fetchall()

        por_proveedor: dict[int, list[int]] = {}
        for fila in dias:
            por_proveedor.setdefault(int(fila["proveedor_id"]), []).append(int(fila["dia"]))
        for proveedor in proveedores:
            proveedor["dias"] = por_proveedor.get(int(proveedor["id"]), [])

        logger.info("Proveedores cargados: %d.", len(proveedores))
        return proveedores

    def obtener_por_id(self, proveedor_id: int) -> dict[str, Any] | None:
        """Devuelve un proveedor por su id (o ``None`` si no existe)."""
        columnas = ", ".join(COLUMNAS_PROVEEDOR)
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(f"SELECT {columnas} FROM proveedores WHERE id = ?", (int(proveedor_id),))
            fila = cursor.fetchone()
            if fila is None:
                return None
            cursor.execute(
                "SELECT dia FROM proveedor_dias WHERE proveedor_id = ? ORDER BY dia",
                (int(proveedor_id),),
            )
            dias = [int(f["dia"]) for f in cursor.fetchall()]

        proveedor = dict(fila)
        proveedor["dias"] = dias
        return proveedor

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def crear(self, datos: dict[str, Any]) -> int:
        """Inserta un proveedor nuevo (con sus días) y devuelve su id."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(
                SQL_INSERT_PROVEEDOR,
                (
                    datos["nombre"],
                    datos.get("contacto") or None,
                    datos.get("telefono") or None,
                    datos.get("email") or None,
                    datos.get("notas") or None,
                    1 if datos.get("activo", True) else 0,
                ),
            )
            proveedor_id = int(cursor.lastrowid)
            self._reemplazar_dias(cursor, proveedor_id, datos.get("dias") or [])
        return proveedor_id

    def actualizar(self, proveedor_id: int, datos: dict[str, Any]) -> None:
        """Actualiza un proveedor y reemplaza sus días de visita.

        Raises:
            ProveedorNoEncontrado: si el id no existe.
        """
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(
                SQL_ACTUALIZAR_PROVEEDOR,
                (
                    datos["nombre"],
                    datos.get("contacto") or None,
                    datos.get("telefono") or None,
                    datos.get("email") or None,
                    datos.get("notas") or None,
                    1 if datos.get("activo", True) else 0,
                    int(proveedor_id),
                ),
            )
            if cursor.rowcount == 0:
                raise ProveedorNoEncontrado(f"No se encontró el proveedor con id={proveedor_id}.")
            self._reemplazar_dias(cursor, int(proveedor_id), datos.get("dias") or [])

    def cambiar_activo(self, proveedor_id: int, activo: bool) -> None:
        """Activa o desactiva (baja lógica) un proveedor.

        Raises:
            ProveedorNoEncontrado: si el id no existe.
        """
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_CAMBIAR_ACTIVO, (1 if activo else 0, int(proveedor_id)))
            if cursor.rowcount == 0:
                raise ProveedorNoEncontrado(f"No se encontró el proveedor con id={proveedor_id}.")

    def cambiar_dia(self, proveedor_id: int, dia: int, viene: bool) -> None:
        """Marca o desmarca un día de visita (usado por el calendario).

        Raises:
            ProveedorNoEncontrado: si el proveedor no existe.
        """
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute("SELECT 1 FROM proveedores WHERE id = ?", (int(proveedor_id),))
            if cursor.fetchone() is None:
                raise ProveedorNoEncontrado(f"No se encontró el proveedor con id={proveedor_id}.")
            if viene:
                cursor.execute(SQL_INSERTAR_DIA, (int(proveedor_id), int(dia)))
            else:
                cursor.execute(SQL_BORRAR_DIA, (int(proveedor_id), int(dia)))

    # ------------------------------------------------------------------
    # Catálogo interno del proveedor (qué productos trae)
    # ------------------------------------------------------------------
    def listar_productos(self, proveedor_id: int) -> list[dict[str, Any]]:
        """Productos del catálogo del proveedor, con su precio de compra sugerido."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_LISTAR_PRODUCTOS_PROVEEDOR, (int(proveedor_id),))
            return [dict(fila) for fila in cursor.fetchall()]

    def ids_productos_vinculados(self, proveedor_id: int) -> set[int]:
        """Ids de los productos ya vinculados al proveedor."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_LISTAR_IDS_PRODUCTOS_PROVEEDOR, (int(proveedor_id),))
            return {int(fila["producto_id"]) for fila in cursor.fetchall()}

    def vincular_producto(
        self,
        proveedor_id: int,
        producto_id: int,
        precio_compra: float | None = None,
        cursor: Any | None = None,
    ) -> None:
        """Agrega (o actualiza el precio de) un producto en el catálogo del proveedor.

        Si ya existe el vínculo y ``precio_compra`` es ``None``, conserva el
        precio guardado.
        """
        parametros = (int(proveedor_id), int(producto_id), precio_compra)
        if cursor is not None:
            cursor.execute(SQL_VINCULAR_PRODUCTO, parametros)
            return
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_VINCULAR_PRODUCTO, parametros)

    def desvincular_producto(self, proveedor_id: int, producto_id: int) -> None:
        """Quita un producto del catálogo del proveedor."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_DESVINCULAR_PRODUCTO, (int(proveedor_id), int(producto_id)))

    # ------------------------------------------------------------------
    def _reemplazar_dias(self, cursor: Any, proveedor_id: int, dias: list[int]) -> None:
        """Borra e inserta de nuevo los días del proveedor (idempotente)."""
        cursor.execute(SQL_BORRAR_DIAS, (proveedor_id,))
        for dia in sorted(set(int(d) for d in dias)):
            cursor.execute(SQL_INSERTAR_DIA, (proveedor_id, dia))
