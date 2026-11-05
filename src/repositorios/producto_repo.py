"""Repositorio de productos — capa de REPOSITORIOS.

Único lugar autorizado para ejecutar SQL sobre la tabla ``productos``.
No contiene reglas de negocio ni código de interfaz.

El patrón Repositorio aísla la base de datos: cambiar de MySQL a SQLite solo
ha afectado a la capa de infraestructura; esta clase se limita a adaptarse al
cursor de ``sqlite3`` y a devolver diccionarios.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from src.infraestructura.conexion import ConexionBD
from src.dominio.excepciones import ProductoDuplicado, ProductoNoEncontrado, StockInsuficiente

logger = logging.getLogger(__name__)

#: Descuento atómico: solo actualiza si hay stock suficiente (o no se controla).
SQL_DESCUENTO_STOCK = (
    "UPDATE productos "
    "SET stock_actual = stock_actual - ?, "
    "    version = version + 1, "
    "    actualizado_en = CURRENT_TIMESTAMP "
    "WHERE id = ? AND (controla_stock = 0 OR stock_actual >= ?)"
)

SQL_STOCK_ACTUAL = "SELECT stock_actual FROM productos WHERE id = ?"

#: Aumento atómico de stock (compras / ingresos de proveedores / ajustes positivos).
SQL_AUMENTO_STOCK = (
    "UPDATE productos "
    "SET stock_actual = stock_actual + ?, "
    "    version = version + 1, "
    "    actualizado_en = CURRENT_TIMESTAMP "
    "WHERE id = ?"
)

SQL_OBTENER_POR_CB = (
    "SELECT id, codigo_barras, codigo_interno, nombre, precio_venta, "
    "       stock_actual, stock_minimo, unidad_medida, es_pesable, "
    "       controla_stock, activo, descripcion, costo, categoria_id, impuesto_id "
    "FROM productos "
    "WHERE codigo_barras = ?"
)

SQL_OBTENER_POR_CI = (
    "SELECT id, codigo_barras, codigo_interno, nombre, precio_venta, "
    "       stock_actual, stock_minimo, unidad_medida, es_pesable, "
    "       controla_stock, activo, descripcion, costo, categoria_id, impuesto_id "
    "FROM productos "
    "WHERE codigo_interno = ?"
)

#: Búsqueda por texto parcial (nombre o código) para el autocompletado de la caja.
SQL_BUSCAR_TEXTO = (
    "SELECT id, codigo_barras, codigo_interno, nombre, precio_venta, "
    "       stock_actual, unidad_medida, es_pesable, impuesto_id "
    "FROM productos "
    "WHERE activo = 1 "
    "  AND (nombre LIKE ? COLLATE NOCASE "
    "       OR codigo_barras LIKE ? COLLATE NOCASE "
    "       OR codigo_interno LIKE ? COLLATE NOCASE) "
    "ORDER BY nombre ASC "
    "LIMIT ?"
)

#: Columnas que se devuelven al leer el catálogo completo (edición/gestión).
COLUMNAS_DETALLE = (
    "id",
    "codigo_barras",
    "codigo_interno",
    "nombre",
    "descripcion",
    "precio_venta",
    "costo",
    "categoria_id",
    "impuesto_id",
    "unidad_medida",
    "es_pesable",
    "controla_stock",
    "stock_actual",
    "stock_minimo",
    "activo",
    "version",
)

#: Orden de columnas del INSERT (coincide con ``_parametros``).
SQL_INSERT_PRODUCTO = (
    "INSERT INTO productos "
    "    (codigo_barras, codigo_interno, nombre, descripcion, precio_venta, costo, "
    "     categoria_id, impuesto_id, unidad_medida, es_pesable, controla_stock, "
    "     stock_actual, stock_minimo, activo) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

SQL_ACTUALIZAR_PRODUCTO = (
    "UPDATE productos SET "
    "    codigo_barras = ?, codigo_interno = ?, nombre = ?, descripcion = ?, "
    "    precio_venta = ?, costo = ?, categoria_id = ?, impuesto_id = ?, "
    "    unidad_medida = ?, es_pesable = ?, "
    "    controla_stock = ?, stock_actual = ?, stock_minimo = ?, activo = ?, "
    "    version = version + 1, actualizado_en = CURRENT_TIMESTAMP "
    "WHERE id = ?"
)

SQL_CAMBIAR_ACTIVO = (
    "UPDATE productos SET activo = ?, version = version + 1, "
    "    actualizado_en = CURRENT_TIMESTAMP WHERE id = ?"
)


class ProductoRepo:
    """Acceso a datos de la tabla ``productos`` (SQLite)."""

    def __init__(self, conexion_bd: ConexionBD | None = None) -> None:
        """Inicializa el repositorio.

        Args:
            conexion_bd: conexión inyectada (útil para tests). Si se omite,
                se crea una ``ConexionBD`` con la configuración por defecto.
        """
        self._bd = conexion_bd or ConexionBD()

    def listar_todos(self, incluir_inactivos: bool = False) -> list[dict[str, Any]]:
        """Devuelve el catálogo, ordenado por nombre.

        Args:
            incluir_inactivos: si es ``True``, incluye también los productos
                desactivados (``activo = 0``).

        Returns:
            Lista de diccionarios con todas las columnas de
            :data:`COLUMNAS_DETALLE`.

        Raises:
            ErrorConexionBD: si falla el acceso a la base de datos.
        """
        columnas = ", ".join(COLUMNAS_DETALLE)
        sql = f"SELECT {columnas} FROM productos"
        if not incluir_inactivos:
            sql += " WHERE activo = 1"
        sql += " ORDER BY nombre ASC"

        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(sql)
            # sqlite3.Row -> dict para que la capa de presentación no dependa
            # del driver de base de datos.
            filas: list[dict[str, Any]] = [dict(fila) for fila in cursor.fetchall()]

        logger.info("Catálogo cargado: %d producto(s).", len(filas))
        return filas

    def obtener_por_id(self, producto_id: int) -> dict[str, Any] | None:
        """Devuelve un producto por su id (o ``None`` si no existe)."""
        columnas = ", ".join(COLUMNAS_DETALLE)
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(f"SELECT {columnas} FROM productos WHERE id = ?", (int(producto_id),))
            fila = cursor.fetchone()
        return dict(fila) if fila is not None else None

    def crear(self, datos: dict[str, Any]) -> int:
        """Inserta un producto nuevo y devuelve su id.

        Raises:
            ProductoDuplicado: si el código de barras o interno ya existe.
            ErrorConexionBD: si falla el acceso a la base de datos.
        """
        try:
            with self._bd.cursor() as (_conexion, cursor):
                cursor.execute(SQL_INSERT_PRODUCTO, self._parametros(datos))
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise self._traducir_integridad(exc) from exc

    def actualizar(self, producto_id: int, datos: dict[str, Any]) -> None:
        """Actualiza un producto existente (incrementa ``version``).

        Raises:
            ProductoNoEncontrado: si el id no existe.
            ProductoDuplicado: si algún código colisiona con otro producto.
            ErrorConexionBD: si falla el acceso a la base de datos.
        """
        try:
            with self._bd.cursor() as (_conexion, cursor):
                cursor.execute(
                    SQL_ACTUALIZAR_PRODUCTO, self._parametros(datos) + (int(producto_id),)
                )
                if cursor.rowcount == 0:
                    raise ProductoNoEncontrado(
                        f"No se encontró el producto con id={producto_id}."
                    )
        except sqlite3.IntegrityError as exc:
            raise self._traducir_integridad(exc) from exc

    def cambiar_activo(self, producto_id: int, activo: bool) -> None:
        """Activa o desactiva (baja lógica) un producto.

        Raises:
            ProductoNoEncontrado: si el id no existe.
            ErrorConexionBD: si falla el acceso a la base de datos.
        """
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_CAMBIAR_ACTIVO, (1 if activo else 0, int(producto_id)))
            if cursor.rowcount == 0:
                raise ProductoNoEncontrado(f"No se encontró el producto con id={producto_id}.")

    @staticmethod
    def _parametros(datos: dict[str, Any]) -> tuple[Any, ...]:
        """Normaliza un diccionario de producto al orden de columnas del SQL."""
        costo = datos.get("costo")
        categoria_id = datos.get("categoria_id")
        impuesto_id = datos.get("impuesto_id")
        return (
            datos.get("codigo_barras") or None,
            str(datos.get("codigo_interno") or "").strip(),
            str(datos.get("nombre") or "").strip(),
            datos.get("descripcion") or None,
            round(float(datos.get("precio_venta") or 0), 2),
            None if costo in (None, "") else round(float(costo), 2),
            None if categoria_id in (None, "") else int(categoria_id),
            None if impuesto_id in (None, "") else int(impuesto_id),
            str(datos.get("unidad_medida") or "UND").strip().upper(),
            1 if datos.get("es_pesable") else 0,
            1 if datos.get("controla_stock", True) else 0,
            round(float(datos.get("stock_actual") or 0), 3),
            round(float(datos.get("stock_minimo") or 0), 3),
            1 if datos.get("activo", True) else 0,
        )

    @staticmethod
    def _traducir_integridad(exc: sqlite3.IntegrityError) -> Exception:
        """Convierte un error de unicidad de SQLite en un error de dominio."""
        mensaje = str(exc).lower()
        if "codigo_barras" in mensaje:
            return ProductoDuplicado("Ya existe un producto con ese código de barras.")
        if "codigo_interno" in mensaje:
            return ProductoDuplicado("Ya existe un producto con ese código interno.")
        return ProductoDuplicado(f"No se pudo guardar el producto: {exc}")

    def buscar_por_codigo(self, codigo: str) -> dict[str, Any] | None:
        """Busca un producto por código de barras **o** código interno.

        Es el método que usa el lector HID: recibe el texto "tecleado" por el
        escáner y devuelve el producto (o ``None`` si no existe).

        Args:
            codigo: código escaneado/ingresado. Se ignoran espacios sobrantes
                y no distingue mayúsculas/minúsculas.

        Returns:
            Diccionario del producto con ``id``, ``codigo_barras``,
            ``codigo_interno``, ``nombre``, ``precio_venta``, ``stock_actual``,
            ``unidad_medida`` y ``es_pesable``; o ``None`` si no se encuentra.

        Raises:
            ErrorConexionBD: si falla el acceso a la base de datos.
        """
        codigo = (codigo or "").strip()
        if not codigo:
            return None

        sql = (
            "SELECT id, codigo_barras, codigo_interno, nombre, precio_venta, "
            "       stock_actual, unidad_medida, es_pesable "
            "FROM productos "
            "WHERE activo = 1 "
            "  AND (codigo_barras = ? COLLATE NOCASE "
            "       OR codigo_interno = ? COLLATE NOCASE) "
            "LIMIT 1"
        )
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(sql, (codigo, codigo))
            fila = cursor.fetchone()

        producto = dict(fila) if fila is not None else None
        if producto is not None:
            logger.debug("Producto encontrado para '%s': %s", codigo, producto["nombre"])
        return producto

    def obtener_por_codigo_barras(self, codigo: str) -> dict[str, Any] | None:
        if not codigo:
            return None
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_OBTENER_POR_CB, (str(codigo).strip(),))
            fila = cursor.fetchone()
            return dict(fila) if fila else None

    def obtener_por_codigo_interno(self, codigo: str) -> dict[str, Any] | None:
        if not codigo:
            return None
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_OBTENER_POR_CI, (str(codigo).strip(),))
            fila = cursor.fetchone()
            return dict(fila) if fila else None

    def buscar_por_texto(self, texto: str, limite: int = 8) -> list[dict[str, Any]]:
        """Busca productos por **nombre o código** con coincidencia parcial.

        Pensado para las sugerencias en vivo de la caja: el cajero puede teclear
        parte del nombre ("cia" → *Ciabatta*) en lugar del código de barras.

        Args:
            texto: fragmento a buscar (no distingue mayúsculas/minúsculas).
            limite: número máximo de coincidencias a devolver.

        Returns:
            Lista de productos ordenados por nombre.
        """
        texto = (texto or "").strip()
        if not texto:
            return []

        patron = f"%{texto}%"
        with self._bd.cursor() as (_conexion, cursor):
            cursor.execute(SQL_BUSCAR_TEXTO, (patron, patron, patron, int(limite)))
            filas = [dict(fila) for fila in cursor.fetchall()]
        return filas

    def actualizar_parcial(self, producto_id: int, datos: dict[str, Any], cursor: Any | None = None) -> None:
        if not datos:
            return
        campos = []
        valores = []
        for k, v in datos.items():
            campos.append(f"{k} = ?")
            valores.append(v)
        campos.append("actualizado_en = CURRENT_TIMESTAMP")
        valores.append(int(producto_id))
        sql = f"UPDATE productos SET {', '.join(campos)} WHERE id = ?"

        if cursor is not None:
            cursor.execute(sql, tuple(valores))
            return
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(sql, tuple(valores))

    def descontar_stock(
        self,
        producto_id: int,
        cantidad: float,
        cursor: Any | None = None,
    ) -> tuple[float, float]:
        """Descuenta stock de forma **atómica y segura**.

        Usa un ``UPDATE`` condicional (``stock_actual >= ?``) para evitar
        condiciones de carrera: si el stock no alcanza, la fila no se actualiza
        y se lanza :class:`StockInsuficiente`.

        Los productos con ``controla_stock = 0`` (p. ej. servicios) no se validan.

        Args:
            producto_id: id del producto.
            cantidad: cantidad a descontar (admite decimales para pesables).
            cursor: cursor de una transacción abierta por el servicio. Si se
                omite, el repositorio abre su propia conexión.

        Returns:
            Tupla ``(stock_anterior, stock_resultante)`` para el kardex.

        Raises:
            StockInsuficiente: si no hay stock suficiente para la cantidad pedida.
        """
        if cursor is not None:
            return self._descontar(cursor, producto_id, cantidad)
        with self._bd.cursor() as (_conexion, cur):
            return self._descontar(cur, producto_id, cantidad)

    def _descontar(self, cursor: Any, producto_id: int, cantidad: float) -> tuple[float, float]:
        """Ejecuta el descuento atómico sobre el cursor recibido."""
        cursor.execute(SQL_DESCUENTO_STOCK, (float(cantidad), int(producto_id), float(cantidad)))
        if cursor.rowcount == 0:
            # No se pudo descontar: averiguamos cuánto hay realmente para informar.
            cursor.execute(SQL_STOCK_ACTUAL, (int(producto_id),))
            fila = cursor.fetchone()
            disponible = float(fila["stock_actual"]) if fila is not None else 0.0
            raise StockInsuficiente(producto_id, float(cantidad), disponible=disponible)

        # Trazabilidad para el kardex: stock posterior + anterior reconstruido.
        cursor.execute(SQL_STOCK_ACTUAL, (int(producto_id),))
        fila = cursor.fetchone()
        resultante = float(fila["stock_actual"]) if fila is not None else 0.0
        anterior = round(resultante + float(cantidad), 3)
        logger.debug(
            "Stock descontado: producto id=%s, cantidad=%s (%s -> %s)",
            producto_id,
            cantidad,
            anterior,
            resultante,
        )
        return anterior, resultante

    # ------------------------------------------------------------------
    # AUMENTAR stock (para compras/entradas)
    # ------------------------------------------------------------------
    def actualizar_stock(
        self,
        producto_id: int,
        cantidad: float,
        cursor: Any | None = None,
    ) -> tuple[float, float]:
        """Aumenta stock de forma atómica (para compras, ajustes positivos, etc.).

        Args:
            producto_id: id del producto.
            cantidad: cantidad a **sumar** (admite decimales).
            cursor: cursor de transacción externa.

        Returns:
            Tupla ``(stock_anterior, stock_resultante)`` para el kardex.
        """
        if cursor is not None:
            return self._aumentar(cursor, producto_id, cantidad)
        with self._bd.cursor() as (_conexion, cur):
            return self._aumentar(cur, producto_id, cantidad)

    def _aumentar(self, cursor: Any, producto_id: int, cantidad: float) -> tuple[float, float]:
        """Ejecuta el aumento atómico sobre el cursor recibido."""
        # Leer stock anterior
        cursor.execute(SQL_STOCK_ACTUAL, (int(producto_id),))
        fila = cursor.fetchone()
        anterior = float(fila["stock_actual"]) if fila is not None else 0.0

        cursor.execute(SQL_AUMENTO_STOCK, (float(cantidad), int(producto_id)))
        # Leer stock resultante
        cursor.execute(SQL_STOCK_ACTUAL, (int(producto_id),))
        fila = cursor.fetchone()
        resultante = float(fila["stock_actual"]) if fila is not None else 0.0

        logger.debug(
            "Stock aumentado: producto id=%s, cantidad=%s (%s -> %s)",
            producto_id, cantidad, anterior, resultante
        )
        return anterior, resultante
