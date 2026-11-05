"""Vista de Caja / Ventas — capa de PRESENTACIÓN.

Pantalla principal del cajero: escaneo de código de barras, carrito de compras,
total en tiempo real y cobro.

El lector USB actúa como teclado (modo HID): "escribe" el código muy rápido y
termina con Enter (``<Return>``). Por eso basta con un ``Entry`` que esté
enfocado y con el evento ``<Return>`` bindeado.

Flujo:
    escanear → Enter → buscar producto → (si es pesable, pedir peso)
    → agregar/sumar al carrito → limpiar el Entry → recuperar el foco.
    F12 → modal de pago → confirmar → VentaService.confirmar_venta()
    → reiniciar carrito y dejar listo para el siguiente cliente.

Arquitectura: la vista **no ejecuta SQL**. Solo conversa con ``VentaService``,
que orquesta la transacción (venta + detalle + descuento de stock).
"""

from __future__ import annotations

import logging
import tkinter as tk
from functools import partial
from tkinter import messagebox, ttk
from typing import Any

import customtkinter as ctk

from src.dominio.catalogos import (
    ETIQUETA_COMPROBANTE,
    MONTO_MINIMO_BOLETA_CON_DOCUMENTO,
    TIPOS_COMPROBANTE,
    TIPO_FACTURA,
    TIPO_TICKET,
    boleta_exige_documento,
    validar_compra,
)
from src.dominio.excepciones import ComprobanteInvalido, ErrorVenta, StockInsuficiente
from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.dialogos import (
    DialogoCantidad,
    DialogoEditarCantidad,
)
from src.presentacion import estilos
from src.presentacion.estilos import (
    COLOR_ACENTO,
    COLOR_ACENTO_HOVER,
    COLOR_COBRAR,
    COLOR_COBRAR_HOVER,
    COLOR_ERROR,
    COLOR_FILA_PAR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
    estilo_treeview,
)
from src.presentacion.recursos import icono
from src.servicios.venta_service import (
    METODO_CONSUMO_PROPIO,
    VentaService,
    redondear_total,
)

logger = logging.getLogger(__name__)

MONEDA = "S/"
#: Alto único de todos los campos del pie (Flat Design, alineación perfecta).
ALTO_CAMPO = 34


class VentaView(ctk.CTkFrame):
    """Pantalla de venta: escáner + carrito + total + cobro."""

    COLUMNAS = ("codigo", "nombre", "cantidad", "precio", "subtotal")
    ENCABEZADOS = ("Código", "Nombre", "Cantidad", "Precio", "Subtotal")
    ANCHOS = (160, 400, 110, 120, 140)
    ALINEACIONES = ("center", "w", "center", "e", "e")

    #: Texto de la franja de estado cuando no hay mensajes (reposo).
    _TEXTO_REPOSO = "Listo para escanear."

    def __init__(
        self,
        master: Any,
        venta_service: VentaService,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._service = venta_service

        # Carrito en memoria: {id_producto: {"producto": {...}, "cantidad": float}}
        self._carrito: dict[int, dict[str, Any]] = {}

        # Comprobante que se emitirá al cobrar. Por defecto, ticket interno: no
        # genera documento fiscal salvo que el cajero elija Boleta o Factura.
        self._etiqueta_a_tipo = {v: k for k, v in ETIQUETA_COMPROBANTE.items()}
        self._var_comprobante = tk.StringVar(value=ETIQUETA_COMPROBANTE[TIPO_TICKET])

        # Identificador del aviso efímero pendiente de limpieza.
        self._aviso_id: str | None = None
        # Reajuste vertical del carrito (debounce al redimensionar).
        self._reajuste_id: str | None = None

        self._configurar_estilos()
        self._construir_ui()
        self._vincular_atajos()
        self.bind("<Configure>", self._al_redimensionar)

        # Auto-foco del lector al cargar la vista.
        self.after(250, self.enfocar_entrada)

    def _al_redimensionar(self, event: Any = None) -> None:
        """Reevalúa el reparto vertical cuando cambia el alto disponible."""
        try:
            if getattr(event, "widget", None) is not self:
                return
        except Exception:  # noqa: BLE001
            pass
        self._ajustar_tabla()

    # ------------------------------------------------------------------ UI
    def _ajustar_tabla(self) -> None:
        """Reparte el alto de la vista entre carrito y pie (COBRAR).

        Regla de la pantalla de Caja: la cabecera de escaneo y el pie tienen
        alto **natural fijo**; el carrito recibe exactamente el resto. Así el
        botón COBRAR (72 px) nunca se comprime, ni siquiera en ventanas bajas.
        """
        if self._reajuste_id is not None:
            try:
                self.after_cancel(self._reajuste_id)
            except Exception:  # noqa: BLE001
                pass
        self._reajuste_id = self.after(20, self._reaplicar_ajuste)

    def _reaplicar_ajuste(self) -> None:
        """Compatibilidad con <Configure> (sin lógica forzada)."""
        self._reajuste_id = None
        return

    @staticmethod
    def _respiro(widget: Any) -> int:
        """Alto total del relleno (pady) con que Tk empaqueta el widget."""
        pady = widget.pack_info().get("pady", 0)
        if isinstance(pady, (tuple, list)):
            return int(pady[0]) + int(pady[1])
        return int(pady) * 2

    def _configurar_estilos(self) -> None:
        estilo_treeview("Venta.Treeview")

    # ------------------------------------------------------------- Foco
    @staticmethod
    def _encender_borde(widget: ctk.CTkEntry, _event: Any = None) -> None:
        """Borde ámbar: el campo está en foco (aquí escribe el lector)."""
        widget.configure(border_color=COLOR_ACENTO)

    @staticmethod
    def _apagar_borde(widget: ctk.CTkEntry, _event: Any = None) -> None:
        """Borde neutro: el campo perdió el foco."""
        widget.configure(border_color=estilos.BORDE_FUERTE)

    def _construir_ui(self) -> None:
        self._construir_zona_escaneo()
        # El pie se empaqueta ANTES del carrito para que reserve su alto.
        # Si va después, el carrito (expandible) se queda con todo el espacio
        # y el pie termina aplastado a 1x1 px, dejando la venta sin botón
        # COBRAR en pantallas bajas como 1366x768.
        self._construir_pie()
        self._construir_carrito()
        self.after_idle(self._ajustar_tabla)

    def _construir_zona_escaneo(self) -> None:
        zona = ctk.CTkFrame(self, fg_color="transparent")
        self._zona_escaneo = zona
        zona.pack(fill="x", padx=estilos.ESPACIO_XL, pady=(estilos.ESPACIO_XL, estilos.ESPACIO_SM))

        ctk.CTkLabel(
            zona,
            text="  Nueva Venta",
            image=icono("carrito", 26, COLOR_ACENTO),
            compound="left",
            text_color=COLOR_TEXTO,
            font=estilos.FUENTE_TITULO,
        ).pack(anchor="w")
        # Se evitan emojis fuera del BMP (U+FFFF): Tk 8.6 en Windows no los
        # renderiza de forma fiable. Los símbolos BMP (✔, ⚠, ⟳) sí funcionan.

        ctk.CTkLabel(
            zona,
            text="Escanea el código de barras, o busca por nombre y presiona Enter:",
            text_color=COLOR_SECUNDARIO,
            font=estilos.FUENTE_NORMAL,
        ).pack(anchor="w", pady=(estilos.ESPACIO_SM, estilos.ESPACIO_XS))

        fila = ctk.CTkFrame(zona, fg_color="transparent")
        fila.pack(fill="x")

        # En reposo el borde es neutro; al enfocarse se enciende en ámbar:
        # es la pista visual de "aquí escribe el lector".
        self._entrada = ctk.CTkEntry(
            fila,
            height=52,
            font=ctk.CTkFont(family=estilos.FAMILIA_MONO, size=24),
            placeholder_text="Código de barras, código interno o nombre…",
            border_color=estilos.BORDE_FUERTE,
            border_width=2,
        )
        self._entrada.pack(side="left", fill="x", expand=True)
        self._entrada.bind("<Return>", self._al_escanear)
        self._entrada.bind("<KeyRelease>", self._al_escribir)
        self._entrada.bind("<Down>", self._bajar_a_sugerencias)
        self._entrada.bind("<Escape>", lambda _e: self._ocultar_sugerencias())
        self._entrada.bind("<FocusIn>", partial(self._encender_borde, self._entrada))
        self._entrada.bind("<FocusOut>", partial(self._apagar_borde, self._entrada))

        # "Vaciar" en estilo fantasma: no compite con el COBRAR en verde.
        self._btn_vaciar = ctk.CTkButton(
            fila,
            text="  Vaciar",
            image=icono("papelera", 18, COLOR_ERROR),
            width=130,
            height=52,
            font=estilos.FUENTE_NEGRITA_13,
            fg_color="transparent",
            hover_color=estilos.COLOR_ALERTA_FUERTE,
            border_width=1,
            border_color=estilos.BORDE_FUERTE,
            text_color=COLOR_ERROR,
            command=self._vaciar_carrito,
        )
        self._btn_vaciar.pack(side="left", padx=(estilos.ESPACIO_MD, 0))

        # Franja de estado (píldora): siempre presente, con fondo tintado
        # según el tono del mensaje (verde = éxito, rojo = error, neutro =
        # reposo). Así un aviso en hora punta no se pierde como texto suelto.
        self._marco_estado = ctk.CTkFrame(
            zona,
            corner_radius=14,
            fg_color=estilos.SUPERFICIE_2,
            border_width=0,
        )
        self._lbl_estado = ctk.CTkLabel(
            self._marco_estado,
            text=self._TEXTO_REPOSO,
            text_color=COLOR_SECUNDARIO,
            font=estilos.FUENTE_ETIQUETA,
            anchor="w",
        )
        self._lbl_estado.pack(padx=estilos.ESPACIO_MD, pady=estilos.ESPACIO_XS)
        self._marco_estado.pack(anchor="w", pady=(estilos.ESPACIO_SM, 0))

        # Panel de sugerencias (autocompletado por nombre). Oculto por
        # defecto; al mostrarse se inserta SIEMPRE justo debajo del Entry
        # (before la franja de estado) para que parezca un desplegable.
        self._panel_sugerencias = ctk.CTkFrame(
            zona,
            fg_color=estilos.SUPERFICIE,
            corner_radius=estilos.RADIO_CONTROL,
            border_width=1,
            border_color=estilos.BORDE,
        )
        self._lista_sugerencias = tk.Listbox(
            self._panel_sugerencias,
            height=1,  # se ajusta dinámicamente en _al_escribir
            activestyle="none",
            font=estilos.FUENTE_ETIQUETA,
            bd=0,
            highlightthickness=0,
            background=estilos.SUPERFICIE,
            foreground=COLOR_TEXTO,
            selectbackground=COLOR_ACENTO,
            selectforeground="#ffffff",
        )
        self._lista_sugerencias.pack(fill="x", padx=estilos.ESPACIO_XS, pady=estilos.ESPACIO_XS)
        self._lista_sugerencias.bind("<Return>", self._elegir_de_lista)
        self._lista_sugerencias.bind("<ButtonRelease-1>", self._elegir_de_lista)
        self._lista_sugerencias.bind("<Escape>", lambda _e: self._volver_a_entrada())
        # Productos mostrados actualmente en el panel (paralelo a las filas).
        self._sugerencias: list[dict[str, Any]] = []

    def _construir_carrito(self) -> None:
        contenedor = ctk.CTkFrame(self, fg_color="transparent")
        contenedor.pack(fill="both", expand=True, padx=estilos.ESPACIO_XL, pady=(estilos.ESPACIO_XS, estilos.ESPACIO_XS))

        self._tabla = ttk.Treeview(
            contenedor,
            columns=self.COLUMNAS,
            show="headings",
            style="Venta.Treeview",
        )
        for columna, titulo, ancho, alineacion in zip(
            self.COLUMNAS, self.ENCABEZADOS, self.ANCHOS, self.ALINEACIONES
        ):
            self._tabla.heading(columna, text=titulo, anchor="center")
            self._tabla.column(
                columna,
                width=ancho,
                anchor=alineacion,
                stretch=(columna == "nombre"),
                minwidth=70,
            )
        self._tabla.pack(side="left", fill="both", expand=True)

        barra = ttk.Scrollbar(contenedor, orient="vertical", command=self._tabla.yview)
        self._tabla.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")

        self._tabla.tag_configure("par", background=COLOR_FILA_PAR)
        self._tabla.tag_configure("impar", background="#ffffff")

        # Supr quita la línea seleccionada (útil sin mouse).
        self._tabla.bind("<Delete>", self._quitar_seleccionado)
        # Edición rápida: doble clic o F2 (RNF-07)
        self._tabla.bind("<Double-1>", self._editar_seleccionado)
        self._tabla.bind("<F2>", self._editar_seleccionado)

    def _construir_pie(self) -> None:
        pie = ctk.CTkFrame(self, fg_color="transparent")
        self._pie = pie
        pie.pack(
            side="bottom",
            fill="x",
            padx=estilos.ESPACIO_XL,
            pady=(estilos.ESPACIO_XS, estilos.ESPACIO_XL),
        )

        # Los atajos van en su propia línea: en un monitor de 1366x768 el
        # bloque de cobro ocupa casi todo el ancho útil y, compartiendo fila,
        # empujaba el botón COBRAR fuera de la ventana.
        self._lbl_atajos = ctk.CTkLabel(
            pie,
            text="Enter: agregar   ·   Supr: quitar   ·   F2: editar cant.   ·   F12: cobrar",
            text_color=COLOR_SECUNDARIO,
            font=estilos.FUENTE_ETIQUETA,
            anchor="w",
        )
        self._lbl_atajos.pack(fill="x")

        # Todos los datos del pie viven en UNA sola rejilla: etiqueta arriba,
        # campo abajo y la misma altura para los cinco controles. Así dejan de
        # "flotar" y la vista conserva una línea base común.
        banda_datos = ctk.CTkFrame(pie, fg_color="transparent")
        banda_datos.pack(fill="x", pady=(estilos.ESPACIO_XS, 0))
        self._construir_fila_comprobante(banda_datos)

        # Total y COBRAR comparten la misma fila: la altura de la fila la fija
        # el botón (72 px) y el bloque del total queda centrado verticalmente.
        banda_cobro = ctk.CTkFrame(pie, fg_color="transparent")
        banda_cobro.pack(fill="x", pady=(estilos.ESPACIO_SM, 0))
        banda_cobro.grid_columnconfigure(0, weight=1)

        bloque_total = ctk.CTkFrame(banda_cobro, fg_color="transparent")
        bloque_total.grid(
            row=0, column=0, sticky="e", padx=(0, estilos.ESPACIO_LG)
        )

        ctk.CTkLabel(
            bloque_total,
            text="TOTAL A PAGAR",
            text_color=COLOR_SECUNDARIO,
            font=estilos.FUENTE_MICRO,
        ).pack(anchor="e")
        self._lbl_total = ctk.CTkLabel(
            bloque_total,
            text=f"{MONEDA} 0.00",
            text_color=COLOR_ACENTO,
            font=estilos.FUENTE_DISPLAY,
        )
        self._lbl_total.pack(anchor="e")

        self._lbl_vuelto = ctk.CTkLabel(
            bloque_total,
            text="",
            text_color=COLOR_OK,
            font=estilos.FUENTE_SECCION,
        )
        self._lbl_vuelto.pack(anchor="e")

        self._btn_cobrar = ctk.CTkButton(
            banda_cobro,
            text="  COBRAR  (F12)",
            image=icono("efectivo", 26, "#ffffff"),
            width=250,
            height=72,
            font=estilos.FUENTE_CTA,
            fg_color=COLOR_COBRAR,
            hover_color=COLOR_COBRAR_HOVER,
            command=self._al_cobrar,
        )
        self._btn_cobrar.grid(row=0, column=1, sticky="e")

    def _construir_fila_comprobante(self, padre: Any) -> None:
        """Rejilla única para comprobante, cliente, documento y pago.

        Reglas que refleja (las valida también el servicio):

        * **Ticket interno** → documento de control, sin datos fiscales.
        * **Boleta** → DNI/RUC obligatorio desde S/ 700,00 (umbral inclusivo).
        * **Factura** → RUC (11 dígitos) y razón social obligatorios.
        """
        fila = ctk.CTkFrame(padre, fg_color="transparent")
        fila.pack(fill="x", pady=(0, estilos.ESPACIO_SM))
        # Solo Cliente absorbe el ancho sobrante; el resto conserva su medida.
        fila.grid_columnconfigure(1, weight=1)

        etiqueta = {
            "text_color": COLOR_SECUNDARIO,
            "font": estilos.FUENTE_ETIQUETA,
        }

        # --- Fila 0: rótulos -------------------------------------------------
        ctk.CTkLabel(fila, text="Comprobante:", **etiqueta).grid(
            row=0, column=0, sticky="w", pady=(0, estilos.ESPACIO_XS)
        )
        ctk.CTkLabel(fila, text="Cliente:", **etiqueta).grid(
            row=0, column=1, sticky="w", pady=(0, estilos.ESPACIO_XS)
        )
        ctk.CTkLabel(fila, text="DNI / RUC:", **etiqueta).grid(
            row=0, column=2, sticky="w", pady=(0, estilos.ESPACIO_XS)
        )
        ctk.CTkLabel(fila, text="Método de pago:", **etiqueta).grid(
            row=0, column=3, sticky="w", pady=(0, estilos.ESPACIO_XS)
        )
        ctk.CTkLabel(fila, text="S/ Recibido:", **etiqueta).grid(
            row=0, column=4, sticky="w", pady=(0, estilos.ESPACIO_XS)
        )

        # --- Fila 1: campos (todos con ALTO_CAMPO) ---------------------------
        self._menu_comprobante = ctk.CTkOptionMenu(
            fila,
            values=[ETIQUETA_COMPROBANTE[tipo] for tipo in TIPOS_COMPROBANTE],
            variable=self._var_comprobante,
            width=150,
            height=ALTO_CAMPO,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
            command=self._al_cambiar_comprobante,
        )
        self._menu_comprobante.grid(
            row=1, column=0, sticky="ew", padx=(0, estilos.ESPACIO_MD)
        )

        self._entrada_cliente = ctk.CTkEntry(
            fila,
            width=240,
            height=ALTO_CAMPO,
            placeholder_text="Nombre o razón social",
            font=estilos.FUENTE_ETIQUETA,
            state="disabled",
        )
        self._entrada_cliente.grid(
            row=1, column=1, sticky="ew", padx=(0, estilos.ESPACIO_MD)
        )

        self._entrada_doc = ctk.CTkEntry(
            fila,
            width=130,
            height=ALTO_CAMPO,
            placeholder_text="Nº doc.",
            font=(estilos.FAMILIA_MONO, 13),
            justify="center",
            state="disabled",
        )
        self._entrada_doc.grid(
            row=1, column=2, sticky="ew", padx=(0, estilos.ESPACIO_MD)
        )

        self._var_metodo_pago = tk.StringVar(value="Efectivo")
        self._menu_metodo = ctk.CTkOptionMenu(
            fila,
            values=["Efectivo", "Yape/Plin", "Tarjeta", "Consumo propio"],
            variable=self._var_metodo_pago,
            width=150,
            height=ALTO_CAMPO,
            fg_color=COLOR_ACENTO,
            button_color=COLOR_ACENTO_HOVER,
            button_hover_color=COLOR_ACENTO_HOVER,
        )
        self._menu_metodo.grid(
            row=1, column=3, sticky="ew", padx=(0, estilos.ESPACIO_MD)
        )

        self._var_monto_recibido = tk.StringVar(value="")
        self._entrada_monto = ctk.CTkEntry(
            fila,
            textvariable=self._var_monto_recibido,
            width=100,
            height=ALTO_CAMPO,
            justify="center",
            font=(estilos.FAMILIA_MONO, 14, "bold"),
            border_color=estilos.BORDE_FUERTE,
            border_width=2,
        )
        self._entrada_monto.grid(row=1, column=4, sticky="ew")
        self._entrada_monto.bind("<KeyRelease>", self._actualizar_vuelto_tiempo_real)
        # Mismo foco que el escáner: ámbar solo cuando el dato se está editando.
        self._entrada_monto.bind("<FocusIn>", partial(self._encender_borde, self._entrada_monto))
        self._entrada_monto.bind("<FocusOut>", partial(self._apagar_borde, self._entrada_monto))

        # Aviso contextual: recuerda al cajero qué le falta pedir al cliente.
        # Ocupa su propia línea para no desalinear los campos y se oculta en
        # ventanas angostas para no robar altura al carrito.
        self._lbl_comprobante = ctk.CTkLabel(
            fila, text="", text_color=COLOR_SECUNDARIO, font=estilos.FUENTE_PEQUENA
        )
        self._lbl_comprobante.grid(
            row=2,
            column=0,
            columnspan=5,
            sticky="w",
            pady=(estilos.ESPACIO_XS, 0),
        )
        self._aviso_comprobante_visible = True
        fila.bind("<Configure>", self._al_redimensionar_comprobante)
        self._actualizar_aviso_comprobante()

    def _al_redimensionar_comprobante(self, event: Any = None) -> None:
        """Muestra u oculta el aviso fiscal según el ancho disponible."""
        ancho = int(getattr(event, "width", 0) or 0)
        if ancho <= 0:
            return
        visible = ancho >= 980
        if visible == self._aviso_comprobante_visible:
            return
        self._aviso_comprobante_visible = visible
        if visible:
            self._lbl_comprobante.grid()
        else:
            self._lbl_comprobante.grid_remove()

    def _tipo_comprobante(self) -> str:
        """Tipo de comprobante elegido en el selector."""
        return self._etiqueta_a_tipo.get(self._var_comprobante.get(), TIPO_TICKET)

    def _al_cambiar_comprobante(self, _valor: Any = None) -> None:
        """Habilita/limpia los datos del cliente según el comprobante elegido."""
        tipo = self._tipo_comprobante()
        requiere_datos = tipo != TIPO_TICKET
        estado = "normal" if requiere_datos else "disabled"
        for entrada in (self._entrada_cliente, self._entrada_doc):
            entrada.configure(state=estado)
        if not requiere_datos:
            self._entrada_cliente.delete(0, "end")
            self._entrada_doc.delete(0, "end")
        self._actualizar_aviso_comprobante()

    def _actualizar_aviso_comprobante(self) -> None:
        """Actualiza el aviso del comprobante (depende del total del carrito)."""
        tipo = self._tipo_comprobante()
        if tipo == TIPO_TICKET:
            texto, color = "Ticket interno: documento de control (no fiscal).", COLOR_SECUNDARIO
        elif tipo == TIPO_FACTURA:
            texto, color = "Requiere razón social y RUC de 11 dígitos.", COLOR_SECUNDARIO
        else:
            total = redondear_total(self._service.calcular_total(self._carrito))
            if boleta_exige_documento(total):
                texto = (
                    f"Boleta de {MONEDA} "
                    f"{MONTO_MINIMO_BOLETA_CON_DOCUMENTO:,.2f} o más: exige DNI o RUC."
                )
                color = COLOR_ERROR
            else:
                texto, color = "DNI opcional (solo si el cliente lo pide).", COLOR_SECUNDARIO
        self._lbl_comprobante.configure(text=texto, text_color=color)

    def _actualizar_vuelto_tiempo_real(self, _event: Any = None) -> None:
        """Calcula el vuelto basándose en el total actual y el monto ingresado."""
        try:
            total = redondear_total(self._service.calcular_total(self._carrito))
            recibido_txt = self._var_monto_recibido.get().strip().replace(",", ".")
            if not recibido_txt:
                self._lbl_vuelto.configure(text="")
                return

            recibido = float(recibido_txt)
            vuelto = recibido - total

            if vuelto < 0:
                self._lbl_vuelto.configure(text=f"Falta: {MONEDA} {abs(vuelto):,.2f}", text_color=COLOR_ERROR)
            else:
                self._lbl_vuelto.configure(text=f"Vuelto: {MONEDA} {vuelto:,.2f}", text_color=COLOR_OK)
        except ValueError:
            self._lbl_vuelto.configure(text="Monto inválido", text_color=COLOR_ERROR)

    def _al_cobrar(self, _event: Any = None) -> str:
        """Registra la venta directamente desde la pantalla principal (F12 / botón COBRAR)."""
        if not self._carrito:
            self._mostrar_estado("El carrito está vacío.", error=True)
            self.enfocar_entrada()
            return "break"

        try:
            total = self._service.calcular_total(self._carrito)
            total_a_pagar = redondear_total(total)
            metodo = self._var_metodo_pago.get()
            recibido_txt = self._var_monto_recibido.get().strip().replace(",", ".")

            if metodo == METODO_CONSUMO_PROPIO:
                # Uso interno de la panadería: no hay cobro al cliente.
                # Se registra como pago exacto (solo descuenta stock).
                monto_recibido = total_a_pagar
            else:
                if not recibido_txt:
                    self._mostrar_estado("Ingrese el monto recibido.", error=True)
                    self._entrada_monto.focus_set()
                    return "break"

                monto_recibido = float(recibido_txt)

                if monto_recibido < total_a_pagar - 0.01:
                    self._mostrar_estado(f"Monto insuficiente. Falta {MONEDA} {total_a_pagar - monto_recibido:,.2f}", error=True)
                    self._entrada_monto.focus_set()
                    return "break"

            # Comprobante: valida los datos del cliente con las MISMAS reglas que
            # el servicio (DNI/RUC obligatorio según tipo y tope de S/ 700).
            tipo_comprobante = self._tipo_comprobante()
            cliente = self._entrada_cliente.get().strip()
            documento = self._entrada_doc.get().strip()
            try:
                validar_compra(tipo_comprobante, total_a_pagar, cliente, documento)
            except ComprobanteInvalido as exc:
                self._mostrar_estado(str(exc), error=True)
                objetivo = self._entrada_cliente if not cliente else self._entrada_doc
                objetivo.focus_set()
                return "break"

            # Confirmar venta (Pago simple). Se pasa el total SIN redondear: el
            # servicio valida contra el detalle del carrito y aplica el redondeo.
            venta = self._service.confirmar_venta(
                self._carrito,
                metodo_pago=metodo,
                total=total,
                monto_recibido=monto_recibido,
                cliente=cliente,
                dni_cliente=documento,
                tipo_comprobante=tipo_comprobante,
            )
        except (ValueError, StockInsuficiente, ErrorConexionBD, ErrorVenta) as exc:
            logger.warning("No se pudo cobrar: %s", exc)
            messagebox.showerror("Error al cobrar", str(exc))
            self.enfocar_entrada()
            return "break"
        except Exception as exc:
            logger.exception("Error inesperado al cobrar.")
            messagebox.showerror("Error inesperado", str(exc))
            self.enfocar_entrada()
            return "break"

        # Reinicio y aviso
        self._reiniciar_tras_cobro()
        self._var_monto_recibido.set("")
        self._lbl_vuelto.configure(text="")
        if venta.numero_comprobante:
            referencia = f"{venta.tipo_comprobante} {venta.numero_comprobante}"
        else:
            referencia = f"Ticket {venta.numero_ticket}"
        aviso = (
            f"✔  {referencia} · Ticket {venta.numero_ticket} · "
            f"Vuelto: {MONEDA} {venta.vuelto:,.2f}"
        )
        # El selector vuelve a Ticket interno: se avisa explícitamente para que
        # el cajero no crea que sigue emitiendo boleta/factura con el siguiente
        # cliente (fue justo lo que confundió la prueba en vivo).
        if tipo_comprobante != TIPO_TICKET:
            aviso += (
                f"   ·   Comprobante vuelto a: {ETIQUETA_COMPROBANTE[TIPO_TICKET]}"
            )
        self._mostrar_aviso(aviso, ms=6000)
        self.enfocar_entrada()
        return "break"

    # -------------------------------------------------------------- Render
    def _refrescar_linea(self, producto_id: int) -> None:
        """Inserta o actualiza una línea de la tabla para el producto dado."""
        item = self._carrito[producto_id]
        producto = item["producto"]
        cantidad = item["cantidad"]
        precio = float(producto.get("precio_venta") or 0)
        subtotal = cantidad * precio

        valores = (
            producto.get("codigo_barras") or producto.get("codigo_interno") or "—",
            producto.get("nombre", ""),
            self._formatear_cantidad(cantidad, producto.get("unidad_medida", "UND")),
            f"{MONEDA} {precio:,.2f}",
            f"{MONEDA} {subtotal:,.2f}",
        )

        iid = str(producto_id)
        if self._tabla.exists(iid):
            self._tabla.item(iid, values=valores)
        else:
            self._tabla.insert(
                "",
                "end",
                iid=iid,
                values=valores,
                tags=("par" if len(self._tabla.get_children()) % 2 == 0 else "impar",),
            )

    @staticmethod
    def _formatear_cantidad(cantidad: float, unidad_medida: str = "UND") -> str:
        """Formatea la cantidad: ``2`` para unidades, ``0.75 kg`` para pesables."""
        texto = f"{cantidad:.3f}".rstrip("0").rstrip(".") or "0"
        unidad = (unidad_medida or "UND").upper()
        return f"{texto} {unidad.lower()}" if unidad != "UND" else texto

    def _actualizar_total(self) -> None:
        """Muestra el total a pagar ya redondeado a 10 céntimos (igual que el cobro)."""
        total = redondear_total(self._service.calcular_total(self._carrito))
        self._lbl_total.configure(text=f"{MONEDA} {total:,.2f}")
        # El aviso de la boleta depende del total (tope de S/ 700).
        self._actualizar_aviso_comprobante()

    def _pintar_estado(self, texto: str, *, exito: bool | None) -> None:
        """Da estilo a la franja de estado según el tono del mensaje.

        Args:
            texto: mensaje a mostrar (o el de reposo).
            exito: ``True`` verde, ``False`` rojo, ``None`` neutro (reposo).
        """
        if exito is True:
            fondo, color = estilos.COLOR_OK_SUAVE, COLOR_OK
        elif exito is False:
            fondo, color = estilos.COLOR_ALERTA_FUERTE, COLOR_ERROR
        else:
            fondo, color = estilos.COLOR_ESTADO_NEUTRO, COLOR_TEXTO
        self._marco_estado.configure(fg_color=fondo)
        self._lbl_estado.configure(text=texto, text_color=color)

    def _mostrar_estado(self, texto: str, error: bool = False) -> None:
        """Muestra un mensaje permanente (se cancela cualquier aviso efímero)."""
        self._cancelar_aviso_pendiente()
        self._pintar_estado(texto, exito=not error)

    def _mostrar_aviso(self, texto: str, error: bool = False, ms: int = 5000) -> None:
        """Mensaje efímero: se muestra y se limpia solo tras ``ms`` milisegundos.

        Ideal para confirmaciones de venta sin robar el foco ni pedir clics.
        """
        self._cancelar_aviso_pendiente()
        self._pintar_estado(texto, exito=not error)
        self._aviso_id = self.after(ms, lambda: self._limpiar_aviso(texto))

    def _cancelar_aviso_pendiente(self) -> None:
        if self._aviso_id is not None:
            try:
                self.after_cancel(self._aviso_id)
            except Exception:  # noqa: BLE001 - el timer pudo haberse disparado ya
                pass
            self._aviso_id = None

    def _limpiar_aviso(self, texto_previo: str) -> None:
        """Limpia el aviso solo si sigue mostrándose el mismo texto."""
        self._aviso_id = None
        if self._lbl_estado.cget("text") == texto_previo:
            self._pintar_estado(self._TEXTO_REPOSO, exito=None)

    # -------------------------------------------------------------- Atajos
    def _vincular_atajos(self) -> None:
        """Vincula atajos de teclado a la vista."""
        # F12 = Cobrar (vinculado al toplevel para que funcione desde cualquier widget)
        toplevel = self.winfo_toplevel()
        toplevel.bind("<F12>", self._al_cobrar)
        # F5 = Recargar catálogo
        toplevel.bind("<F5>", lambda _e: self._mostrar_estado("Catálogo recargado", error=False))

    # ----------------------------------------------------------- Lógica escaneo
    def _al_escanear(self, event: Any = None) -> str:
        """Procesa el código escaneado/tecleado en el Entry."""
        codigo = self._entrada.get().strip()
        if not codigo:
            return "break"
        self._entrada.delete(0, "end")
        self._buscar_y_agregar(codigo)
        return "break"

    def _al_escribir(self, event: Any = None) -> None:
        """Filtra sugerencias mientras el usuario escribe (búsqueda por nombre)."""
        texto = self._entrada.get().strip()
        if len(texto) < 2:
            self._ocultar_sugerencias()
            return
        # Buscar productos que coincidan (delegado al servicio)
        self._sugerencias = self._service.buscar_productos(texto, limite=8)
        if not self._sugerencias:
            self._ocultar_sugerencias()
            return
        self._lista_sugerencias.delete(0, "end")
        for p in self._sugerencias:
            etiqueta = f"{p.get('codigo_interno','')}  {p.get('nombre','')}  S/ {float(p.get('precio_venta',0)):,.2f}"
            self._lista_sugerencias.insert("end", etiqueta)
        # Altura dinámica: 1 fila por resultado, máx. 6
        num_filas = min(len(self._sugerencias), 6)
        self._lista_sugerencias.configure(height=num_filas)
        # Mostrar panel justo debajo del Entry (antes de la franja de estado)
        self._panel_sugerencias.pack(
            fill="x", padx=0, pady=(estilos.ESPACIO_SM, 0), before=self._marco_estado
        )

    def _bajar_a_sugerencias(self, event: Any = None) -> str:
        if self._sugerencias:
            self._lista_sugerencias.focus_set()
            self._lista_sugerencias.selection_set(0)
            self._lista_sugerencias.activate(0)
        return "break"

    def _ocultar_sugerencias(self) -> None:
        self._panel_sugerencias.pack_forget()
        self._sugerencias.clear()

    def _elegir_de_lista(self, event: Any = None) -> str:
        sel = self._lista_sugerencias.curselection()
        if not sel:
            return "break"
        idx = sel[0]
        if idx < len(self._sugerencias):
            producto = self._sugerencias[idx]
            self._ocultar_sugerencias()
            self._agregar_al_carrito(producto)
        self.enfocar_entrada()
        return "break"

    def _volver_a_entrada(self, event: Any = None) -> str:
        self._ocultar_sugerencias()
        self.enfocar_entrada()
        return "break"

    def _buscar_y_agregar(self, codigo: str) -> None:
        """Busca por código exacto (barras/interno) y agrega al carrito."""
        # Búsqueda exacta por código de barras o interno
        producto = self._service.producto_repo.buscar_por_codigo(codigo)
        if producto:
            self._agregar_al_carrito(producto)
            return
        # Fallback: búsqueda parcial por nombre (muestra sugerencias)
        self._al_escribir()

    def _agregar_al_carrito(self, producto: dict[str, Any]) -> None:
        """Agrega producto al carrito o suma cantidad si ya existe."""
        pid = producto["id"]
        if producto.get("es_pesable"):
            dlg = DialogoCantidad(self, producto["nombre"], producto.get("unidad_medida", "UND"))
            cantidad = dlg.mostrar()
            if cantidad is None:
                self.enfocar_entrada()
                return
        else:
            cantidad = 1
        if pid in self._carrito:
            self._carrito[pid]["cantidad"] += cantidad
        else:
            self._carrito[pid] = {"producto": producto, "cantidad": cantidad}
        self._refrescar_linea(pid)
        self._actualizar_total()
        # Limpia el buscador y las sugerencias para escribir el siguiente producto.
        self._entrada.delete(0, "end")
        self._ocultar_sugerencias()
        self._mostrar_estado(f"Agregado: {producto['nombre']}", error=False)
        self.enfocar_entrada()

    def _vaciar_carrito(self) -> None:
        """Vacía completamente el carrito."""
        if not self._carrito:
            return
        self._carrito.clear()
        for iid in self._tabla.get_children():
            self._tabla.delete(iid)
        self._actualizar_total()
        self._mostrar_estado("Carrito vaciado", error=False)
        self.enfocar_entrada()

    def _quitar_seleccionado(self, event: Any = None) -> str:
        sel = self._tabla.selection()
        if not sel:
            return "break"
        pid = int(sel[0])
        nombre = self._carrito[pid]["producto"]["nombre"]
        del self._carrito[pid]
        self._tabla.delete(sel[0])
        self._actualizar_total()
        self._mostrar_estado(f"Quitado: {nombre}", error=False)
        self.enfocar_entrada()
        return "break"

    def _editar_seleccionado(self, event: Any = None) -> str:
        sel = self._tabla.selection()
        if not sel:
            return "break"
        pid = int(sel[0])
        item = self._carrito[pid]
        producto = item["producto"]
        dlg = DialogoEditarCantidad(
            self,
            producto["nombre"],
            item["cantidad"],
            producto.get("unidad_medida", "UND"),
        )
        nueva = dlg.mostrar()
        if nueva is not None:
            item["cantidad"] = nueva
            self._refrescar_linea(pid)
            self._actualizar_total()
            self._mostrar_estado(f"Cantidad actualizada: {producto['nombre']}", error=False)
        self.enfocar_entrada()
        return "break"

    def _reiniciar_tras_cobro(self) -> None:
        """Limpia el carrito y la UI tras una venta exitosa."""
        self._carrito.clear()
        for iid in self._tabla.get_children():
            self._tabla.delete(iid)
        # El comprobante vuelve a su valor por defecto (ticket interno) y los
        # datos del cliente se limpian para el siguiente comprador.
        self._var_comprobante.set(ETIQUETA_COMPROBANTE[TIPO_TICKET])
        self._al_cambiar_comprobante()
        self._actualizar_total()

    def enfocar_entrada(self) -> None:
        """Pone el foco en el Entry del escáner."""
        self._entrada.focus_set()
