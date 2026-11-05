"""Vista "Compras / Ingresos de proveedores" — capa de PRESENTACIÓN.

Lista las compras (ingresos de mercadería) del día con su resumen y permite
registrar una nueva (stock ↑, kardex ``ENTRADA`` y egreso de caja según el
método de pago) y ver el detalle de cada compra.

No ejecuta SQL: delega en ``CompraService`` (inyectado desde el composition
root).

Accesibilidad / atajos:
    * ``Ctrl+N``   → registrar un ingreso.
    * ``Enter``/F2 → ver el detalle de la compra seleccionada.
    * ``F5``       → recargar el listado.
"""

from __future__ import annotations

import logging
from tkinter import messagebox, ttk
from typing import Any

import customtkinter as ctk

from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.componentes import Accion, BarraAcciones
from src.presentacion.dialogos_compras import (
    DialogoDetalleCompra,
    DialogoRegistrarCompra,
)
from src.presentacion.estilos import (
    BORDE,
    COLOR_ACENTO,
    COLOR_FILA_PAR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
    RADIO_SUPERFICIE,
    estilo_treeview,
)
from src.presentacion.recursos import icono

logger = logging.getLogger(__name__)

COLOR_TARJETA = "#ffffff"


class ComprasView(ctk.CTkFrame):
    """Listado de compras/ingresos del día con registro y detalle."""

    COLUMNAS = ("proveedor", "hora", "metodo", "estado", "observaciones", "total")
    ENCABEZADOS = ("Proveedor", "Hora", "Método", "Estado", "Observaciones", "Total")
    ANCHOS = (230, 70, 130, 100, 300, 110)
    ALINEACIONES = ("w", "center", "center", "center", "w", "center")

    def __init__(
        self,
        master: Any,
        compra_service: Any,
        producto_service: Any,
        proveedor_service: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._compra_service = compra_service
        self._producto_service = producto_service
        self._proveedor_service = proveedor_service
        self._fecha: str = ""
        self._compras: list[dict[str, Any]] = []
        self._resumen: dict[str, Any] = {}
        self._por_id: dict[str, dict[str, Any]] = {}

        self._configurar_estilos()
        self._construir_ui()
        self._atajos_teclado()
        self.cargar_datos()

    # ------------------------------------------------------------------ UI
    def _configurar_estilos(self) -> None:
        estilo_treeview("Compras.Treeview")

    def _construir_ui(self) -> None:
        # --- Cabecera ---
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.pack(fill="x", padx=24, pady=(18, 2))

        ctk.CTkLabel(
            cabecera,
            text="Compras / Ingresos",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
        ).pack(side="left")

        self._lbl_fecha = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        )
        self._lbl_fecha.pack(side="left", padx=(14, 0), pady=(14, 0))

        self._lbl_estado = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        )
        self._lbl_estado.pack(side="right", padx=(12, 0))

        # --- Barra de herramientas ---
        barra = BarraAcciones(
            self,
            primaria=Accion("Registrar ingreso", self._registrar_ingreso, "mas"),
            secundarias=[Accion("Ver detalle", self._ver_detalle, "recibo")],
            menu=[Accion("Actualizar", self.cargar_datos, "recargar")],
        )
        barra.pack(fill="x", padx=24, pady=(6, 4))

        ctk.CTkLabel(
            barra.izquierda,
            text="Registra el ingreso de mercadería: el stock y el costo se actualizan al guardar.",
            image=icono("camion", 18, COLOR_SECUNDARIO),
            compound="left",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).pack(side="left")

        # --- Resumen del día ---
        resumen_barra = ctk.CTkFrame(self, fg_color="transparent")
        resumen_barra.pack(fill="x", padx=24, pady=(4, 6))
        for columna in range(4):
            resumen_barra.grid_columnconfigure(columna, weight=1, uniform="kpi")

        self._tarjeta_cantidad = self._crear_tarjeta(resumen_barra)
        self._tarjeta_cantidad.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._lbl_cantidad = self._lbl_tarjeta_valor(self._tarjeta_cantidad)

        self._tarjeta_total = self._crear_tarjeta(resumen_barra)
        self._tarjeta_total.grid(row=0, column=1, sticky="nsew", padx=6)
        self._lbl_total = self._lbl_tarjeta_valor(self._tarjeta_total)

        self._tarjeta_contado = self._crear_tarjeta(resumen_barra)
        self._tarjeta_contado.grid(row=0, column=2, sticky="nsew", padx=6)
        self._lbl_contado = self._lbl_tarjeta_valor(self._tarjeta_contado)

        self._tarjeta_metodos = self._crear_tarjeta(resumen_barra)
        self._tarjeta_metodos.grid(row=0, column=3, sticky="nsew", padx=(6, 0))
        self._lbl_metodos = ctk.CTkLabel(
            self._tarjeta_metodos,
            text="—",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
            wraplength=320,
            justify="left",
        )
        self._lbl_metodos.pack(anchor="w", padx=16, pady=(2, 12))

        # --- Tabla de compras ---
        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=24, pady=(0, 10))

        contenedor_tabla = ctk.CTkFrame(cuerpo, fg_color="transparent")
        contenedor_tabla.pack(side="left", fill="both", expand=True)

        self._tabla = ttk.Treeview(
            contenedor_tabla,
            columns=self.COLUMNAS,
            show="headings",
            style="Compras.Treeview",
            takefocus=True,
        )
        for columna, titulo, ancho, alineacion in zip(
            self.COLUMNAS, self.ENCABEZADOS, self.ANCHOS, self.ALINEACIONES
        ):
            self._tabla.heading(columna, text=titulo, anchor="center")
            self._tabla.column(
                columna,
                width=ancho,
                anchor=alineacion,
                stretch=(columna == "observaciones"),
                minwidth=60,
            )
        self._tabla.pack(side="left", fill="both", expand=True)

        barra_scroll = ttk.Scrollbar(contenedor_tabla, orient="vertical", command=self._tabla.yview)
        self._tabla.configure(yscrollcommand=barra_scroll.set)
        barra_scroll.pack(side="right", fill="y")

        self._tabla.tag_configure("par", background=COLOR_FILA_PAR)
        self._tabla.tag_configure("impar", background="#ffffff")
        self._tabla.bind("<Double-1>", lambda _e: self._ver_detalle())
        self._tabla.bind("<Return>", lambda _e: self._ver_detalle())
        self._tabla.bind("<F2>", lambda _e: self._ver_detalle())

        ctk.CTkLabel(
            self,
            text="Atajos:  Ctrl+N registrar ingreso  ·  Enter/F2 ver detalle  ·  F5 recargar",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=26, pady=(0, 6))

    @staticmethod
    def _crear_tarjeta(padre: Any) -> ctk.CTkFrame:
        tarjeta = ctk.CTkFrame(
            padre,
            width=1,
            corner_radius=RADIO_SUPERFICIE,
            fg_color=COLOR_TARJETA,
            border_width=1,
            border_color=BORDE,
        )
        ctk.CTkLabel(
            tarjeta, text="—", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=16, pady=(10, 0))
        return tarjeta

    def _lbl_tarjeta_valor(self, tarjeta: Any) -> ctk.CTkLabel:
        etiqueta = ctk.CTkLabel(
            tarjeta,
            text="—",
            text_color=COLOR_ACENTO,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
        )
        etiqueta.pack(anchor="w", padx=16, pady=(0, 10))
        return etiqueta

    # ------------------------------------------------------------- Atajos
    def _atajos_teclado(self) -> None:
        raiz = self.winfo_toplevel()
        raiz.bind("<F5>", self._atajo(self.cargar_datos), add="+")
        raiz.bind("<Control-n>", self._atajo(self._registrar_ingreso), add="+")

    def _atajo(self, accion: Any) -> Any:
        def _manejador(_event: Any = None) -> str | None:
            if self.winfo_ismapped():
                accion()
                return "break"
            return None

        return _manejador

    # --------------------------------------------------------------- Datos
    def cargar_datos(self) -> None:
        """Recarga el listado de compras del día y su resumen."""
        try:
            reporte = self._compra_service.compras_del_dia()
        except ErrorConexionBD as exc:
            logger.error("Error de conexión al cargar compras: %s", exc)
            self._lbl_estado.configure(text="⚠  Sin conexión a la base de datos", text_color="#b91c1c")
            messagebox.showerror("Error de conexión", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001 - la UI nunca debe caer
            logger.exception("Error inesperado al cargar compras.")
            self._lbl_estado.configure(text="⚠  Error inesperado", text_color="#b91c1c")
            messagebox.showerror("Error", f"No se pudo cargar las compras:\n\n{exc}", parent=self)
            return

        self._fecha = reporte["fecha"]
        self._compras = reporte["compras"]
        self._resumen = reporte["resumen"]
        self._render()
        self._actualizar_resumen()

    def _render(self) -> None:
        for item in self._tabla.get_children():
            self._tabla.delete(item)
        self._por_id = {str(c.get("compra_id")): c for c in self._compras}

        for indice, compra in enumerate(self._compras):
            self._tabla.insert(
                "",
                "end",
                iid=str(compra.get("compra_id")),
                values=self._formatear_fila(compra),
                tags=("par" if indice % 2 == 0 else "impar",),
            )

        total = len(self._compras)
        self._lbl_estado.configure(
            text=f"{total} compra(s)",
            text_color=COLOR_OK if total else COLOR_SECUNDARIO,
        )

    def _actualizar_resumen(self) -> None:
        resumen = self._resumen or {}
        cantidad = resumen.get("cantidad", 0)
        total_gastado = float(resumen.get("total_gastado") or 0)
        total_contado = float(resumen.get("total_afecta_caja") or 0)
        por_metodo = resumen.get("por_metodo") or {}

        fecha = self._fecha or ""
        self._lbl_fecha.configure(
            text=f"Hoy · {fecha[8:10]}/{fecha[5:7]}/{fecha[0:4]}" if len(fecha) == 10 else ""
        )

        titulos = self._tarjeta_cantidad.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Compras de hoy")
        self._lbl_cantidad.configure(text=str(cantidad))

        titulos = self._tarjeta_total.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Total gastado")
        self._lbl_total.configure(text=f"S/ {total_gastado:,.2f}")

        titulos = self._tarjeta_contado.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Contado (afecta caja)")
        self._lbl_contado.configure(text=f"S/ {total_contado:,.2f}")

        if por_metodo:
            texto = "   ·   ".join(
                f"{metodo} S/ {monto:,.2f}" for metodo, monto in sorted(por_metodo.items())
            )
        else:
            texto = "Aún no hay compras registradas hoy."
        self._lbl_metodos.configure(text=texto)

    # ------------------------------------------------------------ Registro
    def _registrar_ingreso(self) -> None:
        dialogo = DialogoRegistrarCompra(
            self,
            compra_service=self._compra_service,
            producto_service=self._producto_service,
            proveedor_service=self._proveedor_service,
        )
        resultado = dialogo.mostrar()
        if not resultado:
            return

        detalle_caja = (
            f"\nAfecta caja: S/ {resultado['total']:,.2f}"
            if resultado.get("afecta_caja")
            else "\nNo afecta caja (crédito)."
        )
        nuevos = int(resultado.get("productos_vinculados") or 0)
        detalle_catalogo = (
            f"\n\n{nuevos} producto(s) nuevo(s) agregado(s) a la lista del proveedor."
            if nuevos
            else ""
        )
        messagebox.showinfo(
            "Ingreso registrado",
            f"Compra #{resultado['compra_id']} registrada.\n\n"
            f"Proveedor: {resultado['proveedor']}\n"
            f"Productos: {resultado['items']}\n"
            f"Total: S/ {resultado['total']:,.2f}\n"
            f"Método: {resultado['metodo_pago']}"
            f"{detalle_caja}"
            f"{detalle_catalogo}",
            parent=self,
        )
        self.cargar_datos()

    # ---------------------------------------------------------- Detalle
    def _ver_detalle(self) -> None:
        compra = self._obtener_seleccionado()
        if compra is None:
            messagebox.showinfo(
                "Selecciona una compra",
                "Primero selecciona una compra de la lista.",
                parent=self,
            )
            return
        compra_id = int(compra["compra_id"])
        try:
            lineas = self._compra_service.obtener_detalle_compra(compra_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al obtener el detalle de la compra id=%s.", compra_id)
            messagebox.showerror("Error", f"No se pudo cargar el detalle:\n\n{exc}", parent=self)
            return
        DialogoDetalleCompra(self, compra=compra, lineas=lineas).mostrar()

    # ------------------------------------------------------------ Apoyo
    def _obtener_seleccionado(self) -> dict[str, Any] | None:
        seleccion = self._tabla.selection()
        iid = seleccion[0] if seleccion else self._tabla.focus()
        if not iid:
            return None
        return self._por_id.get(str(iid))

    @staticmethod
    def _formatear_fila(compra: dict[str, Any]) -> tuple[Any, ...]:
        return (
            compra.get("proveedor") or "—",
            compra.get("hora") or "—",
            compra.get("metodo_pago") or "—",
            compra.get("estado") or "—",
            compra.get("observaciones") or "",
            f"S/ {float(compra.get('total') or 0):,.2f}",
        )
