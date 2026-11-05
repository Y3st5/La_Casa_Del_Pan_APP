"""Punto de entrada (composition root) — LaCasaDelPanApp (POS).

Fase 1 (SQLite) + Sprints 2-4 — módulos "Caja / Ventas", "Inventario",
"Proveedores", "Calendario" y "Ventas del Día".

Este módulo es el *composition root*: el único lugar donde se ensamblan las
dependencias (configuración, conexión SQLite, repositorios, servicios y
vistas). Además, antes de levantar la interfaz, asegura que la base de datos
tenga el esquema mínimo (``productos``, ``ventas`` y ``venta_detalle``) con
datos de prueba (auto-setup).

Interfaz (diseño "Trigo"): cabecera superior con la marca y el reloj, barra
lateral de navegación con iconos y un área de contenido donde se apilan las
vistas (``tkraise``). El tema claro cálido y los iconos viven en
:mod:`src.presentacion.estilos` y :mod:`src.presentacion.recursos`.

Ejecutar:
    python main.py
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

import customtkinter as ctk

from src.dominio.catalogos import (
    SERIE_BOLETA,
    SERIE_FACTURA,
    SERIE_TICKET,
    TIPO_BOLETA,
    TIPO_FACTURA,
    TIPO_TICKET,
)
from src.dominio.impuestos import (
    AFECTACION_EXONERADO,
    AFECTACION_GRAVADO,
    AFECTACION_INAFECTO,
    CODIGO_EXONERADO,
    CODIGO_IGV,
    CODIGO_INAFECTO,
)
from src.infraestructura.config_reader import (
    ConfigReader,
    ErrorConfiguracion,
)
from src.infraestructura.conexion import ConexionBD, ErrorConexionBD
from src.infraestructura.rutas import (
    asegurar_estructura_datos,
    carpeta_logs,
    ruta_recurso,
)
from src.presentacion import estilos
from src.presentacion.calendario_view import CalendarioView
from src.presentacion.categorias_view import CategoriasView
from src.presentacion.componentes import BarraLateral, EncabezadoSuperior
from src.presentacion.compras_view import ComprasView
from src.presentacion.inventario_view import InventarioView
from src.presentacion.proveedor_view import ProveedorView
from src.presentacion.recursos import foto_logo
from src.presentacion.venta_view import VentaView
from src.presentacion.ventas_del_dia_view import VentasDelDiaView
from src.repositorios.categoria_repo import CategoriaRepo
from src.repositorios.compra_repo import CompraRepo
from src.repositorios.comprobante_electronico_repo import ComprobanteElectronicoRepo
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.proveedor_repo import ProveedorRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.turno_repo import TurnoRepo
from src.servicios.caja_service import CajaService
from src.servicios.categoria_service import CategoriaService
from src.servicios.compra_service import CompraService
from src.servicios.impuesto_service import ImpuestoService
from src.servicios.producto_service import ProductoService
from src.servicios.proveedor_service import ProveedorService
from src.servicios.sunat_service import SunatService
from src.servicios.venta_service import VentaService

# --- Constantes de la aplicación ------------------------------
APP_TITLE = "La Casa del Pan - POS"

#: Tamaño de diseño. Está calculado para entrar completo en un monitor de
#: 1366x768 (el estándar de mostrador) y se ve holgado en pantallas mayores.
WINDOW_WIDTH = 1320
WINDOW_HEIGHT = 700

#: Tamaño mínimo funcional: por debajo de esto el layout de Caja se aprieta.
#: Es menor que el tamaño de diseño para permitir ventanas más chicas en
#: portátiles modestos sin que el contenido se corte.
MIN_WIDTH = 1100
MIN_HEIGHT = 620

#: Margen que se reserva al calcular el tamaño inicial: bordes y barra de
#: título de la ventana. El área útil de Windows ya excluye la barra de tareas.
MARGEN_PANTALLA_ANCHO = 24
MARGEN_PANTALLA_ALTO = 48

#: Margen extra si no se pudo consultar el área útil (hay barra de tareas).
MARGEN_EXTRA_SIN_AREA_UTIL = 48

#: Archivo de tema de color de CustomTkinter (acento ámbar de la marca).
#: Es un recurso de solo lectura: viaja dentro del paquete.
RUTA_TEMA = ruta_recurso("config", "tema_trigo.json")

#: Elementos de navegación: (clave, etiqueta, icono).
ELEMENTOS_NAV: tuple[tuple[str, str, str], ...] = (
    ("ventas", "Caja", "carrito"),
    ("inventario", "Inventario", "caja"),
    ("categorias", "Categorías", "etiqueta"),
    ("compras", "Compras", "ingreso"),
    ("proveedores", "Proveedores", "camion"),
    ("calendario", "Calendario", "calendario"),
    ("ventas_dia", "Ventas del Día", "recibo"),
)

logger = logging.getLogger(__name__)

# ================================================================
#  Auto-setup de la base de datos (Fase 1: esquema mínimo + demo)
#  TODO(Fase 0.3): mover a db/migrations con un migrador versionado.
# ================================================================
SQL_EXISTE_TABLA_PRODUCTOS = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name='productos'"
)

SQL_EXISTE_TABLA_SERIES = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name='series_comprobante'"
)

SQL_CREAR_PRODUCTOS = """
CREATE TABLE IF NOT EXISTS productos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo_barras  TEXT    UNIQUE,
    codigo_interno TEXT    UNIQUE,
    nombre         TEXT    NOT NULL,
    descripcion    TEXT,
    categoria_id   INTEGER,
    impuesto_id    INTEGER,
    precio_venta   REAL    NOT NULL DEFAULT 0,
    costo          REAL,
    unidad_medida  TEXT    NOT NULL DEFAULT 'UND',
    es_pesable     INTEGER NOT NULL DEFAULT 0,
    controla_stock INTEGER NOT NULL DEFAULT 1,
    stock_actual   REAL    NOT NULL DEFAULT 0,
    stock_minimo   REAL    NOT NULL DEFAULT 0,
    activo         INTEGER NOT NULL DEFAULT 1,
    version        INTEGER NOT NULL DEFAULT 0,
    creado_en      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

#: Catálogo de impuestos (tributos). Se siembra con los peruanos habituales y
#: guarda los códigos de los catálogos SUNAT para que el comprobante
#: electrónico (UBL 2.1) no requiera una migración posterior.
SQL_CREAR_IMPUESTOS = """
CREATE TABLE IF NOT EXISTS impuestos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    codigo_sunat    TEXT,                      -- Catálogo N.º 05 (tipos de tributos)
    tipo_afectacion TEXT    NOT NULL DEFAULT '10',  -- Catálogo N.º 07 (afectación IGV)
    tasa            REAL    NOT NULL DEFAULT 0,     -- 0.18 = IGV 18 %
    activo          INTEGER NOT NULL DEFAULT 1,
    orden           INTEGER NOT NULL DEFAULT 0,
    creado_en       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

SQL_SEED_IMPUESTO = (
    "INSERT OR IGNORE INTO impuestos "
    "    (nombre, codigo_sunat, tipo_afectacion, tasa, activo, orden) "
    "VALUES (?, ?, ?, ?, 1, ?)"
)

#: (nombre, código SUNAT, tipo de afectación, tasa, orden) — tributos iniciales.
#: El IGV peruano es 18 % (16 % IGV + 2 % IPM). Los precios de venta **ya
#: incluyen** el impuesto, por lo que la tasa se usa para *extraer* la base.
IMPUESTOS_DEMO: tuple[tuple[object, ...], ...] = (
    ("IGV 18%", CODIGO_IGV, AFECTACION_GRAVADO, 0.18, 1),
    ("Exonerado", CODIGO_EXONERADO, AFECTACION_EXONERADO, 0.0, 2),
    ("Inafecto", CODIGO_INAFECTO, AFECTACION_INAFECTO, 0.0, 3),
)

SQL_INSERT_PRODUCTO = """
INSERT INTO productos
    (nombre, codigo_barras, codigo_interno, unidad_medida,
     es_pesable, precio_venta, costo, stock_actual, stock_minimo, categoria_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

#: Categorías del catálogo (Panes, Abarrotes, Cafetería…). Clasifican los
#: productos y son el eje del desglose de ventas por tipo de negocio.
SQL_CREAR_CATEGORIAS = """
CREATE TABLE IF NOT EXISTS categorias (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    orden          INTEGER NOT NULL DEFAULT 0,
    activo         INTEGER NOT NULL DEFAULT 1,
    creado_en      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

SQL_SEED_CATEGORIA = (
    "INSERT OR IGNORE INTO categorias (nombre, orden, activo) VALUES (?, ?, 1)"
)

#: (nombre, orden) — categorías iniciales del negocio.
CATEGORIAS_DEMO: tuple[tuple[object, ...], ...] = (
    ("Panes", 1),
    ("Abarrotes", 2),
    ("Cafetería", 3),
)

#: Contador persistente por serie de comprobante. Es la fuente de verdad del
#: correlativo: **no** se reinicia cuando el Cierre del Día borra las ventas
#: (SUNAT exige que la numeración de una serie sea continua y no se repita).
SQL_CREAR_SERIES_COMPROBANTE = """
CREATE TABLE IF NOT EXISTS series_comprobante (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    serie              TEXT    NOT NULL UNIQUE,
    tipo_comprobante   TEXT    NOT NULL,
    correlativo_actual INTEGER NOT NULL DEFAULT 0,
    activo             INTEGER NOT NULL DEFAULT 1,
    creado_en          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

#: (serie, tipo de comprobante) — series iniciales del negocio.
SERIES_DEMO: tuple[tuple[str, str], ...] = (
    (SERIE_TICKET, TIPO_TICKET),
    (SERIE_BOLETA, TIPO_BOLETA),
    (SERIE_FACTURA, TIPO_FACTURA),
)

SQL_SEED_SERIE = (
    "INSERT OR IGNORE INTO series_comprobante "
    "    (serie, tipo_comprobante, correlativo_actual) VALUES (?, ?, 0)"
)

#: Cabecera de la venta. ``serie``/``correlativo`` identifican el **ticket
#: interno** (control) de la venta; el comprobante fiscal (boleta/factura) va en
#: ``serie_comprobante``/``correlativo_comprobante`` con su propia numeración.
#: ``tipo_comprobante`` vale ``TICKET`` cuando la venta no emite documento
#: fiscal. La unicidad (serie, correlativo) evita duplicados.
#: "Una venta = un solo documento": si es boleta/factura, serie/correlativo
#: (ticket interno) quedan NULL; si es ticket, serie_comprobante/correlativo_comprobante
#: quedan vacíos.
SQL_CREAR_VENTAS = """
CREATE TABLE IF NOT EXISTS ventas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    serie          TEXT,           -- ticket interno (T001), NULL si es fiscal
    correlativo    INTEGER,        -- ticket interno, NULL si es fiscal
    tipo_comprobante        TEXT    NOT NULL DEFAULT 'TICKET',
    serie_comprobante       TEXT    NOT NULL DEFAULT '',   -- B001 / F001
    correlativo_comprobante INTEGER NOT NULL DEFAULT 0,
    metodo_pago    TEXT    NOT NULL,
    subtotal       REAL    NOT NULL DEFAULT 0,
    total          REAL    NOT NULL,
    monto_recibido REAL    NOT NULL DEFAULT 0,
    vuelto         REAL    NOT NULL DEFAULT 0,
    estado         TEXT    NOT NULL DEFAULT 'EMITIDA',
    creado_en      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (serie, correlativo)
);
"""

#: Detalle de la venta con *snapshot* del producto (guarda el precio aplicado
#: para que el ticket histórico no cambie si luego se actualiza el catálogo).
SQL_CREAR_VENTA_DETALLE = """
CREATE TABLE IF NOT EXISTS venta_detalle (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id        INTEGER NOT NULL,
    producto_id     INTEGER,
    codigo          TEXT,
    descripcion     TEXT    NOT NULL,
    cantidad        REAL    NOT NULL,
    precio_unitario REAL    NOT NULL,
    subtotal        REAL    NOT NULL,
    unidad_sunat    TEXT    NOT NULL DEFAULT '',
    FOREIGN KEY (venta_id) REFERENCES ventas (id) ON DELETE CASCADE
);
"""

#: Pagos divididos de una venta (RF-06): una venta puede pagarse con varios métodos.
#: Ej.: S/ 50 Efectivo + S/ 30 Yape. La suma de montos = total_a_pagar.
SQL_CREAR_VENTA_PAGOS = """
CREATE TABLE IF NOT EXISTS venta_pagos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id        INTEGER NOT NULL,
    metodo_pago     TEXT    NOT NULL,
    monto           REAL    NOT NULL,
    vuelto          REAL    NOT NULL DEFAULT 0,
    creado_en       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (venta_id) REFERENCES ventas (id) ON DELETE CASCADE
);
"""

SQL_CREAR_INDICE_VENTA_PAGOS = (
    "CREATE INDEX IF NOT EXISTS idx_venta_pagos_venta ON venta_pagos (venta_id)"
)

SQL_CREAR_INDICE_DETALLE = (
    "CREATE INDEX IF NOT EXISTS idx_venta_detalle_venta ON venta_detalle (venta_id)"
)

#: Kardex de inventario (RF-14): registra cada variación de stock con
#: trazabilidad (stock anterior/resultante y documento de origen).
SQL_CREAR_MOVIMIENTOS_STOCK = """
CREATE TABLE IF NOT EXISTS movimientos_stock (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id      INTEGER NOT NULL,
    tipo             TEXT    NOT NULL CHECK (
        tipo IN ('ENTRADA', 'SALIDA_VENTA', 'ANULACION_VENTA',
                 'AJUSTE_POSITIVO', 'AJUSTE_NEGATIVO', 'MERMA',
                 'INVENTARIO_INICIAL')
    ),
    cantidad         REAL    NOT NULL,
    stock_anterior   REAL,
    stock_resultante REAL,
    referencia_tipo  TEXT,
    referencia_id    INTEGER,
    usuario_id       INTEGER,
    motivo           TEXT,
    creado_en        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (producto_id) REFERENCES productos (id)
);
"""

SQL_CREAR_INDICE_MOVIMIENTOS = (
    "CREATE INDEX IF NOT EXISTS idx_mov_producto ON movimientos_stock (producto_id, creado_en)"
)

#: Proveedores que abastecen la panadería.
SQL_CREAR_PROVEEDORES = """
CREATE TABLE IF NOT EXISTS proveedores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT    NOT NULL,
    contacto       TEXT,
    telefono       TEXT,
    email          TEXT,
    notas          TEXT,
    activo         INTEGER NOT NULL DEFAULT 1,
    creado_en      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

#: Calendario de visitas de cada proveedor (1 = lunes … 7 = domingo).
SQL_CREAR_PROVEEDOR_DIAS = """
CREATE TABLE IF NOT EXISTS proveedor_dias (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id INTEGER NOT NULL,
    dia          INTEGER NOT NULL CHECK (dia BETWEEN 1 AND 7),
    UNIQUE (proveedor_id, dia),
    FOREIGN KEY (proveedor_id) REFERENCES proveedores (id) ON DELETE CASCADE
);
"""

SQL_CREAR_INDICE_PROVEEDOR_DIAS = (
    "CREATE INDEX IF NOT EXISTS idx_proveedor_dia ON proveedor_dias (dia)"
)

#: Catálogo interno por proveedor: qué productos trae cada uno (muchos-a-muchos).
#: ``precio_compra`` guarda el precio de compra sugerido para ese proveedor.
SQL_CREAR_PROVEEDOR_PRODUCTOS = """
CREATE TABLE IF NOT EXISTS proveedor_productos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id  INTEGER NOT NULL,
    producto_id   INTEGER NOT NULL,
    precio_compra REAL,
    UNIQUE (proveedor_id, producto_id),
    FOREIGN KEY (proveedor_id) REFERENCES proveedores (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id)  REFERENCES productos (id) ON DELETE CASCADE
);
"""

SQL_CREAR_INDICE_PROVEEDOR_PRODUCTOS = (
    "CREATE INDEX IF NOT EXISTS idx_proveedor_producto "
    "ON proveedor_productos (proveedor_id)"
)

#: Compras / Ingresos de proveedores (gasto + stock ↑)
SQL_CREAR_COMPRAS = """
CREATE TABLE IF NOT EXISTS compras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id    INTEGER NOT NULL,
    fecha           TEXT    NOT NULL,
    total           REAL    NOT NULL DEFAULT 0,
    metodo_pago     TEXT    NOT NULL,  -- Efectivo, Yape/Plin, Transferencia, Cuenta corriente
    estado          TEXT    NOT NULL DEFAULT 'RECIBIDO',
    observaciones   TEXT,
    creado_en       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proveedor_id) REFERENCES proveedores (id)
);
"""

SQL_CREAR_COMPRA_DETALLE = """
CREATE TABLE IF NOT EXISTS compra_detalle (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id       INTEGER NOT NULL,
    producto_id     INTEGER NOT NULL,
    cantidad        REAL    NOT NULL,
    precio_compra   REAL    NOT NULL,  -- Costo unitario
    subtotal        REAL    NOT NULL,
    FOREIGN KEY (compra_id) REFERENCES compras (id) ON DELETE CASCADE,
    FOREIGN KEY (producto_id) REFERENCES productos (id)
);
"""

SQL_CREAR_INDICE_COMPRAS = (
    "CREATE INDEX IF NOT EXISTS idx_compras_fecha ON compras (fecha)"
)
SQL_CREAR_INDICE_COMPRA_DETALLE = (
    "CREATE INDEX IF NOT EXISTS idx_compra_detalle_compra ON compra_detalle (compra_id)"
)

#: Cajas / puntos de venta físicos (Fase 1: una caja; Fase 2: multi-caja).
SQL_CREAR_CAJAS = """
CREATE TABLE IF NOT EXISTS cajas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre     TEXT    NOT NULL UNIQUE,
    ubicacion  TEXT,
    activo     INTEGER NOT NULL DEFAULT 1
);
"""

#: Turnos de caja (apertura / cierre / arqueo) — RF-18, RF-19, RN-07.
SQL_CREAR_TURNOS_CAJA = """
CREATE TABLE IF NOT EXISTS turnos_caja (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    caja_id                INTEGER NOT NULL,
    usuario_apertura_id    INTEGER,
    usuario_cierre_id      INTEGER,
    monto_apertura         REAL    NOT NULL DEFAULT 0,
    monto_cierre_declarado REAL,
    monto_cierre_sistema   REAL,
    diferencia             REAL,
    estado                 TEXT    NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO', 'CERRADO')),
    abierto_en             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cerrado_en             TEXT,
    observaciones          TEXT,
    FOREIGN KEY (caja_id) REFERENCES cajas (id)
);
"""

SQL_CREAR_INDICE_TURNOS = (
    "CREATE INDEX IF NOT EXISTS idx_turno_caja_estado ON turnos_caja (caja_id, estado)"
)

#: Comprobantes electrónicos (fase SUNAT): puente entre la venta local y la
#: emisión ante SUNAT. Guarda el XML firmado, el CDR de respuesta, el hash
#: (digest de la firma) y el estado del envío.
#:
#: ``venta_id`` **no** lleva clave foránea con cascada a propósito: el Cierre del
#: Día borra las ventas del día y el comprobante fiscal debe **sobrevivir** como
#: constancia del documento emitido.
SQL_CREAR_COMPROBANTES_ELECTRONICOS = """
CREATE TABLE IF NOT EXISTS comprobantes_electronicos (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_id              INTEGER NOT NULL,
    tipo_comprobante      TEXT    NOT NULL,              -- BOLETA | FACTURA
    serie                 TEXT    NOT NULL,              -- B001 / F001
    correlativo           INTEGER NOT NULL,
    fecha_emision         TEXT    NOT NULL,              -- YYYY-MM-DD
    -- Receptor (cliente) — Catálogo N.º 06: 1=DNI, 6=RUC, 0=sin documento.
    receptor_tipo_doc     TEXT    NOT NULL DEFAULT '',
    receptor_num_doc      TEXT    NOT NULL DEFAULT '',
    receptor_nombre       TEXT    NOT NULL DEFAULT '',
    -- Importes (moneda PEN; los precios ya incluyen IGV).
    moneda                TEXT    NOT NULL DEFAULT 'PEN',
    monto_total           REAL    NOT NULL DEFAULT 0,
    monto_igv             REAL    NOT NULL DEFAULT 0,
    -- Estado del envío: PENDIENTE -> ENVIADO -> ACEPTADO / RECHAZADO / ANULADO.
    estado                TEXT    NOT NULL DEFAULT 'PENDIENTE'
                          CHECK (estado IN (
                              'PENDIENTE', 'ENVIADO', 'ACEPTADO', 'RECHAZADO', 'ANULADO'
                          )),
    -- Archivos y respuesta de SUNAT / OSE.
    hash_cpe              TEXT    NOT NULL DEFAULT '',   -- digest del XML (firma)
    ruta_xml              TEXT    NOT NULL DEFAULT '',
    ruta_cdr              TEXT    NOT NULL DEFAULT '',
    respuesta_codigo      TEXT    NOT NULL DEFAULT '',   -- p. ej. '0' = aceptado
    respuesta_descripcion TEXT    NOT NULL DEFAULT '',
    -- Snapshot autocontenido (JSON): emisor, receptor, totales e ítems, para
    -- poder generar/reenviar el XML UBL 2.1 aunque la venta ya se haya borrado.
    payload_json          TEXT    NOT NULL DEFAULT '',
    intentos              INTEGER NOT NULL DEFAULT 0,
    enviado_en            TEXT,
    creado_en             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (serie, correlativo)
);
"""

SQL_CREAR_INDICE_COMPROBANTES_ESTADO = (
    "CREATE INDEX IF NOT EXISTS idx_comprobantes_estado "
    "ON comprobantes_electronicos (estado)"
)

SQL_CREAR_INDICE_COMPROBANTES_VENTA = (
    "CREATE INDEX IF NOT EXISTS idx_comprobantes_venta "
    "ON comprobantes_electronicos (venta_id)"
)

#: Seed por defecto: una caja "Caja 1" (id=1, coincide con config.ini caja_id=1).
SQL_SEED_CAJA = """
INSERT OR IGNORE INTO cajas (id, nombre, ubicacion, activo)
VALUES (1, 'Caja 1', 'Mostrador principal', 1);
"""

#: Seed de productos demo: (nombre, codigo_barras, codigo_interno, unidad,
#: es_pesable, precio, costo, stock, stock_min, categoría). La categoría se
#: resuelve por nombre contra la tabla ``categorias`` al sembrar.
PRODUCTOS_DEMO: tuple[tuple[object, ...], ...] = (
    # --- Panes ---
    ("Pan Francés", "2000000000015", "PAN001", "KG", 1, 6.00, 3.00, 25.500, 5.000, "Panes"),
    ("Ciabatta", "7751234567890", "CIA001", "UND", 0, 3.50, 1.80, 40.000, 10.000, "Panes"),
    ("Empanada de Carne", "7751234567906", "EMP001", "UND", 0, 4.00, 2.20, 30.000, 10.000, "Panes"),
    ("Pan de Molde", "2000000000016", "MOL001", "UND", 0, 5.50, 3.20, 20.000, 5.000, "Panes"),
    ("Baguette", "2000000000017", "BAG001", "UND", 0, 4.50, 2.30, 24.000, 6.000, "Panes"),
    ("Pan Integral", "2000000000018", "INT001", "UND", 0, 6.00, 3.40, 18.000, 5.000, "Panes"),
    ("Croissant", "2000000000019", "CRO001", "UND", 0, 3.00, 1.50, 36.000, 10.000, "Panes"),
    ("Pan de Yema", "2000000000020", "YEM001", "UND", 0, 1.50, 0.70, 60.000, 15.000, "Panes"),
    # --- Abarrotes ---
    ("Leche Gloria", "2000000000021", "GLI001", "UND", 0, 4.50, 3.80, 48.000, 12.000, "Abarrotes"),
    ("Arroz Extra", "2000000000022", "ARR001", "KG", 1, 3.80, 3.10, 50.000, 10.000, "Abarrotes"),
    ("Azúcar Rubia", "2000000000023", "AZU001", "KG", 1, 4.20, 3.50, 40.000, 10.000, "Abarrotes"),
    ("Aceite Vegetal", "2000000000024", "ACE001", "LT", 0, 9.50, 8.20, 24.000, 6.000, "Abarrotes"),
    ("Huevos", "2000000000025", "HUE001", "KG", 1, 8.50, 7.30, 30.000, 6.000, "Abarrotes"),
    ("Fideos", "2000000000026", "FID001", "UND", 0, 2.80, 2.10, 60.000, 15.000, "Abarrotes"),
    ("Atún", "2000000000027", "ATT001", "UND", 0, 5.50, 4.40, 36.000, 10.000, "Abarrotes"),
    ("Mermelada", "2000000000028", "MER001", "UND", 0, 7.00, 5.60, 18.000, 5.000, "Abarrotes"),
    ("Galleta de Soda", "2000000000029", "GAL001", "UND", 0, 1.20, 0.80, 80.000, 20.000, "Abarrotes"),
    # --- Cafetería ---
    ("Café Pasado", "2000000000030", "CAF001", "UND", 0, 3.00, 1.00, 100.000, 20.000, "Cafetería"),
    ("Café con Leche", "2000000000031", "CAF002", "UND", 0, 4.50, 1.80, 100.000, 20.000, "Cafetería"),
    ("Capuchino", "2000000000032", "CAF003", "UND", 0, 6.00, 2.40, 100.000, 20.000, "Cafetería"),
    ("Chocolate Caliente", "2000000000033", "CAF004", "UND", 0, 5.00, 2.00, 100.000, 20.000, "Cafetería"),
    ("Infusión de Manzanilla", "2000000000034", "CAF005", "UND", 0, 2.50, 0.80, 100.000, 20.000, "Cafetería"),
    ("Jugo de Naranja", "2000000000035", "CAF006", "UND", 0, 5.50, 2.60, 80.000, 15.000, "Cafetería"),
)


def _sembrar_productos_demo(cursor: sqlite3.Cursor) -> int:
    """Inserta los productos demo resolviendo su categoría por nombre.

    Debe ejecutarse **después** de sembrar las categorías. Devuelve cuántos
    productos se insertaron.
    """
    cursor.execute("SELECT id, nombre FROM categorias")
    por_nombre = {
        str(fila["nombre"]).strip().lower(): int(fila["id"]) for fila in cursor.fetchall()
    }
    filas: list[tuple[object, ...]] = []
    for fila in PRODUCTOS_DEMO:
        nombre, cb, ci, unidad, pesable, precio, costo, stock, stock_min, categoria = fila
        categoria_id = por_nombre.get(str(categoria).strip().lower())
        filas.append(
            (
                nombre,
                cb,
                ci,
                unidad,
                1 if pesable else 0,
                precio,
                costo,
                stock,
                stock_min,
                categoria_id,
            )
        )
    cursor.executemany(SQL_INSERT_PRODUCTO, filas)
    return len(filas)


def _agregar_columnas_boleta(cursor: sqlite3.Cursor) -> None:
    """Migra bases creadas antes de la boleta: agrega cliente/DNI a ``ventas``.

    Idempotente: solo agrega la columna si no existe (``pragma_table_info``).
    """
    columnas = (
        ("cliente", "TEXT NOT NULL DEFAULT ''"),
        ("dni_cliente", "TEXT NOT NULL DEFAULT ''"),
    )
    for columna, definicion in columnas:
        cursor.execute(
            "SELECT COUNT(*) FROM pragma_table_info('ventas') WHERE name = ?",
            (columna,),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE ventas ADD COLUMN {columna} {definicion}")
            logger.info("Columna 'ventas.%s' agregada (migración boleta).", columna)


def _crear_tabla_venta_pagos_si_no_existe(cursor: sqlite3.Cursor) -> None:
    """Crea la tabla ``venta_pagos`` e índice si no existen (migración pago dividido)."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='venta_pagos'")
    if cursor.fetchone() is None:
        cursor.execute(SQL_CREAR_VENTA_PAGOS)
        cursor.execute(SQL_CREAR_INDICE_VENTA_PAGOS)
        logger.info("Tabla 'venta_pagos' creada (migración pago dividido).")


def _asegurar_columnas(
    cursor: sqlite3.Cursor,
    tabla: str,
    columnas: tuple[tuple[str, str], ...],
) -> None:
    """Agrega a ``tabla`` las columnas que falten (migración idempotente).

    Args:
        tabla: nombre de la tabla a migrar.
        columnas: pares ``(nombre, definición DDL)`` a asegurar.
    """
    for columna, definicion in columnas:
        cursor.execute(
            f"SELECT COUNT(*) FROM pragma_table_info('{tabla}') WHERE name = ?",
            (columna,),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
            logger.info("Columna '%s.%s' agregada (migración de esquema).", tabla, columna)


def _agregar_columnas_impuestos(cursor: sqlite3.Cursor) -> None:
    """Migra bases creadas antes del IGV: desglose en ``ventas`` y ``venta_detalle``.

    El desglose se guarda como *snapshot* (igual que el precio) para que un
    comprobante histórico no cambie si luego se edita la tasa del impuesto.
    """
    _asegurar_columnas(
        cursor,
        "ventas",
        (
            ("base_gravada", "REAL NOT NULL DEFAULT 0"),
            ("igv", "REAL NOT NULL DEFAULT 0"),
            ("exonerado", "REAL NOT NULL DEFAULT 0"),
            ("inafecto", "REAL NOT NULL DEFAULT 0"),
            ("redondeo", "REAL NOT NULL DEFAULT 0"),
        ),
    )
    _asegurar_columnas(
        cursor,
        "venta_detalle",
        (
            ("impuesto_id", "INTEGER"),
            ("tipo_afectacion", "TEXT"),
            ("tasa_impuesto", "REAL NOT NULL DEFAULT 0"),
            ("base_gravada", "REAL NOT NULL DEFAULT 0"),
            ("igv", "REAL NOT NULL DEFAULT 0"),
        ),
    )


def _sembrar_correlativos(cursor: sqlite3.Cursor) -> None:
    """Inicializa el contador de cada serie con el mayor correlativo ya usado.

    **Migración de una sola vez.** Las versiones anteriores numeraban la boleta
    copiando el correlativo del ticket (ticket y boleta compartían numeración),
    así que la serie de la **boleta** arranca por encima de ese máximo global
    para no repetir un número ya impreso. La factura, que nunca se emitió,
    arranca en ``0``.

    Solo debe ejecutarse cuando ``series_comprobante`` se crea por primera vez
    (lo controla :func:`_agregar_columnas_comprobante`). Si se reejecutara en
    cada arranque, arrastraría la serie de boleta hasta el **número de ticket**
    (que avanza en toda venta) y descuadraría la numeración fiscal.
    """
    cursor.execute("SELECT COALESCE(MAX(correlativo), 0) FROM ventas")
    maximo_global = int(cursor.fetchone()[0] or 0)

    for serie, _tipo in SERIES_DEMO:
        cursor.execute(
            "SELECT COALESCE(MAX(correlativo), 0) FROM ventas WHERE serie = ?",
            (serie,),
        )
        propio = int(cursor.fetchone()[0] or 0)
        inicial = propio
        if serie == SERIE_BOLETA:
            inicial = max(propio, maximo_global)
        cursor.execute(
            "UPDATE series_comprobante SET correlativo_actual = ?, "
            "    actualizado_en = CURRENT_TIMESTAMP "
            "WHERE serie = ? AND correlativo_actual < ?",
            (inicial, serie, inicial),
        )


def _migrar_ventas_una_venta_un_documento(cursor: sqlite3.Cursor) -> None:
    """Migra la tabla ``ventas`` al modelo "Una venta = un solo documento".

    Permite que ``serie`` y ``correlativo`` (ticket interno) sean NULL cuando
    la venta es una boleta o factura (serie_comprobante/correlativo_comprobante
    tienen los valores). Y viceversa: si es ticket, los campos fiscales quedan
    vacíos.
    """
    # Verificar si la migración ya se aplicó (serie y correlativo ya son NULLables)
    cursor.execute("PRAGMA table_info('ventas')")
    cols = {row[1]: row for row in cursor.fetchall()}
    if "serie" in cols and cols["serie"][3] == 0:  # notnull = 0 -> NULL permitido
        return  # ya migrado

    logger.info("Migrando 'ventas' a modelo 'una venta = un documento'...")

    # 1. Crear tabla nueva con el esquema correcto
    cursor.execute("""
        CREATE TABLE ventas_nueva (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            serie          TEXT,           -- ticket interno (T001), NULL si es fiscal
            correlativo    INTEGER,        -- ticket interno, NULL si es fiscal
            tipo_comprobante        TEXT    NOT NULL DEFAULT 'TICKET',
            serie_comprobante       TEXT    NOT NULL DEFAULT '',   -- B001 / F001
            correlativo_comprobante INTEGER NOT NULL DEFAULT 0,
            metodo_pago    TEXT    NOT NULL,
            subtotal       REAL    NOT NULL DEFAULT 0,
            base_gravada   REAL    NOT NULL DEFAULT 0,
            igv            REAL    NOT NULL DEFAULT 0,
            exonerado      REAL    NOT NULL DEFAULT 0,
            inafecto       REAL    NOT NULL DEFAULT 0,
            redondeo       REAL    NOT NULL DEFAULT 0,
            total          REAL    NOT NULL,
            monto_recibido REAL    NOT NULL DEFAULT 0,
            vuelto         REAL    NOT NULL DEFAULT 0,
            estado         TEXT    NOT NULL DEFAULT 'EMITIDA',
            creado_en      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            cliente        TEXT    NOT NULL DEFAULT '',
            dni_cliente    TEXT    NOT NULL DEFAULT '',
            UNIQUE (serie, correlativo)
        )
    """)

    # 2. Copiar datos (serie/correlativo se mantienen; para boletas/facturas
    #    existentes que tuvieran ticket, los datos fiscales están en las columnas
    #    correspondientes. Como el cambio es hacia adelante, no tocamos datos
    #    existentes; solo cambiamos el esquema para nuevas inserciones).
    cursor.execute("""
        INSERT INTO ventas_nueva
        SELECT id, serie, correlativo, tipo_comprobante, serie_comprobante,
               correlativo_comprobante, metodo_pago, subtotal, base_gravada, igv,
               exonerado, inafecto, redondeo, total, monto_recibido, vuelto,
               estado, creado_en,
               COALESCE(cliente, ''),
               COALESCE(dni_cliente, '')
        FROM ventas
    """)

    # 3. Recrear índices de la tabla original (excepto UNIQUE que ya está en DDL)
    cursor.execute("DROP TABLE ventas")
    cursor.execute("ALTER TABLE ventas_nueva RENAME TO ventas")

    # 4. Recrear índice único parcial para comprobantes fiscales
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ventas_comprobante
        ON ventas (serie_comprobante, correlativo_comprobante)
        WHERE serie_comprobante <> ''
    """)

    logger.info("Migración 'ventas' completada: serie/correlativo ahora NULLables.")


def _agregar_columnas_comprobante(cursor: sqlite3.Cursor) -> None:
    """Migra bases creadas antes del Fix #2 (series y tipo de comprobante).

    Añade el tipo de comprobante y la numeración fiscal a ``ventas``, el código
    de unidad SUNAT a ``venta_detalle`` y asegura la tabla de contadores.

    La **siembra de correlativos** (:func:`_sembrar_correlativos`) se ejecuta una
    vez única, cuando ``series_comprobante`` se crea por primera vez. En bases ya
    migradas los contadores se respetan tal cual.
    """
    # Primero: migración a modelo "una venta = un documento"
    _migrar_ventas_una_venta_un_documento(cursor)

    cursor.execute(SQL_EXISTE_TABLA_SERIES)
    series_existia = cursor.fetchone() is not None

    cursor.execute(SQL_CREAR_SERIES_COMPROBANTE)
    cursor.executemany(SQL_SEED_SERIE, SERIES_DEMO)

    _asegurar_columnas(
        cursor,
        "ventas",
        (
            ("tipo_comprobante", "TEXT NOT NULL DEFAULT 'TICKET'"),
            ("serie_comprobante", "TEXT NOT NULL DEFAULT ''"),
            ("correlativo_comprobante", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )
    _asegurar_columnas(
        cursor,
        "venta_detalle",
        (("unidad_sunat", "TEXT NOT NULL DEFAULT ''"),),
    )

    # Un comprobante fiscal nunca puede repetirse: índice único parcial (solo
    # aplica a las ventas que sí emitieron documento).
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ventas_comprobante "
        "ON ventas (serie_comprobante, correlativo_comprobante) "
        "WHERE serie_comprobante <> ''"
    )

    if not series_existia:
        # Primera vez que existe la tabla: sembrar desde la numeración previa.
        _sembrar_correlativos(cursor)


def _crear_tabla_comprobantes_electronicos(cursor: sqlite3.Cursor) -> None:
    """Crea la tabla ``comprobantes_electronicos`` (fase SUNAT) si no existe.

    Idempotente: se ejecuta en cada arranque sin efecto si ya está creada.
    """
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='comprobantes_electronicos'"
    )
    if cursor.fetchone() is None:
        cursor.execute(SQL_CREAR_COMPROBANTES_ELECTRONICOS)
        logger.info("Tabla 'comprobantes_electronicos' creada (fase SUNAT).")
    # Bases creadas antes de que existiera el snapshot JSON.
    _asegurar_columnas(
        cursor,
        "comprobantes_electronicos",
        (("payload_json", "TEXT NOT NULL DEFAULT ''"),),
    )
    cursor.execute(SQL_CREAR_INDICE_COMPROBANTES_ESTADO)
    cursor.execute(SQL_CREAR_INDICE_COMPROBANTES_VENTA)


def _asignar_impuesto_por_defecto(cursor: sqlite3.Cursor) -> None:
    """Asigna el IGV 18 % a los productos que aún no tienen impuesto.

    Se resuelve el id por su código de catálogo SUNAT (``1000``), no por un id
    fijo, para no depender del orden de inserción de la siembra.
    """
    cursor.execute(
        "SELECT id FROM impuestos WHERE codigo_sunat = ? AND activo = 1 "
        "ORDER BY orden ASC, id ASC LIMIT 1",
        (CODIGO_IGV,),
    )
    fila = cursor.fetchone()
    if fila is None:
        logger.warning("No se encontró el impuesto IGV para asignar por defecto.")
        return
    impuesto_id = int(fila[0])
    cursor.execute(
        "UPDATE productos SET impuesto_id = ? WHERE impuesto_id IS NULL",
        (impuesto_id,),
    )
    if cursor.rowcount:
        logger.info(
            "Impuesto IGV (id=%s) asignado a %d producto(s) sin impuesto.",
            impuesto_id,
            cursor.rowcount,
        )


def _asegurar_caja_por_defecto(cursor: sqlite3.Cursor) -> None:
    """Inserta la caja por defecto (id=1) si no existe ninguna caja."""
    cursor.execute("SELECT COUNT(*) FROM cajas")
    if cursor.fetchone()[0] == 0:
        cursor.execute(SQL_SEED_CAJA)
        logger.info("Caja por defecto 'Caja 1' insertada (id=1).")


def inicializar_base_datos(conexion_bd: ConexionBD) -> None:
    """Asegura el esquema mínimo y siembra datos de prueba solo si falta.

    Crea (si no existen) las tablas ``productos``, ``ventas``,
    ``venta_detalle``, ``movimientos_stock`` (kardex), ``proveedores``,
    ``cajas`` y ``turnos_caja``. Si ``productos`` acaba de crearse, inserta
    3 productos de prueba y la caja por defecto. Es idempotente: puede
    ejecutarse en cada arranque y también actualiza bases de datos antiguas.
    """
    with conexion_bd.cursor() as (_conexion, cursor):
        cursor.execute(SQL_EXISTE_TABLA_PRODUCTOS)
        productos_existia = cursor.fetchone() is not None

        cursor.execute(SQL_CREAR_PRODUCTOS)
        # Catálogo de impuestos (IGV 18 %, Exonerado, Inafecto) + siembra idempotente.
        cursor.execute(SQL_CREAR_IMPUESTOS)
        cursor.executemany(SQL_SEED_IMPUESTO, IMPUESTOS_DEMO)
        # Categorías del catálogo (Panes, Abarrotes, Cafetería…) + siembra idempotente.
        cursor.execute(SQL_CREAR_CATEGORIAS)
        cursor.executemany(SQL_SEED_CATEGORIA, CATEGORIAS_DEMO)
        cursor.execute(SQL_CREAR_VENTAS)
        cursor.execute(SQL_CREAR_VENTA_DETALLE)
        cursor.execute(SQL_CREAR_VENTA_PAGOS)
        cursor.execute(SQL_CREAR_INDICE_VENTA_PAGOS)
        cursor.execute(SQL_CREAR_INDICE_DETALLE)
        cursor.execute(SQL_CREAR_MOVIMIENTOS_STOCK)
        cursor.execute(SQL_CREAR_INDICE_MOVIMIENTOS)
        cursor.execute(SQL_CREAR_PROVEEDORES)
        cursor.execute(SQL_CREAR_PROVEEDOR_DIAS)
        cursor.execute(SQL_CREAR_INDICE_PROVEEDOR_DIAS)
        # Catálogo interno de productos por proveedor (muchos-a-muchos).
        cursor.execute(SQL_CREAR_PROVEEDOR_PRODUCTOS)
        cursor.execute(SQL_CREAR_INDICE_PROVEEDOR_PRODUCTOS)
        # Nuevas tablas: compras / ingresos de proveedores.
        cursor.execute(SQL_CREAR_COMPRAS)
        cursor.execute(SQL_CREAR_COMPRA_DETALLE)
        cursor.execute(SQL_CREAR_INDICE_COMPRAS)
        cursor.execute(SQL_CREAR_INDICE_COMPRA_DETALLE)
        # Nuevas tablas: cajas y turnos_caja (Sprint 5).
        cursor.execute(SQL_CREAR_CAJAS)
        cursor.execute(SQL_CREAR_TURNOS_CAJA)
        cursor.execute(SQL_CREAR_INDICE_TURNOS)

        # Migración de bases antiguas (boleta): columnas de cliente.
        _agregar_columnas_boleta(cursor)

        # Migración pago dividido: tabla venta_pagos.
        _crear_tabla_venta_pagos_si_no_existe(cursor)

        # Migración Sprint 5: asegurar caja por defecto (id=1).
        _asegurar_caja_por_defecto(cursor)

        # Migración IGV: desglose tributario + impuesto por defecto en productos.
        _agregar_columnas_impuestos(cursor)
        _asignar_impuesto_por_defecto(cursor)

        # Migración Fix #2 (SUNAT): series independientes, tipo de comprobante
        # y unidad de medida del Catálogo N.º 03.
        _agregar_columnas_comprobante(cursor)

        # Fase SUNAT: tabla de comprobantes electrónicos (XML/CDR/estado de envío).
        _crear_tabla_comprobantes_electronicos(cursor)

        if productos_existia:
            logger.info("Base de datos lista: esquema verificado/actualizado.")
            return

        logger.info("Tabla 'productos' no encontrada. Insertando datos de prueba...")
        insertados = _sembrar_productos_demo(cursor)
        # Los productos demo se insertan sin impuesto: se los asigna el IGV 18 %.
        _asignar_impuesto_por_defecto(cursor)
        logger.info("Esquema creado con %d producto(s) de prueba.", insertados)


# ================================================================
#  Aplicación
# ================================================================
def configurar_logging() -> None:
    """Configura el logging de la aplicación: consola + archivo rotativo.

    En el ``.exe`` sin consola (``console=False``) el archivo es la **única**
    forma de diagnosticar un fallo de arranque. Se escribe en la carpeta de
    datos (``%PROGRAMDATA%\\LaCasaDelPanApp\\logs\\pos.log``) con rotación:
    2 MB por archivo y 5 respaldos.
    """
    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)

    # Evita duplicar handlers si ``main()`` se ejecuta dos veces en el proceso.
    for handler in list(raiz.handlers):
        if getattr(handler, "_lacasadepan", False):
            raiz.removeHandler(handler)
            handler.close()

    # En un ejecutable windowed ``sys.stderr`` es None: no hay consola.
    if sys.stderr is not None:
        consola = logging.StreamHandler()
        consola.setFormatter(formato)
        consola._lacasadepan = True  # type: ignore[attr-defined]
        raiz.addHandler(consola)

    try:
        carpeta = carpeta_logs()
        carpeta.mkdir(parents=True, exist_ok=True)
        archivo = RotatingFileHandler(
            carpeta / "pos.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        archivo.setFormatter(formato)
        archivo._lacasadepan = True  # type: ignore[attr-defined]
        raiz.addHandler(archivo)
    except OSError as exc:
        # Sin log en archivo la app puede seguir; se avisa por consola si hay.
        logger.error("No se pudo abrir el log en archivo: %s", exc)


def instalar_hook_excepciones() -> None:
    """Registra en el log cualquier excepción no controlada antes de cerrar."""
    anterior = sys.excepthook

    def _hook(tipo, valor, traza) -> None:  # type: ignore[no-untyped-def]
        logger.critical(
            "Excepción no controlada: %s", valor, exc_info=(tipo, valor, traza)
        )
        anterior(tipo, valor, traza)

    sys.excepthook = _hook


def habilitar_dpi_awareness() -> None:
    """Mejora la nitidez de la UI en pantallas Windows con escalado."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        except Exception:  # noqa: BLE001 - no debe impedir el arranque
            logger.debug("DPI awareness no disponible en este sistema.")


def calcular_tamano_inicial(
    pantalla_ancho: int, pantalla_alto: int, area_util: bool = True
) -> tuple[int, int]:
    """Calcula el tamaño de ventana ``(ancho, alto)`` para una pantalla dada.

    Parte del tamaño de diseño y lo recorta si hace falta. Es puro para poder
    probarlo sin abrir una ventana real.

    Args:
        pantalla_ancho: ancho disponible (área útil si ``area_util`` es True).
        pantalla_alto: alto disponible.
        area_util: si la barra de tareas ya está descontada del alto.
    """
    margen_alto = MARGEN_PANTALLA_ALTO
    if not area_util:
        margen_alto += MARGEN_EXTRA_SIN_AREA_UTIL
    ancho = min(WINDOW_WIDTH, pantalla_ancho - MARGEN_PANTALLA_ANCHO)
    alto = min(WINDOW_HEIGHT, pantalla_alto - margen_alto)
    # Nunca por debajo del mínimo funcional, salvo pantallas diminutas.
    ancho = max(ancho, min(MIN_WIDTH, pantalla_ancho))
    alto = max(alto, min(MIN_HEIGHT, pantalla_alto))
    return int(ancho), int(alto)


class AplicacionPOS(ctk.CTk):
    """Ventana principal del punto de venta (cabecera + barra lateral + vistas)."""

    def __init__(
        self,
        venta_service: VentaService,
        producto_service: ProductoService,
        proveedor_service: ProveedorService,
        caja_service: CajaService,
        compra_service: CompraService,
        categoria_service: CategoriaService,
        impuesto_service: ImpuestoService | None = None,
        config: ConfigReader | None = None,
    ) -> None:
        super().__init__()

        estilos.aplicar_tema_global()

        self.title(APP_TITLE)
        self.configure(fg_color=estilos.FONDO_APP)
        # Tamaño inicial según la pantalla real: entra completo en 1366x768.
        self._tamano_inicial = self._geometria_inicial()
        self._aplicar_geometria_inicial()
        self._centrar_ventana()
        self._aplicar_icono()

        # Debounce de resize: evita recalcular layout en cada evento mientras se arrastra.
        self._resize_after_id: str | None = None
        self.bind("<Configure>", self._on_configure_resize, add="+")

        # Datos del negocio (para la cabecera y el pie de la barra lateral).
        razon_social = "La Casa del Pan"
        caja = "Caja 1"
        if config is not None:
            try:
                razon_social = config.obtener("negocio", "razon_social", razon_social) or razon_social
                caja_id = config.obtener_int("negocio", "caja_id", 1)
                caja = f"Caja {caja_id}"
            except ErrorConfiguracion:
                logger.debug("Sin datos de negocio en la configuración.")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- Cabecera -------------------------------------------------
        self.encabezado = EncabezadoSuperior(self, negocio=razon_social, caja=caja)
        self.encabezado.grid(row=0, column=0, sticky="ew")

        # --- Cuerpo: barra lateral + contenido ------------------------
        cuerpo = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        cuerpo.grid(row=1, column=0, sticky="nsew")
        cuerpo.grid_rowconfigure(0, weight=1)
        cuerpo.grid_columnconfigure(1, weight=1)

        self.barra = BarraLateral(
            cuerpo,
            ELEMENTOS_NAV,
            self._seleccionar,
            pie=f"{caja}  ·  v1.0\nFase 1 · SQLite",
        )
        self.barra.grid(row=0, column=0, sticky="nsw")

        self.contenido = ctk.CTkFrame(cuerpo, fg_color=estilos.FONDO_APP, corner_radius=0)
        self.contenido.grid(row=0, column=1, sticky="nsew")
        self.contenido.grid_rowconfigure(0, weight=1)
        self.contenido.grid_columnconfigure(0, weight=1)

        # La presentación depende de SERVICIOS, no de repositorios.
        self.vista_ventas = VentaView(self.contenido, venta_service=venta_service)
        self.vista_inventario = InventarioView(
            self.contenido,
            producto_service=producto_service,
            categoria_service=categoria_service,
            impuesto_service=impuesto_service,
        )
        self.vista_categorias = CategoriasView(
            self.contenido, categoria_service=categoria_service
        )
        self.vista_compras = ComprasView(
            self.contenido,
            compra_service=compra_service,
            producto_service=producto_service,
            proveedor_service=proveedor_service,
        )
        self.vista_proveedores = ProveedorView(
            self.contenido,
            proveedor_service=proveedor_service,
            producto_service=producto_service,
        )
        self.vista_calendario = CalendarioView(self.contenido, proveedor_service=proveedor_service)
        self.vista_ventas_dia = VentasDelDiaView(
            self.contenido, venta_service=venta_service, caja_service=caja_service
        )

        self._vistas: dict[str, ctk.CTkFrame] = {
            "ventas": self.vista_ventas,
            "inventario": self.vista_inventario,
            "categorias": self.vista_categorias,
            "compras": self.vista_compras,
            "proveedores": self.vista_proveedores,
            "calendario": self.vista_calendario,
            "ventas_dia": self.vista_ventas_dia,
        }
        self._atajos_navegacion()

        # Arranca en Caja / Ventas.
        self._seleccionar("ventas")

        logger.info(
            "Ventana principal lista (%sx%s).",
            self._tamano_inicial[0],
            self._tamano_inicial[1],
        )

    # ------------------------------------------------------------------
    def _seleccionar(self, clave: str) -> None:
        """Muestra la vista indicada y refresca sus datos.

        Solo la vista activa queda gestionada por ``grid``; las demás se
        ocultan con ``grid_remove``. Así, al estirar la ventana, Tk ya no
        recalcula el layout ni redibuja los Canvas internos de las cinco
        vistas a la vez, que era la causa del lag al redimensionar.
        """
        vista = self._vistas.get(clave)
        if vista is None:
            return
        for otra in self._vistas.values():
            if otra is vista:
                otra.grid(row=0, column=0, sticky="nsew")
            else:
                otra.grid_remove()
        vista.tkraise()
        self.barra.seleccionar(clave)

        if clave == "ventas":
            self.vista_ventas.enfocar_entrada()
        elif clave == "inventario":
            self.vista_inventario.cargar_datos()
        elif clave == "categorias":
            self.vista_categorias.cargar_datos()
        elif clave == "compras":
            self.vista_compras.cargar_datos()
        elif clave == "proveedores":
            self.vista_proveedores.cargar_datos()
        elif clave == "calendario":
            self.vista_calendario.cargar_datos()
        elif clave == "ventas_dia":
            self.vista_ventas_dia.cargar_datos()

    def _atajos_navegacion(self) -> None:
        """Ctrl+1..5 cambian de módulo (atajo de accesibilidad)."""
        claves = [clave for clave, _t, _i in ELEMENTOS_NAV]
        for indice, clave in enumerate(claves, start=1):
            self.bind(f"<Control-Key-{indice}>", lambda _e, c=clave: self._seleccionar(c))

    def _aplicar_icono(self) -> None:
        """Aplica el logo de la cesta como icono de la ventana."""
        try:
            self._icono_ventana = foto_logo(64)  # mantener referencia viva
            self.iconphoto(True, self._icono_ventana)
        except Exception:  # noqa: BLE001 - el icono es decorativo
            logger.debug("No se pudo aplicar el icono de la ventana.")

    def _geometria_inicial(self) -> tuple[int, int]:
        """Calcula el tamaño de arranque según la pantalla disponible.

        Se parte del tamaño de diseño (1320x700) y se recorta si la pantalla
        es más chica. En un monitor de 1366x768 el área útil es ~1366x728, así
        que la ventana abre a 1320x680 y entra completa; en 1920x1080 usa el
        tamaño de diseño sin recortes.
        """
        pantalla_ancho, pantalla_alto, area_util = self._pantalla_util()
        return calcular_tamano_inicial(pantalla_ancho, pantalla_alto, area_util)

    def _pantalla_util(self) -> tuple[int, int, bool]:
        """Devuelve ``(ancho, alto, es_area_util)`` de la pantalla.

        ``es_area_util`` indica si se pudo descontar la barra de tareas
        (``SPI_GETWORKAREA``); si no, se devuelve la pantalla completa.
        """
        ancho = self.winfo_screenwidth()
        alto = self.winfo_screenheight()
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.right - rect.left, rect.bottom - rect.top, True
        except Exception:  # noqa: BLE001 - en otros SO se usa la pantalla completa
            pass
        return ancho, alto, False

    def _aplicar_geometria_inicial(self) -> None:
        """Fija tamaño inicial y mínimo coherentes con la pantalla."""
        ancho, alto = self._tamano_inicial
        self.geometry(f"{ancho}x{alto}")
        self.minsize(min(MIN_WIDTH, ancho), min(MIN_HEIGHT, alto))

    def _centrar_ventana(self) -> None:
        """Centra la ventana en el área útil de la pantalla."""
        self.update_idletasks()
        ancho, alto = self._tamano_inicial
        pantalla_ancho, pantalla_alto, _area = self._pantalla_util()
        x = max(0, (pantalla_ancho - ancho) // 2)
        y = max(0, (pantalla_alto - alto) // 2)
        self.geometry(f"{ancho}x{alto}+{x}+{y}")

    def _on_configure_resize(self, event: object) -> None:
        """Retrasa el reajuste hasta que finaliza el redimensionado (debounce)."""
        try:
            if getattr(event, "widget") is not self:
                return
        except Exception:
            pass
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(80, self._finalizar_resize)

    def _finalizar_resize(self) -> None:
        """Refresca layout cuando el arrastre termina."""
        self._resize_after_id = None
        try:
            self.update_idletasks()
        except Exception:
            pass

    def _configurar_fuentes(self) -> None:
        """Fuentes reutilizables del marco (referencia para las vistas)."""
        self.fuente_titulo = ctk.CTkFont(family=estilos.FAMILIA, size=28, weight="bold")
        self.fuente_subtitulo = ctk.CTkFont(family=estilos.FAMILIA, size=16)
        self.fuente_normal = ctk.CTkFont(family=estilos.FAMILIA, size=13)
        self.fuente_pequena = ctk.CTkFont(family=estilos.FAMILIA, size=11)
        self.fuente_mono = ctk.CTkFont(family=estilos.FAMILIA_MONO, size=13)


def main() -> int:
    """Ensambla dependencias y arranca la aplicación."""
    configurar_logging()
    instalar_hook_excepciones()
    habilitar_dpi_awareness()

    # Estructura escribible (idempotente): config, db, logs, salidas, xml/cdr.
    try:
        asegurar_estructura_datos()
    except OSError as exc:
        logger.exception("No se pudo preparar la carpeta de datos.")
        messagebox.showerror(
            "Error de permisos",
            "No se pudo crear la carpeta de datos de la aplicación.\n\n"
            f"{exc}\n\n"
            "Ejecute la aplicación con un usuario que tenga permisos de "
            "escritura sobre esa carpeta.",
        )
        return 1

    # === TEMA VISUAL (claro cálido "Trigo") ===
    ctk.set_appearance_mode("light")
    try:
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
    except Exception:
        pass
    if RUTA_TEMA.exists():
        ctk.set_default_color_theme(str(RUTA_TEMA))
    else:  # pragma: no cover - respaldo si falta el archivo de tema
        logger.warning("No se encontró %s; se usa el tema por defecto.", RUTA_TEMA)
        ctk.set_default_color_theme("blue")

    # --- Composition root: ensamblado de dependencias ---
    try:
        conexion_bd = ConexionBD()              # infraestructura (SQLite)
        config_reader = ConfigReader()          # datos del negocio (RUC, etc.)
        inicializar_base_datos(conexion_bd)     # auto-setup (esquema + demo)
        producto_repo = ProductoRepo(conexion_bd)  # repositorio
        proveedor_repo = ProveedorRepo(conexion_bd)  # repositorio
        turno_repo = TurnoRepo(conexion_bd)     # repositorio (turnos de caja)
        compra_repo = CompraRepo(conexion_bd)   # repositorio (compras/ingresos)
        categoria_repo = CategoriaRepo(conexion_bd)  # repositorio (categorías)
        impuesto_repo = ImpuestoRepo(conexion_bd)  # repositorio (impuestos/IGV)
        serie_repo = SerieRepo(conexion_bd)     # repositorio (correlativos por serie)
        comprobante_repo = ComprobanteElectronicoRepo(conexion_bd)  # repositorio (SUNAT)
        sunat_service = SunatService(              # servicio (emisión electrónica)
            conexion_bd,
            comprobante_repo=comprobante_repo,
            config=config_reader,
        )
        venta_service = VentaService(
            conexion_bd,
            producto_repo=producto_repo,
            compra_repo=compra_repo,
            impuesto_repo=impuesto_repo,
            serie_repo=serie_repo,
            comprobante_repo=comprobante_repo,
            sunat_service=sunat_service,
            config=config_reader,
        )  # servicio
        producto_service = ProductoService(
            conexion_bd, producto_repo=producto_repo, categoria_repo=categoria_repo
        )  # servicio
        categoria_service = CategoriaService(
            conexion_bd, categoria_repo=categoria_repo
        )  # servicio (categorías)
        impuesto_service = ImpuestoService(
            conexion_bd, impuesto_repo=impuesto_repo
        )  # servicio (impuestos/IGV)
        proveedor_service = ProveedorService(conexion_bd, proveedor_repo=proveedor_repo)  # servicio
        caja_service = CajaService(
            conexion_bd, turno_repo=turno_repo, compra_repo=compra_repo
        )  # servicio (caja/turnos)
        compra_service = CompraService(
            conexion_bd,
            compra_repo=compra_repo,
            producto_repo=producto_repo,
            proveedor_repo=proveedor_repo,
        )  # servicio (compras/ingresos)
    except (ErrorConfiguracion, ErrorConexionBD) as exc:
        logger.error("No se pudo iniciar la aplicación: %s", exc)
        messagebox.showerror("Error de base de datos", str(exc))
        return 1

    app = AplicacionPOS(
        venta_service=venta_service,
        producto_service=producto_service,
        proveedor_service=proveedor_service,
        caja_service=caja_service,
        compra_service=compra_service,
        categoria_service=categoria_service,
        impuesto_service=impuesto_service,
        config=config_reader,
    )  # presentación

    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
