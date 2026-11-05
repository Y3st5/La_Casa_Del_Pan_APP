"""Vista de categorías — capa de PRESENTACIÓN.

Gestiona el **catálogo de categorías** (Panes, Abarrotes, Cafetería…) que
clasifica los productos. Es la base del *desglose de ventas por categoría*.

No ejecuta SQL: delega en ``CategoriaService`` (inyectado desde el composition
root).

Accesibilidad / atajos:
    * ``Ins``            → agregar categoría.
    * ``Enter`` / ``F2`` → editar la categoría seleccionada.
    * ``Supr``           → activar/desactivar la seleccionada.
    * ``Ctrl+F``         → ir al buscador.
    * ``F5``             → recargar.
    * ``Esc``            → limpiar la búsqueda.
    * Doble clic         → editar.
"""

from __future__ import annotations

import logging
from tkinter import messagebox, ttk
from typing import Any

import customtkinter as ctk

from src.dominio.excepciones import ErrorCategoria
from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.componentes import Accion, BarraAcciones
from src.presentacion.dialogos import DialogoCategoria
from src.presentacion.estilos import (
    COLOR_ERROR,
    COLOR_FILA_PAR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
    FAMILIA,
    estilo_treeview,
)
from src.servicios.categoria_service import CategoriaService

logger = logging.getLogger(__name__)


class CategoriasView(ctk.CTkFrame):
    """Lista de categorías con su conteo de productos y gestión (CRUD)."""

    COLUMNAS = ("nombre", "orden", "productos", "estado")
    ENCABEZADOS = ("Categoría", "Orden", "Productos", "Estado")
    ANCHOS = (380, 120, 140, 140)
    ALINEACIONES = ("w", "center", "center", "center")

    def __init__(
        self,
        master: Any,
        categoria_service: CategoriaService,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._servicio = categoria_service
        self._categorias: list[dict[str, Any]] = []
        self._por_id: dict[str, dict[str, Any]] = {}

        self._configurar_estilos()
        self._construir_ui()
        self._atajos_teclado()
        self.cargar_datos()

    # ------------------------------------------------------------------ UI
    def _configurar_estilos(self) -> None:
        estilo_treeview("Categoria.Treeview")

    def _construir_ui(self) -> None:
        # --- Encabezado ---
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.pack(fill="x", padx=24, pady=(18, 6))

        ctk.CTkLabel(
            cabecera,
            text="Categorías",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family=FAMILIA, size=26, weight="bold"),
        ).pack(side="left")

        self._lbl_estado = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=(FAMILIA, 12)
        )
        self._lbl_estado.pack(side="right", padx=(12, 0))

        ctk.CTkLabel(
            self,
            text=(
                "Clasifican los productos y definen el desglose de las ventas "
                "(Panes, Abarrotes, Cafetería…). Cada producto debe tener una."
            ),
            text_color=COLOR_SECUNDARIO,
            font=(FAMILIA, 12),
        ).pack(anchor="w", padx=26, pady=(0, 2))

        # --- Barra de herramientas ---
        barra = BarraAcciones(
            self,
            primaria=Accion("Agregar", self._agregar, "mas"),
            secundarias=[Accion("Editar", self._editar, "lapiz")],
            menu=[
                Accion("Desactivar / reactivar", self._cambiar_estado, "poder", peligro=True),
                Accion("Actualizar", self.cargar_datos, "recargar"),
            ],
        )
        barra.pack(fill="x", padx=24, pady=(4, 2))

        self._var_busqueda = ctk.StringVar()
        self._entrada_busqueda = ctk.CTkEntry(
            barra.izquierda,
            textvariable=self._var_busqueda,
            width=220,
            height=34,
            placeholder_text="Buscar categoría…  (Ctrl+F)",
        )
        self._entrada_busqueda.pack(side="left", fill="x", expand=True)
        self._var_busqueda.trace_add("write", lambda *_a: self._aplicar_filtro())

        self._var_inactivos = ctk.IntVar(value=0)
        ctk.CTkCheckBox(
            barra.izquierda,
            text="Mostrar inactivas",
            variable=self._var_inactivos,
            onvalue=1,
            offvalue=0,
            font=(FAMILIA, 12),
            command=self.cargar_datos,
        ).pack(side="left", padx=14)

        ctk.CTkLabel(
            self,
            text="Atajos:  Ins agregar  ·  Enter/F2 editar  ·  Supr activar/desactivar  ·  Ctrl+F buscar  ·  F5 recargar",
            text_color=COLOR_SECUNDARIO,
            font=(FAMILIA, 11),
        ).pack(anchor="w", padx=26, pady=(2, 4))

        # --- Tabla ---
        contenedor = ctk.CTkFrame(self, fg_color="transparent")
        contenedor.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        self._tabla = ttk.Treeview(
            contenedor,
            columns=self.COLUMNAS,
            show="headings",
            style="Categoria.Treeview",
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
                stretch=(columna == "nombre"),
                minwidth=80,
            )
        self._tabla.pack(side="left", fill="both", expand=True)

        barra_scroll = ttk.Scrollbar(contenedor, orient="vertical", command=self._tabla.yview)
        self._tabla.configure(yscrollcommand=barra_scroll.set)
        barra_scroll.pack(side="right", fill="y")

        self._tabla.tag_configure("par", background=COLOR_FILA_PAR)
        self._tabla.tag_configure("impar", background="#ffffff")
        self._tabla.tag_configure("inactivo", background="#e5e7eb", foreground="#6b7280")

        self._tabla.bind("<Double-1>", lambda _e: self._editar())
        self._tabla.bind("<Return>", lambda _e: self._editar())
        self._tabla.bind("<F2>", lambda _e: self._editar())
        self._tabla.bind("<Delete>", lambda _e: self._cambiar_estado())

    # ------------------------------------------------------------ Atajos
    def _atajos_teclado(self) -> None:
        raiz = self.winfo_toplevel()
        raiz.bind("<Insert>", self._atajo(self._agregar), add="+")
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
        """Recarga las categorías (con su conteo de productos) y repinta."""
        try:
            self._categorias = self._servicio.listar_con_conteo(
                incluir_inactivas=bool(self._var_inactivos.get())
            )
        except ErrorConexionBD as exc:
            logger.error("Error de conexión al cargar categorías: %s", exc)
            self._lbl_estado.configure(
                text="⚠  Sin conexión a la base de datos", text_color=COLOR_ERROR
            )
            messagebox.showerror("Error de conexión", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - la UI nunca debe caer
            logger.exception("Error inesperado al cargar categorías.")
            self._lbl_estado.configure(text="⚠  Error inesperado", text_color=COLOR_ERROR)
            messagebox.showerror("Error", f"No se pudieron cargar las categorías:\n\n{exc}")
            return
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        texto = self._var_busqueda.get().strip().lower()
        if texto:
            visibles = [
                c for c in self._categorias if texto in str(c.get("nombre") or "").lower()
            ]
        else:
            visibles = list(self._categorias)
        self._render(visibles, filtrado=bool(texto))

    def _render(self, categorias: list[dict[str, Any]], filtrado: bool = False) -> None:
        seleccionado = self._obtener_seleccionado()
        id_previo = str(seleccionado["id"]) if seleccionado else None

        for item in self._tabla.get_children():
            self._tabla.delete(item)
        self._por_id = {str(c.get("id")): c for c in categorias}

        for indice, categoria in enumerate(categorias):
            self._tabla.insert(
                "",
                "end",
                iid=str(categoria.get("id")),
                values=self._formatear_fila(categoria),
                tags=("par" if indice % 2 == 0 else "impar",)
                + (() if categoria.get("activo", 1) else ("inactivo",)),
            )

        if id_previo and self._tabla.exists(id_previo):
            self._tabla.selection_set(id_previo)
            self._tabla.focus(id_previo)

        total = len(self._categorias)
        mostrados = len(categorias)
        partes = [f"{mostrados} categoría(s)"]
        if filtrado:
            partes.append(f"filtrado de {total}")
        self._lbl_estado.configure(
            text=" · ".join(partes),
            text_color=COLOR_OK if mostrados else COLOR_SECUNDARIO,
        )

    # ------------------------------------------------------------ CRUD
    def _agregar(self) -> None:
        datos: dict[str, Any] | None = None
        while True:
            datos = DialogoCategoria(self, categoria=datos, es_edicion=False).mostrar()
            if datos is None:
                return
            try:
                nuevo_id = self._servicio.crear(datos)
            except ErrorCategoria as exc:
                messagebox.showerror("No se pudo guardar", str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error inesperado al crear categoría.")
                messagebox.showerror("Error", f"No se pudo crear la categoría:\n\n{exc}")
                return
            self.cargar_datos()
            self._seleccionar(nuevo_id)
            self._lbl_estado.configure(
                text=f"Categoría «{datos.get('nombre')}» creada.", text_color=COLOR_OK
            )
            return

    def _editar(self) -> None:
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        categoria_id = int(actual["id"])
        try:
            completo = self._servicio.obtener(categoria_id) or actual
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al obtener la categoría id=%s.", categoria_id)
            messagebox.showerror("Error", f"No se pudo cargar la categoría:\n\n{exc}")
            return

        datos: dict[str, Any] | None = dict(completo)
        while True:
            datos = DialogoCategoria(self, categoria=datos, es_edicion=True).mostrar()
            if datos is None:
                return
            try:
                self._servicio.actualizar(categoria_id, datos)
            except ErrorCategoria as exc:
                messagebox.showerror("No se pudo guardar", str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error inesperado al actualizar categoría id=%s.", categoria_id)
                messagebox.showerror("Error", f"No se pudo actualizar la categoría:\n\n{exc}")
                return
            self.cargar_datos()
            self._seleccionar(categoria_id)
            self._lbl_estado.configure(
                text=f"Categoría «{datos.get('nombre')}» actualizada.", text_color=COLOR_OK
            )
            return

    def _cambiar_estado(self) -> None:
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        categoria_id = int(actual["id"])
        activo = bool(actual.get("activo", 1))
        accion = "desactivar" if activo else "reactivar"

        productos = int(actual.get("productos", 0))
        aviso = ""
        if activo and productos:
            aviso = (
                f"\n\nTiene {productos} producto(s) asignado(s); seguirán vendiéndose, "
                "pero la categoría dejará de aparecer en el selector."
            )
        if not messagebox.askyesno(
            "Confirmar",
            f"¿Deseas {accion} la categoría «{actual.get('nombre')}»?{aviso}",
            parent=self,
        ):
            return
        try:
            self._servicio.cambiar_activo(categoria_id, not activo)
        except ErrorCategoria as exc:
            messagebox.showerror("No se pudo cambiar el estado", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado al cambiar estado de la categoría id=%s.", categoria_id)
            messagebox.showerror("Error", f"No se pudo cambiar el estado:\n\n{exc}", parent=self)
            return
        self.cargar_datos()
        self._seleccionar(categoria_id)
        verbo = "desactivada" if activo else "reactivada"
        self._lbl_estado.configure(
            text=f"Categoría «{actual.get('nombre')}» {verbo}.", text_color=COLOR_OK
        )

    # ------------------------------------------------------------ Apoyo
    def _obtener_seleccionado(self) -> dict[str, Any] | None:
        seleccion = self._tabla.selection()
        iid = seleccion[0] if seleccion else self._tabla.focus()
        if not iid:
            return None
        return self._por_id.get(str(iid))

    def _seleccionar(self, categoria_id: int) -> None:
        iid = str(categoria_id)
        if self._tabla.exists(iid):
            self._tabla.selection_set(iid)
            self._tabla.focus(iid)
            self._tabla.see(iid)

    def _avisar_seleccion(self) -> None:
        messagebox.showinfo(
            "Selecciona una categoría",
            "Primero selecciona una categoría de la lista.",
            parent=self,
        )

    @staticmethod
    def _formatear_fila(categoria: dict[str, Any]) -> tuple[Any, ...]:
        return (
            categoria.get("nombre", ""),
            categoria.get("orden", 0),
            int(categoria.get("productos", 0)),
            "Activa" if categoria.get("activo", 1) else "Inactiva",
        )
