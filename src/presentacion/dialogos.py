"""Diálogos modales de la interfaz — capa de PRESENTACIÓN."""

from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox
from typing import Any, Callable, Sequence

import customtkinter as ctk

from src.dominio.catalogos import codigo_unidad_sunat
from src.presentacion.estilos import (
    COLOR_ACENTO,
    COLOR_ACENTO_HOVER,
    COLOR_ERROR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
)
from src.servicios.producto_service import UNIDADES_MEDIDA
from src.servicios.proveedor_service import DIAS_SEMANA_CORTOS
from src.servicios.venta_service import redondear_total

logger = logging.getLogger(__name__)

COLOR_BOTON_NEUTRO = "#6b7280"
COLOR_BOTON_NEUTRO_HOVER = "#4b5563"
COLOR_BOTON_OK_HOVER = "#047857"


@dataclass(frozen=True)
class ResultadoPago:
    """Pago único (compatibilidad legacy)."""
    metodo_pago: str
    monto_recibido: float
    total_a_pagar: float
    vuelto: float
    redondeo: float = 0.0
    cliente: str = ""
    dni_cliente: str = ""


@dataclass(frozen=True)
class ResultadoPagoDividido:
    """Resultado de pago dividido: lista de pagos + datos boleta."""
    pagos: list[dict[str, Any]]  # [{"metodo_pago", "monto", "vuelto"}, ...]
    total_a_pagar: float
    redondeo: float
    cliente: str
    dni_cliente: str


class _DialogoModal(ctk.CTkToplevel):
    ANCHO = 400
    ALTO = 260
    TITULO = "Diálogo"

    def __init__(self, master: Any, titulo: str | None = None) -> None:
        super().__init__(master)
        self.withdraw()
        self.title(titulo or self.TITULO)
        self.resizable(False, False)
        self.resultado: Any = None
        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.transient(master)
        self.bind("<Escape>", lambda _event: self._cancelar())
        self._construir()
        self._centrar()

    def mostrar(self) -> Any:
        self.deiconify()
        self.update_idletasks()
        self.wait_visibility()
        self.grab_set()
        self._al_mostrar()
        self.wait_window(self)
        return self.resultado

    def _construir(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def _al_mostrar(self) -> None:
        pass

    def _cancelar(self) -> None:
        self.resultado = None
        self.destroy()

    def _centrar(self) -> None:
        self.update_idletasks()
        padre = self.master
        try:
            x = padre.winfo_rootx() + (padre.winfo_width() - self.ANCHO) // 2
            y = padre.winfo_rooty() + (padre.winfo_height() - self.ALTO) // 3
        except Exception:
            x = (self.winfo_screenwidth() - self.ANCHO) // 2
            y = (self.winfo_screenheight() - self.ALTO) // 2
        self.geometry(f"{self.ANCHO}x{self.ALTO}+{max(0, x)}+{max(0, y)}")

    def _botones(self, texto_ok: str, comando_ok: Any, color_ok: str = COLOR_ACENTO) -> ctk.CTkFrame:
        fila = ctk.CTkFrame(self, fg_color="transparent")
        fila.pack(pady=(4, 16))
        ctk.CTkButton(fila, text="Cancelar (Esc)", width=150, height=38, fg_color=COLOR_BOTON_NEUTRO, hover_color=COLOR_BOTON_NEUTRO_HOVER, command=self._cancelar).pack(side="left", padx=6)
        self._boton_cobrar = ctk.CTkButton(fila, text=texto_ok, width=170, height=38, fg_color=color_ok, hover_color=COLOR_ACENTO_HOVER if color_ok == COLOR_ACENTO else COLOR_BOTON_OK_HOVER, command=comando_ok)
        self._boton_cobrar.pack(side="left", padx=6)
        return fila


class DialogoCantidad(_DialogoModal):
    ANCHO = 420
    ALTO = 250
    TITULO = "Cantidad / Peso"

    def __init__(self, master: Any, nombre_producto: str, unidad_medida: str = "UND") -> None:
        self._nombre = nombre_producto
        self._unidad = (unidad_medida or "UND").upper()
        super().__init__(master, titulo=f"Peso / Cantidad — {self._nombre}")

    def _construir(self) -> None:
        ctk.CTkLabel(self, text=self._nombre, text_color=COLOR_TEXTO, font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold")).pack(pady=(22, 2), padx=24)
        etiqueta = f"Peso ({self._unidad.lower()}):" if self._unidad != "UND" else "Cantidad:"
        ctk.CTkLabel(self, text=etiqueta, text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)).pack()
        self._var_cantidad = tk.StringVar(value="1")
        self._entrada = ctk.CTkEntry(self, textvariable=self._var_cantidad, height=48, justify="center", border_color=COLOR_ACENTO, border_width=2, font=ctk.CTkFont(family="Consolas", size=22))
        self._entrada.pack(fill="x", padx=28, pady=8)
        self._entrada.bind("<Return>", lambda _e: self._aceptar())
        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack()
        self._botones("Agregar (Enter)", self._aceptar)

    def _al_mostrar(self) -> None:
        self._entrada.focus_set()
        self._entrada.select_range(0, "end")

    def _aceptar(self) -> None:
        texto = self._var_cantidad.get().strip().replace(",", ".")
        try:
            cantidad = float(texto)
        except ValueError:
            self._lbl_error.configure(text="Ingresa un número válido.")
            return
        if cantidad <= 0:
            self._lbl_error.configure(text="La cantidad debe ser mayor que cero.")
            return
        self.resultado = round(cantidad, 3)
        self.destroy()


class DialogoEditarCantidad(_DialogoModal):
    ANCHO = 420
    ALTO = 250
    TITULO = "Editar cantidad"

    def __init__(self, master: Any, nombre_producto: str, cantidad_actual: float, unidad_medida: str = "UND") -> None:
        self._nombre = nombre_producto
        self._cantidad_actual = float(cantidad_actual)
        self._unidad = (unidad_medida or "UND").upper()
        super().__init__(master, titulo=f"Editar cantidad — {self._nombre}")

    def _construir(self) -> None:
        ctk.CTkLabel(self, text=self._nombre, text_color=COLOR_TEXTO, font=ctk.CTkFont(family="Segoe UI", size=17, weight="bold")).pack(pady=(22, 2), padx=24)
        etiqueta = f"Cantidad ({self._unidad.lower()}):" if self._unidad != "UND" else "Cantidad:"
        ctk.CTkLabel(self, text=etiqueta, text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)).pack()
        valor_inicial = f"{self._cantidad_actual:.3f}".rstrip("0").rstrip(".") or "0"
        self._var_cantidad = tk.StringVar(value=valor_inicial)
        self._entrada = ctk.CTkEntry(self, textvariable=self._var_cantidad, height=48, justify="center", border_color=COLOR_ACENTO, border_width=2, font=ctk.CTkFont(family="Consolas", size=22))
        self._entrada.pack(fill="x", padx=28, pady=8)
        self._entrada.bind("<Return>", lambda _e: self._aceptar())
        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack()
        self._botones("Guardar (Enter)", self._aceptar)

    def _al_mostrar(self) -> None:
        self._entrada.focus_set()
        self._entrada.select_range(0, "end")

    def _aceptar(self) -> None:
        texto = self._var_cantidad.get().strip().replace(",", ".")
        try:
            cantidad = float(texto)
        except ValueError:
            self._lbl_error.configure(text="Ingresa un número válido.")
            return
        if cantidad <= 0:
            self._lbl_error.configure(text="La cantidad debe ser mayor que cero.")
            return
        self.resultado = round(cantidad, 3)
        self.destroy()


class DialogoPago(_DialogoModal):
    """Diálogo de cobro optimizado para rapidez en panadería.
    Soporta pagos divididos y auto-cobro rápido con Enter.
    """
    ANCHO = 520
    ALTO = 520  # Reducido drásticamente de 700
    TITULO = "Cobrar"

    def __init__(
        self,
        master: Any,
        total: float,
        metodos_pago: Sequence[str],
        total_items: int = 0,
    ) -> None:
        self._total = round(float(total), 2)
        self._total_a_pagar = redondear_total(self._total)
        self._redondeo = round(self._total_a_pagar - self._total, 2)
        self._metodos = list(metodos_pago) or ["Efectivo"]
        self._total_items = total_items
        self._pagos: list[dict[str, Any]] = []
        super().__init__(master, titulo="Cobrar")

    def _construir(self) -> None:
        # --- Cabecera: total ---
        ctk.CTkLabel(self, text="TOTAL A PAGAR", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12, "bold")).pack(pady=(14, 0))
        self._lbl_total = ctk.CTkLabel(self, text=f"S/ {self._total_a_pagar:,.2f}", text_color=COLOR_ACENTO, font=ctk.CTkFont(family="Segoe UI", size=36, weight="bold"))
        self._lbl_total.pack()

        if self._redondeo != 0:
            signo = "+" if self._redondeo > 0 else "−"
            ctk.CTkLabel(self, text=f"Redondeo: {signo}S/ {abs(self._redondeo):.2f}  ·  Exacto: S/ {self._total:,.2f}", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11)).pack()

        ctk.CTkLabel(self, text=f"{self._total_items} producto(s)", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11)).pack(pady=(0, 8))

        # --- Lista de pagos (Súper compacta) ---
        ctk.CTkLabel(self, text="Pagos", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=28)
        # Reducido a 80px para mostrar solo 2-3 filas
        self._frame_pagos = ctk.CTkScrollableFrame(self, height=80, fg_color="#f7f5f1", corner_radius=8)
        self._frame_pagos.pack(fill="x", padx=20, pady=(2, 6))
        self._frame_pagos.grid_columnconfigure(0, weight=3)
        self._frame_pagos.grid_columnconfigure(1, weight=1)
        self._frame_pagos.grid_columnconfigure(2, weight=1)
        self._frame_pagos.grid_columnconfigure(3, weight=1)

        for col, (txt, _) in enumerate([("Método", 180), ("Monto", 100), ("Vuelto", 90), ("", 40)]):
            ctk.CTkLabel(self._frame_pagos, text=txt, font=("Segoe UI", 11, "bold"), text_color=COLOR_SECUNDARIO).grid(row=0, column=col, padx=6, pady=4, sticky="w")

        # --- Agregar nuevo pago ---
        ctk.CTkLabel(self, text="Agregar pago", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=28, pady=(8, 2))
        fila_add = ctk.CTkFrame(self, fg_color="transparent")
        fila_add.pack(fill="x", padx=20, pady=(0, 4))
        fila_add.grid_columnconfigure(0, weight=2)
        fila_add.grid_columnconfigure(1, weight=1)
        fila_add.grid_columnconfigure(2, weight=1)

        self._var_nuevo_metodo = tk.StringVar(value=self._metodos[0])
        self._menu_metodo = ctk.CTkOptionMenu(fila_add, values=self._metodos, variable=self._var_nuevo_metodo, command=self._al_cambiar_nuevo_metodo, width=180, height=34, fg_color=COLOR_ACENTO, button_color=COLOR_ACENTO_HOVER, button_hover_color=COLOR_ACENTO_HOVER)
        self._menu_metodo.grid(row=0, column=0, padx=4, sticky="ew")

        self._var_nuevo_monto = tk.StringVar(value="")
        self._entrada_monto = ctk.CTkEntry(fila_add, textvariable=self._var_nuevo_monto, height=34, justify="center", placeholder_text="Monto", border_color=COLOR_ACENTO, border_width=2, font=("Consolas", 14))
        self._entrada_monto.grid(row=0, column=1, padx=4, sticky="ew")
        # IMPORTANTE: Ahora el Enter de la entrada llama a _aceptar (Cobrar rápido)
        self._entrada_monto.bind("<Return>", lambda _e: self._aceptar())

        self._btn_agregar = ctk.CTkButton(fila_add, text="＋ Agregar", width=100, height=34, fg_color=COLOR_OK, hover_color="#047857", command=self._agregar_pago)
        self._btn_agregar.grid(row=0, column=2, padx=4)

        # Botones de billetes rápidos
        self._frame_billetes = ctk.CTkFrame(self, fg_color="transparent")
        self._frame_billetes.pack(fill="x", padx=20, pady=(0, 6))
        for denom in (10, 20, 50, 100, 200):
            ctk.CTkButton(self._frame_billetes, text=f"S/ {denom}", height=30, width=60, command=lambda v=denom: self._usar_billete_nuevo(v)).pack(side="left", padx=2, expand=True, fill="x")
        ctk.CTkButton(self._frame_billetes, text="Exacto", height=30, fg_color=COLOR_OK, hover_color="#047857", command=self._usar_exacto_nuevo).pack(side="left", padx=2, expand=True, fill="x")

        # --- Resumen Destacado (Vuelto / Falta) ---
        self._lbl_resumen = ctk.CTkLabel(
            self,
            text=self._texto_resumen(),
            text_color=COLOR_SECUNDARIO,
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold") # Fuente grande y destacada
        )
        self._lbl_resumen.pack(pady=(10, 10))

        # --- Datos boleta (Compactos en una fila) ---
        ctk.CTkLabel(self, text="Datos boleta (opcional):", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11)).pack(anchor="w", padx=28)
        fila_cli = ctk.CTkFrame(self, fg_color="transparent")
        fila_cli.pack(fill="x", padx=28, pady=(2, 10))
        fila_cli.grid_columnconfigure(0, weight=3)
        fila_cli.grid_columnconfigure(1, weight=2)
        self._var_cliente = tk.StringVar(value="")
        self._var_dni = tk.StringVar(value="")
        ctk.CTkEntry(fila_cli, textvariable=self._var_cliente, height=30, placeholder_text="Cliente", font=("Segoe UI", 12)).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkEntry(fila_cli, textvariable=self._var_dni, height=30, placeholder_text="DNI/RUC", font=("Segoe UI", 12)).grid(row=0, column=1, sticky="ew")

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack()
        self._botones("Cobrar (Enter)", self._aceptar, color_ok=COLOR_OK)
        self._actualizar_ui()

    def _al_cambiar_nuevo_metodo(self, metodo: str) -> None:
        self._var_nuevo_metodo.set(metodo)
        if metodo == "Efectivo":
            self._entrada_monto.configure(state="normal", placeholder_text="Monto recibido")
            self._frame_billetes.pack(fill="x", padx=20, pady=(0, 6), before=self._lbl_resumen)
            self._var_nuevo_monto.set("")
        else:
            faltante = max(0.0, self._total_a_pagar - self._suma_pagos())
            self._var_nuevo_monto.set(f"{faltante:.2f}")
            self._entrada_monto.configure(state="disabled")
            self._frame_billetes.pack_forget()
        self._actualizar_resumen()

    def _usar_billete_nuevo(self, denominacion: float) -> None:
        if self._var_nuevo_metodo.get() != "Efectivo": return
        self._var_nuevo_monto.set(f"{float(denominacion):.2f}")
        self._actualizar_resumen()

    def _usar_exacto_nuevo(self) -> None:
        if self._var_nuevo_metodo.get() != "Efectivo": return
        faltante = max(0.0, self._total_a_pagar - self._suma_pagos())
        self._var_nuevo_monto.set(f"{faltante:.2f}")
        self._actualizar_resumen()

    def _suma_pagos(self) -> float:
        return sum(float(p["monto"]) for p in self._pagos)

    def _texto_resumen(self) -> str:
        pagado = self._suma_pagos()
        faltante = max(0.0, self._total_a_pagar - pagado)
        if faltante > 0.001:
            return f"Falta: S/ {faltante:,.2f}"
        vuelto_total = round(pagado - self._total_a_pagar, 2)
        return f"Vuelto: S/ {vuelto_total:,.2f}"

    def _actualizar_resumen(self) -> None:
        """Muestra VUELTO (verde) o FALTA (rojo) de forma destacada.

        Incluye en vista previa el monto del Entry para retroalimentación
        en vivo mientras el cajero escribe.
        """
        pagado = self._suma_pagos()
        # Vista previa: sumar el monto del Entry si aún falta por cubrir.
        try:
            monto_entry = float(self._var_nuevo_monto.get().strip().replace(",", "."))
        except ValueError:
            monto_entry = 0.0
        if monto_entry > 0 and pagado + 0.001 < self._total_a_pagar:
            pagado_eff = pagado + monto_entry
        else:
            pagado_eff = pagado
        diferencia = round(pagado_eff - self._total_a_pagar, 2)
        if diferencia < -0.001:
            self._lbl_resumen.configure(text=f"Falta: S/ {abs(diferencia):,.2f}", text_color=COLOR_ERROR)
        else:
            self._lbl_resumen.configure(text=f"Vuelto: S/ {diferencia:,.2f}", text_color=COLOR_OK)

    def _actualizar_ui(self) -> None:
        for w in self._frame_pagos.winfo_children():
            if int(w.grid_info().get("row", 0)) > 0:
                w.destroy()
        for idx, p in enumerate(self._pagos, start=1):
            ctk.CTkLabel(self._frame_pagos, text=p["metodo"], font=("Segoe UI", 11), text_color=COLOR_TEXTO).grid(row=idx, column=0, padx=6, pady=2, sticky="w")
            ctk.CTkLabel(self._frame_pagos, text=f"S/ {p['monto']:,.2f}", font=("Segoe UI", 11, "bold"), text_color=COLOR_TEXTO).grid(row=idx, column=1, padx=6, pady=2, sticky="e")
            vuelto_txt = f"S/ {p.get('vuelto', 0):,.2f}" if p.get("vuelto", 0) > 0 else "—"
            ctk.CTkLabel(self._frame_pagos, text=vuelto_txt, font=("Segoe UI", 11), text_color=COLOR_SECUNDARIO).grid(row=idx, column=2, padx=6, pady=2, sticky="e")
            btn_del = ctk.CTkButton(self._frame_pagos, text="✕", width=28, height=24, fg_color="#ef4444", hover_color="#b91c1c", font=("Segoe UI", 10), command=lambda i=idx-1: self._eliminar_pago(i))
            btn_del.grid(row=idx, column=3, padx=6, pady=2)
        self._actualizar_resumen()

    def _agregar_pago(self) -> bool:
        """Agrega un pago. Retorna True si se agregó correctamente."""
        metodo = self._var_nuevo_metodo.get()
        try:
            monto = float(self._var_nuevo_monto.get().strip().replace(",", "."))
        except ValueError:
            self._lbl_error.configure(text="Ingresa un monto válido.")
            return False
        if monto <= 0:
            self._lbl_error.configure(text="El monto debe ser mayor a cero.")
            return False

        faltante_antes = max(0.0, self._total_a_pagar - self._suma_pagos())
        vuelto = 0.0
        if metodo == "Efectivo" and monto > faltante_antes + 0.001:
            vuelto = round(monto - faltante_antes, 2)
        # Guardamos el monto COMPLETO (lo que realmente entregó el cliente);
        # el servicio calcula el vuelto total con la suma de montos.

        self._pagos.append({"metodo_pago": metodo, "monto": round(monto, 2), "vuelto": vuelto})
        self._lbl_error.configure(text="")
        self._var_nuevo_monto.set("")
        self._actualizar_ui()
        return True

    def _eliminar_pago(self, index: int) -> None:
        if 0 <= index < len(self._pagos):
            self._pagos.pop(index)
            self._actualizar_ui()

    def _al_mostrar(self) -> None:
        self._entrada_monto.focus_set()

    def _aceptar(self) -> None:
        """Lógica de cobro rápido: Auto-Agrega monto pendiente y cierra si es suficiente."""
        # 1. Auto-Agregar: si aún falta por cubrir y hay un monto
        #    válido en el Entry, agrégalo (no hace falta «＋ Agregar»).
        if self._suma_pagos() + 0.001 < self._total_a_pagar:
            try:
                monto_pendiente = float(self._var_nuevo_monto.get().strip().replace(",", "."))
            except ValueError:
                monto_pendiente = 0.0
            if monto_pendiente > 0:
                self._agregar_pago()

        # 2. Validaciones finales
        if not self._pagos:
            self._lbl_error.configure(text="Agrega al menos un pago.")
            return

        if self._suma_pagos() + 0.001 < self._total_a_pagar:
            self._lbl_error.configure(text=f"Falta S/ {self._total_a_pagar - self._suma_pagos():,.2f}")
            return

        # 3. Éxito: retornar resultado
        self.resultado = ResultadoPagoDividido(
            pagos=[{"metodo_pago": p["metodo_pago"], "monto": p["monto"], "vuelto": p.get("vuelto", 0)} for p in self._pagos],
            total_a_pagar=self._total_a_pagar,
            redondeo=self._redondeo,
            cliente=self._var_cliente.get().strip(),
            dni_cliente=self._var_dni.get().strip(),
        )
        self.destroy()


class DialogoProducto(_DialogoModal):
    """Formulario modal para **crear** o **editar** un producto del catálogo.

    Devuelve (vía :meth:`mostrar`) un diccionario con los datos crudos del
    formulario; la validación de negocio la realiza ``ProductoService``. Esto
    permite que, ante un error (p. ej. código duplicado), el usuario vuelva al
    formulario con los datos ya cargados.

    Accesibilidad:
        * ``Tab`` recorre los campos en orden lógico.
        * ``Enter`` guarda y ``Esc`` cancela.
        * El foco inicial queda en el primer campo.
    """

    ANCHO = 640
    ALTO = 700
    TITULO = "Producto"

    #: Texto del ítem por defecto del selector de categoría (obliga a elegir).
    PLACEHOLDER_CATEGORIA = "— Selecciona una categoría —"

    #: Texto del ítem por defecto del selector de impuesto (obliga a elegir).
    PLACEHOLDER_IMPUESTO = "— Selecciona un impuesto —"

    def __init__(
        self,
        master: Any,
        producto: dict[str, Any] | None = None,
        es_edicion: bool | None = None,
        categorias: list[dict[str, Any]] | None = None,
        impuestos: list[dict[str, Any]] | None = None,
    ) -> None:
        self._producto = dict(producto) if producto else {}
        # La categoría es obligatoria: el formulario ofrece el catálogo vigente.
        self._categorias = list(categorias or [])
        self._cat_por_nombre = {
            str(c["nombre"]): int(c["id"]) for c in self._categorias if c.get("id") is not None
        }
        self._nombre_por_cat = {
            int(c["id"]): str(c["nombre"]) for c in self._categorias if c.get("id") is not None
        }
        # Impuestos disponibles (IGV 18 %, Exonerado, Inafecto…).
        self._impuestos = list(impuestos or [])
        self._imp_por_nombre = {
            str(i["nombre"]): int(i["id"]) for i in self._impuestos if i.get("id") is not None
        }
        self._nombre_por_imp = {
            int(i["id"]): str(i["nombre"]) for i in self._impuestos if i.get("id") is not None
        }
        # ``es_edicion`` explícito permite reabrir el formulario (tras un error)
        # conservando el modo original, aunque se pasen datos precargados.
        self._es_edicion = bool(producto) if es_edicion is None else bool(es_edicion)
        super().__init__(
            master,
            titulo="Editar producto" if self._es_edicion else "Nuevo producto",
        )

    def _construir(self) -> None:
        datos = self._producto
        ctk.CTkLabel(
            self,
            text="Los campos marcados con * son obligatorios.",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=24, pady=(12, 0))

        marco = ctk.CTkScrollableFrame(
            self,
            width=self.ANCHO - 44,
            height=self.ALTO - 200,
            fg_color="transparent",
        )
        marco.pack(fill="both", expand=True, padx=16, pady=(4, 0))
        marco.grid_columnconfigure(0, weight=1)
        marco.grid_columnconfigure(1, weight=1)

        self._vars: dict[str, tk.StringVar] = {}

        self._vars["nombre"] = self._campo(
            marco, "Nombre *", datos.get("nombre", ""), 0, 0, colspan=2, foco=True
        )
        self._vars["codigo_barras"] = self._campo(
            marco, "Código de barras (escaneable)", datos.get("codigo_barras") or "", 1, 0
        )
        self._vars["codigo_interno"] = self._campo(
            marco, "Código interno *", datos.get("codigo_interno") or "", 1, 1
        )
        self._vars["precio_venta"] = self._campo(
            marco, "Precio de venta (S/) *", self._num(datos.get("precio_venta", 0)), 2, 0
        )
        self._vars["costo"] = self._campo(
            marco, "Costo (S/)", self._num(datos.get("costo"), vacio_si_none=True), 2, 1
        )
        self._vars["stock_actual"] = self._campo(
            marco, "Stock actual", self._num(datos.get("stock_actual", 0)), 3, 0
        )
        self._vars["stock_minimo"] = self._campo(
            marco, "Stock mínimo (alerta)", self._num(datos.get("stock_minimo", 0)), 3, 1
        )
        self._vars["descripcion"] = self._campo(
            marco, "Descripción", datos.get("descripcion") or "", 4, 0, colspan=2
        )

        # Etiqueta de la unidad + su código del Catálogo N.º 03 de SUNAT. El
        # comprobante electrónico no lleva "kg" ni "docena": lleva "KGM"/"DZN".
        # Se muestran apiladas en la misma celda para no ensanchar el formulario.
        self._var_unidad = tk.StringVar(value=(datos.get("unidad_medida") or "UND").upper())
        marco_unidad = ctk.CTkFrame(marco, fg_color="transparent")
        marco_unidad.grid(row=5, column=0, sticky="w", padx=(12, 8), pady=(6, 0))
        ctk.CTkLabel(
            marco_unidad,
            text="Unidad de medida",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).pack(anchor="w")
        self._lbl_codigo_unidad = ctk.CTkLabel(
            marco_unidad,
            text=self._texto_codigo_unidad(),
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 10),
        )
        self._lbl_codigo_unidad.pack(anchor="w")

        ctk.CTkOptionMenu(
            marco,
            values=list(UNIDADES_MEDIDA),
            variable=self._var_unidad,
            height=36,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
            command=self._actualizar_codigo_unidad,
        ).grid(row=5, column=1, sticky="ew", padx=(8, 12), pady=(6, 0))

        self._var_pesable = self._switch(marco, "Producto pesable (kg/gr)", datos.get("es_pesable", 0), 6, 0)
        self._var_controla = self._switch(marco, "Controla stock", datos.get("controla_stock", 1), 6, 1)
        self._var_activo = self._switch(marco, "Activo (visible en caja)", datos.get("activo", 1), 7, 0)

        # Categoría (obligatoria): clasifica la venta por tipo de negocio.
        ctk.CTkLabel(
            marco,
            text="Categoría *",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).grid(row=8, column=0, sticky="w", padx=(12, 8), pady=(10, 0))
        nombres = [str(c["nombre"]) for c in self._categorias]
        valor_inicial = self._nombre_por_cat.get(
            int(datos["categoria_id"]) if datos.get("categoria_id") not in (None, "") else -1,
            self.PLACEHOLDER_CATEGORIA,
        )
        self._var_categoria = tk.StringVar(value=valor_inicial)
        ctk.CTkOptionMenu(
            marco,
            values=[self.PLACEHOLDER_CATEGORIA, *nombres],
            variable=self._var_categoria,
            height=36,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
        ).grid(row=8, column=1, sticky="ew", padx=(8, 12), pady=(10, 0))

        # Impuesto aplicable (IGV 18 %, Exonerado, Inafecto…). El precio de venta
        # ya lo incluye: el sistema solo lo usa para desglosar la base y el IGV.
        ctk.CTkLabel(
            marco,
            text="Impuesto",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).grid(row=9, column=0, sticky="w", padx=(12, 8), pady=(10, 0))
        nombres_impuesto = [str(i["nombre"]) for i in self._impuestos]
        impuesto_actual = datos.get("impuesto_id")
        valor_impuesto = self._nombre_por_imp.get(
            int(impuesto_actual) if impuesto_actual not in (None, "") else -1,
            nombres_impuesto[0] if nombres_impuesto else self.PLACEHOLDER_IMPUESTO,
        )
        self._var_impuesto = tk.StringVar(value=valor_impuesto)
        ctk.CTkOptionMenu(
            marco,
            values=nombres_impuesto or [self.PLACEHOLDER_IMPUESTO],
            variable=self._var_impuesto,
            height=36,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
        ).grid(row=9, column=1, sticky="ew", padx=(8, 12), pady=(10, 0))
        ctk.CTkLabel(
            marco,
            text="El precio de venta ya incluye este impuesto.",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 10, "italic"),
        ).grid(row=10, column=0, columnspan=2, sticky="w", padx=(14, 8), pady=(0, 4))

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack(pady=(2, 0))
        texto_ok = "Guardar cambios (Enter)" if self._es_edicion else "Crear producto (Enter)"
        self._botones(texto_ok, self._aceptar)

    def _campo(
        self,
        marco: Any,
        etiqueta: str,
        valor: Any,
        fila: int,
        columna: int,
        colspan: int = 1,
        foco: bool = False,
    ) -> tk.StringVar:
        """Crea una fila etiqueta + entrada y devuelve su ``StringVar``."""
        contenedor = ctk.CTkFrame(marco, fg_color="transparent")
        contenedor.grid(row=fila, column=columna, columnspan=colspan, sticky="ew", padx=8, pady=6)
        contenedor.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            contenedor, text=etiqueta, text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        ).grid(row=0, column=0, sticky="w")
        var = tk.StringVar(value=str(valor))
        entrada = ctk.CTkEntry(contenedor, textvariable=var, height=36)
        entrada.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        entrada.bind("<Return>", lambda _e: self._aceptar())
        if foco:
            self._primer_campo = entrada
        return var

    @staticmethod
    def _switch(marco: Any, etiqueta: str, valor: Any, fila: int, columna: int) -> tk.IntVar:
        """Crea un interruptor (0/1) y devuelve su ``IntVar``."""
        var = tk.IntVar(value=1 if valor else 0)
        ctk.CTkSwitch(
            marco,
            text=etiqueta,
            variable=var,
            onvalue=1,
            offvalue=0,
            text_color=COLOR_TEXTO,
            font=("Segoe UI", 12),
        ).grid(row=fila, column=columna, sticky="w", padx=14, pady=10)
        return var

    @staticmethod
    def _num(valor: Any, vacio_si_none: bool = False) -> str:
        """Formatea un número para el formulario (sin ceros sobrantes)."""
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            return "" if vacio_si_none else "0"
        try:
            return f"{float(valor):g}"
        except (TypeError, ValueError):
            return str(valor)

    def _al_mostrar(self) -> None:
        primer = getattr(self, "_primer_campo", None)
        if primer is not None:
            primer.focus_set()
            primer.select_range(0, "end")

    # ---------------------------------------------------------- Catálogo 03
    def _texto_codigo_unidad(self) -> str:
        """Texto del código SUNAT (Catálogo N.º 03) de la unidad elegida."""
        return f"Catálogo SUNAT: {codigo_unidad_sunat(self._var_unidad.get())}"

    def _actualizar_codigo_unidad(self, _valor: Any = None) -> None:
        """Refresca el código SUNAT al cambiar la unidad de medida."""
        self._lbl_codigo_unidad.configure(text=self._texto_codigo_unidad())

    def _aceptar(self) -> None:
        if not self._vars["nombre"].get().strip():
            self._lbl_error.configure(text="El nombre es obligatorio.")
            return
        if not self._vars["codigo_interno"].get().strip():
            self._lbl_error.configure(text="El código interno es obligatorio.")
            return
        nombre_categoria = self._var_categoria.get()
        if nombre_categoria == self.PLACEHOLDER_CATEGORIA or not nombre_categoria:
            self._lbl_error.configure(text="La categoría es obligatoria.")
            return
        categoria_id = self._cat_por_nombre.get(nombre_categoria)
        if categoria_id is None:
            self._lbl_error.configure(text="Selecciona una categoría válida.")
            return
        # El impuesto es opcional: sin él, la venta asume el IGV 18 %.
        impuesto_id = self._imp_por_nombre.get(self._var_impuesto.get())
        # Datos crudos: los normaliza y valida ``ProductoService``.
        self.resultado = {
            "nombre": self._vars["nombre"].get(),
            "codigo_barras": self._vars["codigo_barras"].get(),
            "codigo_interno": self._vars["codigo_interno"].get(),
            "descripcion": self._vars["descripcion"].get(),
            "precio_venta": self._vars["precio_venta"].get(),
            "costo": self._vars["costo"].get(),
            "categoria_id": categoria_id,
            "impuesto_id": impuesto_id,
            "unidad_medida": self._var_unidad.get(),
            "es_pesable": bool(self._var_pesable.get()),
            "controla_stock": bool(self._var_controla.get()),
            "stock_actual": self._vars["stock_actual"].get(),
            "stock_minimo": self._vars["stock_minimo"].get(),
            "activo": bool(self._var_activo.get()),
        }
        self.destroy()


class DialogoCategoria(_DialogoModal):
    """Formulario modal para **crear** o **editar** una categoría del catálogo.

    Devuelve (vía :meth:`mostrar`) un diccionario con los datos crudos; la
    validación de negocio la realiza ``CategoriaService``.

    Accesibilidad:
        * ``Tab`` recorre los campos en orden lógico.
        * ``Enter`` guarda y ``Esc`` cancela.
    """

    ANCHO = 480
    ALTO = 320
    TITULO = "Categoría"

    def __init__(
        self,
        master: Any,
        categoria: dict[str, Any] | None = None,
        es_edicion: bool | None = None,
    ) -> None:
        self._categoria = dict(categoria) if categoria else {}
        self._es_edicion = bool(categoria) if es_edicion is None else bool(es_edicion)
        super().__init__(
            master,
            titulo="Editar categoría" if self._es_edicion else "Nueva categoría",
        )

    def _construir(self) -> None:
        datos = self._categoria
        ctk.CTkLabel(
            self,
            text="Los campos marcados con * son obligatorios.",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=24, pady=(12, 0))

        marco = ctk.CTkFrame(self, fg_color="transparent")
        marco.pack(fill="x", padx=24, pady=(8, 0))
        marco.grid_columnconfigure(0, weight=1)
        marco.grid_columnconfigure(1, weight=1)

        self._vars: dict[str, tk.StringVar] = {}
        self._vars["nombre"] = self._campo(
            marco, "Nombre *", datos.get("nombre", ""), 0, 0, colspan=2, foco=True
        )
        self._vars["orden"] = self._campo(
            marco, "Orden en la lista", self._num(datos.get("orden", 0)), 1, 0
        )

        self._var_activo = tk.IntVar(value=1 if datos.get("activo", 1) else 0)
        ctk.CTkSwitch(
            marco,
            text="Activa",
            variable=self._var_activo,
            onvalue=1,
            offvalue=0,
            text_color=COLOR_TEXTO,
            font=("Segoe UI", 12),
        ).grid(row=1, column=1, sticky="w", padx=14, pady=(26, 6))

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack(pady=(8, 0))
        texto_ok = "Guardar cambios (Enter)" if self._es_edicion else "Crear categoría (Enter)"
        self._botones(texto_ok, self._aceptar)

    def _campo(
        self,
        marco: Any,
        etiqueta: str,
        valor: Any,
        fila: int,
        columna: int,
        colspan: int = 1,
        foco: bool = False,
    ) -> tk.StringVar:
        """Crea una fila etiqueta + entrada y devuelve su ``StringVar``."""
        contenedor = ctk.CTkFrame(marco, fg_color="transparent")
        contenedor.grid(row=fila, column=columna, columnspan=colspan, sticky="ew", padx=8, pady=6)
        contenedor.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            contenedor, text=etiqueta, text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        ).grid(row=0, column=0, sticky="w")
        var = tk.StringVar(value=str(valor))
        entrada = ctk.CTkEntry(contenedor, textvariable=var, height=36)
        entrada.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        entrada.bind("<Return>", lambda _e: self._aceptar())
        if foco:
            self._primer_campo = entrada
        return var

    @staticmethod
    def _num(valor: Any) -> str:
        """Formatea un número para el formulario (sin ceros sobrantes)."""
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            return "0"
        try:
            return f"{float(valor):g}"
        except (TypeError, ValueError):
            return str(valor)

    def _al_mostrar(self) -> None:
        primer = getattr(self, "_primer_campo", None)
        if primer is not None:
            primer.focus_set()
            primer.select_range(0, "end")

    def _aceptar(self) -> None:
        if not self._vars["nombre"].get().strip():
            self._lbl_error.configure(text="El nombre es obligatorio.")
            return
        # Datos crudos: los normaliza y valida ``CategoriaService``.
        self.resultado = {
            "nombre": self._vars["nombre"].get(),
            "orden": self._vars["orden"].get(),
            "activo": bool(self._var_activo.get()),
        }
        self.destroy()


class DialogoProveedor(_DialogoModal):
    """Formulario modal para **crear** o **editar** un proveedor.

    Incluye un selector de **días de visita** (lunes a domingo) que alimenta el
    calendario de la vista de proveedores. Devuelve (vía :meth:`mostrar`) un
    diccionario con los datos crudos; la validación la hace ``ProveedorService``.

    Accesibilidad:
        * ``Tab`` recorre los campos en orden lógico.
        * ``Enter`` guarda y ``Esc`` cancela.
        * Botones "Todos"/"Ninguno" para marcar los 7 días de una sola vez.
    """

    ANCHO = 620
    ALTO = 600
    TITULO = "Proveedor"

    def __init__(
        self,
        master: Any,
        proveedor: dict[str, Any] | None = None,
        es_edicion: bool | None = None,
    ) -> None:
        self._proveedor = dict(proveedor) if proveedor else {}
        self._es_edicion = bool(proveedor) if es_edicion is None else bool(es_edicion)
        super().__init__(
            master,
            titulo="Editar proveedor" if self._es_edicion else "Nuevo proveedor",
        )

    def _construir(self) -> None:
        datos = self._proveedor
        ctk.CTkLabel(
            self,
            text="Los campos marcados con * son obligatorios.",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=24, pady=(12, 0))

        marco = ctk.CTkScrollableFrame(self, width=self.ANCHO - 44, height=self.ALTO - 190, fg_color="transparent")
        marco.pack(fill="both", expand=True, padx=16, pady=(4, 0))

        self._vars: dict[str, tk.StringVar] = {}
        self._vars["nombre"] = self._entrada(marco, "Nombre / Empresa *", datos.get("nombre", ""), foco=True)

        fila = ctk.CTkFrame(marco, fg_color="transparent")
        fila.pack(fill="x", pady=4)
        self._vars["contacto"] = self._entrada(fila, "Persona de contacto", datos.get("contacto") or "", lado="left")
        self._vars["telefono"] = self._entrada(fila, "Teléfono", datos.get("telefono") or "", lado="left")

        self._vars["email"] = self._entrada(marco, "Correo electrónico", datos.get("email") or "")
        self._vars["notas"] = self._entrada(marco, "Notas / productos que trae", datos.get("notas") or "")

        self._var_activo = tk.IntVar(value=1 if datos.get("activo", 1) else 0)
        ctk.CTkSwitch(
            marco,
            text="Proveedor activo",
            variable=self._var_activo,
            onvalue=1,
            offvalue=0,
            text_color=COLOR_TEXTO,
            font=("Segoe UI", 12),
        ).pack(anchor="w", padx=6, pady=(10, 2))

        # --- Calendario de visitas (lunes a domingo) ---
        ctk.CTkLabel(
            marco,
            text="Días que visita la panadería",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=6, pady=(8, 0))

        contenedor_dias = ctk.CTkFrame(marco, fg_color="transparent")
        contenedor_dias.pack(fill="x", padx=2, pady=(2, 4))
        for columna in range(7):
            contenedor_dias.grid_columnconfigure(columna, weight=1)

        self._vars_dia: dict[int, tk.IntVar] = {}
        marcados = {int(d) for d in (datos.get("dias") or [])}
        for dia, corto in enumerate(DIAS_SEMANA_CORTOS, start=1):
            var = tk.IntVar(value=1 if dia in marcados else 0)
            ctk.CTkCheckBox(
                contenedor_dias,
                text=corto,
                variable=var,
                onvalue=1,
                offvalue=0,
                width=70,
                checkbox_width=20,
                checkbox_height=20,
                font=("Segoe UI", 12),
            ).grid(row=0, column=dia - 1, padx=2, pady=2, sticky="w")
            self._vars_dia[dia] = var

        acciones_dias = ctk.CTkFrame(marco, fg_color="transparent")
        acciones_dias.pack(fill="x", padx=2, pady=(0, 6))
        ctk.CTkButton(
            acciones_dias, text="Marcar todos", width=120, height=28,
            fg_color=COLOR_BOTON_NEUTRO, hover_color=COLOR_BOTON_NEUTRO_HOVER,
            command=lambda: self._marcar_dias(True),
        ).pack(side="left", padx=(4, 6))
        ctk.CTkButton(
            acciones_dias, text="Quitar todos", width=120, height=28,
            fg_color=COLOR_BOTON_NEUTRO, hover_color=COLOR_BOTON_NEUTRO_HOVER,
            command=lambda: self._marcar_dias(False),
        ).pack(side="left")

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack(pady=(2, 0))
        texto_ok = "Guardar cambios (Enter)" if self._es_edicion else "Crear proveedor (Enter)"
        self._botones(texto_ok, self._aceptar)

    def _entrada(
        self,
        padre: Any,
        etiqueta: str,
        valor: Any,
        lado: str | None = None,
        foco: bool = False,
    ) -> tk.StringVar:
        """Crea un bloque etiqueta + entrada y devuelve su ``StringVar``."""
        contenedor = ctk.CTkFrame(padre, fg_color="transparent")
        if lado == "left":
            contenedor.pack(side="left", fill="x", expand=True, padx=4)
        else:
            contenedor.pack(fill="x", pady=4)
        ctk.CTkLabel(
            contenedor, text=etiqueta, text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        ).pack(anchor="w")
        var = tk.StringVar(value=str(valor))
        entrada = ctk.CTkEntry(contenedor, textvariable=var, height=36)
        entrada.pack(fill="x", pady=(2, 0))
        entrada.bind("<Return>", lambda _e: self._aceptar())
        if foco:
            self._primer_campo = entrada
        return var

    def _marcar_dias(self, marcar: bool) -> None:
        valor = 1 if marcar else 0
        for var in self._vars_dia.values():
            var.set(valor)

    def _al_mostrar(self) -> None:
        primer = getattr(self, "_primer_campo", None)
        if primer is not None:
            primer.focus_set()
            primer.select_range(0, "end")

    def _aceptar(self) -> None:
        if not self._vars["nombre"].get().strip():
            self._lbl_error.configure(text="El nombre del proveedor es obligatorio.")
            return
        self.resultado = {
            "nombre": self._vars["nombre"].get(),
            "contacto": self._vars["contacto"].get(),
            "telefono": self._vars["telefono"].get(),
            "email": self._vars["email"].get(),
            "notas": self._vars["notas"].get(),
            "activo": bool(self._var_activo.get()),
            "dias": [dia for dia, var in self._vars_dia.items() if var.get()],
        }
        self.destroy()


class DialogoDetalleVenta(_DialogoModal):
    """Visor de una venta guardada: líneas, totales y reimpresión del ticket."""

    ANCHO = 560
    ALTO = 580
    TITULO = "Detalle de venta"

    def __init__(
        self,
        master: Any,
        venta: dict[str, Any],
        lineas: list[dict[str, Any]],
        on_reimprimir: Callable[[], str] | None = None,
    ) -> None:
        self._venta = venta
        self._lineas = lineas
        self._on_reimprimir = on_reimprimir
        super().__init__(master, titulo=f"Venta {venta.get('numero_ticket', '')}")

    # ------------------------------------------------------------------
    def _construir(self) -> None:
        v = self._venta

        ctk.CTkLabel(
            self,
            text=f"Ticket {v.get('numero_ticket', '—')}",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
        ).pack(pady=(20, 0))

        meta = [
            f"Hora: {v.get('hora') or '—'}",
            f"Método: {v.get('metodo_pago') or '—'}",
            f"Artículos: {v.get('items') or 0}",
            f"Estado: {v.get('estado') or '—'}",
        ]
        ctk.CTkLabel(
            self,
            text="    ·    ".join(meta),
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).pack(pady=(2, 8))

        # Cliente de la boleta (si se registró al cobrar).
        cliente = (v.get("cliente") or "").strip()
        dni_cliente = (v.get("dni_cliente") or "").strip()
        if cliente or dni_cliente:
            texto_cliente = f"Cliente: {cliente or 'CONSUMIDOR FINAL'}"
            if dni_cliente:
                texto_cliente += f"    ·    DNI/RUC: {dni_cliente}"
            ctk.CTkLabel(
                self,
                text=texto_cliente,
                text_color=COLOR_SECUNDARIO,
                font=("Segoe UI", 12),
            ).pack(pady=(0, 6))

        # --- Líneas de detalle ---
        self._frame_lineas = ctk.CTkScrollableFrame(
            self, height=230, corner_radius=8, fg_color="#f7f5f1"
        )
        self._frame_lineas.pack(fill="x", padx=24, pady=(6, 4))

        if self._lineas:
            for f in self._lineas:
                fila = ctk.CTkFrame(self._frame_lineas, fg_color="transparent")
                fila.pack(fill="x", padx=10, pady=2)
                ctk.CTkLabel(
                    fila,
                    text=f"{float(f['cantidad']):g}×  {f['descripcion']}",
                    text_color=COLOR_TEXTO,
                    font=("Segoe UI", 12),
                    justify="left",
                ).pack(side="left")
                ctk.CTkLabel(
                    fila,
                    text=f"S/ {float(f['subtotal']):,.2f}",
                    text_color=COLOR_TEXTO,
                    font=("Segoe UI", 12, "bold"),
                ).pack(side="right")
        else:
            ctk.CTkLabel(
                self._frame_lineas,
                text="Sin detalle registrado.",
                text_color=COLOR_SECUNDARIO,
                font=("Segoe UI", 12),
            ).pack(pady=20)

        # --- Totales ---
        subtotal = float(v.get("subtotal") or 0)
        total = float(v.get("total") or 0)
        vuelto = float(v.get("vuelto") or 0)
        redondeo = round(total - subtotal, 2)

        resumen = ctk.CTkFrame(self, fg_color="transparent")
        resumen.pack(fill="x", padx=24, pady=(12, 0))

        partes_resumen = [f"Subtotal: S/ {subtotal:,.2f}"]
        if redondeo != 0:
            signo = "+" if redondeo > 0 else "−"
            partes_resumen.append(f"Redondeo: {signo}S/ {abs(redondeo):.2f}")
        partes_resumen.append(f"Vuelto: S/ {vuelto:,.2f}")
        ctk.CTkLabel(
            resumen,
            text="   ·   ".join(partes_resumen),
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12),
        ).pack(anchor="w")

        ctk.CTkLabel(
            resumen,
            text=f"TOTAL: S/ {total:,.2f}",
            text_color=COLOR_ACENTO,
            font=ctk.CTkFont(family="Segoe UI", size=19, weight="bold"),
        ).pack(anchor="w", pady=(2, 0))

        # --- Botones ---
        fila_botones = ctk.CTkFrame(self, fg_color="transparent")
        fila_botones.pack(pady=(14, 18))
        ctk.CTkButton(
            fila_botones, text="Cerrar (Esc)", width=150, height=38,
            fg_color=COLOR_BOTON_NEUTRO, hover_color=COLOR_BOTON_NEUTRO_HOVER,
            command=self._cancelar,
        ).pack(side="left", padx=6)
        if self._on_reimprimir is not None:
            ctk.CTkButton(
                fila_botones, text="Reimprimir boleta", width=190, height=38,
                fg_color=COLOR_ACENTO, hover_color=COLOR_ACENTO_HOVER,
                command=self._reimprimir,
            ).pack(side="left", padx=6)

    def _reimprimir(self) -> None:
        """Llama al callback de la vista (que usa el servicio) y avisa el resultado."""
        try:
            mensaje = self._on_reimprimir()
        except Exception as exc:  # noqa: BLE001 - el visor no debe caer
            logger.exception("Error al reimprimir la venta id=%s.", self._venta.get("venta_id"))
            messagebox.showerror("No se pudo reimprimir", str(exc), parent=self)
            return
        messagebox.showinfo("Ticket reimpreso", mensaje, parent=self)


class DialogoInicioDia(_DialogoModal):
    """Diálogo para **abrir el turno** (Inicio del Día).

    Muestra el resumen del último cierre (si existe) y permite confirmar/editar
    el monto de apertura (fondo de caja). Devuelve el monto confirmado.
    """

    ANCHO = 500
    ALTO = 480
    TITULO = "Inicio del Día"

    def __init__(
        self,
        master: Any,
        caja_service: Any,
        caja_id: int = 1,
    ) -> None:
        self._caja_service = caja_service
        self._caja_id = caja_id
        self._ultimo_cierre = caja_service.obtener_ultimo_cerrado(caja_id)
        super().__init__(master, titulo=f"Inicio del Día — Caja {caja_id}")

    # ------------------------------------------------------------------
    def _construir(self) -> None:
        ctk.CTkLabel(
            self,
            text="INICIO DEL DÍA",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
        ).pack(pady=(18, 4))

        ctk.CTkLabel(
            self,
            text=f"Caja {self._caja_id}",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 13),
        ).pack(pady=(0, 12))

        # --- Separador ---
        ctk.CTkFrame(self, height=2, fg_color=COLOR_ACENTO, corner_radius=0).pack(
            fill="x", padx=24, pady=(0, 12)
        )

        # --- Resumen del último cierre (si existe) ---
        if self._ultimo_cierre:
            self._construir_resumen_anterior()
        else:
            ctk.CTkLabel(
                self,
                text="No hay cierre anterior registrado.\nSe iniciará con fondo de caja S/ 0.00",
                text_color=COLOR_SECUNDARIO,
                font=("Segoe UI", 12),
                justify="center",
            ).pack(pady=(8, 16))

        # --- Separador ---
        ctk.CTkFrame(self, height=2, fg_color=COLOR_ACENTO, corner_radius=0).pack(
            fill="x", padx=24, pady=(12, 12)
        )

        # --- Monto de apertura (editable) ---
        ctk.CTkLabel(
            self,
            text="Fondo de caja inicial (S/)",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=24)

        monto_inicial = (
            round(float(self._ultimo_cierre.monto_cierre_declarado or 0), 2)
            if self._ultimo_cierre and self._ultimo_cierre.monto_cierre_declarado is not None
            else 0.0
        )
        self._var_monto = tk.StringVar(value=f"{monto_inicial:,.2f}")
        self._entrada_monto = ctk.CTkEntry(
            self,
            textvariable=self._var_monto,
            height=50,
            justify="center",
            border_color=COLOR_ACENTO,
            border_width=2,
            font=ctk.CTkFont(family="Consolas", size=26),
        )
        self._entrada_monto.pack(fill="x", padx=24, pady=(4, 8))
        self._entrada_monto.bind("<Return>", lambda _e: self._aceptar())

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack()

        # Botones rápidos de billetes
        ctk.CTkLabel(self, text="Rápido:", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11)).pack(
            anchor="w", padx=24, pady=(8, 2)
        )
        fila_billetes = ctk.CTkFrame(self, fg_color="transparent")
        fila_billetes.pack(fill="x", padx=24, pady=(0, 12))
        for denom in (10, 20, 50, 100, 200):
            ctk.CTkButton(
                fila_billetes,
                text=f"S/ {denom}",
                height=34,
                width=70,
                command=lambda v=denom: self._var_monto.set(f"{float(v):,.2f}"),
            ).pack(side="left", padx=4, expand=True, fill="x")
        ctk.CTkButton(
            fila_billetes,
            text="0",
            height=34,
            fg_color=COLOR_BOTON_NEUTRO,
            hover_color=COLOR_BOTON_NEUTRO_HOVER,
            command=lambda: self._var_monto.set("0.00"),
        ).pack(side="left", padx=4, expand=True, fill="x")

        self._botones("Abrir Turno (Enter)", self._aceptar, color_ok=COLOR_OK)

    def _construir_resumen_anterior(self) -> None:
        """Construye la tarjeta con el resumen del último cierre."""
        c = self._ultimo_cierre
        marco = ctk.CTkFrame(self, fg_color="#f7f5f1", corner_radius=8)
        marco.pack(fill="x", padx=24, pady=(0, 4))

        ctk.CTkLabel(
            marco,
            text="ÚLTIMO CIERRE",
            text_color=COLOR_ACENTO,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", padx=14, pady=(10, 4))

        # Fecha
        try:
            fecha = c.cerrado_en.split(" ")[0] if c.cerrado_en else "—"
            if len(fecha) == 10:
                fecha = f"{fecha[8:10]}/{fecha[5:7]}/{fecha[0:4]}"
        except Exception:
            fecha = "—"
        ctk.CTkLabel(
            marco,
            text=f"Fecha: {fecha}",
            text_color=COLOR_TEXTO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=14)

        # Montos
        filas = [
            ("Apertura", c.monto_apertura),
            ("Cierre declarado", c.monto_cierre_declarado),
            ("Cierre sistema", c.monto_cierre_sistema),
            ("Diferencia", c.diferencia),
        ]
        for etiqueta, valor in filas:
            color = COLOR_TEXTO
            if etiqueta == "Diferencia" and valor is not None:
                color = COLOR_OK if float(valor) >= 0 else COLOR_ERROR
            ctk.CTkLabel(
                marco,
                text=f"{etiqueta}: S/ {float(valor or 0):,.2f}",
                text_color=color,
                font=("Segoe UI", 11, "bold" if etiqueta == "Diferencia" else "normal"),
            ).pack(anchor="w", padx=14, pady=1)

        if c.observaciones:
            ctk.CTkLabel(
                marco,
                text=f"Obs: {c.observaciones}",
                text_color=COLOR_SECUNDARIO,
                font=("Segoe UI", 10),
                wraplength=420,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(4, 10))
        else:
            ctk.CTkLabel(marco, text="").pack(pady=(0, 10))  # espacio final

    # ------------------------------------------------------------------
    def _al_mostrar(self) -> None:
        self._entrada_monto.focus_set()
        self._entrada_monto.select_range(0, "end")

    def _aceptar(self) -> None:
        texto = self._var_monto.get().strip().replace(",", "")
        try:
            monto = float(texto)
        except ValueError:
            self._lbl_error.configure(text="Ingresa un monto válido (ej. 200.00).")
            return
        if monto < 0:
            self._lbl_error.configure(text="El monto no puede ser negativo.")
            return
        self.resultado = round(monto, 2)
        self.destroy()


class _DialogoArqueoCierre(_DialogoModal):
    """Diálogo para el **arqueo de cierre de día**.

    Muestra el resumen del día y pide el efectivo contado (declarado).
    Devuelve el monto declarado (float) o None si se cancela.
    """

    ANCHO = 520
    ALTO = 520
    TITULO = "Arqueo de Cierre"

    def __init__(
        self,
        master: Any,
        resumen_texto: str,
        monto_sistema: float,
    ) -> None:
        self._resumen_texto = resumen_texto
        self._monto_sistema = round(float(monto_sistema), 2)
        super().__init__(master, titulo="Arqueo de Cierre")

    # ------------------------------------------------------------------
    def _construir(self) -> None:
        ctk.CTkLabel(
            self,
            text="ARQUEO DE CIERRE",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
        ).pack(pady=(18, 8))

        # --- Resumen del día (solo lectura) ---
        ctk.CTkLabel(
            self,
            text=self._resumen_texto,
            text_color=COLOR_SECUNDARIO,
            font=("Consolas", 11),
            justify="left",
        ).pack(padx=24, anchor="w")

        # Separador
        ctk.CTkFrame(self, height=2, fg_color=COLOR_ACENTO, corner_radius=0).pack(
            fill="x", padx=24, pady=(8, 12)
        )

        # --- Esperado por sistema ---
        ctk.CTkLabel(
            self,
            text=f"Efectivo esperado (sistema):  S/ {self._monto_sistema:,.2f}",
            text_color=COLOR_ACENTO,
            font=("Segoe UI", 13, "bold"),
        ).pack(pady=(0, 8))

        # --- Efectivo declarado (editable) ---
        ctk.CTkLabel(
            self,
            text="Efectivo contado (declarado)",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=24)

        self._var_monto = tk.StringVar(value=f"{self._monto_sistema:,.2f}")
        self._entrada_monto = ctk.CTkEntry(
            self,
            textvariable=self._var_monto,
            height=50,
            justify="center",
            border_color=COLOR_ACENTO,
            border_width=2,
            font=ctk.CTkFont(family="Consolas", size=26),
        )
        self._entrada_monto.pack(fill="x", padx=24, pady=(4, 8))
        self._entrada_monto.bind("<Return>", lambda _e: self._aceptar())

        # Diferencia en vivo
        self._lbl_diff = ctk.CTkLabel(
            self,
            text="Diferencia: S/ 0.00",
            text_color=COLOR_OK,
            font=("Segoe UI", 14, "bold"),
        )
        self._lbl_diff.pack(pady=(0, 4))
        self._var_monto.trace_add("write", lambda *_a: self._actualizar_diff())

        self._lbl_error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, font=("Segoe UI", 11))
        self._lbl_error.pack()

        # Botones rápidos
        ctk.CTkLabel(self, text="Rápido:", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 11)).pack(
            anchor="w", padx=24, pady=(8, 2)
        )
        fila_billetes = ctk.CTkFrame(self, fg_color="transparent")
        fila_billetes.pack(fill="x", padx=24, pady=(0, 12))
        for denom in (10, 20, 50, 100, 200):
            ctk.CTkButton(
                fila_billetes,
                text=f"S/ {denom}",
                height=34,
                width=70,
                command=lambda v=denom: self._var_monto.set(f"{float(v):,.2f}"),
            ).pack(side="left", padx=4, expand=True, fill="x")
        ctk.CTkButton(
            fila_billetes,
            text=f"Sistema ({self._monto_sistema:,.2f})",
            height=34,
            fg_color=COLOR_BOTON_NEUTRO,
            hover_color=COLOR_BOTON_NEUTRO_HOVER,
            command=lambda: self._var_monto.set(f"{self._monto_sistema:,.2f}"),
        ).pack(side="left", padx=4, expand=True, fill="x")

        self._botones("Cerrar Turno (Enter)", self._aceptar, color_ok=COLOR_OK)

    def _actualizar_diff(self) -> None:
        try:
            declarado = float(self._var_monto.get().strip().replace(",", ""))
        except ValueError:
            declarado = 0.0
        diff = round(declarado - self._monto_sistema, 2)
        if diff >= 0:
            self._lbl_diff.configure(text=f"Diferencia: +S/ {diff:,.2f}", text_color=COLOR_OK)
        else:
            self._lbl_diff.configure(text=f"Diferencia: S/ {diff:,.2f}", text_color=COLOR_ERROR)

    # ------------------------------------------------------------------
    def _al_mostrar(self) -> None:
        self._entrada_monto.focus_set()
        self._entrada_monto.select_range(0, "end")

    def _aceptar(self) -> None:
        texto = self._var_monto.get().strip().replace(",", "")
        try:
            monto = float(texto)
        except ValueError:
            self._lbl_error.configure(text="Ingresa un monto válido (ej. 200.00).")
            return
        if monto < 0:
            self._lbl_error.configure(text="El monto no puede ser negativo.")
            return
        self.resultado = round(monto, 2)
        self.destroy()
