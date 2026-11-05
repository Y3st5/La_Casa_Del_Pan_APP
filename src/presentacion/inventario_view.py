"""Vista del catálogo de inventario — capa de PRESENTACIÓN.

Muestra los productos en una tabla (``ttk.Treeview``) y permite **gestionar el
catálogo**: agregar, editar y activar/desactivar productos. No ejecuta SQL:
delega en ``ProductoService`` (inyectado desde el composition root).

Accesibilidad / atajos:
    * ``Ins``           → agregar producto.
    * ``Enter`` / ``F2`` → editar el producto seleccionado.
    * ``Supr``          → activar/desactivar el seleccionado.
    * ``Ctrl+F``        → ir al buscador.
    * ``F5``            → recargar.
    * ``Esc``           → limpiar la búsqueda.
    * Doble clic        → editar.
"""

from __future__ import annotations

import logging
from tkinter import filedialog, messagebox, ttk
from typing import Any

import customtkinter as ctk

from src.dominio.excepciones import ErrorProducto
from src.infraestructura.conexion import ErrorConexionBD
from src.presentacion.componentes import Accion, BarraAcciones
from src.presentacion.dialogos import DialogoProducto
from src.presentacion.estilos import (
    COLOR_ALERTA_FUERTE,
    COLOR_ALERTA_LEVE,
    COLOR_ERROR,
    COLOR_FILA_PAR,
    COLOR_OK,
    COLOR_SECUNDARIO,
    COLOR_TEXTO,
    estilo_treeview,
)
from src.servicios.categoria_service import CategoriaService
from src.servicios.impuesto_service import ImpuestoService
from src.servicios.producto_service import ProductoService

logger = logging.getLogger(__name__)

MONEDA = "S/"


class InventarioView(ctk.CTkFrame):
    """Catálogo de inventario con tabla y gestión de productos (CRUD)."""

    COLUMNAS = ("id", "codigo", "nombre", "categoria", "impuesto", "unidad", "precio", "costo", "stock", "estado")
    ENCABEZADOS = ("ID", "Código", "Nombre", "Categoría", "Impuesto", "Unidad", "Precio", "Costo", "Stock", "Estado")
    ANCHOS = (48, 130, 230, 110, 95, 64, 95, 95, 80, 85)
    ALINEACIONES = ("center", "center", "w", "center", "center", "center", "center", "center", "center", "center")

    def __init__(
        self,
        master: Any,
        producto_service: ProductoService,
        categoria_service: CategoriaService | None = None,
        impuesto_service: ImpuestoService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, corner_radius=0, **kwargs)
        self._servicio = producto_service
        self._categoria_servicio = categoria_service
        self._impuesto_servicio = impuesto_service
        self._productos: list[dict[str, Any]] = []
        self._por_id: dict[str, dict[str, Any]] = {}
        self._categorias: list[dict[str, Any]] = []
        self._nombre_categoria_por_id: dict[int, str] = {}
        self._impuestos: list[dict[str, Any]] = []
        self._nombre_impuesto_por_id: dict[int, str] = {}

        self._configurar_estilos()
        self._construir_ui()
        self._atajos_teclado()
        self.cargar_datos()  # Llena la tabla al mostrarse

    # ------------------------------------------------------------------ UI
    def _configurar_estilos(self) -> None:
        """Ajusta el estilo del ``ttk.Treeview`` al look de la app."""
        estilo_treeview("Inventario.Treeview")

    def _construir_ui(self) -> None:
        # --- Encabezado: título y estado ---
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.pack(fill="x", padx=24, pady=(18, 6))

        ctk.CTkLabel(
            cabecera,
            text="Inventario de Productos",
            text_color=COLOR_TEXTO,
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
        ).pack(side="left")

        self._lbl_estado = ctk.CTkLabel(
            cabecera, text="", text_color=COLOR_SECUNDARIO, font=("Segoe UI", 12)
        )
        self._lbl_estado.pack(side="right", padx=(12, 0))

        # --- Barra de herramientas ---
        barra = BarraAcciones(
            self,
            primaria=Accion("Agregar", self._agregar, "mas"),
            secundarias=[Accion("Editar", self._editar, "lapiz")],
            menu=[
                Accion("Importar productos", self._importar_productos, "recargar"),
                Accion("Eliminar / reactivar", self._cambiar_estado, "poder", peligro=True),
                Accion("Actualizar", self.cargar_datos, "recargar"),
            ],
        )
        barra.pack(fill="x", padx=24, pady=(4, 2))

        # Buscador (izquierda)
        self._var_busqueda = ctk.StringVar()
        self._entrada_busqueda = ctk.CTkEntry(
            barra.izquierda,
            textvariable=self._var_busqueda,
            width=220,
            height=34,
            placeholder_text="Buscar por nombre o código…  (Ctrl+F)",
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
            font=("Segoe UI", 12),
            command=self.cargar_datos,
        ).pack(side="left", padx=14)

        # Ayuda de atajos (accesibilidad / descubribilidad)
        ctk.CTkLabel(
            self,
            text="Atajos:  Ins agregar  ·  Enter/F2 editar  ·  Supr activar/desactivar  ·  Ctrl+F buscar  ·  F5 recargar",
            text_color=COLOR_SECUNDARIO,
            font=("Segoe UI", 11),
        ).pack(anchor="w", padx=26, pady=(2, 4))

        # --- Tabla ---
        contenedor = ctk.CTkFrame(self, fg_color="transparent")
        contenedor.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        self._tabla = ttk.Treeview(
            contenedor,
            columns=self.COLUMNAS,
            show="headings",
            style="Inventario.Treeview",
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
                minwidth=60,
            )
        self._tabla.pack(side="left", fill="both", expand=True)

        barra_scroll = ttk.Scrollbar(contenedor, orient="vertical", command=self._tabla.yview)
        self._tabla.configure(yscrollcommand=barra_scroll.set)
        barra_scroll.pack(side="right", fill="y")

        # Filas cebra para facilitar la lectura
        self._tabla.tag_configure("par", background=COLOR_FILA_PAR)
        self._tabla.tag_configure("impar", background="#ffffff")

        # RF-15: alertas de stock (van ANTES que las cebra para tener prioridad,
        # porque ttk usa la opción del primer tag que la defina).
        self._tabla.tag_configure("bajo_minimo", background=COLOR_ALERTA_LEVE)
        self._tabla.tag_configure("sin_stock", background=COLOR_ALERTA_FUERTE)
        self._tabla.tag_configure("inactivo", background="#e5e7eb", foreground="#6b7280")

        # Interacción con ratón
        self._tabla.bind("<Double-1>", lambda _e: self._editar())
        # Acciones de fila (funcionan cuando la tabla tiene el foco)
        self._tabla.bind("<Return>", lambda _e: self._editar())
        self._tabla.bind("<F2>", lambda _e: self._editar())
        self._tabla.bind("<Delete>", lambda _e: self._cambiar_estado())

    def _atajos_teclado(self) -> None:
        """Registra atajos globales, activos solo cuando esta pestaña se ve."""
        raiz = self.winfo_toplevel()
        raiz.bind("<Insert>", self._atajo(self._agregar), add="+")
        raiz.bind("<F5>", self._atajo(self.cargar_datos), add="+")
        raiz.bind("<Control-f>", self._atajo(self._enfocar_busqueda), add="+")
        raiz.bind("<Escape>", self._atajo(self._limpiar_busqueda), add="+")

    def _atajo(self, accion: Any) -> Any:
        """Envuelve una acción para ejecutarla solo si la pestaña está visible."""

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
        """Recarga el catálogo desde el servicio y aplica el filtro activo."""
        self._cargar_categorias()
        self._cargar_impuestos()
        try:
            self._productos = self._servicio.listar(
                incluir_inactivos=bool(self._var_inactivos.get())
            )
        except ErrorConexionBD as exc:
            logger.error("Error de conexión al cargar inventario: %s", exc)
            self._lbl_estado.configure(text="⚠  Sin conexión a la base de datos", text_color=COLOR_ERROR)
            messagebox.showerror("Error de conexión", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - la UI nunca debe caer
            logger.exception("Error inesperado al cargar el inventario.")
            self._lbl_estado.configure(text="⚠  Error inesperado", text_color=COLOR_ERROR)
            messagebox.showerror("Error", f"No se pudo cargar el inventario:\n\n{exc}")
            return
        self._aplicar_filtro()

    def _cargar_categorias(self) -> None:
        """Cachea las categorías activas para mostrarlas y para el formulario."""
        if self._categoria_servicio is None:
            self._categorias = []
            self._nombre_categoria_por_id = {}
            return
        try:
            self._categorias = self._categoria_servicio.listar()
        except Exception:  # noqa: BLE001 - el inventario nunca debe caer por esto
            logger.warning("No se pudieron cargar las categorías.")
            self._categorias = []
        self._nombre_categoria_por_id = {
            int(c["id"]): str(c["nombre"])
            for c in self._categorias
            if c.get("id") is not None
        }

    def _cargar_impuestos(self) -> None:
        """Cachea los impuestos activos para la tabla y el formulario."""
        if self._impuesto_servicio is None:
            self._impuestos = []
            self._nombre_impuesto_por_id = {}
            return
        try:
            self._impuestos = self._impuesto_servicio.listar()
        except Exception:  # noqa: BLE001 - el inventario nunca debe caer por esto
            logger.warning("No se pudieron cargar los impuestos.")
            self._impuestos = []
        self._nombre_impuesto_por_id = {
            int(i["id"]): str(i["nombre"])
            for i in self._impuestos
            if i.get("id") is not None
        }

    def _aplicar_filtro(self) -> None:
        """Filtra la lista cargada por el texto del buscador y repinta la tabla."""
        texto = self._var_busqueda.get().strip().lower()
        if texto:
            visibles = [
                p
                for p in self._productos
                if texto in str(p.get("nombre") or "").lower()
                or texto in str(p.get("codigo_interno") or "").lower()
                or texto in str(p.get("codigo_barras") or "").lower()
            ]
        else:
            visibles = list(self._productos)
        self._render(visibles, filtrado=bool(texto))

    def _render(self, productos: list[dict[str, Any]], filtrado: bool = False) -> None:
        """Pinta las filas de la tabla y actualiza el estado."""
        for item in self._tabla.get_children():
            self._tabla.delete(item)
        self._por_id = {str(p.get("id")): p for p in productos}

        alertas = 0
        for indice, producto in enumerate(productos):
            nivel = self._nivel_alerta(producto)
            if nivel:
                alertas += 1
            self._tabla.insert(
                "",
                "end",
                iid=str(producto.get("id")),
                values=self._formatear_fila(producto),
                tags=self._tags_fila(indice, nivel, bool(producto.get("activo", 1))),
            )

        total = len(self._productos)
        mostrados = len(productos)
        partes = [f"{mostrados} producto(s)"]
        if filtrado:
            partes.append(f"filtrado de {total}")
        if alertas:
            partes.append(f"{alertas} en alerta de stock")
        self._lbl_estado.configure(
            text=" · ".join(partes),
            text_color=COLOR_ALERTA_FUERTE if alertas else (COLOR_OK if mostrados else COLOR_SECUNDARIO),
        )

    # ------------------------------------------------------------- CRUD
    def _agregar(self) -> None:
        """Abre el formulario para crear un producto (reintenta si falla)."""
        self._cargar_categorias()
        self._cargar_impuestos()
        datos: dict[str, Any] | None = None
        while True:
            datos = DialogoProducto(
                self,
                producto=datos,
                es_edicion=False,
                categorias=self._categorias,
                impuestos=self._impuestos,
            ).mostrar()
            if datos is None:
                return
            try:
                nuevo_id = self._servicio.crear(datos)
            except ErrorProducto as exc:
                messagebox.showerror("No se pudo guardar", str(exc))
                continue  # Reabre con los datos ya cargados
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error inesperado al crear producto.")
                messagebox.showerror("Error", f"No se pudo crear el producto:\n\n{exc}")
                return
            self.cargar_datos()
            self._seleccionar(nuevo_id)
            self._lbl_estado.configure(
                text=f"Producto «{datos.get('nombre')}» creado.", text_color=COLOR_OK
            )
            return

    def _editar(self) -> None:
        """Abre el formulario para editar el producto seleccionado."""
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        producto_id = int(actual["id"])
        try:
            completo = self._servicio.obtener(producto_id) or actual
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al obtener el producto id=%s.", producto_id)
            messagebox.showerror("Error", f"No se pudo cargar el producto:\n\n{exc}")
            return

        datos: dict[str, Any] | None = dict(completo)
        self._cargar_categorias()
        self._cargar_impuestos()
        while True:
            datos = DialogoProducto(
                self,
                producto=datos,
                es_edicion=True,
                categorias=self._categorias,
                impuestos=self._impuestos,
            ).mostrar()
            if datos is None:
                return
            try:
                self._servicio.actualizar(producto_id, datos)
            except ErrorProducto as exc:
                messagebox.showerror("No se pudo guardar", str(exc))
                continue  # Reabre con los datos ya cargados
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error inesperado al actualizar producto id=%s.", producto_id)
                messagebox.showerror("Error", f"No se pudo actualizar el producto:\n\n{exc}")
                return
            self.cargar_datos()
            self._seleccionar(producto_id)
            self._lbl_estado.configure(
                text=f"Producto «{datos.get('nombre')}» actualizado.", text_color=COLOR_OK
            )
            return

    def _cambiar_estado(self) -> None:
        """Activa o desactiva (baja lógica) el producto seleccionado."""
        actual = self._obtener_seleccionado()
        if actual is None:
            self._avisar_seleccion()
            return
        producto_id = int(actual["id"])
        activo = bool(actual.get("activo", 1))
        accion = "desactivar" if activo else "reactivar"
        if not messagebox.askyesno(
            "Confirmar",
            f"¿Deseas {accion} el producto «{actual.get('nombre')}»?",
            parent=self,
        ):
            return
        try:
            self._servicio.cambiar_activo(producto_id, not activo)
        except ErrorProducto as exc:
            messagebox.showerror("No se pudo cambiar el estado", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error inesperado al cambiar estado del producto id=%s.", producto_id)
            messagebox.showerror("Error", f"No se pudo cambiar el estado:\n\n{exc}", parent=self)
            return
        self.cargar_datos()
        self._seleccionar(producto_id)
        verbo = "desactivado" if activo else "reactivado"
        self._lbl_estado.configure(
            text=f"Producto «{actual.get('nombre')}» {verbo}.", text_color=COLOR_OK
        )

    def _obtener_seleccionado(self) -> dict[str, Any] | None:
        """Devuelve el producto seleccionado (o el que tiene el foco)."""
        seleccion = self._tabla.selection()
        iid = seleccion[0] if seleccion else self._tabla.focus()
        if not iid:
            return None
        return self._por_id.get(str(iid))

    def _seleccionar(self, producto_id: int) -> None:
        """Selecciona y muestra la fila del producto indicado (si está visible)."""
        iid = str(producto_id)
        if self._tabla.exists(iid):
            self._tabla.selection_set(iid)
            self._tabla.focus(iid)
            self._tabla.see(iid)

    def _avisar_seleccion(self) -> None:
        messagebox.showinfo(
            "Selecciona un producto",
            "Primero selecciona un producto de la tabla.",
            parent=self,
        )

    # ------------------------------------------------------------- Alertas
    @staticmethod
    def _nivel_alerta(producto: dict[str, Any]) -> str | None:
        """Nivel de alerta de un producto (RF-15).

        Returns:
            ``"sin_stock"`` si ``stock_actual <= 0``; ``"bajo_minimo"`` si
            ``0 < stock_actual <= stock_minimo`` (mínimo nulo se asume 0);
            ``None`` en caso contrario.
        """
        stock = float(producto.get("stock_actual") or 0)
        minimo = producto.get("stock_minimo")
        minimo = 0.0 if minimo is None else float(minimo)

        if stock <= 0:
            return "sin_stock"
        if stock <= minimo:
            return "bajo_minimo"
        return None

    @classmethod
    def _tags_fila(
        cls, indice: int, nivel_alerta: str | None, activo: bool
    ) -> tuple[str, ...]:
        """Tags de la fila: la alerta primero (tiene prioridad de color)."""
        tags: list[str] = [nivel_alerta] if nivel_alerta else []
        if not activo:
            tags.append("inactivo")
        tags.append("par" if indice % 2 == 0 else "impar")
        return tuple(tags)

    def _importar_productos(self) -> None:
        """Abre un diálogo para seleccionar un archivo Excel/CSV y lo importa."""
        try:
            ruta = filedialog.askopenfilename(
                title="Seleccionar archivo para importar productos",
                filetypes=[
                    ("Archivos Excel (.xlsx)", "*.xlsx"),
                    ("Archivos CSV", "*.csv"),
                    ("Todos los archivos", "*.*"),
                ],
                parent=self,
            )
            if not ruta:
                return

            resultado = self._servicio.importar_productos(ruta)

            creados = resultado.get("creados", 0)
            actualizados = resultado.get("actualizados", 0)
            errores = resultado.get("errores", 0)
            saltados = resultado.get("saltados", 0)
            total = resultado.get("total", 0)

            mensaje = (
                "Importación completada.\n\n"
                f"Total de filas procesadas: {total}\n"
                f"Productos creados: {creados}\n"
                f"Productos actualizados: {actualizados}\n"
                f"Filas saltadas: {saltados}\n"
                f"Errores: {errores}\n\n"
                "Los productos que ya existían (mismo código o mismo nombre) "
                "se sobrescribieron con los datos del archivo; los que no "
                "existían se crearon nuevos."
            )

            if errores > 0 and resultado.get("detalle_errores"):
                detalles = "\n\n".join(resultado["detalle_errores"][:10])
                mensaje += f"\n\nPrimeros errores:\n{detalles}"

            if errores == 0:
                messagebox.showinfo("Importación exitosa", mensaje, parent=self)
            else:
                messagebox.showwarning("Importación con errores", mensaje, parent=self)

            self.cargar_datos()
            self._lbl_estado.configure(
                text=f"Importados {creados} nuevos, {actualizados} actualizados.",
                text_color=COLOR_OK,
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("Error al importar productos desde archivo.")
            messagebox.showerror(
                "Error al importar",
                f"No se pudo importar el archivo:\n\n{exc}",
                parent=self,
            )

    def _formatear_fila(self, producto: dict[str, Any]) -> tuple[Any, ...]:
        """Convierte una fila de BD en los valores que muestra la tabla."""
        precio = float(producto.get("precio_venta") or 0)
        costo = float(producto.get("costo") or 0)
        stock = float(producto.get("stock_actual") or 0)
        codigo = producto.get("codigo_barras") or producto.get("codigo_interno") or "-"
        categoria_id = producto.get("categoria_id")
        categoria = self._nombre_categoria_por_id.get(
            int(categoria_id), "Sin categoría"
        ) if categoria_id not in (None, "") else "Sin categoría"
        impuesto_id = producto.get("impuesto_id")
        impuesto = self._nombre_impuesto_por_id.get(
            int(impuesto_id), "-"
        ) if impuesto_id not in (None, "") else "-"
        return (
            producto.get("id", ""),
            codigo,
            producto.get("nombre", ""),
            categoria,
            impuesto,
            producto.get("unidad_medida") or "UND",
            f"{MONEDA} {precio:,.2f}",
            f"{MONEDA} {costo:,.2f}",
            f"{stock:g}",
            "Activo" if producto.get("activo", 1) else "Inactivo",
        )
