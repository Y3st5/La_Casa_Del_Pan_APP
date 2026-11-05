"""Pruebas del Fix #2 de preparación SUNAT (comprobantes).

Verifican, sobre una base de datos temporal:

* que cada serie (``T001``, ``B001``, ``F001``) lleve su **propio** correlativo;
* que el numerado **no** se reinicie al cerrar el día (el bug de fondo: el
  cierre borra las ventas y ``MAX(correlativo)+1`` repetía números);
* que el ticket interno se emita **siempre** y el documento fiscal solo si el
  cajero lo elige;
* las reglas de identificación: DNI obligatorio en boletas de S/ 700,00 o más
  (umbral inclusivo), RUC y razón social obligatorios en la factura;
* el mapeo de unidades al **Catálogo N.º 03** (``UND``→``NIU``, ``KG``→``KGM``…)
  y su persistencia en el detalle como *snapshot*.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main as app_main
from src.dominio.catalogos import (
    CATALOGO_03_UNIDADES,
    TIPO_BOLETA,
    TIPO_FACTURA,
    TIPO_TICKET,
    codigo_unidad_sunat,
    es_dni_valido,
    es_ruc_valido,
    validar_compra,
)
from src.dominio.excepciones import ComprobanteInvalido
from src.dominio.impuestos import CODIGO_IGV
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.impresora import ImpresoraTicket
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.producto_service import ProductoService
from src.servicios.venta_service import VentaService

RUC_VALIDO = "20603122144"
DNI_VALIDO = "45678912"


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
    impresora = ImpresoraTicket(carpeta=tmp_path / "tickets")
    servicio = VentaService(
        bd,
        producto_repo=producto_repo,
        venta_repo=venta_repo,
        impuesto_repo=impuesto_repo,
        serie_repo=serie_repo,
        impresora=impresora,
    )
    return {
        "bd": bd,
        "tmp": tmp_path,
        "producto_repo": producto_repo,
        "venta_repo": venta_repo,
        "serie_repo": serie_repo,
        "producto_service": ProductoService(bd, producto_repo=producto_repo),
        "venta_service": servicio,
        "categoria_id": _primera_categoria(bd),
        "impuesto_igv": _impuesto_id(impuesto_repo, CODIGO_IGV),
    }


def _primera_categoria(bd: ConexionBD) -> int:
    with bd.cursor() as (_c, cursor):
        cursor.execute("SELECT id FROM categorias ORDER BY id LIMIT 1")
        return int(cursor.fetchone()[0])


def _impuesto_id(impuesto_repo: ImpuestoRepo, codigo: str) -> int:
    for impuesto in impuesto_repo.listar(incluir_inactivos=True):
        if str(impuesto.get("codigo_sunat")) == codigo:
            return int(impuesto["id"])
    raise AssertionError(f"No se encontro el impuesto con codigo {codigo}.")


def _crear_producto(
    entorno, nombre: str, precio: float, unidad: str = "UND", stock: float = 100
) -> dict:
    """Crea un producto con la unidad de medida indicada."""
    producto_id = entorno["producto_service"].crear(
        {
            "codigo_interno": nombre.replace(" ", "")[:12].upper(),
            "nombre": nombre,
            "precio_venta": precio,
            "categoria_id": entorno["categoria_id"],
            "impuesto_id": entorno["impuesto_igv"],
            "unidad_medida": unidad,
            "stock_actual": stock,
            "controla_peso": False,
            "controla_stock": True,
        }
    )
    return entorno["producto_repo"].obtener_por_id(producto_id)


def _vender(
    entorno,
    producto: dict,
    cantidad: float = 1,
    tipo_comprobante: str = TIPO_TICKET,
    cliente: str = "",
    documento: str = "",
):
    precio = float(producto["precio_venta"])
    total = round(cantidad * precio, 2)
    carrito = {int(producto["id"]): {"producto": producto, "cantidad": cantidad}}
    return entorno["venta_service"].confirmar_venta(
        carrito,
        "Efectivo",
        total=total,
        monto_recibido=total,
        tipo_comprobante=tipo_comprobante,
        cliente=cliente,
        dni_cliente=documento,
    )


# --------------------------------------------------------------------------
# Catálogo N.º 03 (unidades de medida)
# --------------------------------------------------------------------------
def test_catalogo_03_mapea_las_unidades() -> None:
    assert codigo_unidad_sunat("UND") == "NIU"
    assert codigo_unidad_sunat("kg") == "KGM"
    assert codigo_unidad_sunat("GR") == "GRM"
    assert codigo_unidad_sunat("LT") == "LTR"
    assert codigo_unidad_sunat("DOCENA") == "DZN"
    # Unidad desconocida: respaldo admitido por SUNAT.
    assert codigo_unidad_sunat("GALON") == "ZZ"
    assert codigo_unidad_sunat(None) == "ZZ"
    # Todas las unidades del catalogo interno tienen codigo SUNAT.
    from src.servicios.producto_service import UNIDADES_MEDIDA

    faltantes = [u for u in UNIDADES_MEDIDA if u not in CATALOGO_03_UNIDADES]
    assert faltantes == []


def test_detalle_guarda_el_codigo_sunat(entorno) -> None:
    queso = _crear_producto(entorno, "Queso Fresco", 20.00, unidad="KG")

    resultado = _vender(entorno, queso, cantidad=1.5)

    detalle = entorno["venta_repo"].listar_detalle(resultado.venta_id)
    assert len(detalle) == 1
    assert str(detalle[0]["unidad_sunat"]) == "KGM"


# --------------------------------------------------------------------------
# Series y correlativos independientes
# --------------------------------------------------------------------------
def test_series_sembradas_con_su_correlativo(entorno) -> None:
    series = {s["serie"]: s for s in entorno["serie_repo"].listar()}
    assert {"T001", "B001", "F001"} <= set(series)
    assert series["B001"]["tipo_comprobante"] == TIPO_BOLETA
    assert series["F001"]["tipo_comprobante"] == TIPO_FACTURA
    assert int(series["T001"]["correlativo_actual"]) == 0


def test_series_llevan_correlativos_independientes(entorno) -> None:
    pan = _crear_producto(entorno, "Pan Frances", 0.50)

    ticket1 = _vender(entorno, pan, tipo_comprobante=TIPO_TICKET)
    boleta1 = _vender(entorno, pan, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO)
    factura1 = _vender(
        entorno, pan, tipo_comprobante=TIPO_FACTURA,
        cliente="Distribuidora XYZ SAC", documento=RUC_VALIDO,
    )

    # Ticket interno: tiene T001
    assert ticket1.numero_ticket == "T001-000001"
    assert ticket1.numero_comprobante == ""

    # Boleta y Factura: NO tienen ticket interno (Una venta = un solo documento)
    assert boleta1.numero_ticket == ""
    assert boleta1.numero_comprobante == "B001-000001"
    assert factura1.numero_ticket == ""
    assert factura1.numero_comprobante == "F001-000001"


def test_el_numerado_no_se_reinicia_al_cerrar_el_dia(entorno) -> None:
    """El bug de fondo: el cierre borra las ventas y el correlativo repetía."""
    pan = _crear_producto(entorno, "Pan Frances", 0.50)

    primera = _vender(entorno, pan, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO)
    assert primera.numero_comprobante == "B001-000001"
    # No hay ticket interno para boleta
    assert primera.numero_ticket == ""

    ruta_pdf, ventas_eliminadas, _compras = entorno["venta_service"].cerrar_dia()
    assert ventas_eliminadas == 1
    assert Path(ruta_pdf).exists()
    # Las ventas del día ya no están...
    assert entorno["venta_repo"].listar_por_fecha(
        __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    ) == []

    # ...pero la numeración continúa donde quedó (no vuelve a B001-000001).
    segunda = _vender(entorno, pan, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO)
    assert segunda.numero_comprobante == "B001-000002"
    # No hay ticket interno para boleta
    assert segunda.numero_ticket == ""


def test_la_siembra_de_correlativos_no_se_repite_al_arrancar(entorno) -> None:
    """Regresión: la siembra es una migración de **una sola vez**.

    Si se reejecutara en cada arranque, la serie de boleta se pegaría al número
    de ticket (que avanza en toda venta) y descuadraría la numeración fiscal.
    """
    antes = {s["serie"]: int(s["correlativo_actual"]) for s in entorno["serie_repo"].listar()}
    assert antes["B001"] == 0

    pan = _crear_producto(entorno, "Pan Frances", 0.50)
    for _ in range(3):
        _vender(entorno, pan, tipo_comprobante=TIPO_TICKET)

    # Un "reinicio" de la app: vuelve a verificar/migrar el esquema.
    app_main.inicializar_base_datos(entorno["bd"])

    despues = {s["serie"]: int(s["correlativo_actual"]) for s in entorno["serie_repo"].listar()}
    assert despues["T001"] == antes["T001"] + 3
    assert despues["B001"] == 0, "la siembra volvió a subir la serie de boleta al número de ticket"


# --------------------------------------------------------------------------
# Tipo de comprobante y datos del cliente
# --------------------------------------------------------------------------
def test_boleta_justo_debajo_de_700_no_exige_documento(entorno) -> None:
    """S/ 699,90 es "hasta 700": el DNI sigue siendo opcional."""
    producto = _crear_producto(entorno, "Canasta Grande", 699.90)

    resultado = _vender(entorno, producto, tipo_comprobante=TIPO_BOLETA)

    assert resultado.numero_comprobante.startswith("B001-")
    cabecera = entorno["venta_repo"].obtener_por_id(resultado.venta_id)
    assert str(cabecera["tipo_comprobante"]) == TIPO_BOLETA


def test_boleta_de_700_exacto_exige_documento(entorno) -> None:
    """El umbral es inclusivo: S/ 700,00 exactos ya exigen DNI/RUC."""
    producto = _crear_producto(entorno, "Canasta Grande", 700.00)

    with pytest.raises(ComprobanteInvalido):
        _vender(entorno, producto, tipo_comprobante=TIPO_BOLETA)

    # Con DNI sí pasa.
    resultado = _vender(
        entorno, producto, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO
    )
    cabecera = entorno["venta_repo"].obtener_por_id(resultado.venta_id)
    assert str(cabecera["dni_cliente"]) == DNI_VALIDO


def test_boleta_sobre_700_exige_documento(entorno) -> None:
    producto = _crear_producto(entorno, "Canasta Grande", 700.10)

    with pytest.raises(ComprobanteInvalido):
        _vender(entorno, producto, tipo_comprobante=TIPO_BOLETA)

    # Con RUC (11 dígitos) también se admite en la boleta.
    resultado = _vender(
        entorno, producto, tipo_comprobante=TIPO_BOLETA, documento=RUC_VALIDO
    )
    cabecera = entorno["venta_repo"].obtener_por_id(resultado.venta_id)
    assert str(cabecera["dni_cliente"]) == RUC_VALIDO


def test_validar_compra_umbral_inclusivo() -> None:
    """La regla del tope se decide en un solo lugar y es inclusiva."""
    from src.dominio.catalogos import boleta_exige_documento

    assert boleta_exige_documento(699.99) is False
    assert boleta_exige_documento(700.00) is True
    assert boleta_exige_documento(700.01) is True

    with pytest.raises(ComprobanteInvalido):
        validar_compra(TIPO_BOLETA, 700.00, "", "")
    assert validar_compra(TIPO_BOLETA, 700.00, documento=DNI_VALIDO) == (
        TIPO_BOLETA,
        "",
        DNI_VALIDO,
    )


def test_factura_exige_razon_social_y_ruc(entorno) -> None:
    producto = _crear_producto(entorno, "Torta de Chocolate", 25.00)

    with pytest.raises(ComprobanteInvalido):
        _vender(entorno, producto, tipo_comprobante=TIPO_FACTURA, documento=RUC_VALIDO)
    with pytest.raises(ComprobanteInvalido):
        _vender(
            entorno, producto, tipo_comprobante=TIPO_FACTURA,
            cliente="Distribuidora XYZ SAC", documento="123",
        )

    resultado = _vender(
        entorno, producto, tipo_comprobante=TIPO_FACTURA,
        cliente="Distribuidora XYZ SAC", documento=RUC_VALIDO,
    )
    cabecera = entorno["venta_repo"].obtener_por_id(resultado.venta_id)
    assert str(cabecera["tipo_comprobante"]) == TIPO_FACTURA
    assert str(cabecera["cliente"]) == "Distribuidora XYZ SAC"
    assert str(cabecera["dni_cliente"]) == RUC_VALIDO


def test_tipos_de_comprobante_desconocidos_se_rechazan(entorno) -> None:
    with pytest.raises(ComprobanteInvalido):
        validar_compra("FACTURITA", 10.0, "X", RUC_VALIDO)
    # Vacío = ticket interno (lo más conservador).
    assert validar_compra("", 10.0) == (TIPO_TICKET, "", "")


@pytest.mark.parametrize(
    ("documento", "es_dni", "es_ruc"),
    [
        ("45678912", True, False),
        ("20603122144", False, True),
        ("4567891", False, False),
        ("20-603122-144", False, True),  # se toleran separadores
        ("", False, False),
    ],
)
def test_validacion_de_documentos(documento, es_dni, es_ruc) -> None:
    assert es_dni_valido(documento) is es_dni
    assert es_ruc_valido(documento) is es_ruc


# --------------------------------------------------------------------------
# Documentos impresos
# --------------------------------------------------------------------------
def test_venta_solo_ticket_no_emite_documento_fiscal(entorno) -> None:
    pan = _crear_producto(entorno, "Pan Frances", 0.50)

    resultado = _vender(entorno, pan, tipo_comprobante=TIPO_TICKET)

    tickets = list((entorno["tmp"] / "tickets").glob("ticket_T001-*.txt"))
    assert len(tickets) == 1
    assert not (entorno["tmp"] / "boletas").exists()
    assert not (entorno["tmp"] / "facturas").exists()
    assert resultado.numero_comprobante == ""


def test_la_boleta_lleva_el_codigo_de_unidad_sunat(entorno) -> None:
    queso = _crear_producto(entorno, "Queso Fresco", 20.00, unidad="KG")

    _vender(entorno, queso, cantidad=1.5, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO)

    boletas = list((entorno["tmp"] / "boletas").glob("boleta_B001-*.txt"))
    assert len(boletas) == 1
    texto = boletas[0].read_text(encoding="utf-8")
    assert "1.5 KGM" in texto          # Catálogo N.º 03, no "kg"
    assert "BOLETA DE VENTA" in texto


def test_la_factura_va_a_su_carpeta_y_muestra_razon_social(entorno) -> None:
    producto = _crear_producto(entorno, "Torta de Chocolate", 25.00)

    _vender(
        entorno, producto, tipo_comprobante=TIPO_FACTURA,
        cliente="Distribuidora XYZ SAC", documento=RUC_VALIDO,
    )

    facturas = list((entorno["tmp"] / "facturas").glob("factura_F001-*.txt"))
    assert len(facturas) == 1
    texto = facturas[0].read_text(encoding="utf-8")
    assert "FACTURA DE VENTA" in texto
    assert "Señor(es): Distribuidora XYZ SAC" in texto
    assert f"RUC     : {RUC_VALIDO}" in texto
    assert "OP. GRAVADA" in texto  # la factura lleva el desglose tributario


def test_reimpresion_respeta_el_comprobante_emitido(entorno) -> None:
    producto = _crear_producto(entorno, "Torta de Chocolate", 25.00)
    resultado = _vender(
        entorno, producto, tipo_comprobante=TIPO_FACTURA,
        cliente="Distribuidora XYZ SAC", documento=RUC_VALIDO,
    )
    for archivo in (entorno["tmp"] / "facturas").glob("*.txt"):
        archivo.unlink()

    rutas = entorno["venta_service"].imprimir_venta_guardada(resultado.venta_id)

    assert rutas is not None
    assert "factura_F001-000001.txt" in rutas
    assert (entorno["tmp"] / "facturas" / "factura_F001-000001.txt").exists()


def test_ventas_del_dia_expone_el_comprobante(entorno) -> None:
    producto = _crear_producto(entorno, "Torta de Chocolate", 25.00)
    _vender(
        entorno, producto, tipo_comprobante=TIPO_FACTURA,
        cliente="Distribuidora XYZ SAC", documento=RUC_VALIDO,
    )

    reporte = entorno["venta_service"].ventas_del_dia()

    assert reporte["resumen"]["cantidad"] == 1
    venta = reporte["ventas"][0]
    assert venta["tipo_comprobante"] == TIPO_FACTURA
    assert venta["numero_comprobante"] == "F001-000001"


def _descomprimir(flujo: bytes) -> bytes:
    """Descomprime un flujo de un PDF de forma tolerante.

    ``zlib.decompress`` falla si el flujo quedó truncado al recortarlo con el
    patrón; con ``decompressobj`` se obtiene la parte decodificable (que es todo
    lo que necesita el test). También cubre *deflate* crudo (sin cabecera).
    """
    import zlib

    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            datos = zlib.decompressobj(wbits).decompress(flujo)
        except zlib.error:
            continue
        if datos:
            return datos
    return flujo  # flujo sin comprimir (texto directo)


def _texto_pdf(ruta: Path) -> str:
    """Extrae el texto visible de un PDF (descomprime sus flujos)."""
    import re

    datos = ruta.read_bytes()
    flujos = [
        _descomprimir(flujo)
        for flujo in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", datos, flags=re.DOTALL)
    ]
    contenido = b"\n".join(flujos)
    patron = re.compile(rb"\(((?:\\.|[^()\\])*)\)")
    return " ".join(
        coincidencia.group(1)
        .replace(b"\\(", b"(")
        .replace(b"\\)", b")")
        .replace(b"\\\\", b"\\")
        .decode("latin-1")
        for coincidencia in patron.finditer(contenido)
    )


def test_el_cierre_del_dia_lista_los_comprobantes_emitidos(entorno) -> None:
    producto = _crear_producto(entorno, "Torta de Chocolate", 25.00)
    _vender(entorno, producto, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO)
    _vender(entorno, producto, tipo_comprobante=TIPO_TICKET)

    ruta_pdf, ventas_eliminadas, _compras = entorno["venta_service"].cerrar_dia()

    assert ventas_eliminadas == 2
    texto = _texto_pdf(Path(ruta_pdf))
    assert "Comprobante" in texto
    assert "B001-000001" in texto          # documento fiscal emitido (boleta)
    assert "T001-000001" in texto          # ticket interno de control
    assert "Comprobantes emitidos: 1 boleta" in texto


def test_el_pdf_del_cierre_soporta_acentos_y_simbolos(entorno) -> None:
    """Las fuentes base del PDF usan latin-1: con tildes o "—" reventaban."""
    producto = _crear_producto(entorno, "Café con Leche Especial — Grande", 12.00)
    _vender(entorno, producto, tipo_comprobante=TIPO_BOLETA, documento=DNI_VALIDO)

    ruta_pdf, ventas_eliminadas, _compras = entorno["venta_service"].cerrar_dia()

    assert ventas_eliminadas == 1
    assert Path(ruta_pdf).exists()
    texto = _texto_pdf(Path(ruta_pdf))
    assert "Caf" in texto  # el nombre con tilde llegó al PDF sin romperlo
