"""Servicio de compras / ingresos de proveedores — capa de SERVICIOS.

Orquesta el registro de ingresos de mercadería:

- Crea la compra (cabecera) y su detalle.
- Aumenta el stock de cada producto.
- Registra el movimiento en el kardex (tipo ``ENTRADA``).
- Guarda el ``precio_compra`` unitario (trazabilidad de costo).

El efecto en el arqueo de caja (egreso de efectivo físico) **no** se guarda
aquí: ``CajaService`` deduce las compras del día desde la tabla ``compras``
según su método de pago (ver :data:`METODOS_COMPRA_AFECTAN_CAJA`). Así el
repositorio es la única fuente de verdad y el arqueo es reproducible.

No ejecuta SQL: delega en los repositorios.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.infraestructura.conexion import ConexionBD
from src.repositorios.compra_repo import (
    METODOS_COMPRA_AFECTAN_CAJA,
    METODOS_PAGO_COMPRA,
    CompraRepo,
)
from src.repositorios.movimiento_repo import MovimientoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.proveedor_repo import ProveedorRepo

logger = logging.getLogger(__name__)

#: Tipo de movimiento del kardex para una entrada por compra.
TIPO_MOVIMIENTO_ENTRADA = "ENTRADA"

__all__ = [
    "CompraService",
    "ItemCompra",
    "ResultadoCompra",
    "METODOS_PAGO_COMPRA",
    "METODOS_COMPRA_AFECTAN_CAJA",
    "TIPO_MOVIMIENTO_ENTRADA",
]


@dataclass(frozen=True)
class ItemCompra:
    """Línea de una compra a registrar."""

    producto_id: int
    cantidad: float
    precio_compra: float


@dataclass(frozen=True)
class ResultadoCompra:
    """Resultado de registrar una compra."""

    compra_id: int
    proveedor_id: int
    proveedor_nombre: str
    fecha: str
    total: float
    metodo_pago: str
    items: int
    afecta_caja: bool
    monto_caja: float
    #: Cuántos productos se agregaron por primera vez al catálogo del proveedor.
    productos_vinculados: int = 0


class CompraService:
    """Casos de uso relacionados con ingresos de proveedores."""

    METODOS_PAGO = METODOS_PAGO_COMPRA

    def __init__(
        self,
        conexion_bd: ConexionBD,
        compra_repo: CompraRepo | None = None,
        producto_repo: ProductoRepo | None = None,
        proveedor_repo: ProveedorRepo | None = None,
        movimiento_repo: MovimientoRepo | None = None,
    ) -> None:
        self._bd = conexion_bd
        self._compra_repo = compra_repo or CompraRepo(conexion_bd)
        self._producto_repo = producto_repo or ProductoRepo(conexion_bd)
        self._proveedor_repo = proveedor_repo or ProveedorRepo(conexion_bd)
        self._movimiento_repo = movimiento_repo or MovimientoRepo(conexion_bd)
        # Asegurar esquema (idempotente).
        self._compra_repo.asegurar_tablas()

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def compras_del_dia(self, fecha: str | None = None) -> dict[str, Any]:
        """Reporte de compras de un día con resumen agregado."""
        fecha = fecha or datetime.now().strftime("%Y-%m-%d")
        filas = self._compra_repo.listar_por_fecha(fecha)

        compras: list[dict[str, Any]] = []
        total_gastado = 0.0
        total_afecta_caja = 0.0
        por_metodo: dict[str, float] = {}

        for c in filas:
            total = round(float(c["total"]), 2)
            metodo = str(c["metodo_pago"])
            compra = {
                "compra_id": int(c["id"]),
                "proveedor_id": int(c["proveedor_id"]),
                "proveedor": c["proveedor_nombre"] or f"Proveedor #{c['proveedor_id']}",
                "fecha": c["fecha"],
                "hora": str(c["creado_en"] or "")[11:16],
                "total": total,
                "metodo_pago": metodo,
                "estado": c["estado"],
                "observaciones": c["observaciones"],
            }
            compras.append(compra)
            total_gastado += total
            por_metodo[metodo] = round(por_metodo.get(metodo, 0.0) + total, 2)
            if metodo in METODOS_COMPRA_AFECTAN_CAJA:
                total_afecta_caja += total

        return {
            "fecha": fecha,
            "compras": compras,
            "resumen": {
                "cantidad": len(compras),
                "total_gastado": round(total_gastado, 2),
                "total_afecta_caja": round(total_afecta_caja, 2),
                "por_metodo": por_metodo,
            },
        }

    def obtener_detalle_compra(self, compra_id: int) -> list[dict[str, Any]]:
        """Detalle de una compra (líneas con producto, cantidad, costo, subtotal)."""
        return self._compra_repo.listar_detalle(compra_id)

    def obtener_proveedores_activos(self) -> list[dict[str, Any]]:
        """Proveedores activos para el selector del diálogo de ingreso."""
        return self._proveedor_repo.listar(incluir_inactivos=False)

    # ------------------------------------------------------------------
    # Caso de uso principal: registrar ingreso de proveedor
    # ------------------------------------------------------------------
    def registrar_compra(
        self,
        proveedor_id: int,
        items: list[ItemCompra],
        metodo_pago: str,
        fecha: str | None = None,
        observaciones: str | None = None,
    ) -> ResultadoCompra:
        """Registra una compra completa: cabecera + detalle + stock + kardex.

        Args:
            proveedor_id: id del proveedor (debe existir).
            items: líneas ``(producto_id, cantidad, precio_compra)``.
            metodo_pago: uno de :data:`METODOS_PAGO_COMPRA`.
            fecha: fecha de la compra (``'YYYY-MM-DD'``). Si ``None``, hoy.
            observaciones: notas libres (factura, lote, etc.).

        Returns:
            :class:`ResultadoCompra`.

        Raises:
            ValueError: si no hay items, el método es inválido o el proveedor
                no existe.
        """
        if not items:
            raise ValueError("La compra debe tener al menos un producto.")

        if metodo_pago not in METODOS_PAGO_COMPRA:
            raise ValueError(f"Método de pago no válido para una compra: '{metodo_pago}'.")

        proveedor = self._proveedor_repo.obtener_por_id(int(proveedor_id))
        if proveedor is None:
            raise ValueError(f"No existe el proveedor con id={proveedor_id}.")

        fecha = fecha or datetime.now().strftime("%Y-%m-%d")
        total = round(sum(item.cantidad * item.precio_compra for item in items), 2)
        afecta_caja = metodo_pago in METODOS_COMPRA_AFECTAN_CAJA

        # Productos ya vinculados al proveedor (para detectar los nuevos).
        vinculados_antes = self._proveedor_repo.ids_productos_vinculados(int(proveedor_id))

        # ---- Transacción ÚNICA: cabecera + detalle + stock + kardex ----
        with self._bd.cursor() as (_conexion, cursor):
            compra_id = self._compra_repo.crear_compra(
                proveedor_id=proveedor_id,
                fecha=fecha,
                total=total,
                metodo_pago=metodo_pago,
                estado="RECIBIDO",
                observaciones=observaciones,
                cursor=cursor,
            )

            for item in items:
                producto_id = int(item.producto_id)
                cantidad = float(item.cantidad)
                precio_compra = float(item.precio_compra)

                # Detalle de compra (guarda el costo unitario).
                self._compra_repo.agregar_detalle(
                    compra_id=compra_id,
                    producto_id=producto_id,
                    cantidad=cantidad,
                    precio_compra=precio_compra,
                    cursor=cursor,
                )

                # Stock ↑ (atómico).
                stock_anterior, stock_resultante = self._producto_repo.actualizar_stock(
                    producto_id, cantidad, cursor=cursor
                )

                # Kardex: ENTRADA (cantidad positiva).
                self._movimiento_repo.registrar(
                    producto_id=producto_id,
                    tipo=TIPO_MOVIMIENTO_ENTRADA,
                    cantidad=cantidad,
                    stock_anterior=stock_anterior,
                    stock_resultante=stock_resultante,
                    referencia_tipo="COMPRA",
                    referencia_id=compra_id,
                    motivo=f"Compra #{compra_id} — {proveedor['nombre']}",
                    cursor=cursor,
                )

                # Auto-vincula el producto al catálogo del proveedor (con su precio).
                self._proveedor_repo.vincular_producto(
                    int(proveedor_id), producto_id, precio_compra, cursor=cursor
                )

        productos_vinculados = sum(
            1 for item in items if int(item.producto_id) not in vinculados_antes
        )

        resultado = ResultadoCompra(
            compra_id=compra_id,
            proveedor_id=int(proveedor_id),
            proveedor_nombre=str(proveedor["nombre"]),
            fecha=fecha,
            total=total,
            metodo_pago=metodo_pago,
            items=len(items),
            afecta_caja=afecta_caja,
            monto_caja=total if afecta_caja else 0.0,
            productos_vinculados=productos_vinculados,
        )
        logger.info(
            "Compra #%d registrada | proveedor=%s | total=S/ %.2f | metodo=%s | items=%d | "
            "afecta_caja=%s | nuevos_en_catalogo=%d",
            compra_id,
            proveedor["nombre"],
            total,
            metodo_pago,
            len(items),
            afecta_caja,
            productos_vinculados,
        )
        return resultado
