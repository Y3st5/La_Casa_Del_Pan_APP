"""Vista "Ventas del Día" — capa de PRESENTACIÓN.

Log de las ventas registradas **hoy** (o de la fecha consultada): lista con
ticket, hora, método de pago, ítems y montos, más un resumen agregado
(ventas, total vendido y desglose por método). Doble clic (o Enter) abre el
detalle de una venta y permite **reimprimir su ticket**.

No ejecuta SQL: delega en ``VentaService`` (inyectado desde el composition
root).

Accesibilidad / atajos:
    * ``Ctrl+F``         → ir al buscador (por ticket o método).
    * ``Enter`` / F2     → ver el detalle de la venta seleccionada.
    * ``F5``             → recargar el log del día.
    * ``Esc``            → limpiar la búsqueda.
    * Doble clic         → ver detalle.
"""

from __future__ import annotations

import logging
from tkinter import messagebox, ttk
from typing import Any

import customtkinter as ctk

from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.componentes import Accion, BarraAcciones
from src.presentacion.dialogos import DialogoDetalleVenta, DialogoInicioDia, _DialogoArqueoCierre
from src.presentacion.estilos import (
    COLOR_ACENTO,
    COLOR_FILA_PAR,
    COLOR_FILA_IMPAR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
    COLOR_ERROR,
    SUPERFICIE,
    BORDE,
    RADIO_SUPERFICIE,
    RADIO_CONTROL,
    estilo_treeview,
)

logger = logging.getLogger(__name__)

#: Orden preferido de las tarjetas por método de pago (los no listados van al final).
ORDEN_METODOS = ("Efectivo", "Yape/Plin", "Tarjeta", "Consumo propio", "Fiado")


class VentasDelDiaView(ctk.CTkFrame):
    """Log de las ventas del día con resumen y detalle por venta."""

    COLUMNAS = ("producto", "hora", "items", "metodo", "subtotal", "total", "vuelto")
    ENCABEZADOS = ("Producto", "Hora", "Art.", "Método", "Subtotal", "Total", "Vuelto")
    ANCHOS = (280, 80, 60, 110, 100, 100, 100)
    ALINEACIONES = ("w", "center", "center", "center", "e", "e", "e")

    def __init__(
        self, master: Any, venta_service: Any, caja_service: Any | None = None, **kwargs: Any
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._servicio = venta_service
        self._caja_service = caja_service
        self._fecha: str = ""
        self._ventas: list[dict[str, Any]] = []
        self._resumen: dict[str, Any] = {}
        self._por_id: dict[str, dict[str, Any]] = {}

        self._configurar_estilos()
        self._construir_ui()
        self._atajos_teclado()
        self.cargar_datos()

    # ------------------------------------------------------------------ UI
    def _configurar_estilos(self) -> None:
        estilo_treeview("VentasDia.Treeview")

    def _construir_ui(self) -> None:
        # --- Cabecera ---
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.pack(fill="x", padx=24, pady=(18, 2))

        ctk.CTkLabel(
            cabecera,
            text="Ventas del Día",
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

        # Estado de la emisión electrónica (SUNAT): pendientes de envío.
        self._lbl_sunat = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        )
        self._lbl_sunat.pack(side="right", padx=(12, 0))

        # --- Barra de herramientas ---
        barra = BarraAcciones(
            self,
            primaria=Accion("Cerrar día", self._cerrar_dia, "calendario"),
            secundarias=[Accion("Ver detalle", self._ver_detalle, "recibo")],
            menu=[
                Accion("Inicio del día", self._inicio_dia, "efectivo"),
                Accion("Enviar a SUNAT", self._enviar_sunat, "etiqueta"),
                Accion("Abrir reportes", self._abrir_carpeta_reportes, "carpeta"),
                Accion("Actualizar", self.cargar_datos, "recargar"),
            ],
        )
        barra.pack(fill="x", padx=24, pady=(6, 4))

        self._var_busqueda = ctk.StringVar()
        self._entrada_busqueda = ctk.CTkEntry(
            barra.izquierda,
            textvariable=self._var_busqueda,
            width=260,
            height=34,
            corner_radius=RADIO_CONTROL,
            border_width=1,
            border_color=BORDE,
            placeholder_text="Buscar por ticket o método…  (Ctrl+F)",
        )
        self._entrada_busqueda.pack(side="left", fill="x", expand=True)
        self._var_busqueda.trace_add("write", lambda *_a: self._aplicar_filtro())

        # --- Resumen del día ---
        resumen_barra = ctk.CTkFrame(self, fg_color="transparent")
        resumen_barra.pack(fill="x", padx=24, pady=(4, 6))

        self._tarjeta_ventas = self._crear_tarjeta(resumen_barra)
        self._tarjeta_ventas.pack(side="left", fill="x", expand=True)
        self._lbl_cantidad = self._lbl_tarjeta_valor(self._tarjeta_ventas)

        self._tarjeta_total = self._crear_tarjeta(resumen_barra)
        self._tarjeta_total.pack(side="left", fill="x", expand=True, padx=12)
        self._lbl_total = self._lbl_tarjeta_valor(self._tarjeta_total)

        # Nueva: Fondo de apertura (turno actual)
        self._tarjeta_apertura = self._crear_tarjeta(resumen_barra)
        self._tarjeta_apertura.pack(side="left", fill="x", expand=True, padx=12)
        self._lbl_apertura = self._lbl_tarjeta_valor(self._tarjeta_apertura)

        # Efectivo esperado (apertura + ventas en efectivo - compras al contado)
        self._tarjeta_esperado = self._crear_tarjeta(resumen_barra)
        self._tarjeta_esperado.pack(side="left", fill="x", expand=True, padx=12)
        self._lbl_esperado = self._lbl_tarjeta_valor(self._tarjeta_esperado)

        # Las tarjetas por método de pago se crean/renuevan al recargar, porque
        # cambian según los métodos usados ese día (una tarjeta por método).
        # Van en su propia franja y se reacomodan en varias filas cuando no
        # entran a lo ancho (p. ej. 1366x768 con 4-5 métodos).
        self._resumen_barra = resumen_barra
        self._marco_metodos = ctk.CTkFrame(self, fg_color="transparent", height=1)
        self._marco_metodos.pack(fill="x", padx=24, pady=(0, 6))
        for columna in range(6):
            self._marco_metodos.grid_columnconfigure(columna, weight=0, uniform="metodo")
        self._marco_metodos.bind("<Configure>", self._al_redimensionar_metodos)
        self._tarjetas_metodos: list[Any] = []
        self._columnas_metodos = 0

        # --- Desgloses del día: categorías (izq.) y tributario (der.) ---
        desgloses = ctk.CTkFrame(self, fg_color="transparent")
        desgloses.pack(fill="x", padx=24, pady=(0, 6))
        desgloses.grid_columnconfigure(0, weight=1, uniform="desglose")
        desgloses.grid_columnconfigure(1, weight=1, uniform="desglose")
        # El texto de cada tarjeta se ajusta al ancho real (evita cortes).
        desgloses.bind("<Configure>", self._ajustar_wrap_desgloses)

        # --- Desglose por categoría (Panes, Abarrotes, Cafetería…) ---
        self._tarjeta_categorias = ctk.CTkFrame(desgloses, corner_radius=RADIO_SUPERFICIE, fg_color=SUPERFICIE)
        self._tarjeta_categorias.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(
            self._tarjeta_categorias,
            text="Ventas por categoría",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(8, 0))
        self._lbl_categorias = ctk.CTkLabel(
            self._tarjeta_categorias,
            text="—",
            text_color=COLOR_TEXTO,
            font=("Segoe UI", 12),
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._lbl_categorias.pack(anchor="w", fill="x", padx=16, pady=(2, 10))

        # --- Resumen tributario (IGV) — es el desglose que se declara a SUNAT ---
        self._tarjeta_tributario = ctk.CTkFrame(desgloses, corner_radius=RADIO_SUPERFICIE, fg_color=SUPERFICIE)
        self._tarjeta_tributario.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(
            self._tarjeta_tributario,
            text="Resumen tributario (IGV)",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(8, 0))
        self._lbl_tributario = ctk.CTkLabel(
            self._tarjeta_tributario,
            text="—",
            text_color=COLOR_TEXTO,
            font=("Segoe UI", 12),
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._lbl_tributario.pack(anchor="w", fill="x", padx=16, pady=(2, 10))

        # --- Tabla de ventas ---
        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=24, pady=(0, 10))

        contenedor_tabla = ctk.CTkFrame(cuerpo, fg_color="transparent")
        contenedor_tabla.pack(side="left", fill="both", expand=True)

        self._tabla = ttk.Treeview(
            contenedor_tabla,
            columns=self.COLUMNAS,
            show="headings",
            style="VentasDia.Treeview",
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
                stretch=(columna == "producto"),
                minwidth=60,
            )
        self._tabla.pack(side="left", fill="both", expand=True)

        barra_scroll = ttk.Scrollbar(
            contenedor_tabla, orient="vertical", command=self._tabla.yview
        )
        self._tabla.configure(yscrollcommand=barra_scroll.set)
        barra_scroll.pack(side="right", fill="y")

        self._tabla.tag_configure("par", background=COLOR_FILA_PAR)
        self._tabla.tag_configure("impar", background=COLOR_FILA_IMPAR)

        self._tabla.bind("<Double-1>", lambda _e: self._ver_detalle())
        self._tabla.bind("<Return>", lambda _e: self._ver_detalle())
        self._tabla.bind("<F2>", lambda _e: self._ver_detalle())

        # --- Pie: atajos ---
        ctk.CTkLabel(
            self,
            text="Atajos:  Enter/F2 ver detalle  ·  Ctrl+F buscar  ·  F5 recargar  ·  Esc limpiar",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=26, pady=(0, 6))

    @staticmethod
    def _crear_tarjeta(padre: Any, titulo: str = "—") -> ctk.CTkFrame:
        tarjeta = ctk.CTkFrame(padre, corner_radius=RADIO_SUPERFICIE, fg_color=SUPERFICIE)
        ctk.CTkLabel(
            tarjeta,
            text=titulo,
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11, "bold"),
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

    def _al_redimensionar_metodos(self, event: Any = None) -> None:
        """Recalcula la distribución de tarjetas cuando cambia el ancho."""
        self._reubicar_tarjetas_metodos(event)

    def _reubicar_tarjetas_metodos(self, event: Any = None) -> None:
        """Reparte las tarjetas de métodos en filas según el ancho disponible.

        Cada tarjeta necesita ~240 px para que el título y el monto se lean
        completos; si no caben todas en una fila, se reacomodan en varias.
        """
        if not self._tarjetas_metodos:
            return
        ancho = int(getattr(event, "width", 0) or 0)
        if ancho <= 0:
            ancho = self._marco_metodos.winfo_width()
        columnas = 4 if ancho <= 0 else ancho // 240
        columnas = max(1, min(len(self._tarjetas_metodos), columnas, 6))
        if columnas == self._columnas_metodos:
            return
        self._columnas_metodos = columnas
        # Solo las columnas en uso reparten el ancho disponible.
        for indice in range(6):
            self._marco_metodos.grid_columnconfigure(
                indice, weight=1 if indice < columnas else 0, uniform="metodo"
            )
        for indice, tarjeta in enumerate(self._tarjetas_metodos):
            fila, columna = divmod(indice, columnas)
            tarjeta.grid(
                row=fila,
                column=columna,
                sticky="ew",
                padx=(0 if columna == 0 else 12, 0),
                pady=(0, 6),
            )

    def _ajustar_wrap_desgloses(self, event: Any = None) -> None:
        """Ajusta el ancho de envoltura de las tarjetas de desglose."""
        ancho = int(getattr(event, "width", 0) or 0)
        if ancho <= 0:
            return
        wraplength = max(220, (ancho - 24) // 2 - 40)
        self._lbl_categorias.configure(wraplength=wraplength)
        self._lbl_tributario.configure(wraplength=wraplength)

    @staticmethod
    def _ordenar_metodos(por_metodo: dict[str, Any]) -> list[tuple[str, float]]:
        """Ordena los métodos de pago: primero el orden habitual, luego el resto."""

        def clave(item: tuple[str, Any]) -> tuple[int, Any]:
            metodo = item[0]
            if metodo in ORDEN_METODOS:
                return (0, ORDEN_METODOS.index(metodo))
            return (1, metodo)

        return [
            (metodo, float(monto))
            for metodo, monto in sorted(por_metodo.items(), key=clave)
        ]

    # ------------------------------------------------------------- Atajos
    def _atajos_teclado(self) -> None:
        raiz = self.winfo_toplevel()
        raiz.bind("<F5>", self._atajo(self.cargar_datos), add="+")
        raiz.bind("<Control-f>", self._atajo(self._enfocar_busqueda), add="+")
        raiz.bind("<Escape>", self._atajo(self._limpiar_busqueda), add="+")

    def _atajo(self, accion: Any) -> Any:
        def _manejador(_event: Any = None) -> str | None:
            if self.winfo_ismapped():
                accion()
                return "break"
            return None

        return _manejador

    def _enfocar_busqueda(self) -> None:
        self._entrada_busqueda.focus_set()
        self._entrada_busqueda.select_range(0, "end")

    def _limpiar_busqueda(self) -> None:
        self._var_busqueda.set("")

    # --------------------------------------------------------------- Datos
    def cargar_datos(self) -> None:
        """Recarga el log de ventas del día y el resumen."""
        try:
            reporte = self._servicio.ventas_del_dia()
        except ErrorConexionBD as exc:
            logger.error("Error de conexión al cargar ventas del día: %s", exc)
            self._lbl_estado.configure(text="⚠  Sin conexión a la base de datos", text_color=COLOR_ERROR)
            messagebox.showerror("Error de conexión", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001 - la UI nunca debe caer
            logger.exception("Error inesperado al cargar ventas del día.")
            self._lbl_estado.configure(text="⚠  Error inesperado", text_color=COLOR_ERROR)
            messagebox.showerror("Error", f"No se pudo cargar las ventas del día:\n\n{exc}", parent=self)
            return

        self._fecha = reporte["fecha"]
        self._ventas = reporte["ventas"]
        self._resumen = reporte["resumen"]
        self._aplicar_filtro()
        self._actualizar_resumen()
        self._actualizar_estado_sunat()

    def _aplicar_filtro(self) -> None:
        texto = self._var_busqueda.get().strip().lower()
        if texto:
            visibles = [
                v
                for v in self._ventas
                if texto in str(v.get("primer_producto") or "").lower()
                or texto in str(v.get("metodo_pago") or "").lower()
            ]
        else:
            visibles = list(self._ventas)
        self._render(visibles, filtrado=bool(texto))

    def _render(self, ventas: list[dict[str, Any]], filtrado: bool = False) -> None:
        for item in self._tabla.get_children():
            self._tabla.delete(item)
        self._por_id = {str(v.get("venta_id")): v for v in ventas}

        for indice, venta in enumerate(ventas):
            self._tabla.insert(
                "",
                "end",
                iid=str(venta.get("venta_id")),
                values=self._formatear_fila(venta),
                tags=("par" if indice % 2 == 0 else "impar",),
            )

        total = len(self._ventas)
        mostrados = len(ventas)
        partes = [f"{mostrados} venta(s)"]
        if filtrado:
            partes.append(f"filtrado de {total}")
        self._lbl_estado.configure(
            text=" · ".join(partes),
            text_color=COLOR_OK if mostrados else COLOR_SECUNDARIO,
        )

    def _actualizar_resumen(self) -> None:
        resumen = self._resumen or {}
        cantidad = resumen.get("cantidad", 0)
        total_vendido = float(resumen.get("total_vendido") or 0)
        por_metodo = resumen.get("por_metodo") or {}

        fecha = self._fecha or ""
        self._lbl_fecha.configure(
            text=f"Hoy · {fecha[8:10]}/{fecha[5:7]}/{fecha[0:4]}" if len(fecha) == 10 else ""
        )

        titulos = self._tarjeta_ventas.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Ventas realizadas")
        self._lbl_cantidad.configure(text=str(cantidad))

        titulos = self._tarjeta_total.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Total vendido")
        self._lbl_total.configure(text=f"S/ {total_vendido:,.2f}")

        # --- NUEVO: Fondo de apertura y Efectivo esperado ---
        monto_apertura = 0.0
        monto_esperado = 0.0
        if self._caja_service is not None:
            turno = self._caja_service.obtener_turno_abierto(1)
            if turno:
                monto_apertura = float(turno.monto_apertura or 0)
                # Solo el efectivo físico entra a la caja; Yape/Plin y tarjeta
                # no forman parte del efectivo esperado en el cajón.
                ventas_efectivo = float(por_metodo.get("Efectivo", 0.0))
                compras_contado = self._caja_service.total_compras_afectan_caja(self._fecha)
                monto_esperado = round(monto_apertura + ventas_efectivo - compras_contado, 2)

        titulos = self._tarjeta_apertura.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Fondo apertura")
        self._lbl_apertura.configure(text=f"S/ {monto_apertura:,.2f}")

        titulos = self._tarjeta_esperado.winfo_children() or []
        if titulos:
            titulos[0].configure(text="Efectivo esperado")
        self._lbl_esperado.configure(text=f"S/ {monto_esperado:,.2f}")

        # --- Tarjetas por método de pago (título = método, valor = S/ monto) ---
        for tarjeta in self._tarjetas_metodos:
            tarjeta.destroy()
        self._tarjetas_metodos = []
        self._columnas_metodos = 0
        for metodo, monto in self._ordenar_metodos(por_metodo):
            tarjeta = self._crear_tarjeta(self._marco_metodos, titulo=metodo)
            self._lbl_tarjeta_valor(tarjeta).configure(text=f"S/ {float(monto):,.2f}")
            self._tarjetas_metodos.append(tarjeta)
        self._reubicar_tarjetas_metodos()

        # --- Desglose por categoría ---
        por_categoria = resumen.get("por_categoria") or []
        if por_categoria:
            partes_cat = [
                f"{c['categoria']}  S/ {float(c['subtotal']):,.2f}" for c in por_categoria
            ]
            texto_cat = "     ·     ".join(partes_cat)
            ajuste = float(resumen.get("ajuste_redondeo") or 0)
            if abs(ajuste) >= 0.01:
                texto_cat += f"     ·     Ajuste redondeo  S/ {ajuste:,.2f}"
            texto_cat += f"\nTotal  S/ {total_vendido:,.2f}"
        else:
            texto_cat = "Aún no hay ventas por categoría hoy."
        self._lbl_categorias.configure(text=texto_cat)

        # --- Resumen tributario (base imponible + IGV) ---
        self._lbl_tributario.configure(
            text=self._texto_tributario(resumen.get("tributario") or {})
        )

    @staticmethod
    def _texto_tributario(tributario: dict[str, Any]) -> str:
        """Arma el texto del desglose tributario del día (IGV).

        Es puro (no toca Tk) para poder probarlo sin instanciar la vista.
        """
        base = float(tributario.get("base_gravada") or 0)
        igv = float(tributario.get("igv") or 0)
        exonerado = float(tributario.get("exonerado") or 0)
        inafecto = float(tributario.get("inafecto") or 0)
        tasa = float(tributario.get("tasa_igv") or 0.18)
        if not (base or igv or exonerado or inafecto):
            return "Aún no hay ventas con desglose tributario hoy."
        partes = [
            f"Operaciones gravadas  S/ {base:,.2f}",
            f"IGV ({tasa * 100:.0f}%)  S/ {igv:,.2f}",
        ]
        if exonerado:
            partes.append(f"Exonerado  S/ {exonerado:,.2f}")
        if inafecto:
            partes.append(f"Inafecto  S/ {inafecto:,.2f}")
        return "     ·     ".join(partes)

    # ---------------------------------------------------------- Detalle
    def _ver_detalle(self) -> None:
        venta = self._obtener_seleccionado()
        if venta is None:
            messagebox.showinfo(
                "Selecciona una venta",
                "Primero selecciona una venta de la lista.",
                parent=self,
            )
            return
        venta_id = int(venta["venta_id"])
        try:
            lineas = self._servicio.obtener_detalle_venta(venta_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al obtener el detalle de la venta id=%s.", venta_id)
            messagebox.showerror("Error", f"No se pudo cargar el detalle:\n\n{exc}", parent=self)
            return
        DialogoDetalleVenta(
            self,
            venta=venta,
            lineas=lineas,
            on_reimprimir=lambda: self._reimprimir(venta_id),
        ).mostrar()

    def _reimprimir(self, venta_id: int) -> str:
        """Reimprime el ticket de una venta guardada; devuelve el mensaje para la UI."""
        ruta = self._servicio.imprimir_venta_guardada(venta_id)
        if not ruta:
            return "La venta ya no existe en la base de datos."
        return f"Ticket guardado en:\n{ruta}"

    # ------------------------------------------------------------ Apoyo
    def _obtener_seleccionado(self) -> dict[str, Any] | None:
        seleccion = self._tabla.selection()
        iid = seleccion[0] if seleccion else self._tabla.focus()
        if not iid:
            return None
        return self._por_id.get(str(iid))

    @staticmethod
    def _formatear_fila(venta: dict[str, Any]) -> tuple[Any, ...]:
        items = venta.get("items", 0)
        primer_producto = venta.get("primer_producto") or "—"
        if items > 1:
            producto_str = f"{primer_producto}  (+{items - 1} más)"
        else:
            producto_str = primer_producto
        return (
            producto_str,
            venta.get("hora") or "—",
            items,
            venta.get("metodo_pago", "—"),
            f"S/ {float(venta.get('subtotal') or 0):,.2f}",
            f"S/ {float(venta.get('total') or 0):,.2f}",
            f"S/ {float(venta.get('vuelto') or 0):,.2f}",
        )

    # -------------------------------------------------------- Cierre de día
    def _cerrar_dia(self) -> None:
        """Genera reporte PDF del día, hace arqueo de caja, cierra el turno y elimina las ventas."""
        if not self._ventas:
            messagebox.showinfo(
                "Cerrar Día",
                "No hay ventas registradas hoy para cerrar.",
                parent=self,
            )
            return

        if self._caja_service is None:
            messagebox.showerror(
                "Error",
                "Servicio de caja no disponible. Reinicie la aplicación.",
                parent=self,
            )
            return

        # 1. Verificar que hay un turno abierto
        turno_abierto = self._caja_service.obtener_turno_abierto(1)
        if not turno_abierto:
            messagebox.showwarning(
                "Cerrar Día",
                "No hay un turno abierto para esta caja.\n"
                "Primero haga 'Inicio del Día' para abrir el turno.",
                parent=self,
            )
            return

        # 2. Resumen para confirmación
        total_vendido = self._resumen.get("total_vendido", 0.0)
        cantidad = self._resumen.get("cantidad", 0)
        por_metodo = self._resumen.get("por_metodo", {})

        lineas = [
            f"CIERRE DE DÍA — {self._fecha}",
            "=" * 40,
            f"Ventas realizadas: {cantidad}",
            f"Total vendido: S/ {total_vendido:,.2f}",
            "-" * 40,
            "Desglose por método:",
        ]
        for metodo, monto in sorted(por_metodo.items()):
            lineas.append(f"  {metodo}: S/ {monto:,.2f}")

        por_categoria = self._resumen.get("por_categoria") or []
        if por_categoria:
            lineas.append("-" * 40)
            lineas.append("Desglose por categoría:")
            for cat in por_categoria:
                lineas.append(f"  {cat['categoria']}: S/ {float(cat['subtotal']):,.2f}")
            ajuste = float(self._resumen.get("ajuste_redondeo") or 0)
            if abs(ajuste) >= 0.01:
                lineas.append(f"  Ajuste por redondeo: S/ {ajuste:,.2f}")
            lineas.append(f"  Total: S/ {total_vendido:,.2f}")

        tributario = self._resumen.get("tributario") or {}
        if any(
            float(tributario.get(clave) or 0)
            for clave in ("base_gravada", "igv", "exonerado", "inafecto")
        ):
            lineas.append("-" * 40)
            lineas.append("Resumen tributario (IGV):")
            lineas.append(
                f"  Operaciones gravadas: S/ {float(tributario.get('base_gravada') or 0):,.2f}"
            )
            lineas.append(
                f"  IGV ({float(tributario.get('tasa_igv') or 0.18) * 100:.0f}%): "
                f"S/ {float(tributario.get('igv') or 0):,.2f}"
            )
            if float(tributario.get("exonerado") or 0):
                lineas.append(
                    f"  Exonerado: S/ {float(tributario.get('exonerado') or 0):,.2f}"
                )
            if float(tributario.get("inafecto") or 0):
                lineas.append(
                    f"  Inafecto: S/ {float(tributario.get('inafecto') or 0):,.2f}"
                )
        lineas.append("=" * 40)

        # 3. Mostrar resumen y pedir efectivo declarado (arqueo)
        resumen_texto = "\n".join(lineas)
        compras_contado = self._caja_service.total_compras_afectan_caja(self._fecha)
        # Solo el efectivo físico entra a la caja (coincide con la tarjeta
        # "Efectivo esperado" del resumen).
        ventas_efectivo = float(self._resumen.get("por_metodo", {}).get("Efectivo", 0.0))
        monto_sistema = (
            turno_abierto.monto_apertura + ventas_efectivo - compras_contado
        )

        # Diálogo personalizado para arqueo
        dlg = _DialogoArqueoCierre(self, resumen_texto, monto_sistema)
        monto_declarado = dlg.mostrar()
        if monto_declarado is None:
            return  # Usuario canceló

        # 4. Cerrar turno (arqueo) + Cerrar día (PDF + eliminar ventas)
        try:
            # Primero: arqueo y cierre de turno
            resultado_cierre = self._caja_service.cerrar_turno(
                caja_id=1,
                monto_cierre_declarado=monto_declarado,
                usuario_cierre_id=None,  # TODO: usuario actual
                observaciones=f"Cierre de día {self._fecha}",
            )

            # Segundo: generar PDF y limpiar las ventas y compras del día
            pdf_path, eliminadas, compras_eliminadas = self._servicio.cerrar_dia(self._fecha)

        except ValueError as e:
            messagebox.showwarning("Cerrar Día", str(e), parent=self)
            return
        except Exception as exc:
            logger.exception("Error al cerrar día.")
            messagebox.showerror("Error", f"No se pudo cerrar el día:\n\n{exc}", parent=self)
            return

        # 5. Mostrar resultado completo
        diff_color = "OK" if resultado_cierre.diferencia >= 0 else "ATENCION"
        mensaje_final = (
            f"Día {self._fecha} cerrado correctamente.\n\n"
            f"--- ARQUEO DE CAJA ---\n"
            f"Apertura:           S/ {resultado_cierre.monto_apertura:,.2f}\n"
            f"Ventas efectivo:    S/ {resultado_cierre.total_ventas_efectivo:,.2f}\n"
            f"Compras contado:   -S/ {resultado_cierre.total_compras_efectivo:,.2f}\n"
            f"Esperado (sistema): S/ {resultado_cierre.monto_cierre_sistema:,.2f}\n"
            f"Declarado (contado): S/ {resultado_cierre.monto_cierre_declarado:,.2f}\n"
            f"Diferencia:         [{diff_color}] S/ {resultado_cierre.diferencia:,.2f}\n\n"
            f"--- VENTAS ---\n"
            f"Ventas eliminadas:  {eliminadas}\n"
            f"Compras eliminadas: {compras_eliminadas}\n"
            f"Reporte PDF:        {pdf_path}\n\n"
            f"¿Desea abrir la carpeta de reportes?"
        )

        respuesta = messagebox.askyesno("Día Cerrado", mensaje_final, parent=self)
        if respuesta:
            import os
            carpeta = os.path.dirname(pdf_path)
            try:
                os.startfile(carpeta)
            except AttributeError:
                import subprocess
                subprocess.run(["xdg-open", carpeta])
        self.cargar_datos()

    # ------------------------------------------------------ Emisión SUNAT
    def _actualizar_estado_sunat(self) -> None:
        """Pinta en la cabecera cuántos comprobantes esperan envío a SUNAT."""
        if not hasattr(self, "_lbl_sunat"):
            return
        if not getattr(self._servicio, "sunat_habilitado", lambda: False)():
            self._lbl_sunat.configure(text="SUNAT: deshabilitado", text_color=COLOR_SECUNDARIO)
            return
        try:
            pendientes = int(self._servicio.contar_pendientes_sunat())
        except Exception:  # noqa: BLE001 - el rótulo nunca debe tumbar la vista
            logger.exception("No se pudo contar los comprobantes pendientes de SUNAT.")
            pendientes = 0
        if pendientes:
            self._lbl_sunat.configure(
                text=f"SUNAT: {pendientes} pendiente(s)", text_color=COLOR_ACENTO
            )
        else:
            self._lbl_sunat.configure(text="SUNAT: al día", text_color=COLOR_OK)

    def _enviar_sunat(self) -> None:
        """Envía (o reintenta) los comprobantes PENDIENTES a SUNAT."""
        if not getattr(self._servicio, "sunat_habilitado", lambda: False)():
            messagebox.showinfo(
                "SUNAT",
                "La emisión electrónica está deshabilitada.\n\n"
                "Actívala con  [sunat] habilitado = true  en config.ini.",
                parent=self,
            )
            return

        try:
            pendientes = int(self._servicio.contar_pendientes_sunat())
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudo consultar los pendientes de SUNAT.")
            messagebox.showerror("SUNAT", f"No se pudo consultar los pendientes:\n\n{exc}", parent=self)
            return

        if not pendientes:
            messagebox.showinfo("SUNAT", "No hay comprobantes pendientes de envío.", parent=self)
            return

        if not messagebox.askyesno(
            "Enviar a SUNAT",
            f"Se enviarán {pendientes} comprobante(s) pendiente(s) a SUNAT.\n\n¿Continuar?",
            parent=self,
        ):
            return

        try:
            resultados = self._servicio.enviar_pendientes_sunat()
        except Exception as exc:  # noqa: BLE001 - la vista nunca debe caer
            logger.exception("Error al enviar comprobantes pendientes a SUNAT.")
            messagebox.showerror("SUNAT", f"No se pudo enviar a SUNAT:\n\n{exc}", parent=self)
            self._actualizar_estado_sunat()
            return

        aceptados = [r for r in resultados if getattr(r, "aceptado", False)]
        rechazados = [r for r in resultados if getattr(r, "estado", "") == "RECHAZADO"]
        pendientes_restantes = len(resultados) - len(aceptados) - len(rechazados)

        lineas = [f"{getattr(r, 'numero', '?')}: {getattr(r, 'estado', '')}" for r in resultados]
        detalle = "\n".join(lineas[:20])
        if len(lineas) > 20:
            detalle += f"\n… y {len(lineas) - 20} más."

        resumen = (
            f"Aceptados: {len(aceptados)}\n"
            f"Rechazados: {len(rechazados)}\n"
            f"Sin enviar (reintentar): {pendientes_restantes}"
        )
        texto = f"{resumen}\n\n{detalle}" if detalle else resumen

        if rechazados or pendientes_restantes:
            messagebox.showwarning("SUNAT", texto, parent=self)
        else:
            messagebox.showinfo("SUNAT", texto, parent=self)

        self.cargar_datos()

    def _abrir_carpeta_reportes(self) -> None:
        """Abre la carpeta donde se guardan los PDFs de cierre de día."""
        from src.infraestructura.rutas import carpeta_reportes
        import os
        carpeta = carpeta_reportes()
        carpeta.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(carpeta)  # Windows
        except AttributeError:
            subprocess.run(["xdg-open", str(carpeta)])  # Linux/macOS

    # -------------------------------------------------------- Inicio del Día
    def _inicio_dia(self) -> None:
        """Abre el diálogo de Inicio del Día para confirmar/editar el fondo de caja."""
        if self._caja_service is None:
            messagebox.showerror(
                "Error",
                "Servicio de caja no disponible. Reinicie la aplicación.",
                parent=self,
            )
            return

        # Verificar si ya hay un turno abierto
        if self._caja_service.hay_turno_abierto(1):  # caja_id=1 por defecto
            if not messagebox.askyesno(
                "Turno ya abierto",
                "Ya existe un turno ABIERTO para esta caja.\n"
                "¿Desea abrir el diálogo de todos modos (para ver el resumen)?",
                parent=self,
            ):
                return

        dlg = DialogoInicioDia(self, caja_service=self._caja_service, caja_id=1)
        monto_apertura = dlg.mostrar()
        if monto_apertura is None:
            return  # Usuario canceló

        try:
            resultado = self._caja_service.abrir_turno(
                caja_id=1,
                monto_apertura=monto_apertura,
                usuario_apertura_id=None,  # TODO: obtener usuario actual de la sesión
            )
        except ValueError as e:
            messagebox.showerror("Error al abrir turno", str(e), parent=self)
            return
        except Exception as exc:
            logger.exception("Error inesperado al abrir turno.")
            messagebox.showerror("Error", f"No se pudo abrir el turno:\n\n{exc}", parent=self)
            return

        messagebox.showinfo(
            "Turno Abierto",
            f"Inicio del Día confirmado.\n\n"
            f"Turno #{resultado.turno_id}\n"
            f"Fondo de caja: S/ {resultado.monto_apertura:,.2f}\n"
            f"Caja: {resultado.caja_id}\n"
            f"Hora: {resultado.abierto_en[11:16]}",
            parent=self,
        )
        self.cargar_datos()  # Refrescar por si cambia el estado