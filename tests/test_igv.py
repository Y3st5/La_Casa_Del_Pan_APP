"""Pruebas del calculo de IGV y su persistencia (tabla ``impuestos``).

Verifican, sobre una base de datos temporal:

* que la tabla ``impuestos`` se cree y se siembre con los codigos SUNAT;
* que los productos reciban el impuesto por defecto (IGV 18 %);
* que la venta guarde el desglose tributario y que la **identidad cuadre**
  exactamente al centimo (``base + igv + exonerado + inafecto == subtotal``);
* que la suma de las lineas coincida con la cabecera (requisito UBL 2.1);
* que la boleta muestre ``OP. GRAVADA``, ``IGV (18 %)`` y el total.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

import main as app_main
from src.dominio.catalogos import TIPO_BOLETA, TIPO_TICKET
from src.dominio.impuestos import CODIGO_EXONERADO, CODIGO_IGV
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.impresora import ImpresoraTicket
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.producto_service import ProductoService
from src.servicios.venta_service import VentaService

CENTIMO = Decimal("0.01")


@pytest.fixture()
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Base de datos temporal con el esquema completo y la impresora aislada."""
    import src.infraestructura.impresora as modulo_impresora
    import src.servicios.venta_service as modulo_venta

    # Cada módulo importa ``RAIZ_PROYECTO`` en su espacio de nombres: hay que
    # aislar los dos para que ni tickets, ni boletas, ni el PDF del cierre
    # toquen las carpetas reales del proyecto.
    monkeypatch.setattr(modulo_impresora, "RAIZ_PROYECTO", tmp_path)
    monkeypatch.setattr(modulo_venta, "RAIZ_PROYECTO", tmp_path)

    bd = ConexionBD(ruta_bd=str(tmp_path / "prueba.db"))
    app_main.inicializar_base_datos(bd)

    producto_repo = ProductoRepo(bd)
    impuesto_repo = ImpuestoRepo(bd)
    venta_repo = VentaRepo(bd)
    impresora = ImpresoraTicket(carpeta=tmp_path / "tickets")
    servicio = VentaService(
        bd,
        producto_repo=producto_repo,
        venta_repo=venta_repo,
        impuesto_repo=impuesto_repo,
        impresora=impresora,
    )
    return {
        "bd": bd,
        "tmp": tmp_path,
        "producto_repo": producto_repo,
        "impuesto_repo": impuesto_repo,
        "venta_repo": venta_repo,
        "producto_service": ProductoService(bd, producto_repo=producto_repo),
        "venta_service": servicio,
        "categoria_id": _primera_categoria(bd),
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


# --------------------------------------------------------------------------
# Tabla de impuestos
# --------------------------------------------------------------------------
def test_tabla_impuestos_sembrada(entorno) -> None:
    impuestos = entorno["impuesto_repo"].listar()
    codigos = {str(i["codigo_sunat"]) for i in impuestos}
    assert CODIGO_IGV in codigos
    assert CODIGO_EXONERADO in codigos
    igv = next(i for i in impuestos if str(i["codigo_sunat"]) == CODIGO_IGV)
    assert float(igv["tasa"]) == pytest.approx(0.18)
    assert str(igv["tipo_afectacion"]) == "10"  # gravado


def test_productos_existentes_reciben_igv_por_defecto(entorno) -> None:
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    with entorno["bd"].cursor() as (_c, cursor):
        cursor.execute("SELECT COUNT(*) FROM productos WHERE impuesto_id IS NULL")
        assert int(cursor.fetchone()[0]) == 0
        cursor.execute("SELECT COUNT(*) FROM productos WHERE impuesto_id = ?", (impuesto_igv,))
        assert int(cursor.fetchone()[0]) > 0


# --------------------------------------------------------------------------
# Desglose de la venta
# --------------------------------------------------------------------------
def _crear_producto(entorno, nombre: str, precio: float, impuesto_id: int) -> dict:
    servicio = entorno["producto_service"]
    producto_id = servicio.crear(
        {
            "codigo_interno": nombre.replace(" ", "")[:12].upper(),
            "nombre": nombre,
            "precio_venta": precio,
            "categoria_id": entorno["categoria_id"],
            "impuesto_id": impuesto_id,
            "stock_actual": 100,
            "controla_stock": True,
        }
    )
    return entorno["producto_repo"].obtener_por_id(producto_id)


def _vender(
    entorno,
    producto: dict,
    cantidad: float,
    total: float,
    tipo_comprobante: str = TIPO_TICKET,
    cliente: str = "",
    dni_cliente: str = "",
):
    carrito = {int(producto["id"]): {"producto": producto, "cantidad": cantidad}}
    return entorno["venta_service"].confirmar_venta(
        carrito,
        "Efectivo",
        total=total,
        monto_recibido=total,
        tipo_comprobante=tipo_comprobante,
        cliente=cliente,
        dni_cliente=dni_cliente,
    )


def test_venta_gravada_cuadra_al_centimo(entorno) -> None:
    """El caso del jefe: 2 panes de S/ 0.50 -> base 0.85 + IGV 0.15 = 1.00."""
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    pan = _crear_producto(entorno, "Pan Frances", 0.50, impuesto_igv)

    resultado = _vender(entorno, pan, 2, total=1.00)

    assert resultado.subtotal == pytest.approx(1.00)
    assert resultado.base_gravada == pytest.approx(0.85)
    assert resultado.igv == pytest.approx(0.15)
    assert resultado.redondeo == pytest.approx(0.00)
    assert resultado.total == pytest.approx(1.00)
    # Identidad tributaria exacta.
    assert (
        Decimal(str(resultado.base_gravada))
        + Decimal(str(resultado.igv))
        + Decimal(str(resultado.exonerado))
        + Decimal(str(resultado.inafecto))
        == Decimal(str(resultado.subtotal))
    )

    cabecera = entorno["venta_repo"].obtener_por_id(resultado.venta_id)
    assert cabecera["base_gravada"] == pytest.approx(0.85)
    assert cabecera["igv"] == pytest.approx(0.15)

    detalle = entorno["venta_repo"].listar_detalle(resultado.venta_id)
    # El carrito agrupa por producto: 2 unidades son una sola línea de cantidad 2.
    assert len(detalle) == 1
    linea = detalle[0]
    assert float(linea["cantidad"]) == pytest.approx(2.0)
    assert Decimal(str(linea["base_gravada"])) == Decimal("0.85")
    assert Decimal(str(linea["igv"])) == Decimal("0.15")
    assert int(linea["impuesto_id"]) == impuesto_igv
    assert str(linea["tipo_afectacion"]) == "10"
    assert float(linea["tasa_impuesto"]) == pytest.approx(0.18)


def test_dos_lineas_reparten_el_residuo_de_centimos(entorno) -> None:
    """Dos líneas de S/ 0.50: las líneas deben sumar exactamente la cabecera."""
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    pan_a = _crear_producto(entorno, "Pan A", 0.50, impuesto_igv)
    pan_b = _crear_producto(entorno, "Pan B", 0.50, impuesto_igv)

    carrito = {
        int(pan_a["id"]): {"producto": pan_a, "cantidad": 1},
        int(pan_b["id"]): {"producto": pan_b, "cantidad": 1},
    }
    resultado = entorno["venta_service"].confirmar_venta(
        carrito, "Efectivo", total=1.00, monto_recibido=1.00
    )

    assert resultado.base_gravada == pytest.approx(0.85)
    assert resultado.igv == pytest.approx(0.15)

    detalle = entorno["venta_repo"].listar_detalle(resultado.venta_id)
    assert len(detalle) == 2
    assert sum(Decimal(str(d["base_gravada"])) for d in detalle) == Decimal("0.85")
    assert sum(Decimal(str(d["igv"])) for d in detalle) == Decimal("0.15")
    for linea in detalle:
        # Cada línea tiene una base e IGV "de verdad" (no todo el residuo en una).
        assert Decimal(str(linea["base_gravada"])) + Decimal(str(linea["igv"])) == Decimal("0.50")
        assert Decimal("0.06") <= Decimal(str(linea["igv"])) <= Decimal("0.09")


def test_muchas_lineas_pequenas_no_pierden_centimos(entorno) -> None:
    """9 panes de S/ 0.10: el reparto de centimos no debe descuadrar el total."""
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    pan = _crear_producto(entorno, "Panecillo", 0.10, impuesto_igv)

    resultado = _vender(entorno, pan, 9, total=0.90)
    assert resultado.subtotal == pytest.approx(0.90)
    assert resultado.base_gravada == pytest.approx(0.76)
    assert resultado.igv == pytest.approx(0.14)

    detalle = entorno["venta_repo"].listar_detalle(resultado.venta_id)
    assert len(detalle) == 1
    assert sum(Decimal(str(d["base_gravada"])) for d in detalle) == Decimal("0.76")
    assert sum(Decimal(str(d["igv"])) for d in detalle) == Decimal("0.14")
    assert all(Decimal(str(d["igv"])) >= Decimal("0") for d in detalle)


def test_venta_exonerada_no_tiene_igv(entorno) -> None:
    impuesto_exo = _impuesto_id(entorno["impuesto_repo"], CODIGO_EXONERADO)
    producto = _crear_producto(entorno, "Leche Fresca", 3.50, impuesto_exo)

    resultado = _vender(entorno, producto, 1, total=3.50)

    assert resultado.igv == pytest.approx(0.00)
    assert resultado.base_gravada == pytest.approx(0.00)
    assert resultado.exonerado == pytest.approx(3.50)
    assert resultado.subtotal == pytest.approx(3.50)

    detalle = entorno["venta_repo"].listar_detalle(resultado.venta_id)
    assert detalle[0]["igv"] == pytest.approx(0.00)
    assert str(detalle[0]["tipo_afectacion"]) == "20"


def test_redondeo_no_rompe_la_identidad_tributaria(entorno) -> None:
    """Con centimos, el total se redondea pero la base + IGV no se tocan."""
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    producto = _crear_producto(entorno, "Postre", 1.14, impuesto_igv)

    resultado = _vender(entorno, producto, 1, total=1.14)

    assert resultado.subtotal == pytest.approx(1.14)
    assert resultado.total == pytest.approx(1.10)
    assert resultado.redondeo == pytest.approx(-0.04)
    assert (
        Decimal(str(resultado.base_gravada)) + Decimal(str(resultado.igv))
        == Decimal(str(resultado.subtotal))
    )


def test_boleta_muestra_el_desglose(entorno) -> None:
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    pan = _crear_producto(entorno, "Pan Integral", 0.50, impuesto_igv)

    # La boleta solo se emite si el cajero elige ese tipo de comprobante: el
    # valor por defecto es el ticket interno (sin documento fiscal).
    resultado = _vender(entorno, pan, 2, total=1.00, tipo_comprobante=TIPO_BOLETA)

    boletas = list((entorno["tmp"] / "boletas").glob("boleta_*.txt"))
    assert len(boletas) == 1
    texto = boletas[0].read_text(encoding="utf-8")
    assert "OP. GRAVADA" in texto
    assert "IGV (18%)" in texto
    assert "TOTAL A PAGAR" in texto
    assert "0.85" in texto and "0.15" in texto
    # Con "Una venta = un solo documento", la boleta NO genera ticket interno
    assert resultado.numero_ticket == ""
    assert resultado.numero_comprobante.startswith("B001-")


# --------------------------------------------------------------------------
# Resumen tributario en el PDF del Cierre de Día
# --------------------------------------------------------------------------
def _descomprimir(flujo: bytes) -> bytes:
    """Descomprime un flujo de un PDF de forma tolerante (zlib o deflate crudo)."""
    import zlib

    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            datos = zlib.decompressobj(wbits).decompress(flujo)
        except zlib.error:
            continue
        if datos:
            return datos
    return flujo


def _texto_pdf(ruta: Path) -> str:
    """Extrae el texto visible de un PDF (descomprime sus flujos).

    En el PDF los paréntesis se escapan (``\\(`` y ``\\)``), por eso el patrón
    los respeta antes de normalizarlos para poder buscar texto legible.
    """
    import re

    datos = ruta.read_bytes()
    flujos = [
        _descomprimir(flujo)
        for flujo in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", datos, flags=re.DOTALL)
    ]
    contenido = b"\n".join(flujos)
    patron = re.compile(rb"\(((?:\\.|[^()\\])*)\)")
    partes = []
    for coincidencia in patron.finditer(contenido):
        texto = coincidencia.group(1)
        texto = texto.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
        partes.append(texto.decode("latin-1"))
    return " ".join(partes)


def test_cierre_de_dia_incluye_resumen_tributario(entorno) -> None:
    impuesto_igv = _impuesto_id(entorno["impuesto_repo"], CODIGO_IGV)
    impuesto_exo = _impuesto_id(entorno["impuesto_repo"], CODIGO_EXONERADO)
    pan = _crear_producto(entorno, "Pan Frances", 0.50, impuesto_igv)
    leche = _crear_producto(entorno, "Leche Fresca", 3.50, impuesto_exo)

    _vender(entorno, pan, 2, total=1.00)          # gravado: base 0.85 + IGV 0.15
    _vender(entorno, leche, 1, total=3.50)        # exonerado: 3.50 sin IGV

    ruta_pdf, ventas_eliminadas, _compras = entorno["venta_service"].cerrar_dia()
    assert ventas_eliminadas == 2

    pdf = Path(ruta_pdf)
    assert pdf.exists()
    # El PDF debe quedar dentro del tmp_path del test (no en reportes/ real).
    assert entorno["tmp"] in pdf.parents
    texto = _texto_pdf(pdf)

    assert "Resumen tributario" in texto
    assert "Operaciones gravadas" in texto
    assert "IGV (18%)" in texto
    assert "Operaciones exoneradas" in texto
    # Base imponible 0.85 + IGV 0.15 (los importes van sin separador de miles).
    assert "0.85" in texto
    assert "0.15" in texto
    assert "3.50" in texto
    assert "Total cobrado" in texto
    assert "4.50" in texto  # 1.00 + 3.50
