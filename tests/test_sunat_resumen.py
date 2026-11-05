"""Pruebas del Fix #3 de preparación SUNAT.

Verifican, sobre una base de datos temporal:

* que el reporte **"Ventas del Día"** (`VentaService.ventas_del_dia`) entregue el
  **resumen tributario** (base imponible + IGV 18 %) que antes solo estaba en el
  PDF del cierre;
* el texto que la vista usa para pintarlo (método puro, sin Tk);
* que exista la tabla **`comprobantes_electronicos`** con las columnas del
  puente hacia SUNAT (XML/CDR/hash/estado).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main as app_main
from src.dominio.catalogos import TIPO_TICKET
from src.dominio.impuestos import CODIGO_IGV
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.impresora import ImpresoraTicket
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.producto_service import ProductoService
from src.servicios.venta_service import VentaService

#: Columnas mínimas que debe tener la tabla de comprobantes electrónicos.
COLUMNAS_COMPROBANTES = {
    "id",
    "venta_id",
    "tipo_comprobante",
    "serie",
    "correlativo",
    "fecha_emision",
    "receptor_tipo_doc",
    "receptor_num_doc",
    "receptor_nombre",
    "moneda",
    "monto_total",
    "monto_igv",
    "estado",
    "hash_cpe",
    "ruta_xml",
    "ruta_cdr",
    "respuesta_codigo",
    "respuesta_descripcion",
    "payload_json",
    "intentos",
    "enviado_en",
    "creado_en",
    "actualizado_en",
}


@pytest.fixture()
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Base de datos temporal con el esquema completo y las salidas aisladas."""
    import src.infraestructura.impresora as modulo_impresora
    import src.servicios.venta_service as modulo_venta

    monkeypatch.setattr(modulo_impresora, "RAIZ_PROYECTO", tmp_path)
    monkeypatch.setattr(modulo_venta, "RAIZ_PROYECTO", tmp_path)

    bd = ConexionBD(ruta_bd=str(tmp_path / "prueba.db"))
    app_main.inicializar_base_datos(bd)

    producto_repo = ProductoRepo(bd)
    impuesto_repo = ImpuestoRepo(bd)
    venta_repo = VentaRepo(bd)
    serie_repo = SerieRepo(bd)
    servicio = VentaService(
        bd,
        producto_repo=producto_repo,
        venta_repo=venta_repo,
        impuesto_repo=impuesto_repo,
        serie_repo=serie_repo,
        impresora=ImpresoraTicket(carpeta=tmp_path / "tickets"),
    )
    with bd.cursor() as (_c, cursor):
        categoria_id = int(cursor.execute("SELECT id FROM categorias ORDER BY id LIMIT 1").fetchone()[0])
    impuesto_id = next(
        int(i["id"])
        for i in impuesto_repo.listar(incluir_inactivos=True)
        if str(i.get("codigo_sunat")) == CODIGO_IGV
    )
    return {
        "bd": bd,
        "tmp": tmp_path,
        "venta_repo": venta_repo,
        "venta_service": servicio,
        "producto_service": ProductoService(bd, producto_repo=producto_repo),
        "producto_repo": producto_repo,
        "categoria_id": categoria_id,
        "impuesto_id": impuesto_id,
    }


def _vender(entorno, nombre: str, precio: float, cantidad: float = 1):
    """Crea un producto con IGV y registra una venta por el total indicado."""
    producto_id = entorno["producto_service"].crear(
        {
            "codigo_interno": nombre.replace(" ", "")[:12].upper(),
            "nombre": nombre,
            "precio_venta": precio,
            "categoria_id": entorno["categoria_id"],
            "impuesto_id": entorno["impuesto_id"],
            "unidad_medida": "UND",
            "stock_actual": 100,
            "controla_peso": False,
            "controla_stock": True,
        }
    )
    producto = entorno["producto_repo"].obtener_por_id(producto_id)
    total = round(cantidad * precio, 2)
    carrito = {int(producto["id"]): {"producto": producto, "cantidad": cantidad}}
    return entorno["venta_service"].confirmar_venta(
        carrito,
        "Efectivo",
        total=total,
        monto_recibido=total,
        tipo_comprobante=TIPO_TICKET,
    )


def _fecha_de(entorno, venta_id: int) -> str:
    """Fecha (``YYYY-MM-DD``) del asiento tal como la reporta el repositorio."""
    venta = entorno["venta_repo"].obtener_por_id(venta_id)
    return str(venta["creado_en"])[:10]


# --------------------------------------------------------------------------
# Resumen tributario en "Ventas del Día"
# --------------------------------------------------------------------------
def test_ventas_del_dia_incluye_resumen_tributario(entorno) -> None:
    # S/ 11,80 con IGV incluido → base S/ 10,00 + IGV S/ 1,80.
    resultado = _vender(entorno, "Cafe con Leche", 11.80)
    fecha = _fecha_de(entorno, resultado.venta_id)

    reporte = entorno["venta_service"].ventas_del_dia(fecha)
    tributario = reporte["resumen"]["tributario"]

    assert tributario["tasa_igv"] == pytest.approx(0.18)
    assert tributario["base_gravada"] == pytest.approx(10.00, abs=0.01)
    assert tributario["igv"] == pytest.approx(1.80, abs=0.01)
    assert tributario["total"] == pytest.approx(11.80, abs=0.01)
    # Identidad tributaria: base + IGV == subtotal de la venta.
    assert tributario["base_gravada"] + tributario["igv"] == pytest.approx(
        resultado.subtotal, abs=0.01
    )


def test_ventas_del_dia_sin_ventas_trae_tributario_en_cero(entorno) -> None:
    reporte = entorno["venta_service"].ventas_del_dia("2000-01-01")
    tributario = reporte["resumen"]["tributario"]

    assert tributario["base_gravada"] == 0
    assert tributario["igv"] == 0
    assert tributario["total"] == 0


# --------------------------------------------------------------------------
# Texto de la vista (método puro, sin Tk)
# --------------------------------------------------------------------------
def test_texto_tributario_de_la_vista() -> None:
    from src.presentacion.ventas_del_dia_view import VentasDelDiaView

    texto = VentasDelDiaView._texto_tributario(
        {"tasa_igv": 0.18, "base_gravada": 10.0, "igv": 1.8, "exonerado": 0.0, "inafecto": 0.0}
    )
    assert "Operaciones gravadas" in texto
    assert "IGV (18%)" in texto
    assert "10.00" in texto and "1.80" in texto
    # Sin datos → mensaje neutro (no ceros feos).
    assert "Aún no hay" in VentasDelDiaView._texto_tributario({})
    # El exonerado solo aparece si tiene monto.
    con_exo = VentasDelDiaView._texto_tributario(
        {"base_gravada": 5.0, "igv": 0.9, "exonerado": 2.0}
    )
    assert "Exonerado" in con_exo
    sin_exo = VentasDelDiaView._texto_tributario({"base_gravada": 5.0, "igv": 0.9})
    assert "Exonerado" not in sin_exo


# --------------------------------------------------------------------------
# Tabla de comprobantes electrónicos (fase SUNAT)
# --------------------------------------------------------------------------
def test_tabla_comprobantes_electronicos_existe(entorno) -> None:
    with entorno["bd"].cursor() as (_c, cursor):
        existe = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='comprobantes_electronicos'"
        ).fetchone()
        assert existe is not None, "no se creó la tabla comprobantes_electronicos"
        columnas = {
            str(fila["name"])
            for fila in cursor.execute(
                "SELECT * FROM pragma_table_info('comprobantes_electronicos')"
            )
        }

    faltantes = COLUMNAS_COMPROBANTES - columnas
    assert not faltantes, f"faltan columnas: {sorted(faltantes)}"


def test_comprobantes_electronicos_guarda_un_pendiente(entorno) -> None:
    """La tabla está lista para registrar el comprobante pendiente de enviar."""
    with entorno["bd"].cursor() as (_c, cursor):
        cursor.execute(
            "INSERT INTO comprobantes_electronicos "
            "(venta_id, tipo_comprobante, serie, correlativo, fecha_emision, "
            " receptor_tipo_doc, receptor_num_doc, receptor_nombre, monto_total, monto_igv) "
            "VALUES (1, 'BOLETA', 'B001', 1, '2026-10-18', '1', '45678912', "
            "'CLIENTE', 118.0, 18.0)"
        )
        fila = cursor.execute(
            "SELECT estado, moneda, intentos FROM comprobantes_electronicos "
            "WHERE serie='B001' AND correlativo=1"
        ).fetchone()

    assert str(fila["estado"]) == "PENDIENTE"
    assert str(fila["moneda"]) == "PEN"
    assert int(fila["intentos"]) == 0
