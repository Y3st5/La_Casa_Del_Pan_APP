"""Vista del calendario de visitas — capa de PRESENTACIÓN.

Agenda semanal moderna (vista de semana, estilo *Google Calendar* mejorado):

* Encabezado con el mes, el rango de la semana y un distintivo de "hoy".
* Cabecera de días con abreviatura + número de fecha; **hoy** se resalta en un
  círculo ámbar y su columna entera queda iluminada.
* Cada proveedor aparece como una **tarjeta (chip) moderna**: fondo tenue del
  color del proveedor, **barra de acento a la izquierda**, esquinas redondeadas
  y efecto *hover* al pasar el ratón.
* Contador de visitas por día en la cabecera de cada columna.

Es de **solo lectura**: la programación de los días se edita en la sección
*Proveedores* (diálogo Agregar/Editar).

No ejecuta SQL: delega en ``ProveedorService`` (inyectado desde el composition
root).

Atajos:
    * ``F5``  → recargar.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from tkinter import messagebox

import customtkinter as ctk

from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.estilos import (
    BORDE,
    COLOR_ACENTO,
    COLOR_ACENTO_HOVER,
    COLOR_ACENTO_SUAVE,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TENUE,
    COLOR_TEXTO,
    FAMILIA,
    SUPERFICIE,
)
from src.presentacion.componentes import Tooltip
from src.presentacion.recursos import icono
from src.servicios.proveedor_service import DIAS_SEMANA_CORTOS, ProveedorService

logger = logging.getLogger(__name__)

COLOR_BOTON_NEUTRO = "#6b7280"
COLOR_BOTON_NEUTRO_HOVER = "#4b5563"

#: Gris de texto secundario y línea de rejilla.
GRIS_TEXTO = "#70757a"
LINEA_REJILLA = "#ece7de"

#: Fondo cálido y tenue para la columna del día de hoy.
FONDO_HOY = "#fdf7ee"

#: Paleta de "eventos": colores vivos y modernos con buen contraste.
COLORES_CHIP: tuple[str, ...] = (
    "#1a73e8",  # azul
    "#16a34a",  # verde
    "#ef4444",  # rojo
    "#f97316",  # naranja
    "#a855f7",  # morado
    "#14b8a6",  # teal
    "#f59e0b",  # ámbar
    "#6366f1",  # índigo
    "#ec4899",  # rosa
    "#06b6d4",  # cyan
)

#: Color para proveedores desactivados.
COLOR_CHIP_INACTIVO = "#9aa0a6"

MESES: tuple[str, ...] = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


# ======================================================================
#  Utilidades de color (para tintes y hover)
# ======================================================================
def _hex_a_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rgb_a_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(v))) for v in rgb))


def _mezclar(color_a: str, color_b: str, factor: float) -> str:
    """Mezcla lineal: ``factor`` = 0 → color_a, 1 → color_b."""
    r1, g1, b1 = _hex_a_rgb(color_a)
    r2, g2, b2 = _hex_a_rgb(color_b)
    return _rgb_a_hex(
        (
            r1 + (r2 - r1) * factor,
            g1 + (g2 - g1) * factor,
            b1 + (b2 - b1) * factor,
        )
    )


def _tinte(color: str, intensidad: float = 0.88) -> str:
    """Devuelve una versión muy clara del color (fondo de chip)."""
    return _mezclar(color, "#ffffff", intensidad)


def _oscurecer(color: str, intensidad: float = 0.18) -> str:
    """Devuelve una versión más oscura del color (para hover/borde)."""
    return _mezclar(color, "#000000", intensidad)


class CalendarioView(ctk.CTkFrame):
    """Agenda semanal de visitas de proveedores (aspecto moderno)."""

    def __init__(
        self,
        master: Any,
        proveedor_service: ProveedorService,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._servicio = proveedor_service
        self._proveedores: list[dict[str, Any]] = []

        # Referencias que se refrescan en cada repintado.
        self._celdas_cuerpo: list[ctk.CTkScrollableFrame] = []
        self._contadores: list[ctk.CTkLabel] = []

        self._construir_ui()
        self._atajos_teclado()
        self.cargar_datos()

    # ------------------------------------------------------------------ UI
    def _construir_ui(self) -> None:
        # --- Encabezado -------------------------------------------------
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.pack(fill="x", padx=24, pady=(18, 6))

        titulo = ctk.CTkFrame(cabecera, fg_color="transparent")
        titulo.pack(side="left")
        ctk.CTkLabel(
            titulo,
            text="  Calendario de Visitas",
            image=icono("calendario", 24, COLOR_ACENTO),
            compound="left",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family=FAMILIA, size=26, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            titulo,
            text="Proveedores que visitan cada día de la semana.",
            text_color=COLOR_SECUNDARIO,
            font=(FAMILIA, 12),
        ).pack(anchor="w", pady=(2, 0))

        self._lbl_estado = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=(FAMILIA, 12)
        )
        self._lbl_estado.pack(side="right", padx=(12, 0))

        # --- Barra de herramientas -------------------------------------
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.pack(fill="x", padx=24, pady=(4, 8))

        ctk.CTkLabel(
            barra,
            text="La programación de días se edita en «Proveedores» (Agregar / Editar).",
            text_color=COLOR_SECUNDARIO,
            font=(FAMILIA, 11),
        ).pack(side="left")

        ctk.CTkButton(
            barra,
            text="  Actualizar",
            image=icono("recargar", 17, "#ffffff"),
            width=140,
            height=34,
            font=(FAMILIA, 12, "bold"),
            fg_color=COLOR_BOTON_NEUTRO,
            hover_color=COLOR_BOTON_NEUTRO_HOVER,
            corner_radius=8,
            command=self.cargar_datos,
        ).pack(side="right")

        self._var_inactivos = ctk.IntVar(value=0)
        ctk.CTkCheckBox(
            barra,
            text="Mostrar inactivos",
            variable=self._var_inactivos,
            onvalue=1,
            offvalue=0,
            font=(FAMILIA, 12),
            command=self.cargar_datos,
        ).pack(side="right", padx=14)

        # --- Tarjeta del calendario ------------------------------------
        tarjeta = ctk.CTkFrame(
            self,
            fg_color=SUPERFICIE,
            corner_radius=16,
            border_width=1,
            border_color=BORDE,
        )
        tarjeta.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        tarjeta.grid_columnconfigure(0, weight=1)
        tarjeta.grid_rowconfigure(1, weight=1)

        self._construir_subcabecera(tarjeta)

        # Rejilla principal: fila 0 = cabecera de días, fila 1 = cuerpo.
        self._rejilla = ctk.CTkFrame(tarjeta, fg_color="transparent")
        self._rejilla.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
        for columna in range(7):
            self._rejilla.grid_columnconfigure(columna, weight=1, uniform="dia")
        self._rejilla.grid_rowconfigure(1, weight=1)

        self._construir_rejilla()

    def _construir_subcabecera(self, tarjeta: ctk.CTkFrame) -> None:
        """Franja superior de la tarjeta: mes, rango de semana y distintivo de hoy."""
        sub = ctk.CTkFrame(tarjeta, fg_color="transparent")
        sub.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))

        # Bloque izquierdo: mes grande + rango de la semana.
        bloque = ctk.CTkFrame(sub, fg_color="transparent")
        bloque.pack(side="left")
        self._lbl_mes = ctk.CTkLabel(
            bloque, text="", text_color=COLOR_TEXTO, font=(FAMILIA, 19, "bold")
        )
        self._lbl_mes.pack(side="left")
        self._lbl_semana = ctk.CTkLabel(
            bloque, text="", text_color=GRIS_TEXTO, font=(FAMILIA, 11)
        )
        self._lbl_semana.pack(side="left", padx=(12, 0), pady=(6, 0))

        # Bloque derecho: distintivo "Hoy".
        chip_hoy = ctk.CTkFrame(
            sub,
            fg_color=COLOR_ACENTO_SUAVE,
            corner_radius=14,
            border_width=1,
            border_color=_mezclar(COLOR_ACENTO, "#ffffff", 0.7),
        )
        chip_hoy.pack(side="right")
        ctk.CTkLabel(
            chip_hoy,
            text="Hoy",
            text_color=COLOR_ACENTO_HOVER,
            font=(FAMILIA, 11, "bold"),
        ).pack(side="left", padx=(12, 2), pady=4)
        self._lbl_hoy_fecha = ctk.CTkLabel(
            chip_hoy,
            text="",
            text_color=COLOR_ACENTO_HOVER,
            font=(FAMILIA, 11),
        )
        self._lbl_hoy_fecha.pack(side="left", padx=(0, 12), pady=4)

    def _construir_rejilla(self) -> None:
        """Crea la cabecera de días y los contenedores de cada columna."""
        lunes = self._lunes_de_hoy()
        hoy_iso = datetime.now().isoweekday()

        for indice in range(7):
            fecha = lunes + timedelta(days=indice)
            es_hoy = (indice + 1) == hoy_iso

            # --- Cabecera del día --------------------------------------
            cab = ctk.CTkFrame(
                self._rejilla,
                fg_color=COLOR_ACENTO_SUAVE if es_hoy else "transparent",
                corner_radius=10,
            )
            cab.grid(row=0, column=indice, sticky="ew", padx=2, pady=(6, 3))
            cab.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                cab,
                text=DIAS_SEMANA_CORTOS[indice].upper(),
                text_color=COLOR_ACENTO_HOVER if es_hoy else GRIS_TEXTO,
                font=(FAMILIA, 10, "bold"),
            ).pack(pady=(8, 4))

            circulo = ctk.CTkFrame(
                cab,
                fg_color=COLOR_ACENTO if es_hoy else "transparent",
                width=32,
                height=32,
                corner_radius=16,
            )
            circulo.pack(pady=(0, 4))
            circulo.pack_propagate(False)
            ctk.CTkLabel(
                circulo,
                text=str(fecha.day),
                text_color="#ffffff" if es_hoy else COLOR_TEXTO,
                font=(FAMILIA, 13, "bold"),
            ).pack(expand=True)

            # Contador de visitas (se refresca en ``_dibujar``).
            contador = ctk.CTkLabel(
                cab,
                text="",
                text_color=COLOR_ACENTO_HOVER if es_hoy else COLOR_TENUE,
                font=(FAMILIA, 9),
            )
            contador.pack(pady=(0, 8))
            self._contadores.append(contador)

            # Línea de acento superior de la columna de hoy.
            if es_hoy:
                ctk.CTkFrame(cab, fg_color=COLOR_ACENTO, height=3, corner_radius=2).place(
                    relx=0, rely=0, relwidth=1, anchor="nw"
                )

            # --- Cuerpo desplazable de la columna ----------------------
            cuerpo = ctk.CTkScrollableFrame(
                self._rejilla,
                fg_color=FONDO_HOY if es_hoy else "transparent",
                corner_radius=8,
            )
            cuerpo.grid(row=1, column=indice, sticky="nsew", padx=2, pady=(0, 4))
            self._celdas_cuerpo.append(cuerpo)

            # Separador vertical entre columnas.
            if indice > 0:
                ctk.CTkFrame(
                    self._rejilla, fg_color=LINEA_REJILLA, width=1, corner_radius=0
                ).place(relx=indice / 7, rely=0, relheight=1, anchor="n")

    # ------------------------------------------------------------ Atajos
    def _atajos_teclado(self) -> None:
        raiz = self.winfo_toplevel()
        raiz.bind("<F5>", self._atajo(self.cargar_datos), add="+")

    def _atajo(self, accion: Any) -> Any:
        def _manejador(_event: Any = None) -> str | None:
            if self.winfo_ismapped():
                accion()
                return "break"
            return None

        return _manejador

    # --------------------------------------------------------------- Datos
    def cargar_datos(self) -> None:
        """Recarga los proveedores y repinta la agenda semanal."""
        try:
            self._proveedores = self._servicio.listar(
                incluir_inactivos=bool(self._var_inactivos.get())
            )
        except ErrorConexionBD as exc:
            logger.error("Error de conexión al cargar el calendario: %s", exc)
            self._lbl_estado.configure(
                text="⚠  Sin conexión a la base de datos", text_color="#b91c1c"
            )
            messagebox.showerror("Error de conexión", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - la UI nunca debe caer
            logger.exception("Error inesperado al cargar el calendario.")
            self._lbl_estado.configure(text="⚠  Error inesperado", text_color="#b91c1c")
            messagebox.showerror("Error", f"No se pudo cargar el calendario:\n\n{exc}")
            return
        self._dibujar()

    # ------------------------------------------------------------ Pintado
    def _dibujar(self) -> None:
        """Repinta las columnas a partir de ``self._proveedores``."""
        self._actualizar_subcabecera()

        por_dia: dict[int, list[dict[str, Any]]] = {dia: [] for dia in range(1, 8)}
        sin_dia = 0
        for proveedor in self._proveedores:
            dias = sorted(
                {int(d) for d in (proveedor.get("dias") or []) if str(d).isdigit() and 1 <= int(d) <= 7}
            )
            if not dias:
                sin_dia += 1
            for dia in dias:
                por_dia[dia].append(proveedor)

        # Ordena cada día por nombre para una lectura estable.
        for lista in por_dia.values():
            lista.sort(key=lambda p: str(p.get("nombre") or "").lower())

        for indice in range(7):
            cuerpo = self._celdas_cuerpo[indice]
            for hijo in cuerpo.winfo_children():
                hijo.destroy()

            lista = por_dia[indice + 1]

            # Contador de visitas del día.
            n = len(lista)
            self._contadores[indice].configure(
                text="Sin visitas" if n == 0 else (f"{n} visita" if n == 1 else f"{n} visitas")
            )

            if not lista:
                vacio = ctk.CTkFrame(cuerpo, fg_color="transparent")
                vacio.pack(fill="x", padx=6, pady=14)
                ctk.CTkLabel(
                    vacio,
                    text="○",
                    text_color="#d8d0c4",
                    font=(FAMILIA, 16),
                ).pack()
                ctk.CTkLabel(
                    vacio,
                    text="Sin visitas",
                    text_color="#cfc7ba",
                    font=(FAMILIA, 10),
                ).pack(pady=(2, 0))
                continue

            for proveedor in lista:
                self._dibujar_chip(cuerpo, proveedor)

        total_visitas = sum(len(v) for v in por_dia.values())
        resumen = f"{len(self._proveedores)} proveedor(es) · {total_visitas} visita(s) semanales"
        if sin_dia:
            resumen += f" · {sin_dia} sin día asignado"
        self._lbl_estado.configure(
            text=resumen,
            text_color=COLOR_OK if self._proveedores else COLOR_SECUNDARIO,
        )

    def _actualizar_subcabecera(self) -> None:
        hoy = datetime.now()
        lunes = self._lunes_de_hoy()
        domingo = lunes + timedelta(days=6)
        self._lbl_mes.configure(text=f"{MESES[hoy.month - 1]} {hoy.year}")
        self._lbl_semana.configure(
            text=f"Semana del {lunes.day} al {domingo.day} de {MESES[hoy.month - 1].lower()}"
        )
        self._lbl_hoy_fecha.configure(
            text=f"{DIAS_SEMANA_CORTOS[hoy.isoweekday() - 1]} {hoy.day}"
        )

    def _dibujar_chip(self, padre: ctk.CTkScrollableFrame, proveedor: dict[str, Any]) -> None:
        """Dibuja una tarjeta compacta de proveedor (una línea + hover con nota).

        La tarjeta es baja (solo el nombre). Al pasar el ratón se ilumina y, a
        los pocos milisegundos, aparece un globo con el contacto y la **nota /
        producto que trae** el proveedor.
        """
        activo = bool(proveedor.get("activo", 1))
        color = self._color_proveedor(proveedor) if activo else COLOR_CHIP_INACTIVO
        fondo = _tinte(color, 0.90)
        fondo_hover = _tinte(color, 0.76)

        chip = ctk.CTkFrame(
            padre,
            fg_color=fondo,
            corner_radius=8,
            border_width=1,
            border_color=_tinte(color, 0.58),
        )
        chip.pack(fill="x", padx=4, pady=(3, 1))

        # Barra de acento a la izquierda.
        barra = ctk.CTkFrame(chip, fg_color=color, width=4, height=1, corner_radius=2)
        barra.pack(side="left", fill="y", padx=(3, 0), pady=3)

        nombre_lbl = ctk.CTkLabel(
            chip,
            text=proveedor.get("nombre") or "—",
            anchor="w",
            justify="left",
            wraplength=120,
            text_color=COLOR_TEXTO if activo else "#6b7280",
            font=(FAMILIA, 11, "bold"),
        )
        nombre_lbl.pack(side="left", fill="x", expand=True, padx=(7, 6), pady=4)

        if not activo:
            chip.configure(border_color="#d1d5db")

        # --- Efecto hover (ilumina la tarjeta) ------------------------
        def _entrar(_e: Any = None) -> None:
            chip.configure(fg_color=fondo_hover, border_color=_oscurecer(color, 0.25))

        def _salir(_e: Any = None) -> None:
            chip.configure(
                fg_color=fondo,
                border_color="#d1d5db" if not activo else _tinte(color, 0.58),
            )

        hijos: list[Any] = [chip, barra, nombre_lbl]
        for w in hijos:
            w.bind("<Enter>", _entrar, add="+")
            w.bind("<Leave>", _salir, add="+")

        # --- Globo con el detalle y la nota ---------------------------
        Tooltip(chip, lambda p=proveedor, c=color: self._contenido_tooltip(p, c), hijos=hijos)

    def _contenido_tooltip(self, proveedor: dict[str, Any], color: str) -> dict[str, Any]:
        """Arma el contenido del globo para un proveedor."""
        dias = sorted(
            {int(d) for d in (proveedor.get("dias") or []) if str(d).isdigit() and 1 <= int(d) <= 7}
        )
        dias_txt = ", ".join(DIAS_SEMANA_CORTOS[d - 1] for d in dias) if dias else "Sin día asignado"
        estado = "Activo" if bool(proveedor.get("activo", 1)) else "Inactivo"

        campos = [
            ("Contacto", proveedor.get("contacto")),
            ("Teléfono", proveedor.get("telefono")),
            ("Email", proveedor.get("email")),
            ("Visita", dias_txt),
            ("Estado", estado),
        ]
        return {
            "titulo": proveedor.get("nombre") or "—",
            "campos": campos,
            "nota": proveedor.get("notas"),
            "color": color,
        }

    # ------------------------------------------------------------ Apoyo
    @staticmethod
    def _lunes_de_hoy() -> datetime:
        """Devuelve la fecha (00:00) del lunes de la semana actual."""
        hoy = datetime.now()
        return (hoy - timedelta(days=hoy.isoweekday() - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    @staticmethod
    def _color_proveedor(proveedor: dict[str, Any]) -> str:
        """Color estable (por proveedor) dentro de la paleta de eventos."""
        clave = str(proveedor.get("id") or proveedor.get("nombre") or "")
        if not clave:
            return COLORES_CHIP[0]
        indice = sum(ord(c) for c in clave) % len(COLORES_CHIP)
        return COLORES_CHIP[indice]
