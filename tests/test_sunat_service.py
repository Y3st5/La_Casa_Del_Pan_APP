"""Pruebas del servicio de emisión electrónica (SUNAT) — Opción B.

Verifican, sobre una base de datos temporal y **sin tocar la red**, que
``SunatService``:

* no envía nada cuando ``[sunat] habilitado = false``;
* genera, firma (simulada) y archiva el XML firmado en ``xml/``;
* guarda el ZIP del CDR en ``cdr/`` y actualiza ``comprobantes_electronicos``
  (estado, ``hash_cpe`` = ``ds:DigestValue``, rutas, códigos, intentos,
  ``enviado_en``);
* deja el comprobante **PENDIENTE** (reintentable) si falla algo local;
* marca **RECHAZADO** cuando SUNAT responde con un código 2xxx/3xxx;
* se dispara *best effort* al cobrar una boleta/factura desde ``VentaService``.

La firma real y el envío SOAP se monkeypatchean: aquí se prueba la
**orquestación**, no criptografía ni red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main as app_main
from src.dominio.catalogos import TIPO_BOLETA
from src.dominio.impuestos import CODIGO_IGV
from src.infraestructura.config_reader import ConfigReader
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.impresora import ImpresoraTicket
from src.infraestructura.sunat_ws import CDRResultado
from src.repositorios.comprobante_electronico_repo import (
    ESTADO_ACEPTADO,
    ESTADO_PENDIENTE,
    ESTADO_RECHAZADO,
    ComprobanteElectronicoRepo,
)
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.producto_service import ProductoService
from src.servicios.sunat_service import CODIGO_ERROR_LOCAL, SunatService
from src.servicios.venta_service import VentaService

# XML "firmado" falso: basta con que contenga un <ds:DigestValue> para que
# ``calcular_hash_cpe`` (que NO se monkeypatchea) lo lea de verdad.
XML_FIRMADO = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<Invoice xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
    b"<ds:Signature><ds:DigestValue>ABC123=</ds:DigestValue></ds:Signature>"
    b"</Invoice>"
)
HASH_ESPERADO = "ABC123="
CDR_FAKE = b"PK\x03\x04fake-cdr-zip"


# --------------------------------------------------------------------------
# Entorno (BD temporal + salidas aisladas)
# --------------------------------------------------------------------------
@pytest.fixture()
def entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """BD temporal con el esquema completo; sin acceso a red ni a config real."""
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

    with bd.cursor() as (_c, cursor):
        categoria_id = int(
            cursor.execute("SELECT id FROM categorias ORDER BY id LIMIT 1").fetchone()[0]
        )
    impuesto_id = next(
        int(i["id"])
        for i in impuesto_repo.listar(incluir_inactivos=True)
        if str(i.get("codigo_sunat")) == CODIGO_IGV
    )

    datos = {
        "bd": bd,
        "tmp": tmp_path,
        "impuesto_repo": impuesto_repo,
        "venta_repo": venta_repo,
        "serie_repo": serie_repo,
        "producto_repo": producto_repo,
        "comprobante_repo": comprobante_repo,
        "producto_service": ProductoService(bd, producto_repo=producto_repo),
        "categoria_id": categoria_id,
        "impuesto_id": impuesto_id,
    }

    # VentaService SIN emisión automática (para las pruebas de SunatService puro).
    datos["venta_service"] = VentaService(
        bd,
        producto_repo=producto_repo,
        venta_repo=venta_repo,
        impuesto_repo=impuesto_repo,
        serie_repo=serie_repo,
        comprobante_repo=comprobante_repo,
        impresora=ImpresoraTicket(carpeta=tmp_path / "tickets"),
    )
    return datos


def _crear_config(tmp: Path, **sunat: object) -> ConfigReader:
    """Escribe un ``config.ini`` temporal (sección ``[sunat]`` incluida)."""
    valores: dict[str, object] = {
        "habilitado": "true",
        "entorno": "beta",
        "cert_path": "config/certificado_prueba.pfx",
        "cert_pass": "test123",
        "usuario_sol": "20603122144MODDATOS",
        "clave_sol": "MODDATOS",
    }
    valores.update(sunat)

    lineas = [
        "[negocio]",
        "razon_social = LA CASA DEL PAN S.A.C.",
        "ruc = 20603122144",
        "",
        "[sunat]",
    ]
    lineas += [f"{clave} = {valor}" for clave, valor in valores.items()]
    ruta = tmp / "config_test.ini"
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ConfigReader(ruta)


def _crear_sunat(entorno, config: ConfigReader) -> SunatService:
    return SunatService(
        entorno["bd"],
        comprobante_repo=entorno["comprobante_repo"],
        config=config,
        raiz_proyecto=entorno["tmp"],
    )


def _crear_producto(entorno, nombre: str, precio: float) -> dict:
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
    return entorno["producto_repo"].obtener_por_id(producto_id)


def _cobrar_boleta(entorno, servicio: VentaService, precio: float = 11.80):
    """Cobra una boleta (S/ 11,80 por defecto) y devuelve el resultado."""
    producto = _crear_producto(entorno, f"Pan {int(precio * 100)}", precio)
    carrito = {int(producto["id"]): {"producto": producto, "cantidad": 1.0}}
    return servicio.confirmar_venta(
        carrito, "Efectivo", total=precio, monto_recibido=precio,
        tipo_comprobante=TIPO_BOLETA,
    )


def _parchear_red(
    monkeypatch: pytest.MonkeyPatch,
    estado: str = ESTADO_ACEPTADO,
    codigo: str = "0",
    descripcion: str = "La Boleta numero B001-00000001, ha sido aceptada",
    *,
    fallar: bool = False,
) -> None:
    """Sustituye firma y envío por dobles: valida la orquestación, no la red."""
    import src.servicios.sunat_service as modulo

    monkeypatch.setattr(modulo, "firmar_xml", lambda xml, *_a, **_k: XML_FIRMADO)

    def _generar_certificado(_ruc: str, ruta: str, _clave: str) -> None:
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        Path(ruta).write_bytes(b"pfx-de-prueba")

    monkeypatch.setattr(modulo, "generar_certificado_prueba", _generar_certificado)

    def _enviar(xml_firmado: bytes, _config, _nombre_zip: str) -> CDRResultado:
        if fallar:
            raise ConnectionError("sin internet")
        return CDRResultado(
            estado=estado,
            respuesta_codigo=codigo,
            respuesta_descripcion=descripcion,
            hash_cpe=HASH_ESPERADO,
            xml_firmado_bytes=xml_firmado,
            cdr_bytes=b"",
            cdr_zip_bytes=CDR_FAKE,
        )

    monkeypatch.setattr(modulo, "enviar_xml_firmado", _enviar)


# --------------------------------------------------------------------------
# Interruptor [sunat] habilitado
# --------------------------------------------------------------------------
def test_deshabilitado_no_envia_ni_toca_la_red(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)  # si se llamara, se notaría
    _cobrar_boleta(entorno, entorno["venta_service"])

    config = _crear_config(entorno["tmp"], habilitado="false")
    sunat = _crear_sunat(entorno, config)

    assert sunat.habilitado is False
    assert sunat.contar_pendientes() == 1
    assert sunat.procesar_pendientes() == []

    comprobante = entorno["comprobante_repo"].listar_por_estado(ESTADO_PENDIENTE)[0]
    assert comprobante["estado"] == ESTADO_PENDIENTE
    assert comprobante["intentos"] == 0
    assert comprobante["hash_cpe"] == ""


# --------------------------------------------------------------------------
# Camino feliz: genera, firma, archiva y actualiza la fila
# --------------------------------------------------------------------------
def test_procesar_aceptado_archiva_xml_cdr_y_actualiza(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)
    resultado_venta = _cobrar_boleta(entorno, entorno["venta_service"])
    comprobante_id = int(resultado_venta.comprobante_electronico_id)
    assert comprobante_id > 0

    config = _crear_config(entorno["tmp"])
    sunat = _crear_sunat(entorno, config)

    assert sunat.contar_pendientes() == 1
    resultado = sunat.procesar(comprobante_id)

    assert resultado.aceptado is True
    assert resultado.estado == ESTADO_ACEPTADO
    assert resultado.respuesta_codigo == "0"
    assert resultado.numero == "B001-000001"

    # Archivos físicos con el nombre estándar SUNAT.
    fila = entorno["comprobante_repo"].obtener(comprobante_id)
    nombre = f"20603122144-03-B001-{int(fila['correlativo']):08d}"
    ruta_xml = entorno["tmp"] / "xml" / f"{nombre}.xml"
    ruta_cdr = entorno["tmp"] / "cdr" / f"R-{nombre}.zip"
    assert ruta_xml.exists() and ruta_xml.read_bytes() == XML_FIRMADO
    assert ruta_cdr.exists() and ruta_cdr.read_bytes() == CDR_FAKE

    fila = entorno["comprobante_repo"].obtener(comprobante_id)
    assert fila["estado"] == ESTADO_ACEPTADO
    assert fila["hash_cpe"] == HASH_ESPERADO  # ds:DigestValue (Base64)
    assert fila["ruta_xml"] == str(ruta_xml)
    assert fila["ruta_cdr"] == str(ruta_cdr)
    assert fila["respuesta_codigo"] == "0"
    assert "aceptada" in fila["respuesta_descripcion"]
    assert fila["intentos"] == 1
    assert fila["enviado_en"]
    assert sunat.contar_pendientes() == 0


def test_procesar_pendientes_envia_todos_los_pendientes(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)
    servicio = entorno["venta_service"]
    for precio in (11.80, 23.60):
        _cobrar_boleta(entorno, servicio, precio=precio)

    sunat = _crear_sunat(entorno, _crear_config(entorno["tmp"]))
    assert sunat.contar_pendientes() == 2

    resultados = sunat.procesar_pendientes()
    assert len(resultados) == 2
    assert all(r.aceptado for r in resultados)
    assert sunat.contar_pendientes() == 0


# --------------------------------------------------------------------------
# Rechazo de SUNAT: queda RECHAZADO y no se reintenta
# --------------------------------------------------------------------------
def test_rechazo_de_sunat_se_marca_rechazado(entorno, monkeypatch) -> None:
    _parchear_red(
        monkeypatch,
        estado=ESTADO_RECHAZADO,
        codigo="2326",
        descripcion="El XML no contiene el tag de impuestos",
    )
    resultado_venta = _cobrar_boleta(entorno, entorno["venta_service"])
    comprobante_id = int(resultado_venta.comprobante_electronico_id)

    sunat = _crear_sunat(entorno, _crear_config(entorno["tmp"]))
    resultado = sunat.procesar(comprobante_id)

    assert resultado.aceptado is False
    assert resultado.estado == ESTADO_RECHAZADO
    fila = entorno["comprobante_repo"].obtener(comprobante_id)
    assert fila["estado"] == ESTADO_RECHAZADO
    assert fila["respuesta_codigo"] == "2326"
    # Ya no está PENDIENTE: no se reintenta solo.
    assert sunat.contar_pendientes() == 0


# --------------------------------------------------------------------------
# Fallo local (sin internet / certificado): PENDIENTE y reintentable
# --------------------------------------------------------------------------
def test_fallo_local_deja_pendiente_para_reintentar(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch, fallar=True)
    resultado_venta = _cobrar_boleta(entorno, entorno["venta_service"])
    comprobante_id = int(resultado_venta.comprobante_electronico_id)

    sunat = _crear_sunat(entorno, _crear_config(entorno["tmp"]))
    resultado = sunat.procesar(comprobante_id)

    assert resultado.estado == ESTADO_PENDIENTE
    assert resultado.error

    fila = entorno["comprobante_repo"].obtener(comprobante_id)
    assert fila["estado"] == ESTADO_PENDIENTE
    assert fila["respuesta_codigo"] == CODIGO_ERROR_LOCAL
    assert "sin internet" in fila["respuesta_descripcion"]
    assert fila["intentos"] == 1
    assert fila["enviado_en"] is None
    # El XML firmado sí quedó archivado como evidencia.
    assert fila["ruta_xml"] and Path(fila["ruta_xml"]).exists()
    assert sunat.contar_pendientes() == 1


def test_produccion_sin_credenciales_falla_sin_romper(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)
    resultado_venta = _cobrar_boleta(entorno, entorno["venta_service"])
    comprobante_id = int(resultado_venta.comprobante_electronico_id)

    config = _crear_config(entorno["tmp"], entorno="produccion", usuario_sol="", clave_sol="")
    sunat = _crear_sunat(entorno, config)

    resultado = sunat.procesar(comprobante_id)
    assert resultado.estado == ESTADO_PENDIENTE
    assert "SOL" in resultado.error
    assert entorno["comprobante_repo"].obtener(comprobante_id)["estado"] == ESTADO_PENDIENTE


# --------------------------------------------------------------------------
# Envío automático al cobrar (best effort, sin congelar la caja)
# --------------------------------------------------------------------------
def test_cobrar_boleta_envia_automaticamente(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)
    config = _crear_config(entorno["tmp"])
    sunat = _crear_sunat(entorno, config)

    servicio = VentaService(
        entorno["bd"],
        producto_repo=entorno["producto_repo"],
        venta_repo=entorno["venta_repo"],
        impuesto_repo=entorno["impuesto_repo"],
        serie_repo=entorno["serie_repo"],
        comprobante_repo=entorno["comprobante_repo"],
        sunat_service=sunat,
        impresora=ImpresoraTicket(carpeta=entorno["tmp"] / "tickets"),
        config=config,
    )

    assert servicio.sunat_habilitado() is True
    resultado = _cobrar_boleta(entorno, servicio)

    fila = entorno["comprobante_repo"].obtener(int(resultado.comprobante_electronico_id))
    assert fila["estado"] == ESTADO_ACEPTADO
    assert fila["hash_cpe"] == HASH_ESPERADO
    assert servicio.contar_pendientes_sunat() == 0


def _venta_service_con_sunat(entorno, config: ConfigReader, sunat: SunatService) -> VentaService:
    """``VentaService`` con emisión electrónica activa (impresora aislada)."""
    return VentaService(
        entorno["bd"],
        producto_repo=entorno["producto_repo"],
        venta_repo=entorno["venta_repo"],
        impuesto_repo=entorno["impuesto_repo"],
        serie_repo=entorno["serie_repo"],
        comprobante_repo=entorno["comprobante_repo"],
        sunat_service=sunat,
        impresora=ImpresoraTicket(carpeta=entorno["tmp"] / "tickets"),
        config=config,
    )


def test_cobrar_no_se_cae_si_sunat_falla(entorno, monkeypatch) -> None:
    """La caja nunca se detiene: si SUNAT falla, la venta igual se registra."""
    _parchear_red(monkeypatch, fallar=True)
    config = _crear_config(entorno["tmp"])
    sunat = _crear_sunat(entorno, config)
    servicio = _venta_service_con_sunat(entorno, config, sunat)

    resultado = _cobrar_boleta(entorno, servicio)

    assert resultado.venta_id > 0  # la venta quedó registrada
    fila = entorno["comprobante_repo"].obtener(int(resultado.comprobante_electronico_id))
    assert fila["estado"] == ESTADO_PENDIENTE
    assert servicio.contar_pendientes_sunat() == 1


# --------------------------------------------------------------------------
# "preparar": genera y firma el XML en local (sin red) para obtener el hash
# --------------------------------------------------------------------------
def test_preparar_devuelve_hash_y_archiva_sin_enviar(entorno, monkeypatch) -> None:
    import src.servicios.sunat_service as modulo

    llamadas = {"firma": 0}
    enviados: list[str] = []

    def _firmar(xml, *_a, **_k):
        llamadas["firma"] += 1
        return XML_FIRMADO

    def _enviar(xml_firmado, _config, nombre_zip):
        enviados.append(nombre_zip)
        return CDRResultado(
            estado=ESTADO_ACEPTADO, respuesta_codigo="0",
            respuesta_descripcion="aceptada", hash_cpe=HASH_ESPERADO,
            xml_firmado_bytes=xml_firmado, cdr_bytes=b"", cdr_zip_bytes=CDR_FAKE,
        )

    monkeypatch.setattr(modulo, "firmar_xml", _firmar)
    monkeypatch.setattr(modulo, "generar_certificado_prueba", lambda *_a, **_k: None)
    monkeypatch.setattr(modulo, "enviar_xml_firmado", _enviar)

    resultado_venta = _cobrar_boleta(entorno, entorno["venta_service"])
    comprobante_id = int(resultado_venta.comprobante_electronico_id)
    sunat = _crear_sunat(entorno, _crear_config(entorno["tmp"]))

    hash_cpe = sunat.preparar(comprobante_id)

    assert hash_cpe == HASH_ESPERADO
    assert llamadas["firma"] == 1
    assert enviados == []  # preparar NO envía nada

    fila = entorno["comprobante_repo"].obtener(comprobante_id)
    assert fila["estado"] == ESTADO_PENDIENTE  # sigue pendiente de envío
    assert fila["hash_cpe"] == HASH_ESPERADO
    assert fila["ruta_xml"] and Path(fila["ruta_xml"]).exists()
    assert fila["ruta_cdr"] == ""
    assert fila["intentos"] == 0
    assert fila["enviado_en"] is None
    assert not (entorno["tmp"] / "cdr").exists() or not list((entorno["tmp"] / "cdr").iterdir())

    # Y el envío posterior reutiliza el XML ya preparado (no vuelve a firmar).
    resultado = sunat.procesar(comprobante_id)
    assert resultado.estado == ESTADO_ACEPTADO
    assert llamadas["firma"] == 1
    assert enviados and enviados[0].endswith(".zip")


# --------------------------------------------------------------------------
# El "Resumen" (hash) sale impreso en la boleta/factura
# --------------------------------------------------------------------------
def _texto_boleta_impresa(entorno, numero: str) -> str:
    ruta = entorno["tmp"] / "boletas" / f"boleta_{numero}.txt"
    return ruta.read_text(encoding="utf-8")


def test_boleta_impresa_incluye_resumen_y_aviso_beta(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)
    config = _crear_config(entorno["tmp"])
    sunat = _crear_sunat(entorno, config)
    servicio = _venta_service_con_sunat(entorno, config, sunat)

    resultado = _cobrar_boleta(entorno, servicio)

    texto = _texto_boleta_impresa(entorno, resultado.numero_comprobante)
    assert f"Resumen: {HASH_ESPERADO}" in texto
    assert "demostración" in texto  # aviso de beta


def test_venta_reimpresa_incluye_resumen(entorno, monkeypatch) -> None:
    _parchear_red(monkeypatch)
    config = _crear_config(entorno["tmp"])
    sunat = _crear_sunat(entorno, config)
    servicio = _venta_service_con_sunat(entorno, config, sunat)

    resultado = _cobrar_boleta(entorno, servicio)
    rutas = servicio.imprimir_venta_guardada(int(resultado.venta_id))

    assert rutas is not None
    texto = _texto_boleta_impresa(entorno, resultado.numero_comprobante)
    assert f"Resumen: {HASH_ESPERADO}" in texto


def test_leyenda_de_produccion_en_el_comprobante(tmp_path) -> None:
    """En producción la leyenda es la oficial (sin aviso de demostración)."""
    from datetime import datetime

    from src.infraestructura.impresora import ImpresoraTicket, LineaTicket, TicketVenta

    ticket = TicketVenta(
        numero_ticket="F001-00000001",
        fecha=datetime(2026, 10, 18, 10, 30),
        lineas=[
            LineaTicket(
                cantidad=1.0, unidad_medida="UND", descripcion="Pan",
                precio_unitario=11.80, importe=11.80,
            )
        ],
        total=11.80,
        metodo_pago="Efectivo",
        tipo_documento="FACTURA",
        numero_documento="F001-00000001",
        hash_cpe="XYZ123=",
        entorno="produccion",
    )

    texto = ImpresoraTicket(carpeta=tmp_path).render_boleta(ticket)

    assert "Representación impresa de la Factura de Venta Electrónica" in texto
    assert "Resumen: XYZ123=" in texto
    assert "demostración" not in texto

