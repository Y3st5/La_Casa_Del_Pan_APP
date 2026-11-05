"""Diálogos del módulo Compras / Ingresos de proveedores — capa de PRESENTACIÓN.

Se mantienen en un módulo aparte para no engrosar ``dialogos.py``. Reutilizan
la base ``_DialogoModal`` y los tokens de color del diseño "Trigo".
"""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

import customtkinter as ctk

from src.presentacion.dialogos import (
    COLOR_BOTON_NEUTRO,
    COLOR_BOTON_NEUTRO_HOVER,
    COLOR_BOTON_OK_HOVER,
    _DialogoModal,
)
from src.presentacion.estilos import (
    COLOR_ACENTO,
    COLOR_ACENTO_HOVER,
    COLOR_ERROR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
)
from src.presentacion.recursos import icono

logger = logging.getLogger(__name__)


class DialogoRegistrarCompra(_DialogoModal):
    """Diálogo para **registrar un ingreso de mercadería** (compra a proveedor).

    Permite elegir proveedor, agregar varios productos (cantidad y precio de
    compra), elegir el método de pago y escribir observaciones. Al aceptar,
    llama a ``CompraService.registrar_compra`` (stock ↑ + kardex + caja).
    """

    ANCHO = 760
    ALTO = 710
    TITULO = "Registrar ingreso de proveedor"

    def __init__(
        self,
        master: Any,
        compra_service: Any,
        producto_service: Any,
        proveedor_service: Any = None,
    ) -> None:
        self._compra_service = compra_service
        self._producto_service = producto_service
        self._proveedor_service = proveedor_service
        self._items: list[dict[str, Any]] = []
        self._proveedores = compra_service.obtener_proveedores_activos()
        self._productos = producto_service.listar()
        #: Catálogo del proveedor elegido: ``{producto_id: precio de compra sugerido}``.
        self._catalogo: dict[int, float | None] = {}
        super().__init__(master, titulo=self.TITULO)

    # ------------------------------------------------------------------
    def _construir(self) -> None:
        ctk.CTkLabel(
            self,
            text="REGISTRAR INGRESO DE PROVEEDOR",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
        ).pack(pady=(16, 2))

        # --- Proveedor ---
        fila_prov = ctk.CTkFrame(self, fg_color="transparent")
        fila_prov.pack(fill="x", padx=24, pady=(6, 6))
        ctk.CTkLabel(
            fila_prov, text="Proveedor *", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        ).pack(side="left", padx=(0, 8))

        valores_prov = [f"{p['id']} - {p['nombre']}" for p in self._proveedores]
        self._var_proveedor = tk.StringVar(value=valores_prov[0] if valores_prov else "")
        ctk.CTkOptionMenu(
            fila_prov,
            values=valores_prov or ["(sin proveedores)"],
            variable=self._var_proveedor,
            width=320,
            height=34,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
            command=self._al_elegir_proveedor,
        ).pack(side="left")

        ctk.CTkFrame(self, height=2, fg_color=COLOR_ACENTO, corner_radius=0).pack(
            fill="x", padx=24, pady=(2, 8)
        )

        # --- Agregar producto ---
        fila_titulo = ctk.CTkFrame(self, fg_color="transparent")
        fila_titulo.pack(fill="x", padx=24)
        ctk.CTkLabel(
            fila_titulo,
            text="Agregar producto",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        self._var_ver_todos = ctk.IntVar(value=0)
        ctk.CTkCheckBox(
            fila_titulo,
            text="Ver todos los productos",
            variable=self._var_ver_todos,
            onvalue=1,
            offvalue=0,
            font=("Segoe UI", 11),
            command=self._refrescar_productos,
        ).pack(side="right")

        fila_add = ctk.CTkFrame(self, fg_color="transparent")
        fila_add.pack(fill="x", padx=22, pady=(2, 4))

        # Encabezados de columna (para saber qué es cada campo).
        est_fuente_col = ("Segoe UI", 10, "bold")
        ctk.CTkLabel(
            fila_add, text="Producto", text_color=COLOR_SECUNDARIO, font=est_fuente_col
        ).grid(row=0, column=0, padx=(0, 6), pady=(0, 2), sticky="w")
        ctk.CTkLabel(
            fila_add, text="Cantidad", text_color=COLOR_SECUNDARIO, font=est_fuente_col
        ).grid(row=0, column=1, padx=4, pady=(0, 2))
        ctk.CTkLabel(
            fila_add, text="Precio compra", text_color=COLOR_SECUNDARIO, font=est_fuente_col
        ).grid(row=0, column=2, padx=4, pady=(0, 2))
        ctk.CTkLabel(
            fila_add, text=" ", text_color=COLOR_SECUNDARIO, font=est_fuente_col
        ).grid(row=0, column=3, padx=6, pady=(0, 2))

        self._var_producto = tk.StringVar()
        self._menu_producto = ctk.CTkOptionMenu(
            fila_add,
            values=["(sin productos)"],
            variable=self._var_producto,
            width=300,
            height=34,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
            command=self._al_elegir_producto,
        )
        self._menu_producto.grid(row=1, column=0, padx=(0, 6))

        self._var_cantidad = tk.StringVar(value="1")
        ctk.CTkEntry(
            fila_add,
            textvariable=self._var_cantidad,
            width=80,
            height=34,
            justify="center",
            placeholder_text="1",
            border_color=COLOR_ACENTO,
            border_width=2,
            font=("Consolas", 14),
        ).grid(row=1, column=1, padx=4)

        self._var_precio = tk.StringVar(value="")
        ctk.CTkEntry(
            fila_add,
            textvariable=self._var_precio,
            width=100,
            height=34,
            justify="center",
            placeholder_text="0.00",
            border_color=COLOR_ACENTO,
            border_width=2,
            font=("Consolas", 14),
        ).grid(row=1, column=2, padx=4)

        ctk.CTkButton(
            fila_add,
            text="  Agregar",
            image=icono("mas", 15, "#ffffff"),
            width=110,
            height=34,
            fg_color=COLOR_OK,
            hover_color=COLOR_BOTON_OK_HOVER,
            font=("Segoe UI", 12, "bold"),
            command=self._agregar_item,
        ).grid(row=1, column=3, padx=6)

        self._lbl_hint = ctk.CTkLabel(
            self, text="", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 10)
        )
        self._lbl_hint.pack(anchor="w", padx=26)

        # --- Lista de items ---
        self._frame_items = ctk.CTkScrollableFrame(
            self, height=165, fg_color="#f7f5f1", corner_radius=8
        )
        self._frame_items.pack(fill="x", padx=22, pady=(8, 4))
        for columna in range(5):
            self._frame_items.grid_columnconfigure(columna, weight=3 if columna == 0 else 1)
        self._pintar_cabecera_items()

        self._lbl_total = ctk.CTkLabel(
            self,
            text="Total: S/ 0.00",
            text_color=COLOR_ACENTO,
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        )
        self._lbl_total.pack(pady=(2, 6))

        # --- Método de pago ---
        fila_pago = ctk.CTkFrame(self, fg_color="transparent")
        fila_pago.pack(fill="x", padx=24, pady=(2, 4))
        ctk.CTkLabel(
            fila_pago, text="Método de pago *", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        ).pack(side="left", padx=(0, 8))
        self._var_metodo = tk.StringVar(value=self._compra_service.METODOS_PAGO[0])
        ctk.CTkOptionMenu(
            fila_pago,
            values=list(self._compra_service.METODOS_PAGO),
            variable=self._var_metodo,
            width=200,
            height=34,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
        ).pack(side="left")

        # --- Observaciones ---
        self._var_obs = tk.StringVar()
        ctk.CTkEntry(
            self,
            textvariable=self._var_obs,
            height=34,
            placeholder_text="Observaciones (factura, lote, etc.)",
            border_color=COLOR_ACENTO,
            border_width=2,
            font=("Segoe UI", 12),
        ).pack(fill="x", padx=24, pady=(6, 2))

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack()
        self._refrescar_productos()
        self._botones("Registrar ingreso (Enter)", self._aceptar, color_ok=COLOR_OK)

    # ------------------------------------------------------------------
    def _pintar_cabecera_items(self) -> None:
        for columna, texto in enumerate(("Producto", "Cant.", "P. compra", "Subtotal", "")):
            ctk.CTkLabel(
                self._frame_items,
                text=texto,
                font=("Segoe UI", 11, "bold"),
                text_color=COLOR_SECUNDARIO,
            ).grid(row=0, column=columna, padx=6, pady=4, sticky="w")

    def _proveedor_actual_id(self) -> int | None:
        """Id del proveedor seleccionado en el desplegable (o ``None``)."""
        valor = self._var_proveedor.get()
        if not valor or " - " not in valor:
            return None
        try:
            return int(valor.split(" - ", 1)[0])
        except ValueError:
            return None

    def _al_elegir_proveedor(self, _valor: str | None = None) -> None:
        """Al cambiar de proveedor, recarga su catálogo de productos."""
        self._refrescar_productos()

    def _refrescar_productos(self) -> None:
        """Arma el desplegable de productos según el catálogo del proveedor.

        Por defecto muestra solo los productos que ese proveedor trae; con la
        casilla *Ver todos* se puede elegir cualquier producto del catálogo.
        """
        self._catalogo = {}
        proveedor_id = self._proveedor_actual_id()
        if proveedor_id is not None and self._proveedor_service is not None:
            try:
                for fila in self._proveedor_service.listar_productos(proveedor_id):
                    self._catalogo[int(fila["producto_id"])] = fila.get("precio_sugerido")
            except Exception:  # noqa: BLE001 - la UI nunca debe caer
                logger.exception(
                    "No se pudo cargar el catálogo del proveedor id=%s.", proveedor_id
                )
                self._catalogo = {}

        productos = self._opciones_producto()
        valores = [f"{p['id']} - {p['nombre']}" for p in productos]
        anterior = self._var_producto.get()
        self._menu_producto.configure(values=valores or ["(sin productos)"])
        self._var_producto.set(
            anterior if anterior in valores else (valores[0] if valores else "(sin productos)")
        )

        if self._catalogo and not self._var_ver_todos.get():
            self._lbl_hint.configure(
                text=f"Catálogo del proveedor: {len(self._catalogo)} producto(s). "
                "Marca «Ver todos» para elegir otros."
            )
        elif self._catalogo:
            self._lbl_hint.configure(text="Mostrando todos los productos (catálogo primero).")
        else:
            self._lbl_hint.configure(
                text="Este proveedor no tiene lista propia; se muestran todos los productos."
            )

    def _opciones_producto(self) -> list[dict[str, Any]]:
        """Productos a mostrar en el desplegable según el catálogo del proveedor."""
        if not self._catalogo:
            return list(self._productos)
        if not self._var_ver_todos.get():
            return [p for p in self._productos if int(p["id"]) in self._catalogo]
        return sorted(
            self._productos,
            key=lambda p: (
                0 if int(p["id"]) in self._catalogo else 1,
                str(p.get("nombre") or "").lower(),
            ),
        )

    def _al_elegir_producto(self, valor: str) -> None:
        """Sugiere el precio del proveedor (o el último costo conocido)."""
        producto = self._buscar_producto(valor)
        if producto is None:
            return
        sugerido = self._catalogo.get(int(producto["id"]))
        if sugerido:
            self._var_precio.set(f"{float(sugerido):.2f}")
            self._lbl_hint.configure(
                text=f"Precio sugerido del proveedor: S/ {float(sugerido):,.2f}"
            )
            return
        costo = producto.get("costo")
        if costo:
            self._var_precio.set(f"{float(costo):.2f}")
            self._lbl_hint.configure(text=f"Costo registrado: S/ {float(costo):,.2f}")
        else:
            self._lbl_hint.configure(text="Sin costo registrado; ingrese el precio de compra.")

    def _buscar_producto(self, valor: str) -> dict[str, Any] | None:
        if not valor or " - " not in valor:
            return None
        pid = int(valor.split(" - ", 1)[0])
        return next((p for p in self._productos if int(p["id"]) == pid), None)

    def _agregar_item(self) -> None:
        if not self._proveedores:
            self._lbl_error.configure(text="No hay proveedores registrados.")
            return
        producto = self._buscar_producto(self._var_producto.get())
        if producto is None:
            self._lbl_error.configure(text="Selecciona un producto válido.")
            return

        try:
            cantidad = float(self._var_cantidad.get().strip().replace(",", "."))
        except ValueError:
            self._lbl_error.configure(text="Cantidad inválida.")
            return
        if cantidad <= 0:
            self._lbl_error.configure(text="La cantidad debe ser mayor que cero.")
            return

        texto_precio = self._var_precio.get().strip().replace(",", ".")
        try:
            precio = float(texto_precio) if texto_precio else 0.0
        except ValueError:
            self._lbl_error.configure(text="Precio de compra inválido.")
            return
        if precio < 0:
            self._lbl_error.configure(text="El precio no puede ser negativo.")
            return

        self._items.append(
            {
                "producto_id": int(producto["id"]),
                "producto_nombre": str(producto["nombre"]),
                "cantidad": round(cantidad, 3),
                "precio_compra": round(precio, 2),
                "subtotal": round(cantidad * precio, 2),
            }
        )
        self._lbl_error.configure(text="")
        self._var_cantidad.set("1")
        self._var_precio.set("")
        self._actualizar_items()

    def _actualizar_items(self) -> None:
        for w in self._frame_items.winfo_children():
            if int(w.grid_info().get("row", 0)) > 0:
                w.destroy()

        total = 0.0
        for indice, item in enumerate(self._items, start=1):
            ctk.CTkLabel(
                self._frame_items,
                text=item["producto_nombre"],
                font=("Segoe UI", 11),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=0, padx=6, pady=2, sticky="w")
            ctk.CTkLabel(
                self._frame_items,
                text=f"{item['cantidad']:g}",
                font=("Segoe UI", 11),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=1, padx=6, pady=2, sticky="e")
            ctk.CTkLabel(
                self._frame_items,
                text=f"S/ {item['precio_compra']:,.2f}",
                font=("Segoe UI", 11),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=2, padx=6, pady=2, sticky="e")
            ctk.CTkLabel(
                self._frame_items,
                text=f"S/ {item['subtotal']:,.2f}",
                font=("Segoe UI", 11, "bold"),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=3, padx=6, pady=2, sticky="e")
            ctk.CTkButton(
                self._frame_items,
                text="Quitar",
                width=64,
                height=24,
                fg_color=COLOR_BOTON_NEUTRO,
                hover_color=COLOR_BOTON_NEUTRO_HOVER,
                font=("Segoe UI", 10),
                command=lambda i=indice - 1: self._eliminar_item(i),
            ).grid(row=indice, column=4, padx=6, pady=2)
            total += item["subtotal"]

        self._lbl_total.configure(text=f"Total: S/ {total:,.2f}")

    def _eliminar_item(self, indice: int) -> None:
        if 0 <= indice < len(self._items):
            self._items.pop(indice)
            self._actualizar_items()

    # ------------------------------------------------------------------
    def _aceptar(self) -> None:
        from src.servicios.compra_service import ItemCompra

        if not self._items:
            self._lbl_error.configure(text="Agrega al menos un producto.")
            return
        if not self._proveedores:
            self._lbl_error.configure(text="No hay proveedores registrados.")
            return

        proveedor_id = int(self._var_proveedor.get().split(" - ", 1)[0])
        items = [
            ItemCompra(
                producto_id=it["producto_id"],
                cantidad=it["cantidad"],
                precio_compra=it["precio_compra"],
            )
            for it in self._items
        ]

        try:
            resultado = self._compra_service.registrar_compra(
                proveedor_id=proveedor_id,
                items=items,
                metodo_pago=self._var_metodo.get(),
                observaciones=self._var_obs.get().strip() or None,
            )
        except ValueError as exc:
            self._lbl_error.configure(text=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - la UI no debe caer
            logger.exception("Error al registrar la compra.")
            self._lbl_error.configure(text=f"Error: {exc}")
            return

        self.resultado = {
            "compra_id": resultado.compra_id,
            "proveedor": resultado.proveedor_nombre,
            "total": resultado.total,
            "metodo_pago": resultado.metodo_pago,
            "items": resultado.items,
            "afecta_caja": resultado.afecta_caja,
            "productos_vinculados": getattr(resultado, "productos_vinculados", 0),
        }
        self.destroy()


class DialogoDetalleCompra(_DialogoModal):
    """Diálogo de solo lectura con el detalle de una compra registrada."""

    ANCHO = 640
    ALTO = 560
    TITULO = "Detalle de compra"

    def __init__(self, master: Any, compra: dict[str, Any], lineas: list[dict[str, Any]]) -> None:
        self._compra = compra
        self._lineas = lineas
        super().__init__(master, titulo=f"Compra #{compra.get('compra_id')}")

    def _construir(self) -> None:
        ctk.CTkLabel(
            self,
            text=f"Compra #{self._compra.get('compra_id')} · {self._compra.get('proveedor')}",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(18, 2))

        ctk.CTkLabel(
            self,
            text=(
                f"Método: {self._compra.get('metodo_pago') or '—'}"
                f"    ·    Estado: {self._compra.get('estado') or '—'}"
            ),
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).pack(anchor="w", padx=24)

        obs = self._compra.get("observaciones")
        if obs:
            ctk.CTkLabel(
                self,
                text=f"Observaciones: {obs}",
                text_color=COLOR_SECUNDARIO,
                font=("Segoe UI", 11),
            ).pack(anchor="w", padx=24, pady=(2, 0))

        marco = ctk.CTkScrollableFrame(self, fg_color="#f7f5f1", corner_radius=8)
        marco.pack(fill="both", expand=True, padx=24, pady=12)
        for columna in range(4):
            marco.grid_columnconfigure(columna, weight=3 if columna == 0 else 1)

        for columna, texto in enumerate(("Producto", "Cant.", "P. compra", "Subtotal")):
            ctk.CTkLabel(
                marco, text=texto, font=("Segoe UI", 11, "bold"), text_color=COLOR_SECUNDARIO
            ).grid(row=0, column=columna, padx=6, pady=4, sticky="w")

        for indice, linea in enumerate(self._lineas, start=1):
            ctk.CTkLabel(
                marco,
                text=str(linea.get("producto_nombre") or "—"),
                font=("Segoe UI", 11),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=0, padx=6, pady=2, sticky="w")
            ctk.CTkLabel(
                marco,
                text=f"{float(linea.get('cantidad') or 0):g}",
                font=("Segoe UI", 11),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=1, padx=6, pady=2, sticky="e")
            ctk.CTkLabel(
                marco,
                text=f"S/ {float(linea.get('precio_compra') or 0):,.2f}",
                font=("Segoe UI", 11),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=2, padx=6, pady=2, sticky="e")
            ctk.CTkLabel(
                marco,
                text=f"S/ {float(linea.get('subtotal') or 0):,.2f}",
                font=("Segoe UI", 11, "bold"),
                text_color=COLOR_TEXTO,
            ).grid(row=indice, column=3, padx=6, pady=2, sticky="e")

        ctk.CTkLabel(
            self,
            text=f"TOTAL: S/ {float(self._compra.get('total') or 0):,.2f}",
            text_color=COLOR_ACENTO,
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        ).pack(pady=(0, 4))

        ctk.CTkButton(
            self,
            text="Cerrar (Esc)",
            width=170,
            height=38,
            fg_color=COLOR_BOTON_NEUTRO,
            hover_color=COLOR_BOTON_NEUTRO_HOVER,
            command=self.destroy,
        ).pack(pady=(0, 16))


class DialogoProductosProveedor(_DialogoModal):
    """Gestiona el **catálogo interno** de un proveedor: qué productos trae.

    Permite agregar/quitar productos y fijar su **precio de compra sugerido**
    (que se autocompleta al registrar una compra de ese proveedor). Los cambios
    se aplican al pulsar *Guardar*.
    """

    ANCHO = 740
    ALTO = 640
    TITULO = "Productos del proveedor"

    def __init__(
        self,
        master: Any,
        proveedor: dict[str, Any],
        proveedor_service: Any,
        producto_service: Any,
    ) -> None:
        self._proveedor = dict(proveedor)
        self._proveedor_service = proveedor_service
        self._producto_service = producto_service
        self._proveedor_id = int(proveedor["id"])
        self._todos = producto_service.listar()
        self._vinculados = proveedor_service.listar_productos(self._proveedor_id)
        self._originales = {int(p["producto_id"]) for p in self._vinculados}
        self._removidos: set[int] = set()
        self._vars_precio: dict[int, tk.StringVar] = {}
        self._filas: list[Any] = []
        super().__init__(master, titulo=f"Productos de «{self._proveedor.get('nombre')}»")

    def _construir(self) -> None:
        ctk.CTkLabel(
            self,
            text=f"«{self._proveedor.get('nombre')}»",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        ).pack(pady=(16, 0))
        ctk.CTkLabel(
            self,
            text=(
                "Productos que trae este proveedor. El precio es el de compra sugerido "
                "(se autocompleta al registrar una compra). Pulsa Guardar para aplicar."
            ),
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
            wraplength=self.ANCHO - 60,
            justify="left",
        ).pack(anchor="w", padx=24, pady=(2, 8))

        # --- Fila para agregar ---
        fila_add = ctk.CTkFrame(self, fg_color="transparent")
        fila_add.pack(fill="x", padx=22, pady=(0, 4))
        ctk.CTkLabel(
            fila_add, text="Agregar:", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12, "bold")
        ).pack(side="left", padx=(0, 6))

        self._var_nuevo = tk.StringVar()
        self._menu_nuevo = ctk.CTkOptionMenu(
            fila_add,
            values=["(sin productos)"],
            variable=self._var_nuevo,
            width=330,
            height=34,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
        )
        self._menu_nuevo.pack(side="left")

        self._var_precio_nuevo = tk.StringVar()
        ctk.CTkEntry(
            fila_add,
            textvariable=self._var_precio_nuevo,
            width=100,
            height=34,
            justify="center",
            placeholder_text="Precio",
            border_color=COLOR_ACENTO,
            border_width=2,
            font=("Consolas", 13),
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            fila_add,
            text="  Agregar",
            image=icono("mas", 15, "#ffffff"),
            width=120,
            height=34,
            fg_color=COLOR_OK,
            hover_color=COLOR_BOTON_OK_HOVER,
            font=("Segoe UI", 12, "bold"),
            command=self._agregar,
        ).pack(side="left")

        # --- Lista del catálogo ---
        self._frame = ctk.CTkScrollableFrame(self, fg_color="#f7f5f1", corner_radius=8)
        self._frame.pack(fill="both", expand=True, padx=22, pady=(6, 4))
        for columna, peso in enumerate((4, 2, 1)):
            self._frame.grid_columnconfigure(columna, weight=peso)

        self._lbl_estado = ctk.CTkLabel(
            self, text="", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11)
        )
        self._lbl_estado.pack()
        self._botones("Guardar (Enter)", self._guardar)
        self._refrescar()

    def _al_mostrar(self) -> None:
        self._menu_nuevo.focus_set()

    # ------------------------------------------------------------------
    def _refrescar(self) -> None:
        # Conserva lo que el usuario ya haya escrito en los precios.
        textos_previos = {pid: var.get() for pid, var in self._vars_precio.items()}
        for widget in self._filas:
            widget.destroy()
        self._filas = []
        self._vars_precio = {}

        for columna, texto in enumerate(("Producto", "Precio compra (S/)", "")):
            etiqueta = ctk.CTkLabel(
                self._frame, text=texto, font=("Segoe UI", 11, "bold"), text_color=COLOR_SECUNDARIO
            )
            etiqueta.grid(row=0, column=columna, padx=6, pady=4, sticky="w")
            self._filas.append(etiqueta)

        for indice, prod in enumerate(self._vinculados, start=1):
            pid = int(prod["producto_id"])
            nombre = ctk.CTkLabel(
                self._frame, text=str(prod.get("nombre") or "—"), font=("Segoe UI", 12),
                text_color=COLOR_TEXTO,
            )
            nombre.grid(row=indice, column=0, padx=6, pady=2, sticky="w")

            sugerido = prod.get("precio_sugerido")
            if pid in textos_previos:
                valor_inicial = textos_previos[pid]
            elif sugerido is not None:
                valor_inicial = f"{float(sugerido):.2f}"
            else:
                valor_inicial = ""
            var = tk.StringVar(value=valor_inicial)
            entrada = ctk.CTkEntry(
                self._frame, textvariable=var, width=110, height=28, justify="center",
                border_color=COLOR_ACENTO, border_width=1, font=("Consolas", 12),
            )
            entrada.grid(row=indice, column=1, padx=6, pady=2)
            self._vars_precio[pid] = var

            boton = ctk.CTkButton(
                self._frame, text="Quitar", width=70, height=26,
                fg_color=COLOR_BOTON_NEUTRO, hover_color=COLOR_BOTON_NEUTRO_HOVER,
                font=("Segoe UI", 10), command=lambda p=pid: self._quitar(p),
            )
            boton.grid(row=indice, column=2, padx=6, pady=2)

            self._filas.extend((nombre, entrada, boton))

        if not self._vinculados:
            vacio = ctk.CTkLabel(
                self._frame,
                text="Este proveedor aún no tiene productos. Agrega uno arriba.",
                font=("Segoe UI", 11), text_color=COLOR_SECUNDARIO,
            )
            vacio.grid(row=1, column=0, columnspan=3, padx=6, pady=10, sticky="w")
            self._filas.append(vacio)

        self._refrescar_menu()
        self._lbl_estado.configure(
            text=f"{len(self._vinculados)} producto(s) en el catálogo.", text_color=COLOR_SECUNDARIO
        )

    def _refrescar_menu(self) -> None:
        ids = {int(p["producto_id"]) for p in self._vinculados}
        disponibles = [p for p in self._todos if int(p["id"]) not in ids]
        valores = [f"{p['id']} - {p['nombre']}" for p in disponibles]
        self._menu_nuevo.configure(values=valores or ["(sin productos disponibles)"])
        self._var_nuevo.set(valores[0] if valores else "(sin productos disponibles)")

    def _agregar(self) -> None:
        valor = self._var_nuevo.get()
        if " - " not in valor:
            self._lbl_estado.configure(
                text="No hay productos disponibles para agregar.", text_color=COLOR_ERROR
            )
            return
        pid = int(valor.split(" - ", 1)[0])
        producto = next((p for p in self._todos if int(p["id"]) == pid), None)
        if producto is None:
            return

        texto = self._var_precio_nuevo.get().strip().replace(",", ".")
        try:
            precio = float(texto) if texto else None
        except ValueError:
            self._lbl_estado.configure(text="Precio inválido.", text_color=COLOR_ERROR)
            return
        if precio is not None and precio < 0:
            self._lbl_estado.configure(text="El precio no puede ser negativo.", text_color=COLOR_ERROR)
            return

        self._vinculados.append(
            {"producto_id": pid, "nombre": producto.get("nombre"), "precio_sugerido": precio}
        )
        self._removidos.discard(pid)
        self._var_precio_nuevo.set("")
        self._refrescar()
        self._lbl_estado.configure(
            text=f"«{producto.get('nombre')}» agregado. No olvides Guardar.", text_color=COLOR_OK
        )

    def _quitar(self, producto_id: int) -> None:
        self._vinculados = [
            p for p in self._vinculados if int(p["producto_id"]) != producto_id
        ]
        if producto_id in self._originales:
            self._removidos.add(producto_id)
        self._refrescar()
        self._lbl_estado.configure(text="Producto quitado. No olvides Guardar.", text_color=COLOR_OK)

    def _guardar(self) -> None:
        # 1) Validar todos los precios antes de guardar nada.
        precios: dict[int, float | None] = {}
        for prod in self._vinculados:
            pid = int(prod["producto_id"])
            var = self._vars_precio.get(pid)
            texto = var.get().strip().replace(",", ".") if var is not None else ""
            try:
                precio = float(texto) if texto else None
            except ValueError:
                self._lbl_estado.configure(
                    text=f"Precio inválido para «{prod.get('nombre')}».", text_color=COLOR_ERROR
                )
                return
            if precio is not None and precio < 0:
                self._lbl_estado.configure(
                    text=f"El precio de «{prod.get('nombre')}» no puede ser negativo.",
                    text_color=COLOR_ERROR,
                )
                return
            precios[pid] = precio

        # 2) Persistir.
        try:
            for pid in self._removidos:
                self._proveedor_service.desvincular_producto(self._proveedor_id, pid)
            for pid, precio in precios.items():
                self._proveedor_service.vincular_producto(self._proveedor_id, pid, precio)
        except Exception as exc:  # noqa: BLE001 - la UI no debe caer
            logger.exception("Error al guardar el catálogo del proveedor id=%s.", self._proveedor_id)
            self._lbl_estado.configure(text=f"Error al guardar: {exc}", text_color=COLOR_ERROR)
            return

        self.resultado = True
        self.destroy()
