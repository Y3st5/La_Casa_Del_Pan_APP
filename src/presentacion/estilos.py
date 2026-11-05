"""Sistema de diseño central de la interfaz — capa de PRESENTACIÓN.

Fuente única de verdad para **colores**, **tipografía** y **estilos ttk** de
LaCasaDelPanApp. Ninguna vista debe definir colores propios: todas importan de
aquí. El tema se aplica **una sola vez** en el arranque (``aplicar_tema_global``)
y luego cada vista pide su estilo concreto.

Paleta: **"Trigo" — claro y cálido**. Fondo color papel, superficies blancas,
acentos ámbar/marrón de panadería y una barra lateral espresso para dar
jerarquía. Pensada para buena legibilidad bajo la luz del mostrador.

Los nombres ``COLOR_*`` se mantienen estables por compatibilidad con las vistas
existentes; solo cambian sus valores.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)

TEMA_TTK = "clam"

# ======================================================================
#  Paleta "Trigo" (claro y cálido)
# ======================================================================
# -- Superficies -------------------------------------------------------
FONDO_APP = "#f4f1ec"        # papel cálido (fondo de ventana)
SUPERFICIE = "#ffffff"       # tarjetas y contenido
SUPERFICIE_2 = "#faf8f5"     # superficies alternas (cebra, paneles)
BORDE = "#e6e0d6"            # bordes suaves
BORDE_FUERTE = "#d6cec1"     # bordes marcados

# -- Texto -------------------------------------------------------------
COLOR_TEXTO = "#2a2521"       # casi negro cálido
COLOR_SECUNDARIO = "#7c736a"  # gris cálido (texto de apoyo)
COLOR_TENUE = "#a89f94"       # texto deshabilitado

# -- Marca (panadería) -------------------------------------------------
COLOR_ACENTO = "#b45309"        # ámbar-700
COLOR_ACENTO_HOVER = "#92400e"  # ámbar-800
COLOR_ACENTO_SUAVE = "#fef3c7"  # ámbar-100 (fondos suaves / selección)
COLOR_MARCA = "#6b3f1d"         # marrón masa madre (acento secundario)

# -- Semánticos --------------------------------------------------------
COLOR_OK = "#15803d"            # verde-700 (texto de éxito)
COLOR_OK_HOVER = "#166534"      # verde-800
COLOR_COBRAR = "#166534"        # verde-800 corporativo (CTA principal)
COLOR_COBRAR_HOVER = "#14532d"  # verde-900
COLOR_ERROR = "#b91c1c"         # rojo-700
COLOR_ERROR_HOVER = "#991b1b"   # rojo-800
COLOR_INFO = "#1d4ed8"          # azul-700

# -- Tablas ------------------------------------------------------------
COLOR_FILA_PAR = "#faf8f5"
COLOR_FILA_IMPAR = "#ffffff"
COLOR_CABECERA_TABLA = "#f0ece4"
COLOR_SELECCION = "#f1ece3"          # crema neutro (selección de fila)
COLOR_SELECCION_TEXTO = "#2a2521"    # mismo tono que el texto normal
COLOR_BORDE_TABLA = "#ece7de"

# -- Alertas de stock (RF-15) -----------------------------------------
COLOR_ALERTA_LEVE = "#fef3c7"    # ámbar tenue (stock <= mínimo)
COLOR_ALERTA_FUERTE = "#fee2e2"  # rojo tenue (stock agotado)

# -- Barra lateral (espresso) -----------------------------------------
SIDEBAR_FONDO = "#2b2320"
SIDEBAR_FONDO_ALT = "#241d1b"
SIDEBAR_HOVER = "#3b312b"
SIDEBAR_ACTIVO = COLOR_ACENTO
SIDEBAR_TEXTO = "#efe8df"
SIDEBAR_TEXTO_SEC = "#a99e93"

# -- Semánticos de estado (mensajes de la app) -------------------------
COLOR_OK_SUAVE = "#f0fdf4"        # verde-50 (fondo del mensaje de éxito)
COLOR_ESTADO_NEUTRO = "#f1ece4"   # crema suave (píldora de reposo/información)

# ======================================================================
#  Tokens de diseño
#  Escala fija de espaciado, radios y tipografía. Las vistas NO inventan
#  tamaños: usan estos nombres, de modo que toda la app tenga el mismo
#  ritmo visual. (4 pt base: 4 · 8 · 12 · 16 · 24 · 32)
# ======================================================================
ESPACIO_XS = 4
ESPACIO_SM = 8
ESPACIO_MD = 12
ESPACIO_LG = 16
ESPACIO_XL = 24
ESPACIO_XXL = 32

#: Radios de esquina (dos niveles: superficies vs. controles).
RADIO_SUPERFICIE = 12   # tarjetas, paneles
RADIO_CONTROL = 8       # botones, entries, menús, listas

# -- Tipografía --------------------------------------------------------
FAMILIA = "Segoe UI"
FAMILIA_MONO = "Consolas"

#: Escala tipográfica semántica (se aplican por widget en CTk).
FUENTE_DISPLAY = (FAMILIA, 32, "bold")   # cifras protagonistas (TOTAL)
FUENTE_TITULO = (FAMILIA, 24, "bold")    # título de vista
FUENTE_MARCA = (FAMILIA, 16, "bold")     # marca en la cabecera
FUENTE_SECCION = (FAMILIA, 18, "bold")   # cifras secundarias (vuelto)
FUENTE_SUBTITULO = (FAMILIA, 15)
FUENTE_NORMAL = (FAMILIA, 13)
FUENTE_NEGRITA_13 = (FAMILIA, 13, "bold")  # mismo cuerpo, énfasis (ítem activo)
FUENTE_ETIQUETA = (FAMILIA, 12)          # etiquetas de campo / ayuda
FUENTE_PEQUENA = (FAMILIA, 11)           # texto de apoyo
FUENTE_MICRO = (FAMILIA, 10, "bold")     # rótulos en mayúsculas (KPI, secciones)
FUENTE_CTA = (FAMILIA, 20, "bold")       # botón de acción principal (COBRAR)
FUENTE_CODIGO = (FAMILIA_MONO, 14, "bold")  # importes y códigos

# ======================================================================
#  Aplicación de estilos
# ======================================================================
_tema_aplicado = False

#: Layout plano: sin el borde 3D que ``clam`` dibuja por defecto.
_LAYOUT_ARBOL_PLANO = [("Treeview.treearea", {"sticky": "nswe"})]
_LAYOUT_CABECERA_PLANA = [
    (
        "Treeheading.cell",
        {
            "sticky": "nswe",
            "children": [
                (
                    "Treeheading.padding",
                    {
                        "sticky": "nswe",
                        "children": [
                            ("Treeheading.image", {"side": "right", "sticky": ""}),
                            ("Treeheading.text", {"sticky": "we"}),
                        ],
                    },
                )
            ],
        },
    )
]


def _aplanar_treeview(estilo: ttk.Style, nombre: str) -> None:
    """Quita el relieve de la tabla y de su cabecera (Flat Design).

    ``clam`` compone la cabecera con ``Treeheading.border``, que dibuja un
    bisel 3D. El layout propio conserva el texto y el padding, pero elimina
    ese elemento; ``relief="flat"`` por sí solo no lo consigue.
    """
    estilo.layout(nombre, _LAYOUT_ARBOL_PLANO)
    estilo.layout(f"{nombre}.Heading", _LAYOUT_CABECERA_PLANA)



def aplicar_tema() -> ttk.Style:
    """Aplica el tema base ttk una única vez y devuelve el objeto ``Style``."""
    global _tema_aplicado
    estilo = ttk.Style()
    if not _tema_aplicado:
        try:
            estilo.theme_use(TEMA_TTK)
        except tk.TclError:  # pragma: no cover - depende del entorno
            logger.debug("Tema '%s' no disponible; se usa el actual.", TEMA_TTK)
        _tema_aplicado = True
    return estilo


def aplicar_tema_global() -> ttk.Style:
    """Configura de una vez todos los estilos ttk compartidos de la app.

    Se llama en el arranque (``main.py``), después de crear la ventana raíz y
    antes de construir las vistas.
    """
    estilo = aplicar_tema()
    _configurar_treeview_base(estilo)
    _configurar_scrollbar(estilo)
    _configurar_notebook(estilo)
    _configurar_separador(estilo)
    return estilo


# ----------------------------------------------------------------------
def _configurar_treeview_base(estilo: ttk.Style) -> None:
    """Estilo por defecto de las tablas (se puede especializar por vista)."""
    _aplanar_treeview(estilo, "Treeview")
    estilo.configure(
        "Treeview",
        font=(FAMILIA, 11),
        rowheight=36,
        background=SUPERFICIE,
        fieldbackground=SUPERFICIE,
        foreground=COLOR_TEXTO,
        borderwidth=0,
        relief="flat",
    )
    estilo.configure(
        "Treeview.Heading",
        font=(FAMILIA, 11, "bold"),
        background=COLOR_CABECERA_TABLA,
        foreground="#5c534b",
        relief="flat",
        borderwidth=0,
        padding=(10, 9),
    )
    estilo.map(
        "Treeview.Heading",
        background=[("active", "#e7e1d6")],
        foreground=[("active", COLOR_ACENTO_HOVER)],
    )
    estilo.map(
        "Treeview",
        background=[("selected", COLOR_SELECCION)],
        foreground=[("selected", COLOR_SELECCION_TEXTO)],
    )


def _configurar_scrollbar(estilo: ttk.Style) -> None:
    """Barras de desplazamiento discretas, acordes al fondo papel."""
    for orient in ("Vertical", "Horizontal"):
        estilo.configure(
            f"{orient}.TScrollbar",
            background=BORDE_FUERTE,
            troughcolor=SUPERFICIE_2,
            bordercolor=SUPERFICIE_2,
            arrowcolor=COLOR_SECUNDARIO,
            gripcount=0,
            relief="flat",
        )
        estilo.map(
            f"{orient}.TScrollbar",
            background=[("active", COLOR_SECUNDARIO), ("pressed", COLOR_ACENTO)],
        )


def _configurar_notebook(estilo: ttk.Style) -> None:
    """Estilo de pestañas (compatibilidad; el layout nuevo usa barra lateral)."""
    estilo.configure(
        "TNotebook",
        background=FONDO_APP,
        borderwidth=0,
        tabmargins=(10, 8, 10, 0),
    )
    estilo.configure(
        "TNotebook.Tab",
        font=(FAMILIA, 12, "bold"),
        padding=(22, 10),
        background=FONDO_APP,
        foreground=COLOR_SECUNDARIO,
    )
    estilo.map(
        "TNotebook.Tab",
        background=[("selected", SUPERFICIE)],
        foreground=[("selected", COLOR_ACENTO)],
    )


def _configurar_separador(estilo: ttk.Style) -> None:
    estilo.configure("TSeparator", background=BORDE)


# ======================================================================
#  API heredada (compatibilidad con las vistas)
# ======================================================================
def estilo_treeview(nombre: str) -> str:
    """Registra y devuelve un estilo de ``ttk.Treeview`` con el look de la app.

    Args:
        nombre: identificador del estilo, p. ej. ``"Venta.Treeview"``.
    """
    estilo = aplicar_tema()
    _aplanar_treeview(estilo, nombre)
    estilo.configure(
        nombre,
        font=(FAMILIA, 11),
        rowheight=36,
        background=SUPERFICIE,
        fieldbackground=SUPERFICIE,
        foreground=COLOR_TEXTO,
        borderwidth=0,
        relief="flat",
    )
    estilo.configure(
        f"{nombre}.Heading",
        font=(FAMILIA, 11, "bold"),
        background=COLOR_CABECERA_TABLA,
        foreground="#5c534b",
        relief="flat",
        borderwidth=0,
        padding=(10, 9),
    )
    estilo.map(
        f"{nombre}.Heading",
        background=[("active", "#e7e1d6")],
        foreground=[("active", COLOR_ACENTO_HOVER)],
    )
    estilo.map(
        nombre,
        background=[("selected", COLOR_SELECCION)],
        foreground=[("selected", COLOR_SELECCION_TEXTO)],
    )
    return nombre


def configurar_notebook() -> None:
    """Aplica el estilo de ``ttk.Notebook`` (API heredada)."""
    _configurar_notebook(aplicar_tema())
