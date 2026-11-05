"""Componentes de interfaz reutilizables — capa de PRESENTACIÓN.

Widgets de alto nivel construidos sobre CustomTkinter y el sistema de diseño
(:mod:`src.presentacion.estilos`) más los recursos gráficos generados con
Pillow (:mod:`src.presentacion.recursos`).

Contenido:
    * :class:`Tarjeta` — panel blanco con sombra visual (borde suave).
    * :class:`TarjetaKPI` — tarjeta de indicador (título + valor + icono).
    * :class:`BarraAcciones` — barra de herramientas con jerarquía y menú «Más…».
    * :class:`BarraLateral` — navegación lateral con logo e iconos.
    * :class:`EncabezadoSuperior` — barra superior con la marca y el reloj.
"""

from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Sequence

import customtkinter as ctk

from src.presentacion import estilos
from src.presentacion.recursos import icono, logo_ctk

logger = logging.getLogger(__name__)


class Tarjeta(ctk.CTkFrame):
    """Panel blanco redondeado con borde suave, para agrupar contenido."""

    def __init__(self, master, *, relleno: int = 16, radio: int = 12, **kwargs) -> None:
        kwargs.setdefault("fg_color", estilos.SUPERFICIE)
        kwargs.setdefault("corner_radius", radio)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", estilos.BORDE)
        super().__init__(master, **kwargs)
        self._relleno = relleno


class TarjetaKPI(ctk.CTkFrame):
    """Indicador compacto: icono + título + valor, con franja de color."""

    def __init__(
        self,
        master,
        titulo: str,
        valor: str,
        *,
        nombre_icono: str = "grafico",
        color_acento: str = estilos.COLOR_ACENTO,
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", estilos.SUPERFICIE)
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", estilos.BORDE)
        super().__init__(master, **kwargs)

        self.grid_columnconfigure(1, weight=1)

        # Franja de acento a la izquierda.
        franja = ctk.CTkFrame(self, width=5, corner_radius=0, fg_color=color_acento)
        franja.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 12), pady=14)

        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.grid(row=0, column=1, sticky="ew", pady=(14, 0))
        cabecera.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            cabecera,
            image=icono(nombre_icono, 18, color_acento),
            text="",
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkLabel(
            cabecera,
            text=titulo.upper(),
            font=(estilos.FAMILIA, 11, "bold"),
            text_color=estilos.COLOR_SECUNDARIO,
            anchor="w",
        ).grid(row=0, column=1, sticky="w")

        self._valor = ctk.CTkLabel(
            self,
            text=valor,
            font=(estilos.FAMILIA, 26, "bold"),
            text_color=estilos.COLOR_TEXTO,
            anchor="w",
        )
        self._valor.grid(row=1, column=1, sticky="w", pady=(0, 16), padx=(34, 0))

    def actualizar(self, valor: str) -> None:
        self._valor.configure(text=valor)


class EncabezadoSuperior(ctk.CTkFrame):
    """Barra superior: marca a la izquierda, reloj y estado a la derecha."""

    def __init__(self, master, *, negocio: str, caja: str = "Caja 1", **kwargs) -> None:
        kwargs.setdefault("fg_color", estilos.SUPERFICIE)
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("height", 66)
        super().__init__(master, **kwargs)
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1)

        # --- Marca ---------------------------------------------------
        marca = ctk.CTkFrame(self, fg_color="transparent")
        marca.grid(row=0, column=0, sticky="w", padx=(estilos.ESPACIO_LG, 0), pady=10)

        ctk.CTkLabel(marca, image=logo_ctk(38), text="").grid(row=0, column=0, rowspan=2, padx=(0, estilos.ESPACIO_MD))

        ctk.CTkLabel(
            marca,
            text=negocio,
            font=estilos.FUENTE_MARCA,
            text_color=estilos.COLOR_TEXTO,
            anchor="w",
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            marca,
            text="PUNTO DE VENTA",
            font=estilos.FUENTE_MICRO,
            text_color=estilos.COLOR_SECUNDARIO,
            anchor="w",
        ).grid(row=1, column=1, sticky="w")

        # --- Reloj y estado -----------------------------------------
        derecha = ctk.CTkFrame(self, fg_color="transparent")
        derecha.grid(row=0, column=2, sticky="e", padx=(0, estilos.ESPACIO_LG))

        self._reloj = ctk.CTkLabel(
            derecha,
            text="",
            font=estilos.FUENTE_SECCION,
            text_color=estilos.COLOR_TEXTO,
        )
        self._reloj.pack(side="left", padx=(0, estilos.ESPACIO_MD))
        self._fecha = ctk.CTkLabel(
            derecha,
            text="",
            font=estilos.FUENTE_PEQUENA,
            text_color=estilos.COLOR_SECUNDARIO,
        )
        self._fecha.pack(side="left", padx=(0, estilos.ESPACIO_MD))

        # Chip de estado de la caja (píldora verde: "todo en orden").
        chip = ctk.CTkFrame(derecha, fg_color=estilos.COLOR_OK_SUAVE, corner_radius=999)
        chip.pack(side="left")
        ctk.CTkLabel(
            chip,
            text=f"●  {caja}",
            font=(estilos.FAMILIA, 12, "bold"),
            text_color=estilos.COLOR_OK_HOVER,
            padx=10,
            pady=4,
        ).pack()

        # Línea inferior (borde).
        ctk.CTkFrame(self, height=1, fg_color=estilos.BORDE, corner_radius=0).place(
            relx=0, rely=1.0, relwidth=1.0, anchor="sw"
        )

        self._programar_reloj()

    def _programar_reloj(self) -> None:
        from datetime import datetime

        ahora = datetime.now()
        self._reloj.configure(text=ahora.strftime("%H:%M"))
        self._fecha.configure(text=ahora.strftime("%a %d %b").capitalize())
        self.after(20000, self._programar_reloj)


@dataclass(frozen=True)
class Accion:
    """Acción de una :class:`BarraAcciones`.

    Attributes:
        texto: etiqueta visible en el botón o en el menú.
        comando: callback que ejecuta la acción.
        icono: nombre del icono en :mod:`src.presentacion.recursos`.
        peligro: marca la acción como destructiva (texto rojo).
    """

    texto: str
    comando: Callable[[], None]
    icono: str | None = None
    peligro: bool = False


class BarraAcciones(ctk.CTkFrame):
    """Barra de herramientas con jerarquía y desbordamiento.

    Reglas del sistema de diseño:

    * **Una sola acción primaria** (sólida, verde) por pantalla.
    * Las acciones frecuentes van como botones fantasma (contorno neutro).
    * Las acciones ocasionales viven en el menú «Más…», que nunca se
      comprime aunque la ventana sea estrecha.
    * La zona izquierda (``barra.izquierda``) aloja el buscador y los filtros:
      es la que absorbe el espacio sobrante, no los botones.

    Así ninguna barra depende de que todos los botones "quepan" a la vez.
    """

    _ALTO = 34
    _ANCHO_PRIMARIA = 170
    _ANCHO_SECUNDARIA = 130
    _ANCHO_MENU = 110

    def __init__(
        self,
        master,
        *,
        primaria: Accion | None = None,
        secundarias: Sequence[Accion] = (),
        menu: Sequence[Accion] = (),
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

        self.botones: dict[str, ctk.CTkButton] = {}
        self.menu_textos: list[str] = []
        self._menu: tk.Menu | None = None
        self._btn_mas: ctk.CTkButton | None = None

        # Zona izquierda: buscador/filtros. Crece con la ventana.
        # width/height iniciales = 1 para que una barra sin contenido a la
        # izquierda no herede el tamaño por defecto de CTkFrame (200x200).
        self.izquierda = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        self.izquierda.pack(side="left", fill="x", expand=True)

        # Zona derecha: primaria al extremo, luego el menú y las secundarias.
        derecha = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        derecha.pack(side="right")

        if primaria is not None:
            self._crear_boton(derecha, primaria, primaria=True)
        if menu:
            self._crear_menu(derecha, menu)
        for accion in reversed(secundarias):
            self._crear_boton(derecha, accion, primaria=False)

    # ------------------------------------------------------------------ piezas
    def _crear_boton(self, padre, accion: Accion, *, primaria: bool) -> None:
        if primaria:
            fondo, hover, texto = estilos.COLOR_OK, estilos.COLOR_OK_HOVER, "#ffffff"
            icono_color = "#ffffff"
            ancho, borde = self._ANCHO_PRIMARIA, 0
            separacion = estilos.ESPACIO_MD
        else:
            # Secundarias: botón fantasma con cuerpo blanco y contorno suave.
            if accion.peligro:
                fondo, hover, texto = estilos.SUPERFICIE, estilos.COLOR_ALERTA_FUERTE, estilos.COLOR_ERROR
                icono_color = estilos.COLOR_ERROR
            else:
                fondo, hover, texto = estilos.SUPERFICIE, estilos.SUPERFICIE_2, estilos.COLOR_TEXTO
                icono_color = estilos.COLOR_SECUNDARIO
            ancho, borde = self._ANCHO_SECUNDARIA, 1
            separacion = estilos.ESPACIO_SM

        boton = ctk.CTkButton(
            padre,
            text=f"  {accion.texto}",
            image=icono(accion.icono, 16, icono_color) if accion.icono else None,
            width=ancho,
            height=self._ALTO,
            font=estilos.FUENTE_NEGRITA_13,
            corner_radius=estilos.RADIO_CONTROL,
            fg_color=fondo,
            hover_color=hover,
            text_color=texto,
            border_width=borde,
            border_color=estilos.BORDE_FUERTE,
            command=accion.comando,
        )
        boton.pack(side="right", padx=(separacion, 0))
        self.botones[accion.texto] = boton

    def _crear_menu(self, padre, acciones: Sequence[Accion]) -> None:
        self._menu = tk.Menu(
            self,
            tearoff=0,
            bd=0,
            relief="flat",
            bg=estilos.SUPERFICIE,
            fg=estilos.COLOR_TEXTO,
            activebackground=estilos.COLOR_ACENTO_SUAVE,
            activeforeground=estilos.COLOR_TEXTO,
            font=estilos.FUENTE_NORMAL,
        )
        for accion in acciones:
            opciones: dict = {"label": accion.texto, "command": accion.comando}
            if accion.peligro:
                opciones["foreground"] = estilos.COLOR_ERROR
            self._menu.add_command(**opciones)
            self.menu_textos.append(accion.texto)

        self._btn_mas = ctk.CTkButton(
            padre,
            text="  Más",
            image=icono("chevron", 12, estilos.COLOR_SECUNDARIO),
            compound="right",
            width=self._ANCHO_MENU,
            height=self._ALTO,
            font=estilos.FUENTE_NEGRITA_13,
            corner_radius=estilos.RADIO_CONTROL,
            fg_color=estilos.SUPERFICIE,
            hover_color=estilos.SUPERFICIE_2,
            text_color=estilos.COLOR_TEXTO,
            border_width=1,
            border_color=estilos.BORDE_FUERTE,
            command=self._mostrar_menu,
        )
        self._btn_mas.pack(side="right", padx=(estilos.ESPACIO_SM, 0))
        self.botones["Más"] = self._btn_mas

    def _mostrar_menu(self) -> None:
        if self._menu is None or self._btn_mas is None:
            return
        x = self._btn_mas.winfo_rootx()
        y = self._btn_mas.winfo_rooty() + self._btn_mas.winfo_height() + 2
        try:
            self._menu.tk_popup(x, y)
        finally:
            self._menu.grab_release()


class BarraLateral(ctk.CTkFrame):
    """Navegación lateral: sección + botones con iconos + pie."""

    def __init__(
        self,
        master,
        elementos: Sequence[tuple[str, str, str]],
        al_seleccionar: Callable[[str], None],
        *,
        ancho: int = 224,
        pie: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("fg_color", estilos.SIDEBAR_FONDO)
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("width", ancho)
        super().__init__(master, **kwargs)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)

        self._al_seleccionar = al_seleccionar
        self._botones: dict[str, ctk.CTkButton] = {}
        self._iconos: dict[str, str] = {}
        self._activo: str | None = None

        ctk.CTkLabel(
            self,
            text="MENÚ",
            font=estilos.FUENTE_MICRO,
            text_color=estilos.SIDEBAR_TEXTO_SEC,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=estilos.ESPACIO_MD, pady=(estilos.ESPACIO_XL, estilos.ESPACIO_SM))

        fila = 1
        for clave, texto, nombre_icono in elementos:
            boton = ctk.CTkButton(
                self,
                text=f"  {texto}",
                image=icono(nombre_icono, 19, estilos.SIDEBAR_TEXTO) if nombre_icono else None,
                anchor="w",
                height=42,
                corner_radius=0,
                fg_color="transparent",
                hover_color=estilos.SIDEBAR_HOVER,
                text_color=estilos.SIDEBAR_TEXTO,
                font=estilos.FUENTE_NORMAL,
                command=lambda c=clave: self._al_seleccionar(c),
            )
            boton.grid(row=fila, column=0, sticky="ew", padx=estilos.ESPACIO_MD, pady=estilos.ESPACIO_XS)
            self._botones[clave] = boton
            self._iconos[clave] = nombre_icono
            fila += 1

        # Empuja el pie hacia abajo.
        self.grid_rowconfigure(fila, weight=1)

        if pie:
            ctk.CTkFrame(self, height=1, fg_color=estilos.SIDEBAR_HOVER, corner_radius=0).grid(
                row=fila + 1, column=0, sticky="ew", padx=estilos.ESPACIO_MD, pady=(0, estilos.ESPACIO_SM)
            )
            ctk.CTkLabel(
                self,
                text=pie,
                font=estilos.FUENTE_PEQUENA,
                text_color=estilos.SIDEBAR_TEXTO_SEC,
                anchor="w",
                justify="left",
            ).grid(row=fila + 2, column=0, sticky="ew", padx=estilos.ESPACIO_MD, pady=(0, estilos.ESPACIO_LG))

    def seleccionar(self, clave: str) -> None:
        """Marca visualmente el elemento activo.

        El ítem activo usa **negrita** y color blanco; los demás, peso normal.
        Así el ojo distingue "dónde estoy" sin solo depender del color.
        """
        self._activo = clave
        for nombre, boton in self._botones.items():
            nombre_icono = self._iconos[nombre]
            if nombre == clave:
                boton.configure(
                    fg_color=estilos.SIDEBAR_ACTIVO,
                    text_color="#ffffff",
                    hover_color=estilos.COLOR_ACENTO_HOVER,
                    font=estilos.FUENTE_NEGRITA_13,
                    image=icono(nombre_icono, 19, "#ffffff") if nombre_icono else None,
                )
            else:
                boton.configure(
                    fg_color="transparent",
                    text_color=estilos.SIDEBAR_TEXTO,
                    hover_color=estilos.SIDEBAR_HOVER,
                    font=estilos.FUENTE_NORMAL,
                    image=icono(nombre_icono, 19, estilos.SIDEBAR_TEXTO) if nombre_icono else None,
                )


class Tooltip:
    """Globo de ayuda que aparece al pasar el ratón sobre uno o varios widgets.

    El contenido es **dinámico**: se pide al mostrarse mediante ``contenido()``,
    que debe devolver un diccionario con las claves:

    * ``titulo`` (str): encabezado del globo.
    * ``campos`` (list[tuple[str, str]]): pares ``(etiqueta, valor)``.
    * ``nota`` (str | None): texto destacado al pie (p. ej. la nota del proveedor).
    * ``color`` (str | None): color de la franja superior (acento).

    Es tolerante a fallos: nunca debe tumbar la interfaz anfitriona.
    """

    def __init__(
        self,
        widget: "ctk.CTkBaseClass",
        contenido: Callable[[], dict],
        *,
        retardo: int = 220,
        hijos: Sequence["ctk.CTkBaseClass"] | None = None,
    ) -> None:
        self._widget = widget
        self._contenido = contenido
        self._retardo = retardo
        self._hijos = list(hijos or [])
        self._mostrar_id: str | None = None
        self._ocultar_id: str | None = None
        self._ventana: tk.Toplevel | None = None

        for w in [widget, *self._hijos]:
            w.bind("<Enter>", self._al_entrar, add="+")
            w.bind("<Leave>", self._al_salir, add="+")
            w.bind("<ButtonPress>", self._al_salir, add="+")
            w.bind("<Destroy>", self._al_destruir, add="+")

    # ------------------------------------------------------------ manejo
    def _al_entrar(self, _event: object = None) -> None:
        self._cancelar_ocultar()
        if self._ventana is None and self._mostrar_id is None:
            self._mostrar_id = self._widget.after(self._retardo, self._mostrar)

    def _al_salir(self, _event: object = None) -> None:
        self._cancelar_mostrar()
        if self._ventana is not None and self._ocultar_id is None:
            self._ocultar_id = self._widget.after(120, self._ocultar)

    def _al_destruir(self, _event: object = None) -> None:
        self._cancelar_mostrar()
        self._cancelar_ocultar()
        self._ocultar()

    def _cancelar_mostrar(self) -> None:
        if self._mostrar_id is not None:
            try:
                self._widget.after_cancel(self._mostrar_id)
            except Exception:  # noqa: BLE001
                pass
            self._mostrar_id = None

    def _cancelar_ocultar(self) -> None:
        if self._ocultar_id is not None:
            try:
                self._widget.after_cancel(self._ocultar_id)
            except Exception:  # noqa: BLE001
                pass
            self._ocultar_id = None

    def _ocultar(self) -> None:
        if self._ventana is not None:
            try:
                self._ventana.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._ventana = None

    # ----------------------------------------------------------- mostrar
    def _mostrar(self) -> None:
        self._mostrar_id = None
        if self._ventana is not None:
            return
        try:
            datos = self._contenido() or {}
        except Exception:  # noqa: BLE001
            logger.exception("Error generando el contenido del tooltip.")
            return

        color = datos.get("color") or estilos.COLOR_ACENTO

        ventana = tk.Toplevel(self._widget)
        ventana.overrideredirect(True)
        try:
            ventana.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        ventana.configure(bg=estilos.SUPERFICIE)

        tarjeta = ctk.CTkFrame(
            ventana,
            fg_color=estilos.SUPERFICIE,
            corner_radius=10,
            border_width=1,
            border_color=estilos.BORDE,
        )
        tarjeta.pack()

        # Franja de acento superior.
        ctk.CTkFrame(tarjeta, fg_color=color, height=4, corner_radius=0).pack(fill="x")

        cuerpo = ctk.CTkFrame(tarjeta, fg_color="transparent")
        cuerpo.pack(fill="both", padx=14, pady=10)

        if datos.get("titulo"):
            ctk.CTkLabel(
                cuerpo,
                text=str(datos["titulo"]),
                font=(estilos.FAMILIA, 13, "bold"),
                text_color=estilos.COLOR_TEXTO,
                anchor="w",
                justify="left",
                wraplength=280,
            ).pack(anchor="w")

        for etiqueta, valor in datos.get("campos", []):
            if valor is None or str(valor).strip() == "":
                continue
            fila = ctk.CTkFrame(cuerpo, fg_color="transparent")
            fila.pack(anchor="w", fill="x", pady=(3, 0))
            ctk.CTkLabel(
                fila,
                text=f"{etiqueta}: ",
                font=(estilos.FAMILIA, 10, "bold"),
                text_color=estilos.COLOR_SECUNDARIO,
                anchor="w",
            ).pack(side="left", anchor="n")
            ctk.CTkLabel(
                fila,
                text=str(valor),
                font=(estilos.FAMILIA, 10),
                text_color=estilos.COLOR_TEXTO,
                anchor="w",
                justify="left",
                wraplength=220,
            ).pack(side="left", anchor="w")

        nota = datos.get("nota")
        ctk.CTkFrame(cuerpo, height=1, fg_color=estilos.BORDE).pack(fill="x", pady=(8, 6))
        ctk.CTkLabel(
            cuerpo,
            text="NOTA / PRODUCTO QUE TRAE",
            font=(estilos.FAMILIA, 8, "bold"),
            text_color=estilos.COLOR_ACENTO,
            anchor="w",
        ).pack(anchor="w")
        if nota and str(nota).strip():
            ctk.CTkLabel(
                cuerpo,
                text=str(nota),
                font=(estilos.FAMILIA, 10),
                text_color=estilos.COLOR_TEXTO,
                anchor="w",
                justify="left",
                wraplength=280,
            ).pack(anchor="w", pady=(2, 0))
        else:
            ctk.CTkLabel(
                cuerpo,
                text="Sin nota registrada.",
                font=(estilos.FAMILIA, 10, "italic"),
                text_color=estilos.COLOR_TENUE,
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))

        # Posición junto al cursor, ajustada a los bordes de la pantalla.
        ventana.update_idletasks()
        ancho = ventana.winfo_reqwidth()
        alto = ventana.winfo_reqheight()
        x = self._widget.winfo_pointerx() + 16
        y = self._widget.winfo_pointery() + 16
        ancho_pantalla = ventana.winfo_screenwidth()
        alto_pantalla = ventana.winfo_screenheight()
        if x + ancho > ancho_pantalla:
            x = max(0, ancho_pantalla - ancho - 8)
        if y + alto > alto_pantalla:
            y = max(0, alto_pantalla - alto - 8)
        ventana.geometry(f"+{x}+{y}")

        self._ventana = ventana
