"""Excepciones de negocio — capa de DOMINIO.

No dependen de la base de datos ni de la interfaz: describen *qué* falló en
términos del negocio, no *cómo*.
"""

from __future__ import annotations


class ErrorDominio(Exception):
    """Raíz de todos los errores de negocio."""


class ErrorVenta(ErrorDominio):
    """Error genérico durante el proceso de venta."""


class CarritoVacio(ErrorVenta):
    """Se intentó cobrar un carrito sin productos."""


class ComprobanteInvalido(ErrorVenta):
    """Los datos del comprobante (tipo, DNI/RUC, razón social) no son válidos."""


class StockInsuficiente(ErrorVenta):
    """No hay stock suficiente para completar la venta."""

    def __init__(
        self,
        producto_id: int,
        cantidad_solicitada: float,
        disponible: float | None = None,
        mensaje: str | None = None,
    ) -> None:
        self.producto_id = producto_id
        self.cantidad_solicitada = cantidad_solicitada
        self.disponible = disponible

        if mensaje is None:
            mensaje = (
                f"Stock insuficiente para el producto id={producto_id} "
                f"(solicitado: {cantidad_solicitada:g}"
            )
            mensaje += f", disponible: {disponible:g})." if disponible is not None else ")."
        super().__init__(mensaje)


class ErrorProducto(ErrorDominio):
    """Error genérico en la gestión de productos del catálogo."""


class DatosProductoInvalidos(ErrorProducto):
    """Los datos enviados no cumplen las reglas de negocio del producto."""


class ProductoDuplicado(ErrorProducto):
    """Ya existe un producto con el mismo código de barras o interno."""


class ProductoNoEncontrado(ErrorProducto):
    """No se encontró el producto indicado."""


class ErrorProveedor(ErrorDominio):
    """Error genérico en la gestión de proveedores."""


class DatosProveedorInvalidos(ErrorProveedor):
    """Los datos enviados no cumplen las reglas de negocio del proveedor."""


class ProveedorNoEncontrado(ErrorProveedor):
    """No se encontró el proveedor indicado."""


class ErrorCategoria(ErrorDominio):
    """Error genérico en la gestión de categorías del catálogo."""


class DatosCategoriaInvalidos(ErrorCategoria):
    """Los datos enviados no cumplen las reglas de negocio de la categoría."""


class CategoriaDuplicada(ErrorCategoria):
    """Ya existe una categoría con el mismo nombre."""


class CategoriaNoEncontrada(ErrorCategoria):
    """No se encontró la categoría indicada."""
