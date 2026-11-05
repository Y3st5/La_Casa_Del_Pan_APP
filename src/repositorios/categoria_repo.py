"""Repositorio de categorías — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre la tabla ``categorias``.

Las categorías clasifican los productos del catálogo (Panes, Abarrotes,
Cafetería…) y son el eje del **desglose de ventas por tipo de negocio**.

Aquí NO hay reglas de negocio: la validación vive en ``CategoriaService``.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from src.dominio.excepciones import CategoriaDuplicada, CategoriaNoEncontrada
from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

#: Columnas devueltas al leer categorías.
COLUMNAS_CATEGORIA = ("id", "nombre", "orden", "activo", "creado_en", "actualizado_en")

SQL_INSERT_CATEGORIA = (
    "INSERT INTO categorias (nombre, orden, activo) VALUES (?, ?, ?)"
)

SQL_ACTUALIZAR_CATEGORIA = (
    "UPDATE categorias SET nombre = ?, orden = ?, activo = ?, "
    "    actualizado_en = CURRENT_TIMESTAMP WHERE id = ?"
)

SQL_CAMBIAR_ACTIVO = (
    "UPDATE categorias SET activo = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?"
)


class CategoriaRepo:
    """Acceso a datos de la tabla ``categorias`` (SQLite)."""

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
    def listar(self, incluir_inactivas: bool = False) -> list[dict[str, Any]]:
        """Devuelve las categorías ordenadas por ``orden`` y luego por nombre."""
        columnas = ", ".join(COLUMNAS_CATEGORIA)
        sql = f"SELECT {columnas} FROM categorias"
        if not incluir_inactivas:
            sql += " WHERE activo = 1"
        sql += " ORDER BY orden ASC, nombre ASC"

        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(sql)
            filas: list[dict[str, Any]] = [dict(fila) for fila in cursor.fetchall()]

        logger.info("Categorías cargadas: %d.", len(filas))
        return filas

    def obtener_por_id(self, categoria_id: int) -> dict[str, Any] | None:
        """Devuelve una categoría por su id (o ``None`` si no existe)."""
        columnas = ", ".join(COLUMNAS_CATEGORIA)
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(
                f"SELECT {columnas} FROM categorias WHERE id = ?", (int(categoria_id),)
            )
            fila = cursor.fetchone()
        return dict(fila) if fila is not None else None

    def obtener_por_nombre(self, nombre: str) -> dict[str, Any] | None:
        """Devuelve una categoría por su nombre exacto (sin distinguir mayúsculas)."""
        columnas = ", ".join(COLUMNAS_CATEGORIA)
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(
                f"SELECT {columnas} FROM categorias WHERE nombre = ? COLLATE NOCASE",
                (str(nombre).strip(),),
            )
            fila = cursor.fetchone()
        return dict(fila) if fila is not None else None

    def contar_productos(self, categoria_id: int) -> int:
        """Cuenta cuántos productos están asignados a una categoría."""
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(
                "SELECT COUNT(*) AS total FROM productos WHERE categoria_id = ?",
                (int(categoria_id),),
            )
            fila = cursor.fetchone()
        return int(fila["total"]) if fila is not None else 0

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def crear(self, datos: dict[str, Any]) -> int:
        """Inserta una categoría nueva y devuelve su id.

        Raises:
            CategoriaDuplicada: si el nombre ya existe.
        """
        try:
            with self._bd.cursor() as (_conexion, cursor):
                cursor.execute(
                    SQL_INSERT_CATEGORIA,
                    (
                        str(datos.get("nombre") or "").strip(),
                        int(datos.get("orden") or 0),
                        1 if datos.get("activo", True) else 0,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise self._traducir_integridad(exc) from exc

    def actualizar(self, categoria_id: int, datos: dict[str, Any]) -> None:
        """Actualiza una categoría existente.

        Raises:
            CategoriaNoEncontrada: si el id no existe.
            CategoriaDuplicada: si el nombre colisiona con otra categoría.
        """
        try:
            with self._bd.cursor() as (_conexion, cursor):
                cursor.execute(
                    SQL_ACTUALIZAR_CATEGORIA,
                    (
                        str(datos.get("nombre") or "").strip(),
                        int(datos.get("orden") or 0),
                        1 if datos.get("activo", True) else 0,
                        int(categoria_id),
                    ),
                )
                if cursor.rowcount == 0:
                    raise CategoriaNoEncontrada(
                        f"No se encontró la categoría con id={categoria_id}."
                    )
        except sqlite3.IntegrityError as exc:
            raise self._traducir_integridad(exc) from exc

    def cambiar_activo(self, categoria_id: int, activo: bool) -> None:
        """Activa o desactiva (baja lógica) una categoría.

        Raises:
            CategoriaNoEncontrada: si el id no existe.
        """
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_CAMBIAR_ACTIVO, (1 if activo else 0, int(categoria_id)))
            if cursor.rowcount == 0:
                raise CategoriaNoEncontrada(
                    f"No se encontró la categoría con id={categoria_id}."
                )

    # ------------------------------------------------------------------
    @staticmethod
    def _traducir_integridad(exc: sqlite3.IntegrityError) -> Exception:
        """Traduce el error de unicidad de SQLite a un error de dominio."""
        mensaje = str(exc).lower()
        if "nombre" in mensaje or "unique" in mensaje:
            return CategoriaDuplicada("Ya existe una categoría con ese nombre.")
        return CategoriaDuplicada(f"No se pudo guardar la categoría: {exc}")
