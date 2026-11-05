"""Pruebas de la validación de comprobante en la pantalla de Caja (F12 / COBRAR).

Reproducen el hueco visto en la prueba en vivo: una Boleta de S/ 700,00 o más
sin DNI debe quedar **bloqueada también en la UI** (no solo en el servicio), y
el selector debe volver a Ticket interno dejando un **aviso visible**.

Estas pruebas construyen la vista real de CustomTkinter; si no hay entorno
gráfico (Tk) se saltan automáticamente.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

import main as app_main
from src.dominio.catalogos import (
    ETIQUETA_COMPROBANTE,
    TIPO_BOLETA,
    TIPO_FACTURA,
    TIPO_TICKET,
)
from src.dominio.impuestos import CODIGO_IGV
from src.infraestructura.conexion import ConexionBD
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.producto_service import ProductoService
from src.servicios.venta_service import VentaService

RUC_VALIDO = "20603122144"
DNI_VALIDO = "45678912"


def _hoy() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _primera_categoria(bd: ConexionBD) -> int:
    with bd.cursor() as (_c, cursor):
        cursor.execute("SELECT id FROM categorias ORDER BY id LIMIT 1")
        return int(cursor.fetchone()[0])


def _impuesto_id(impuesto_repo: ImpuestoRepo, codigo: str) -> int:
    for impuesto in impuesto_repo.listar(incluir_inactivos=True):
        if str(impuesto.get("codigo_sunat")) == codigo:
            return int(impuesto["id"])
    raise AssertionError(f"No se encontro el impuesto con codigo {codigo}.")


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
    impresora = modulo_impresora.ImpresoraTicket(carpeta=tmp_path / "tickets")
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
        "producto_service": ProductoService(bd, producto_repo=producto_repo),
        "venta_service": servicio,
        "categoria_id": _primera_categoria(bd),
        "impuesto_igv": _impuesto_id(impuesto_repo, CODIGO_IGV),
    }


@pytest.fixture(scope="module")
def raiz():
    """Un único root de Tk para todo el módulo.

    Los iconos de ``src.presentacion.recursos`` se cachean (``lru_cache``) y un
    ``CTkImage`` queda ligado al intérprete Tk donde se creó: si se destruye el
    root y se crea otro, el cache queda inválido ("image pyimage1 does not
    exist"). Por eso el root se comparte entre las pruebas del módulo.
    """
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001 - sin entorno gráfico
        pytest.skip(f"Sin entorno gráfico (Tk): {exc}")

    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture()
def vista(entorno, raiz):
    """Construye la vista real de Caja sobre la base de datos temporal."""
    from src.presentacion.venta_view import VentaView

    pantalla = VentaView(raiz, venta_service=entorno["venta_service"])
    raiz.update()
    try:
        yield pantalla, raiz
    finally:
        try:
            pantalla.destroy()
        except Exception:  # noqa: BLE001
            pass


def _crear_producto(entorno, nombre: str, precio: float, unidad: str = "UND", stock: float = 100) -> dict:
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


def _cargar(pantalla, producto: dict, cantidad: float = 1.0) -> None:
    pantalla._carrito = {int(producto["id"]): {"producto": producto, "cantidad": cantidad}}
    pantalla._actualizar_total()


def _elegir(pantalla, tipo: str) -> None:
    pantalla._var_comprobante.set(ETIQUETA_COMPROBANTE[tipo])
    pantalla._al_cambiar_comprobante()


def _ventas(entorno) -> list[dict]:
    return entorno["venta_repo"].listar_por_fecha(_hoy())


# --------------------------------------------------------------------------
# Layout: la pantalla de Caja debe caber en 1366x768
# --------------------------------------------------------------------------
def test_el_pie_de_caja_entra_en_1366x768(entorno, raiz) -> None:
    """Regresión: el pie (COBRAR, total, comprobante) quedaba fuera de la ventana.

    El carrito expandible se empaquetaba antes que el pie y, al no poder
    reducirse, aplastaba el pie a 1x1 px en pantallas de 768 px de alto.
    """
    ctk = pytest.importorskip("customtkinter")
    from src.presentacion.venta_view import VentaView

    ventana = ctk.CTkToplevel(raiz)
    ventana.geometry("1320x672")
    vista = VentaView(ventana, venta_service=entorno["venta_service"])
    vista.pack(fill="both", expand=True)
    ventana.update_idletasks()
    ventana.update()

    pie = vista._pie
    assert pie.winfo_height() > 100, "el pie quedó aplastado"
    assert pie.winfo_y() + pie.winfo_height() <= vista.winfo_height() + 1

    boton = vista._btn_cobrar
    assert boton.winfo_height() >= 60, "el botón COBRAR se comprimió"
    borde_derecho = ventana.winfo_rootx() + ventana.winfo_width()
    assert boton.winfo_rootx() + boton.winfo_width() <= borde_derecho + 1

    ventana.destroy()


def test_flat_design_del_pie_y_la_tabla(entorno, raiz) -> None:
    """Regresión del ajuste Flat Design: pie alineado, tabla plana."""
    ctk = pytest.importorskip("customtkinter")
    from src.presentacion import estilos
    from src.presentacion.venta_view import ALTO_CAMPO, VentaView

    ventana = ctk.CTkToplevel(raiz)
    ventana.geometry("1320x672")
    vista = VentaView(ventana, venta_service=entorno["venta_service"])
    vista.pack(fill="both", expand=True)
    ventana.update_idletasks()
    ventana.update()

    try:
        # 1) Los cinco campos del pie comparten alto y línea base.
        campos = (
            vista._menu_comprobante,
            vista._entrada_cliente,
            vista._entrada_doc,
            vista._menu_metodo,
            vista._entrada_monto,
        )
        assert [campo.winfo_height() for campo in campos] == [ALTO_CAMPO] * len(campos)
        assert len({campo.winfo_y() for campo in campos}) == 1

        # 2) La cabecera ya no dibuja el bisel 3D y la selección es crema.
        estilo = estilos.aplicar_tema()
        assert all(
            "border" not in elemento
            for elemento, _opciones in estilo.layout("Venta.Treeview.Heading")
        )
        assert ("selected", estilos.COLOR_SELECCION) in estilo.map(
            "Venta.Treeview", "background"
        )

        # 3) El bloque del total queda centrado con la altura del botón.
        boton = vista._btn_cobrar
        bloque = next(w for w in boton.master.grid_slaves() if w is not boton)
        centro_bloque = bloque.winfo_rooty() + bloque.winfo_height() / 2
        centro_boton = boton.winfo_rooty() + boton.winfo_height() / 2
        assert abs(centro_bloque - centro_boton) <= 1

        # 4) La píldora de estado es plana (sin borde) y redondeada.
        assert vista._marco_estado.cget("border_width") == 0
        assert vista._marco_estado.cget("corner_radius") >= 12
    finally:
        ventana.destroy()


# --------------------------------------------------------------------------
# El atajo F12 usa el mismo flujo que el boton COBRAR
# --------------------------------------------------------------------------
def test_f12_esta_vinculado_al_cobro(vista) -> None:
    _pantalla, raiz = vista
    assert raiz.bind("<F12>")  # el atajo F12 existe y apunta a _al_cobrar


# --------------------------------------------------------------------------
# Ticket interno: se cobra siempre, sin datos fiscales
# --------------------------------------------------------------------------
def test_ticket_se_cobra_sin_datos(vista, entorno) -> None:
    pantalla, raiz = vista
    pan = _crear_producto(entorno, "Pan Frances", 0.50)
    _cargar(pantalla, pan, cantidad=2)          # S/ 1,00
    pantalla._var_monto_recibido.set("1.00")

    pantalla._al_cobrar()
    raiz.update()

    ventas = _ventas(entorno)
    assert len(ventas) == 1
    assert ventas[0]["tipo_comprobante"] == TIPO_TICKET
    assert not (entorno["tmp"] / "boletas").exists()


# --------------------------------------------------------------------------
# Boleta por debajo del tope: DNI opcional
# --------------------------------------------------------------------------
def test_boleta_menor_a_700_cobra_sin_dni(vista, entorno) -> None:
    pantalla, raiz = vista
    producto = _crear_producto(entorno, "Torta Porcion", 5.00)
    _cargar(pantalla, producto, cantidad=10)    # S/ 50,00
    _elegir(pantalla, TIPO_BOLETA)
    pantalla._var_monto_recibido.set("50.00")

    pantalla._al_cobrar()
    raiz.update()

    ventas = _ventas(entorno)
    assert len(ventas) == 1
    assert ventas[0]["tipo_comprobante"] == TIPO_BOLETA
    assert ventas[0]["dni_cliente"] in ("", None)


# --------------------------------------------------------------------------
# Boleta de S/ 700,00 o mas: el candado que fallo en la prueba en vivo
# --------------------------------------------------------------------------
def test_boleta_de_700_sin_dni_queda_bloqueada(vista, entorno) -> None:
    pantalla, raiz = vista
    producto = _crear_producto(entorno, "Canasta Grande", 700.00)
    _cargar(pantalla, producto, cantidad=1)
    _elegir(pantalla, TIPO_BOLETA)
    pantalla._var_monto_recibido.set("700.00")

    pantalla._al_cobrar()
    raiz.update()

    # No se registra ninguna venta y el cajero ve el motivo.
    assert _ventas(entorno) == []
    aviso = pantalla._lbl_estado.cget("text")
    assert "700" in aviso and "DNI" in aviso
    # La pantalla sigue en Boleta para que el cajero complete el dato.
    assert pantalla._tipo_comprobante() == TIPO_BOLETA


def test_boleta_de_700_con_dni_se_cobra(vista, entorno) -> None:
    pantalla, raiz = vista
    producto = _crear_producto(entorno, "Canasta Grande", 700.00)
    _cargar(pantalla, producto, cantidad=1)
    _elegir(pantalla, TIPO_BOLETA)
    pantalla._entrada_doc.insert(0, DNI_VALIDO)
    pantalla._var_monto_recibido.set("700.00")

    pantalla._al_cobrar()
    raiz.update()

    ventas = _ventas(entorno)
    assert len(ventas) == 1
    assert ventas[0]["tipo_comprobante"] == TIPO_BOLETA
    assert ventas[0]["serie_comprobante"] == "B001"
    assert ventas[0]["dni_cliente"] == DNI_VALIDO


# --------------------------------------------------------------------------
# Factura: razon social + RUC de 11 digitos
# --------------------------------------------------------------------------
def test_factura_exige_razon_social_y_ruc(vista, entorno) -> None:
    pantalla, raiz = vista
    producto = _crear_producto(entorno, "Torta de Chocolate", 25.00)
    _cargar(pantalla, producto, cantidad=1)
    _elegir(pantalla, TIPO_FACTURA)
    pantalla._var_monto_recibido.set("25.00")

    # Sin razon social ni RUC: bloqueada.
    pantalla._al_cobrar()
    raiz.update()
    assert _ventas(entorno) == []
    assert "razón social" in pantalla._lbl_estado.cget("text")

    # Con ambos datos: se cobra como factura.
    pantalla._entrada_cliente.insert(0, "Distribuidora XYZ SAC")
    pantalla._entrada_doc.insert(0, RUC_VALIDO)
    pantalla._al_cobrar()
    raiz.update()

    ventas = _ventas(entorno)
    assert len(ventas) == 1
    assert ventas[0]["tipo_comprobante"] == TIPO_FACTURA
    assert ventas[0]["serie_comprobante"] == "F001"
    assert ventas[0]["dni_cliente"] == RUC_VALIDO


# --------------------------------------------------------------------------
# Tras cobrar, el selector vuelve a Ticket interno con aviso visible
# --------------------------------------------------------------------------
def test_el_selector_vuelve_a_ticket_con_aviso(vista, entorno) -> None:
    pantalla, raiz = vista
    producto = _crear_producto(entorno, "Torta Porcion", 5.00)
    _cargar(pantalla, producto, cantidad=10)    # S/ 50,00
    _elegir(pantalla, TIPO_BOLETA)
    pantalla._var_monto_recibido.set("50.00")

    pantalla._al_cobrar()
    raiz.update()

    assert pantalla._tipo_comprobante() == TIPO_TICKET
    assert "Ticket interno" in pantalla._lbl_estado.cget("text")
