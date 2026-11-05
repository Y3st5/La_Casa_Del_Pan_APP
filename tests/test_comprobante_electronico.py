"""Pruebas del puente SUNAT: registro automático del comprobante electrónico.

Al cobrar una **boleta** (``B001``) o **factura** (``F001``) se registra el
comprobante en ``comprobantes_electronicos`` con estado ``PENDIENTE``; el
**ticket interno** (``T001``) no genera ningún registro fiscal.

El *payload* guarda un snapshot autocontenido (emisor, receptor, totales e
ítems con unidad SUNAT y desglose por línea) para poder generar o reenviar el
XML UBL 2.1 **aun después del Cierre del Día**, que borra ``ventas`` y
``venta_detalle``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main as app_main
from src.dominio.catalogos import TIPO_BOLETA, TIPO_FACTURA, TIPO_TICKET
from src.dominio.impuestos import AFECTACION_GRAVADO, CODIGO_IGV
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.impresora import ImpresoraTicket
from src.repositorios.comprobante_electronico_repo import (
    ESTADO_ACEPTADO,
    ESTADO_PENDIENTE,
    ComprobanteElectronicoRepo,
)
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.producto_service import ProductoService
from src.servicios.venta_service import VentaService


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
    comprobante_repo = ComprobanteElectronicoRepo(bd)
    servicio = VentaService(
        bd,
        producto_repo=producto_repo,
        venta_repo=venta_repo,
        impuesto_repo=impuesto_repo,
        serie_repo=serie_repo,
        comprobante_repo=comprobante_repo,
        impresora=ImpresoraTicket(carpeta=tmp_path / "tickets"),
    )
    with bd.cursor() as (_c, cursor):
        categoria_id = int(
            cursor.execute("SELECT id FROM categorias ORDER BY id LIMIT 1").fetchone()[0]
        )
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
        "comprobante_repo": comprobante_repo,
        "producto_service": ProductoService(bd, producto_repo=producto_repo),
        "producto_repo": producto_repo,
        "categoria_id": categoria_id,
        "impuesto_id": impuesto_id,
    }


def _crear_producto(entorno, nombre: str, precio: float, stock: float = 100) -> dict:
    """Crea un producto gravado con IGV y devuelve su fila."""
    producto_id = entorno["producto_service"].crear(
        {
            "codigo_interno": nombre.replace(" ", "")[:12].upper(),
            "nombre": nombre,
            "precio_venta": precio,
            "categoria_id": entorno["categoria_id"],
            "impuesto_id": entorno["impuesto_id"],
            "unidad_medida": "UND",
            "stock_actual": stock,
            "controla_peso": False,
            "controla_stock": True,
        }
    )
    return entorno["producto_repo"].obtener_por_id(producto_id)


def _cobrar(
    entorno,
    producto: dict,
    tipo: str,
    cantidad: float = 1.0,
    cliente: str = "",
    dni_cliente: str = "",
):
    """Registra una venta del producto con el tipo de comprobante indicado."""
    total = round(cantidad * float(producto["precio_venta"]), 2)
    carrito = {int(producto["id"]): {"producto": producto, "cantidad": cantidad}}
    return entorno["venta_service"].confirmar_venta(
        carrito,
        "Efectivo",
        total=total,
        monto_recibido=total,
        cliente=cliente,
        dni_cliente=dni_cliente,
        tipo_comprobante=tipo,
    )


# --------------------------------------------------------------------------
# Ticket interno: no genera registro fiscal
# --------------------------------------------------------------------------
def test_ticket_interno_no_registra_comprobante(entorno) -> None:
    producto = _crear_producto(entorno, "Pan Frances", 2.00)
    resultado = _cobrar(entorno, producto, TIPO_TICKET)

    assert resultado.comprobante_electronico_id == 0
    assert entorno["comprobante_repo"].obtener_por_venta(resultado.venta_id) is None
    assert entorno["comprobante_repo"].listar_por_estado(ESTADO_PENDIENTE) == []


# --------------------------------------------------------------------------
# Boleta: registro PENDIENTE con snapshot completo
# --------------------------------------------------------------------------
def test_boleta_registra_pendiente_con_snapshot(entorno) -> None:
    # S/ 11,80 con IGV incluido → base S/ 10,00 + IGV S/ 1,80.
    producto = _crear_producto(entorno, "Pan Frances", 11.80)
    resultado = _cobrar(
        entorno, producto, TIPO_BOLETA, cliente="Juan Perez", dni_cliente="45678912"
    )

    assert resultado.comprobante_electronico_id > 0
    comprobante = entorno["comprobante_repo"].obtener_por_venta(resultado.venta_id)
    assert comprobante is not None

    # Cabecera fiscal.
    assert comprobante["estado"] == ESTADO_PENDIENTE
    assert comprobante["tipo_comprobante"] == TIPO_BOLETA
    assert comprobante["serie"] == "B001"
    assert comprobante["correlativo"] == 1
    assert comprobante["receptor_tipo_doc"] == "1"  # DNI
    assert comprobante["receptor_num_doc"] == "45678912"
    assert comprobante["moneda"] == "PEN"
    assert comprobante["monto_total"] == pytest.approx(11.80, abs=0.01)
    assert comprobante["monto_igv"] == pytest.approx(1.80, abs=0.01)

    # La fecha de emisión fiscal es la misma que la de la venta (un solo instante).
    venta = entorno["venta_repo"].obtener_por_id(resultado.venta_id)
    assert comprobante["fecha_emision"] == str(venta["creado_en"])[:10]

    # Snapshot autocontenido (independiente de ventas/venta_detalle).
    payload = comprobante["payload"]
    assert payload["emisor"]["razon_social"] == "LA CASA DEL PAN S.A.C."
    assert payload["emisor"]["ruc"] == "20603122144"
    assert payload["receptor"] == {
        "tipo_doc": "1",
        "num_doc": "45678912",
        "nombre": "Juan Perez",
    }
    assert payload["comprobante"]["serie"] == "B001"
    assert payload["comprobante"]["correlativo"] == 1
    assert payload["comprobante"]["moneda"] == "PEN"
    assert payload["totales"]["base_gravada"] == pytest.approx(10.00, abs=0.01)
    assert payload["totales"]["igv"] == pytest.approx(1.80, abs=0.01)
    assert payload["totales"]["total"] == pytest.approx(11.80, abs=0.01)

    # Ítems: unidad SUNAT y desglose por línea para el XML UBL 2.1.
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["descripcion"] == "Pan Frances"
    assert item["unidad_sunat"] == "NIU"  # Catálogo N.º 03
    assert item["tipo_afectacion"] == AFECTACION_GRAVADO
    assert item["base_gravada"] == pytest.approx(10.00, abs=0.01)
    assert item["igv"] == pytest.approx(1.80, abs=0.01)


def test_boleta_sin_documento_usa_tipo_cero(entorno) -> None:
    """Boleta a consumidor final sin DNI: Catálogo N.º 06 → tipo '0'."""
    producto = _crear_producto(entorno, "Yogur Natural", 5.00)
    resultado = _cobrar(entorno, producto, TIPO_BOLETA, cliente="")

    comprobante = entorno["comprobante_repo"].obtener_por_venta(resultado.venta_id)
    assert comprobante is not None
    assert comprobante["receptor_tipo_doc"] == "0"
    assert comprobante["receptor_num_doc"] == ""


# --------------------------------------------------------------------------
# Factura: el receptor siempre va con RUC
# --------------------------------------------------------------------------
def test_factura_registra_receptor_ruc(entorno) -> None:
    producto = _crear_producto(entorno, "Torta Chocolate", 50.00)
    resultado = _cobrar(
        entorno,
        producto,
        TIPO_FACTURA,
        cantidad=2,
        cliente="Panaderia Andina S.A.C.",
        dni_cliente="20987654321",
    )

    comprobante = entorno["comprobante_repo"].obtener_por_venta(resultado.venta_id)
    assert comprobante is not None
    assert comprobante["serie"] == "F001"
    assert comprobante["receptor_tipo_doc"] == "6"  # RUC
    assert comprobante["receptor_num_doc"] == "20987654321"
    assert comprobante["payload"]["receptor"]["tipo_doc"] == "6"


# --------------------------------------------------------------------------
# El comprobante sobrevive al Cierre del Día
# --------------------------------------------------------------------------
def test_comprobante_sobrevive_al_cierre_del_dia(entorno) -> None:
    producto = _crear_producto(entorno, "Pan Frances", 11.80)
    resultado = _cobrar(
        entorno, producto, TIPO_BOLETA, cliente="Juan Perez", dni_cliente="45678912"
    )
    venta_id = resultado.venta_id
    comprobante_id = resultado.comprobante_electronico_id
    fecha = str(entorno["venta_repo"].obtener_por_id(venta_id)["creado_en"])[:10]

    # Cierra el día: borra ventas y venta_detalle (no revierte stock), pero NO
    # el comprobante electrónico (no tiene ON DELETE CASCADE, por diseño).
    _pdf, ventas_eliminadas, _compras = entorno["venta_service"].cerrar_dia(fecha)
    assert ventas_eliminadas >= 1
    assert entorno["venta_repo"].obtener_por_id(venta_id) is None

    comprobante = entorno["comprobante_repo"].obtener(comprobante_id)
    assert comprobante is not None
    assert comprobante["estado"] == ESTADO_PENDIENTE

    # El snapshot conserva los ítems: el XML se puede generar o reenviar luego.
    items = comprobante["payload"]["items"]
    assert len(items) == 1
    assert items[0]["descripcion"] == "Pan Frances"
    assert items[0]["base_gravada"] == pytest.approx(10.00, abs=0.01)
    assert items[0]["igv"] == pytest.approx(1.80, abs=0.01)

    # Y queda alcanzable por fecha para el resumen diario de boletas.
    del_dia = entorno["comprobante_repo"].listar_por_fecha(fecha)
    assert any(c["id"] == comprobante_id for c in del_dia)


# --------------------------------------------------------------------------
# Repositorio: actualización de estado y listados
# --------------------------------------------------------------------------
def test_repo_actualiza_estado_y_lista(entorno) -> None:
    producto = _crear_producto(entorno, "Pan Frances", 11.80)
    resultado = _cobrar(
        entorno, producto, TIPO_BOLETA, cliente="Juan Perez", dni_cliente="45678912"
    )
    repo = entorno["comprobante_repo"]

    pendientes = repo.listar_por_estado(ESTADO_PENDIENTE)
    assert [c["id"] for c in pendientes] == [resultado.comprobante_electronico_id]

    repo.actualizar_estado(
        resultado.comprobante_electronico_id,
        ESTADO_ACEPTADO,
        respuesta_codigo="0",
        respuesta_descripcion="Aceptado",
        hash_cpe="abc123",
        ruta_xml="xml/B001-000001.xml",
        ruta_cdr="cdr/B001-000001.zip",
        incrementar_intentos=True,
    )
    actualizado = repo.obtener(resultado.comprobante_electronico_id)
    assert actualizado is not None
    assert actualizado["estado"] == ESTADO_ACEPTADO
    assert actualizado["respuesta_codigo"] == "0"
    assert actualizado["respuesta_descripcion"] == "Aceptado"
    assert actualizado["hash_cpe"] == "abc123"
    assert actualizado["ruta_xml"] == "xml/B001-000001.xml"
    assert actualizado["intentos"] == 1
    assert actualizado["enviado_en"]
    assert repo.listar_por_estado(ESTADO_PENDIENTE) == []

    with pytest.raises(ValueError):
        repo.actualizar_estado(resultado.comprobante_electronico_id, "NO_EXISTE")


# --------------------------------------------------------------------------
# Dos ventas seguidas: correlativos fiscales independientes del ticket
# --------------------------------------------------------------------------
def test_correlativo_fiscal_independiente_del_ticket(entorno) -> None:
    producto = _crear_producto(entorno, "Pan Frances", 2.00)
    ticket = _cobrar(entorno, producto, TIPO_TICKET)
    boleta = _cobrar(entorno, producto, TIPO_BOLETA, cliente="Ana", dni_cliente="12345678")
    factura = _cobrar(
        entorno, producto, TIPO_FACTURA, cliente="Empresa SAC", dni_cliente="20123456789"
    )

    repo = entorno["comprobante_repo"]
    assert repo.obtener_por_venta(ticket.venta_id) is None
    assert repo.obtener_por_venta(boleta.venta_id)["correlativo"] == 1
    assert repo.obtener_por_venta(factura.venta_id)["correlativo"] == 1

    # Ticket: tiene serie T001 y correlativo
    assert ticket.serie == "T001"
    assert ticket.correlativo == 1
    assert ticket.numero_ticket == "T001-000001"
    assert ticket.numero_comprobante == ""

    # Boleta: NO tiene ticket interno (serie/correlativo = None/0)
    assert boleta.serie is None or boleta.serie == ""
    assert boleta.correlativo == 0
    assert boleta.numero_ticket == ""
    assert boleta.numero_comprobante == "B001-000001"

    # Factura: NO tiene ticket interno
    assert factura.serie is None or factura.serie == ""
    assert factura.correlativo == 0
    assert factura.numero_ticket == ""
    assert factura.numero_comprobante == "F001-000001"
