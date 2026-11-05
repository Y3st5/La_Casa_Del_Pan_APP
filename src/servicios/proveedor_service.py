"""Servicio de proveedores — capa de SERVICIOS.

Centraliza la gestión de proveedores y su **calendario de visitas** (lunes a
domingo), validando las reglas de negocio antes de delegar en el repositorio.

La presentación no ejecuta SQL: depende solo de este servicio.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from src.dominio.excepciones import DatosProveedorInvalidos, ProveedorNoEncontrado
from src.infraestructura.conexion import ConexionBD
from src.repositorios.proveedor_repo import ProveedorRepo

logger = logging.getLogger(__name__)

#: Nombres de los días de la semana, indexados con ``dia - 1`` (1 = lunes).
DIAS_SEMANA: tuple[str, ...] = (
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
)

#: Abreviaturas de los días, indexadas con ``dia - 1`` (1 = lunes).
DIAS_SEMANA_CORTOS: tuple[str, ...] = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


class ProveedorService:
    """Casos de uso de gestión de proveedores y días de visita."""

    def __init__(
        self,
        conexion_bd: ConexionBD | None = None,
        proveedor_repo: ProveedorRepo | None = None,
    ) -> None:
        """Inicializa el servicio.

        Args:
            conexion_bd: conexión a inyectar si no se pasa un repositorio.
            proveedor_repo: repositorio ya construido (tests / composition root).
        """
        self._proveedor_repo = proveedor_repo or ProveedorRepo(conexion_bd)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def listar(self, incluir_inactivos: bool = False) -> list[dict[str, Any]]:
        """Devuelve los proveedores (con sus días) ordenados por nombre."""
        return self._proveedor_repo.listar(incluir_inactivos=incluir_inactivos)

    def obtener(self, proveedor_id: int) -> dict[str, Any] | None:
        """Devuelve un proveedor por su id (o ``None``)."""
        return self._proveedor_repo.obtener_por_id(proveedor_id)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def crear(self, datos: dict[str, Any]) -> int:
        """Valida y crea un proveedor. Devuelve el id asignado.

        Raises:
            DatosProveedorInvalidos: si los datos no cumplen las reglas.
        """
        limpio = self._normalizar(datos)
        nuevo_id = self._proveedor_repo.crear(limpio)
        logger.info("Proveedor creado id=%s (%s).", nuevo_id, limpio["nombre"])
        return nuevo_id

    def actualizar(self, proveedor_id: int, datos: dict[str, Any]) -> None:
        """Valida y actualiza un proveedor (incluidos sus días de visita).

        Raises:
            DatosProveedorInvalidos: si los datos no cumplen las reglas.
            ProveedorNoEncontrado: si el id no existe.
        """
        limpio = self._normalizar(datos)
        self._proveedor_repo.actualizar(proveedor_id, limpio)
        logger.info("Proveedor actualizado id=%s (%s).", proveedor_id, limpio["nombre"])

    def cambiar_activo(self, proveedor_id: int, activo: bool) -> None:
        """Activa o desactiva (baja lógica) un proveedor.

        Raises:
            ProveedorNoEncontrado: si el id no existe.
        """
        self._proveedor_repo.cambiar_activo(proveedor_id, activo)
        logger.info("Proveedor id=%s %s.", proveedor_id, "activado" if activo else "desactivado")

    def cambiar_dia(self, proveedor_id: int, dia: int, viene: bool) -> None:
        """Marca/desmarca un día de visita del calendario (lunes=1 … domingo=7).

        Raises:
            DatosProveedorInvalidos: si el día no está entre 1 y 7.
            ProveedorNoEncontrado: si el id no existe.
        """
        dia = self._validar_dia(dia)
        self._proveedor_repo.cambiar_dia(proveedor_id, dia, viene)

    # ------------------------------------------------------------------
    # Catálogo interno del proveedor (qué productos trae)
    # ------------------------------------------------------------------
    def listar_productos(self, proveedor_id: int) -> list[dict[str, Any]]:
        """Productos del catálogo del proveedor (con precio de compra sugerido)."""
        return self._proveedor_repo.listar_productos(proveedor_id)

    def vincular_producto(
        self, proveedor_id: int, producto_id: int, precio_compra: Any = None
    ) -> None:
        """Agrega un producto al catálogo del proveedor (idempotente).

        Raises:
            DatosProveedorInvalidos: si el precio de compra es inválido.
            ProveedorNoEncontrado: si el proveedor no existe.
        """
        if self._proveedor_repo.obtener_por_id(int(proveedor_id)) is None:
            raise ProveedorNoEncontrado(f"No se encontró el proveedor con id={proveedor_id}.")
        precio = self._normalizar_precio(precio_compra)
        self._proveedor_repo.vincular_producto(int(proveedor_id), int(producto_id), precio)
        logger.info("Producto id=%s vinculado al proveedor id=%s.", producto_id, proveedor_id)

    def desvincular_producto(self, proveedor_id: int, producto_id: int) -> None:
        """Quita un producto del catálogo del proveedor."""
        self._proveedor_repo.desvincular_producto(int(proveedor_id), int(producto_id))
        logger.info("Producto id=%s desvinculado del proveedor id=%s.", producto_id, proveedor_id)

    @staticmethod
    def _normalizar_precio(precio: Any) -> float | None:
        """Convierte el precio de compra a ``float >= 0`` (o ``None`` si vacío)."""
        if precio in (None, ""):
            return None
        try:
            valor = round(float(precio), 2)
        except (TypeError, ValueError) as exc:
            raise DatosProveedorInvalidos(f"Precio de compra inválido: {precio!r}.") from exc
        if valor < 0:
            raise DatosProveedorInvalidos("El precio de compra no puede ser negativo.")
        return valor

    # ------------------------------------------------------------------
    # Validación / normalización
    # ------------------------------------------------------------------
    def _normalizar(self, datos: dict[str, Any]) -> dict[str, Any]:
        """Valida y normaliza los datos recibidos desde la presentación.

        Raises:
            DatosProveedorInvalidos: ante cualquier dato faltante o inválido.
        """
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise DatosProveedorInvalidos("El nombre del proveedor es obligatorio.")
        return {
            "nombre": nombre,
            "contacto": str(datos.get("contacto") or "").strip() or None,
            "telefono": str(datos.get("telefono") or "").strip() or None,
            "email": str(datos.get("email") or "").strip() or None,
            "notas": str(datos.get("notas") or "").strip() or None,
            "activo": bool(datos.get("activo", True)),
            "dias": self._normalizar_dias(datos.get("dias")),
        }

    @classmethod
    def _normalizar_dias(cls, dias: Iterable[Any] | None) -> list[int]:
        """Devuelve la lista de días válidos (1..7), única y ordenada."""
        if not dias:
            return []
        normalizados = {cls._validar_dia(d) for d in dias}
        return sorted(normalizados)

    @staticmethod
    def _validar_dia(dia: Any) -> int:
        """Convierte ``dia`` a entero y verifica que esté entre 1 y 7."""
        try:
            numero = int(dia)
        except (TypeError, ValueError) as exc:
            raise DatosProveedorInvalidos(f"Día inválido: {dia!r}.") from exc
        if not 1 <= numero <= 7:
            raise DatosProveedorInvalidos(f"El día debe estar entre 1 (lunes) y 7 (domingo): {numero}.")
        return numero
