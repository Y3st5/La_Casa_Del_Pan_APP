"""Vista de proveedores — capa de PRESENTACIÓN.

Muestra la **lista completa de proveedores** para gestionarlos: agregar, editar
y activar/desactivar. Los **días de visita** se programan dentro del diálogo de
Agregar/Editar; para *ver* la agenda semanal existe la sección **Calendario**.

No ejecuta SQL: delega en ``ProveedorService`` (inyectado desde el composition
root).

Accesibilidad / atajos:
    * ``Ins``            → agregar proveedor.
    * ``Enter`` / ``F2`` → editar el proveedor seleccionado.
    * ``Supr``           → activar/desactivar el seleccionado.
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

from src.dominio.excepciones import ErrorProveedor
from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.componentes import Accion, BarraAcciones
from src.presentacion.dialogos import DialogoProveedor
from src.presentacion.dialogos_compras import DialogoProductosProveedor
from src.presentacion.estilos import (
    COLOR_FILA_PAR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
    FAMILIA,
    estilo_treeview,
)
from src.servicios.proveedor_service import DIAS_SEMANA_CORTOS, ProveedorService

logger = logging.getLogger(__name__)


class ProveedorView(ctk.CTkFrame):
    """Lista de proveedores con su calendario de días de visita."""

    COLUMNAS = ("nombre", "contacto", "telefono", "dias")
    ENCABEZADOS = ("Proveedor", "Contacto", "Teléfono", "Días de visita")
    ANCHOS = (320, 200, 160, 260)
    ALINEACIONES = ("w", "w", "w", "w")

    def __init__(
        self,
        master: Any,
        proveedor_service: ProveedorService,
        producto_service: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._servicio = proveedor_service
        self._producto_service = producto_service
        self._proveedores: list[dict[str, Any]] = []
        self._por_id: dict[str, dict[str, Any]] = {}

        self._configurar_estilos()
        self._construir_ui()
        self._atajos_teclado()
        self.cargar_datos()

    # ------------------------------------------------------------------ UI
    def _configurar_estilos(self) -> None:
        estilo_treeview("Proveedor.Treeview")

    def _construir_ui(self) -> None:
        # --- Encabezado ---
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.pack(fill="x", padx=24, pady=(18, 6))

        ctk.CTkLabel(
            cabecera,
            text="Proveedores",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family=FAMILIA, size=26, weight="bold"),
        ).pack(side="left")

        self._lbl_estado = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=(FAMILIA, 12)
        )
        self._lbl_estado.pack(side="right", padx=(12, 0))

        # --- Barra de herramientas ---
        barra = BarraAcciones(
            self,
            primaria=Accion("Agregar", self._agregar, "mas"),
            secundarias=[
                Accion("Editar", self._editar, "lapiz"),
                Accion("Productos", self._gestionar_productos, "etiqueta"),
            ],
            menu=[
                Accion("Eliminar / reactivar", self._cambiar_estado, "poder", peligro=True),
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
            placeholder_text="Buscar proveedor…  (Ctrl+F)",
        )
        self._entrada_busqueda.pack(side="left", fill="x", expand=True)
        self._var_busqueda.trace_add("write", lambda *_a: self._aplicar_filtro())

        self._var_inactivos = ctk.IntVar(value=0)
        ctk.CTkCheckBox(
            barra.izquierda,
            text="Mostrar inactivos",
            variable=self._var_inactivos,
            onvalue=1,
            offvalue=0,
            font=(FAMILIA, 12),
            command=self.cargar_datos,
        ).pack(side="left", padx=14)

        ctk.CTkLabel(
            self,
            text="Atajos:  Ins agregar  ·  Enter/F2 editar  ·  F3 productos  ·  Supr activar/desactivar  ·  Ctrl+F buscar  ·  F5 recargar",
            text_color=COLOR_SECUNDARIO,
            font=(FAMILIA, 11),
        ).pack(anchor="w", padx=26, pady=(2, 4))

        # --- Cuerpo: solo la lista ocupando todo el ancho ---
        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self._construir_lista(cuerpo)

    def _construir_lista(self, padre: ctk.CTkFrame) -> None:
        contenedor = ctk.CTkFrame(padre, fg_color="transparent")
        contenedor.pack(side="left", fill="both", expand=True)

        self._tabla = ttk.Treeview(
            contenedor,
            columns=self.COLUMNAS,
            show="headings",
            style="Proveedor.Treeview",
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
        raiz.bind("<F3>", self._atajo(self._gestionar_productos), add="+")
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
        """Recarga los proveedores y refresca la lista."""
        try:
            self._proveedores = self._servicio.listar(
                incluir_inactivos=bool(self._var_inactivos.get())
            )
        except ErrorConexionBD as exc:
            logger.error("Error de conexión al cargar proveedores: %s", exc)
            self._lbl_estado.configure(text="⚠  Sin conexión a la base de datos", text_color="#b91c1c")
            messagebox.showerror("Error de conexión", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - la UI nunca debe caer
            logger.exception("Error inesperado al cargar proveedores.")
            self._lbl_estado.configure(text="⚠  Error inesperado", text_color="#b91c1c")
            messagebox.showerror("Error", f"No se pudo cargar los proveedores:\n\n{exc}")
            return
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        texto = self._var_busqueda.get().strip().lower()
        if texto:
            visibles = [
                p
                for p in self._proveedores
                if texto in str(p.get("nombre") or "").lower()
                or texto in str(p.get("contacto") or "").lower()
                or texto in str(p.get("notas") or "").lower()
            ]
        else:
            visibles = list(self._proveedores)
        self._render(visibles, filtrado=bool(texto))

    def _render(self, proveedores: list[dict[str, Any]], filtrado: bool = False) -> None:
        seleccionado = self._obtener_seleccionado()
        id_previo = str(seleccionado["id"]) if seleccionado else None

        for item in self._tabla.get_children():
            self._tabla.delete(item)
        self._por_id = {str(p.get("id")): p for p in proveedores}

        for indice, proveedor in enumerate(proveedores):
            self._tabla.insert(
                "",
                "end",
                iid=str(proveedor.get("id")),
                values=self._formatear_fila(proveedor),
                tags=("par" if indice % 2 == 0 else "impar",)
                + (() if proveedor.get("activo", 1) else ("inactivo",)),
            )

        if id_previo and self._tabla.exists(id_previo):
            self._tabla.selection_set(id_previo)
            self._tabla.focus(id_previo)

        total = len(self._proveedores)
        mostrados = len(proveedores)
        partes = [f"{mostrados} proveedor(es)"]
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
            datos = DialogoProveedor(self, proveedor=datos, es_edicion=False).mostrar()
            if datos is None:
                return
            try:
                nuevo_id = self._servicio.crear(datos)
            except ErrorProveedor as exc:
                messagebox.showerror("No se pudo guardar", str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error inesperado al crear proveedor.")
                messagebox.showerror("Error", f"No se pudo crear el proveedor:\n\n{exc}")
                return
            self.cargar_datos()
            self._seleccionar(nuevo_id)
            self._lbl_estado.configure(text=f"Proveedor «{datos.get('nombre')}» creado.", text_color=COLOR_OK)
            return

    def _editar(self) -> None:
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        proveedor_id = int(actual["id"])
        try:
            completo = self._servicio.obtener(proveedor_id) or actual
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al obtener el proveedor id=%s.", proveedor_id)
            messagebox.showerror("Error", f"No se pudo cargar el proveedor:\n\n{exc}")
            return

        datos: dict[str, Any] | None = dict(completo)
        while True:
            datos = DialogoProveedor(self, proveedor=datos, es_edicion=True).mostrar()
            if datos is None:
                return
            try:
                self._servicio.actualizar(proveedor_id, datos)
            except ErrorProveedor as exc:
                messagebox.showerror("No se pudo guardar", str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error inesperado al actualizar proveedor id=%s.", proveedor_id)
                messagebox.showerror("Error", f"No se pudo actualizar el proveedor:\n\n{exc}")
                return
            self.cargar_datos()
            self._seleccionar(proveedor_id)
            self._lbl_estado.configure(text=f"Proveedor «{datos.get('nombre')}» actualizado.", text_color=COLOR_OK)
            return

    def _cambiar_estado(self) -> None:
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        proveedor_id = int(actual["id"])
        activo = bool(actual.get("activo", 1))
        accion = "desactivar" if activo else "reactivar"
        if not messagebox.askyesno(
            "Confirmar", f"¿Deseas {accion} el proveedor «{actual.get('nombre')}»?", parent=self
        ):
            return
        try:
            self._servicio.cambiar_activo(proveedor_id, not activo)
        except ErrorProveedor as exc:
            messagebox.showerror("No se pudo cambiar el estado", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado al cambiar estado del proveedor id=%s.", proveedor_id)
            messagebox.showerror("Error", f"No se pudo cambiar el estado:\n\n{exc}", parent=self)
            return
        self.cargar_datos()
        self._seleccionar(proveedor_id)

    def _gestionar_productos(self) -> None:
        """Abre el catálogo de productos (qué trae) del proveedor seleccionado."""
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        if self._producto_service is None:
            messagebox.showinfo(
                "No disponible",
                "La gestión de productos del proveedor no está habilitada.",
                parent=self,
            )
            return

        proveedor_id = int(actual["id"])
        try:
            completo = self._servicio.obtener(proveedor_id) or actual
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al obtener el proveedor id=%s.", proveedor_id)
            messagebox.showerror("Error", f"No se pudo cargar el proveedor:\n\n{exc}", parent=self)
            return

        resultado = DialogoProductosProveedor(
            self,
            proveedor=completo,
            proveedor_service=self._servicio,
            producto_service=self._producto_service,
        ).mostrar()
        if resultado:
            self._lbl_estado.configure(
                text=f"Catálogo de «{completo.get('nombre')}» actualizado.", text_color=COLOR_OK
            )

    # ------------------------------------------------------------ Apoyo
    def _obtener_seleccionado(self) -> dict[str, Any] | None:
        seleccion = self._tabla.selection()
        iid = seleccion[0] if seleccion else self._tabla.focus()
        if not iid:
            return None
        return self._por_id.get(str(iid))

    def _seleccionar(self, proveedor_id: int) -> None:
        iid = str(proveedor_id)
        if self._tabla.exists(iid):
            self._tabla.selection_set(iid)
            self._tabla.focus(iid)
            self._tabla.see(iid)

    def _avisar_seleccion(self) -> None:
        messagebox.showinfo(
            "Selecciona un proveedor",
            "Primero selecciona un proveedor de la lista.",
            parent=self,
        )

    @staticmethod
    def _dias_texto(dias: Any) -> str:
        """Convierte un iterable de días (1..7) en texto legible."""
        numeros = sorted({int(d) for d in (dias or [])})
        if not numeros:
            return "—"
        return ", ".join(DIAS_SEMANA_CORTOS[d - 1] for d in numeros)

    @staticmethod
    def _formatear_fila(proveedor: dict[str, Any]) -> tuple[Any, ...]:
        return (
            proveedor.get("nombre", ""),
            proveedor.get("contacto") or "—",
            proveedor.get("telefono") or "—",
            ProveedorView._dias_texto(proveedor.get("dias")),
        )
