"""Servicio de categorías — capa de SERVICIOS.

Centraliza las operaciones del catálogo de **categorías** (listar, crear,
editar y activar/desactivar) y valida las reglas de negocio antes de delegar en
``CategoriaRepo``.

Las categorías clasifican los productos y son el eje del **desglose de ventas**
(Panes, Abarrotes, Cafetería…). La presentación no ejecuta SQL: depende solo de
este servicio.
"""

from __future__ import annotations

import logging
from typing import Any

from src.dominio.excepciones import CategoriaDuplicada, DatosCategoriaInvalidos
from src.infraestructura.conexion import ConexionBD
from src.repositorios.categoria_repo import CategoriaRepo

logger = logging.getLogger(__name__)

#: Categorías que se crean la primera vez (coinciden con el giro del negocio).
CATEGORIAS_POR_DEFECTO: tuple[str, ...] = ("Panes", "Abarrotes", "Cafetería")

#: Nombre de la categoría usada para productos que aún no se clasificaron.
CATEGORIA_SIN_ASIGNAR = "Sin categoría"


class CategoriaService:
    """Casos de uso de gestión del catálogo de categorías."""

    def __init__(
        self,
        conexion_bd: ConexionBD | None = None,
        categoria_repo: CategoriaRepo | None = None,
    ) -> None:
        self._bd = conexion_bd
        self._repo = categoria_repo or CategoriaRepo(conexion_bd)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def listar(self, incluir_inactivas: bool = False) -> list[dict[str, Any]]:
        """Devuelve las categorías ordenadas por ``orden`` y nombre."""
        return self._repo.listar(incluir_inactivas=incluir_inactivas)

    def listar_con_conteo(self, incluir_inactivas: bool = False) -> list[dict[str, Any]]:
        """Como :meth:`listar`, añadiendo ``productos`` (cantidad asignada)."""
        categorias = self.listar(incluir_inactivas=incluir_inactivas)
        for categoria in categorias:
            categoria["productos"] = self._repo.contar_productos(int(categoria["id"]))
        return categorias

    def contar_productos(self, categoria_id: int) -> int:
        """Número de productos asignados a una categoría."""
        return self._repo.contar_productos(int(categoria_id))

    def obtener(self, categoria_id: int) -> dict[str, Any] | None:
        """Devuelve una categoría por su id (o ``None`` si no existe)."""
        return self._repo.obtener_por_id(categoria_id)

    def obtener_por_nombre(self, nombre: str) -> dict[str, Any] | None:
        """Devuelve una categoría por nombre exacto (sin distinguir mayúsculas)."""
        return self._repo.obtener_por_nombre(nombre)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def crear(self, datos: dict[str, Any]) -> int:
        """Valida y crea una categoría nueva. Devuelve el id asignado.

        Raises:
            DatosCategoriaInvalidos: si el nombre está vacío.
            CategoriaDuplicada: si el nombre ya existe.
        """
        limpio = self._normalizar(datos)
        if self._repo.obtener_por_nombre(limpio["nombre"]) is not None:
            raise CategoriaDuplicada(f"Ya existe la categoría «{limpio['nombre']}».")
        nuevo_id = self._repo.crear(limpio)
        logger.info("Categoría creada id=%s (%s).", nuevo_id, limpio["nombre"])
        return nuevo_id

    def actualizar(self, categoria_id: int, datos: dict[str, Any]) -> None:
        """Valida y actualiza una categoría existente.

        Raises:
            DatosCategoriaInvalidos: si el nombre está vacío.
            CategoriaNoEncontrada: si el id no existe.
            CategoriaDuplicada: si el nombre colisiona con otra categoría.
        """
        limpio = self._normalizar(datos)
        existente = self._repo.obtener_por_nombre(limpio["nombre"])
        if existente is not None and int(existente["id"]) != int(categoria_id):
            raise CategoriaDuplicada(f"Ya existe la categoría «{limpio['nombre']}».")
        self._repo.actualizar(categoria_id, limpio)
        logger.info("Categoría actualizada id=%s (%s).", categoria_id, limpio["nombre"])

    def cambiar_activo(self, categoria_id: int, activo: bool) -> None:
        """Activa o desactiva (baja lógica) una categoría.

        Raises:
            CategoriaNoEncontrada: si el id no existe.
        """
        self._repo.cambiar_activo(categoria_id, activo)
        logger.info("Categoría id=%s %s.", categoria_id, "activada" if activo else "desactivada")

    # ------------------------------------------------------------------
    # Apoyo
    # ------------------------------------------------------------------
    def asegurar_categoria(self, nombre: str, orden: int = 0) -> int:
        """Devuelve el id de la categoría ``nombre``, creándola si no existe.

        Útil para importaciones y siembra de datos: garantiza que el producto
        quede clasificado sin obligar al usuario a crear la categoría antes.
        """
        nombre = str(nombre or "").strip() or CATEGORIA_SIN_ASIGNAR
        existente = self._repo.obtener_por_nombre(nombre)
        if existente is not None:
            return int(existente["id"])
        return self._repo.crear({"nombre": nombre, "orden": orden, "activo": True})

    @staticmethod
    def _normalizar(datos: dict[str, Any]) -> dict[str, Any]:
        """Valida y normaliza los datos recibidos desde la presentación.

        Raises:
            DatosCategoriaInvalidos: ante cualquier dato faltante o inválido.
        """
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise DatosCategoriaInvalidos("El nombre de la categoría es obligatorio.")
        if len(nombre) > 100:
            raise DatosCategoriaInvalidos("El nombre de la categoría es demasiado largo (máx. 100).")

        orden = datos.get("orden")
        if orden in (None, ""):
            orden = 0
        try:
            orden = int(orden)
        except (TypeError, ValueError) as exc:
            raise DatosCategoriaInvalidos("El orden debe ser un número entero.") from exc
        if orden < 0:
            raise DatosCategoriaInvalidos("El orden no puede ser negativo.")

        return {
            "nombre": nombre,
            "orden": orden,
            "activo": bool(datos.get("activo", True)),
        }
