"""Servicio de productos — capa de SERVICIOS.

Centraliza las operaciones del catálogo (listar, crear, editar, activar y
desactivar) y valida las reglas de negocio antes de delegar en el repositorio.

La presentación **no ejecuta SQL ni conoce el repositorio**: depende solo de
este servicio (ver ``Arquitectura.md`` y ``Modelo de Datos``).
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from src.dominio.excepciones import DatosProductoInvalidos
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.importador_productos import leer_archivo
from src.repositorios.producto_repo import ProductoRepo

logger = logging.getLogger(__name__)

#: Unidades de medida admitidas por el catálogo (coincide con el DDL).
UNIDADES_MEDIDA: tuple[str, ...] = ("UND", "KG", "GR", "LT", "ML", "DOCENA", "PAQUETE")


class ProductoService:
    """Casos de uso de gestión del catálogo de productos."""

    #: Campo de ``productos`` → columna de la que proviene en el archivo.
    _CAMPO_A_COLUMNA: dict[str, str] = {"categoria_id": "categoria"}

    def __init__(
        self,
        conexion_bd: ConexionBD | None = None,
        producto_repo: ProductoRepo | None = None,
        categoria_repo: Any | None = None,
    ) -> None:
        """Inicializa el servicio.

        Args:
            conexion_bd: conexión a inyectar al repositorio si no se pasa uno.
            producto_repo: repositorio ya construido (útil para tests y para
                compartir instancia desde el composition root).
            categoria_repo: repositorio de categorías (opcional). Se usa para
                clasificar productos al **importar**; si se omite, no se
                resuelve la categoría por nombre.
        """
        self._producto_repo = producto_repo or ProductoRepo(conexion_bd)
        self._categoria_repo = categoria_repo

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def listar(self, incluir_inactivos: bool = False) -> list[dict[str, Any]]:
        """Devuelve el catálogo completo (o solo los activos) ordenado por nombre."""
        return self._producto_repo.listar_todos(incluir_inactivos=incluir_inactivos)

    def obtener(self, producto_id: int) -> dict[str, Any] | None:
        """Devuelve un producto por su id (o ``None`` si no existe)."""
        return self._producto_repo.obtener_por_id(producto_id)

    def buscar_por_codigo(self, codigo: str) -> dict[str, Any] | None:
        """Busca un producto por código de barras o interno (lector HID)."""
        return self._producto_repo.buscar_por_codigo(codigo)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def crear(self, datos: dict[str, Any]) -> int:
        """Valida y crea un producto nuevo. Devuelve el id asignado.

        Raises:
            DatosProductoInvalidos: si los datos no cumplen las reglas.
            ProductoDuplicado: si el código de barras o interno ya existe.
        """
        limpio = self._normalizar(datos)
        nuevo_id = self._producto_repo.crear(limpio)
        logger.info("Producto creado id=%s (%s).", nuevo_id, limpio["nombre"])
        return nuevo_id

    def actualizar(self, producto_id: int, datos: dict[str, Any]) -> None:
        """Valida y actualiza un producto existente.

        Raises:
            DatosProductoInvalidos: si los datos no cumplen las reglas.
            ProductoNoEncontrado: si el id no existe.
            ProductoDuplicado: si el código de barras o interno ya existe.
        """
        limpio = self._normalizar(datos)
        self._producto_repo.actualizar(producto_id, limpio)
        logger.info("Producto actualizado id=%s (%s).", producto_id, limpio["nombre"])

    def cambiar_activo(self, producto_id: int, activo: bool) -> None:
        """Activa o desactiva (baja lógica) un producto.

        Raises:
            ProductoNoEncontrado: si el id no existe.
        """
        self._producto_repo.cambiar_activo(producto_id, activo)
        estado = "activado" if activo else "desactivado"
        logger.info("Producto id=%s %s.", producto_id, estado)

    # ------------------------------------------------------------------
    # Validación / normalización
    # ------------------------------------------------------------------
    def _normalizar(self, datos: dict[str, Any]) -> dict[str, Any]:
        """Valida y normaliza los datos recibidos desde la presentación.

        Raises:
            DatosProductoInvalidos: ante cualquier dato faltante o inválido.
        """
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise DatosProductoInvalidos("El nombre del producto es obligatorio.")

        codigo_interno = str(datos.get("codigo_interno") or "").strip()
        if not codigo_interno:
            raise DatosProductoInvalidos("El código interno es obligatorio.")

        unidad = str(datos.get("unidad_medida") or "UND").strip().upper()
        if unidad not in UNIDADES_MEDIDA:
            raise DatosProductoInvalidos(
                f"Unidad de medida no válida: '{unidad}'. "
                f"Use una de: {', '.join(UNIDADES_MEDIDA)}."
            )

        # La categoría es obligatoria (clasifica la venta por tipo de negocio).
        categoria_id = datos.get("categoria_id")
        if categoria_id in (None, ""):
            raise DatosProductoInvalidos(
                "La categoría es obligatoria. Selecciona una (p. ej. Panes, Abarrotes, Cafetería)."
            )
        try:
            categoria_id = int(categoria_id)
        except (TypeError, ValueError) as exc:
            raise DatosProductoInvalidos("La categoría seleccionada no es válida.") from exc

        # Impuesto aplicable (opcional). Si no se indica, queda ``None`` y el
        # cálculo de la venta asume el IGV 18 % por defecto.
        impuesto_id = datos.get("impuesto_id")
        if impuesto_id in (None, ""):
            impuesto_id = None
        else:
            try:
                impuesto_id = int(impuesto_id)
            except (TypeError, ValueError) as exc:
                raise DatosProductoInvalidos("El impuesto seleccionado no es válido.") from exc

        return {
            "codigo_barras": str(datos.get("codigo_barras") or "").strip() or None,
            "codigo_interno": codigo_interno,
            "nombre": nombre,
            "descripcion": str(datos.get("descripcion") or "").strip() or None,
            "precio_venta": self._a_numero(datos.get("precio_venta"), "precio de venta"),
            "costo": self._a_numero(datos.get("costo"), "costo", opcional=True),
            "categoria_id": categoria_id,
            "impuesto_id": impuesto_id,
            "unidad_medida": unidad,
            "es_pesable": bool(datos.get("es_pesable")),
            "controla_stock": bool(datos.get("controla_stock", True)),
            "stock_actual": self._a_numero(datos.get("stock_actual"), "stock actual"),
            "stock_minimo": self._a_numero(datos.get("stock_minimo"), "stock mínimo"),
            "activo": bool(datos.get("activo", True)),
        }

    def importar_productos(self, ruta_archivo: str) -> dict[str, Any]:
        """Importa productos desde Excel o CSV."""
        try:
            from src.infraestructura.importador_productos import leer_archivo

            filas = leer_archivo(ruta_archivo)
        except Exception as exc:  # noqa: BLE001
            raise DatosProductoInvalidos(f"No se pudo leer el archivo: {exc}") from exc

        creados = 0
        actualizados = 0
        errores = 0
        saltados = 0
        detalle_errores: list[str] = []

        # Pre-resolver las categorías del archivo por nombre (creando las
        # nuevas) antes de abrir la transacción de importación.
        mapa_categorias = self._preparar_categorias(filas)

        # Índice ``nombre normalizado → id`` para emparejar productos que ya
        # existen aunque el archivo no traiga código (evita duplicados).
        mapa_nombres = self._indice_nombres()

        with self._producto_repo._bd.cursor() as (conexion, cursor):
            for i, fila in enumerate(filas, start=2):
                try:
                    nombre = str(fila.get("nombre") or "").strip()
                    if not nombre:
                        saltados += 1
                        continue

                    codigo_barras = str(fila.get("codigo_barras") or "").strip() or None
                    codigo_interno = str(fila.get("codigo_interno") or "").strip() or None

                    clave_nombre = self._normalizar_nombre(nombre)

                    # Criterios de coincidencia, en orden: código de barras →
                    # código interno → nombre (sin tildes ni mayúsculas).
                    existe = None
                    if codigo_barras:
                        existe = self._producto_repo.obtener_por_codigo_barras(codigo_barras)
                    if existe is None and codigo_interno:
                        existe = self._producto_repo.obtener_por_codigo_interno(codigo_interno)
                    if existe is None and clave_nombre:
                        pid_por_nombre = mapa_nombres.get(clave_nombre)
                        if pid_por_nombre is not None:
                            existe = {"id": pid_por_nombre}

                    datos = {
                        "codigo_barras": codigo_barras,
                        "codigo_interno": codigo_interno,
                        "nombre": nombre,
                        "descripcion": str(fila.get("descripcion") or "").strip() or None,
                        "precio_venta": self._a_numero(fila.get("precio_venta"), "precio de venta"),
                        "costo": self._a_numero(fila.get("costo"), "costo", opcional=True),
                        "categoria_id": self._categoria_id_de_fila(fila, mapa_categorias),
                        "unidad_medida": self._normalizar_unidad(fila.get("unidad_medida")),
                        "es_pesable": self._a_bool(fila.get("es_pesable")),
                        "controla_stock": self._a_bool(fila.get("controla_stock"), default=True),
                        "stock_actual": self._a_numero(fila.get("stock_actual"), "stock actual"),
                        "stock_minimo": self._a_numero(fila.get("stock_minimo"), "stock mínimo"),
                        "activo": self._a_bool(fila.get("activo"), default=True),
                    }

                    if existe:
                        pid = int(existe["id"])
                        # Actualización PARCIAL: solo se sobrescriben los campos
                        # que el archivo trae con valor. Las celdas vacías y las
                        # columnas ausentes conservan lo que ya tenía el producto
                        # (así el import no borra códigos, categoría ni costo).
                        datos_update = {
                            campo: valor
                            for campo, valor in datos.items()
                            if self._trae_valor(fila.get(self._CAMPO_A_COLUMNA.get(campo, campo)))
                        }
                        datos_update["nombre"] = nombre
                        self._producto_repo.actualizar_parcial(pid, datos_update, cursor=cursor)
                        actualizados += 1
                    else:
                        if not datos.get("codigo_interno"):
                            datos["codigo_interno"] = (
                                nombre.replace(" ", "_").upper()[:30] or str(i)
                            )
                        pid = int(self._producto_repo.crear(datos))
                        creados += 1

                    # Registrar el nombre en el índice para que las filas
                    # siguientes del mismo archivo coincidan entre sí.
                    if clave_nombre:
                        mapa_nombres[clave_nombre] = pid
                except Exception as exc:  # noqa: BLE001
                    errores += 1
                    detalle_errores.append(f"Fila {i}: {exc}")
                    logger.warning("Error importando fila %s: %s", i, exc)

            conexion.commit()

        return {
            "total": len(filas),
            "creados": creados,
            "actualizados": actualizados,
            "errores": errores,
            "saltados": saltados,
            "detalle_errores": detalle_errores,
        }

    def _preparar_categorias(self, filas: list[dict[str, Any]]) -> dict[str, int]:
        """Resuelve ``nombre de categoría → id`` y crea las categorías nuevas.

        Se ejecuta **antes** de la transacción de importación para no anidar
        escrituras en la misma conexión SQLite. Si no hay repositorio de
        categorías inyectado, devuelve un mapa vacío (todo queda sin clasificar).
        """
        if self._categoria_repo is None:
            return {}
        try:
            existentes = {
                str(c["nombre"]).strip().lower(): int(c["id"])
                for c in self._categoria_repo.listar(incluir_inactivas=True)
            }
        except Exception:  # noqa: BLE001 - la importación no debe caer por esto
            logger.warning("No se pudieron leer las categorías para la importación.")
            return {}

        nombres: list[str] = []
        for fila in filas:
            nombre = str(fila.get("categoria") or "").strip()
            if nombre and nombre.lower() not in existentes:
                nombres.append(nombre)

        for nombre in dict.fromkeys(nombres):  # dedup conservando el orden
            try:
                existentes[nombre.lower()] = self._categoria_repo.crear(
                    {"nombre": nombre, "orden": 0, "activo": True}
                )
            except Exception:  # noqa: BLE001 - posible colisión simultánea
                existente = self._categoria_repo.obtener_por_nombre(nombre)
                if existente is not None:
                    existentes[nombre.lower()] = int(existente["id"])
        return existentes

    def _indice_nombres(self) -> dict[str, int]:
        """Mapa ``nombre normalizado → id`` de todo el catálogo.

        Incluye los productos inactivos: importar un producto desactivado debe
        **reactivarlo y sobrescribirlo**, no crear un duplicado. Si no se puede
        leer el catálogo, devuelve un mapa vacío (la importación sigue).
        """
        try:
            productos = self._producto_repo.listar_todos(incluir_inactivos=True)
        except Exception:  # noqa: BLE001 - la importación no debe caer por esto
            logger.warning("No se pudo leer el catálogo para emparejar por nombre.")
            return {}

        indice: dict[str, int] = {}
        for producto in productos:
            clave = self._normalizar_nombre(producto.get("nombre"))
            if clave:
                indice.setdefault(clave, int(producto["id"]))
        return indice

    @staticmethod
    def _normalizar_nombre(valor: Any) -> str:
        """Normaliza un nombre para compararlo.

        Quita tildes, pasa a minúsculas y colapsa los espacios: así
        ``"Azúcar  Rubia"`` y ``"AZUCAR RUBIA"`` se consideran el mismo nombre.
        """
        texto = str(valor or "").strip()
        if not texto:
            return ""
        sin_tildes = "".join(
            caracter
            for caracter in unicodedata.normalize("NFKD", texto)
            if not unicodedata.combining(caracter)
        )
        return " ".join(sin_tildes.lower().split())

    @staticmethod
    def _trae_valor(valor: Any) -> bool:
        """``True`` si la celda del archivo trae un valor (no está vacía)."""
        return valor is not None and str(valor).strip() != ""

    @staticmethod
    def _categoria_id_de_fila(fila: dict[str, Any], mapa: dict[str, int]) -> int | None:
        """Devuelve el id de categoría de una fila importada (o ``None``)."""
        nombre = str(fila.get("categoria") or "").strip()
        if not nombre:
            return None
        return mapa.get(nombre.lower())

    @staticmethod
    def _normalizar_unidad(v: Any) -> str:
        if not v:
            return "UND"
        u = str(v).strip().upper()
        if u in UNIDADES_MEDIDA:
            return u
        return "UND"

    @staticmethod
    def _a_bool(valor: Any, default: bool = False) -> bool:
        if valor is None:
            return default
        if isinstance(valor, bool):
            return valor
        s = str(valor).strip().upper()
        if s in ("1", "SI", "S", "YES", "Y", "TRUE", "VERDADERO"):
            return True
        if s in ("0", "NO", "N", "FALSE", "FALSO", "F"):
            return False
        return default

    @staticmethod
    def _a_numero(valor: Any, etiqueta: str, opcional: bool = False) -> float | None:
        """Convierte ``valor`` a ``float`` (admite coma decimal) validando ≥ 0.

        Raises:
            DatosProductoInvalidos: si no es un número o es negativo.
        """
        if valor is None or (isinstance(valor, str) and not valor.strip()):
            if opcional:
                return None
            valor = 0
        try:
            numero = float(str(valor).replace(",", ".").strip())
        except (TypeError, ValueError) as exc:
            raise DatosProductoInvalidos(f"El {etiqueta} debe ser un número.") from exc
        if numero < 0:
            raise DatosProductoInvalidos(f"El {etiqueta} no puede ser negativo.")
        return numero
