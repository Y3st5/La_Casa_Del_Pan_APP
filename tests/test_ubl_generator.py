"""Pruebas del generador XML UBL 2.1 (CustomizationID 2.0).

Verifica que:
- Factura (01) y Boleta (03) generan XML bien formado.
- Los namespaces, catálogos (01, 03, 05, 06, 07) y estructura son correctos.
- El *payload* del snapshot (emisor, receptor, totales, ítems) se traduce fielmente.
- Se puede regenerar el XML **sin consultar la BD** (solo desde el payload).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from src.infraestructura.ubl_generator import (
    AFECTACION_EXONERADO,
    AFECTACION_GRAVADO,
    AFECTACION_INAFECTO,
    CODIGO_IGV,
    TIPO_COMPROBANTE_BOLETA,
    TIPO_COMPROBANTE_FACTURA,
    _validar_payload,
    generar_xml_comprobante,
    guardar_xml_comprobante,
)


# ----------------------------------------------------------------------
# Payloads de prueba (simulan lo que viene de comprobantes_electronicos.payload_json)
# ----------------------------------------------------------------------


def _payload_factura_base() -> dict:
    """Factura simple: 1 ítem gravado IGV 18 %."""
    return {
        "emisor": {
            "razon_social": "LA CASA DEL PAN S.A.C.",
            "nombre_comercial": "LA CASA DEL PAN",
            "ruc": "20603122144",
            "direccion": "AV. NICOLAS AYLLON NRO. 1502 URB. CHACLACAYO",
            "ubigeo": "150107",
            "telefono": "",
        },
        "receptor": {
            "tipo_doc": "6",
            "num_doc": "20123456789",
            "nombre": "PANADERIA LOS ANDES S.A.C.",
        },
        "comprobante": {
            "tipo": "FACTURA",
            "serie": "F001",
            "correlativo": 1,
            "fecha_emision": "2026-10-18",
            "hora_emision": "10:30:00",
            "moneda": "PEN",
        },
        "totales": {
            "subtotal": 118.00,
            "base_gravada": 100.00,
            "igv": 18.00,
            "exonerado": 0.0,
            "inafecto": 0.0,
            "redondeo": 0.0,
            "total": 118.00,
        },
        "items": [
            {
                "descripcion": "PAN FRANCES",
                "cantidad": 50,
                "unidad_sunat": "NIU",
                "precio_unitario": 2.36,  # con IGV
                "importe": 118.00,
                "tipo_afectacion": AFECTACION_GRAVADO,
                "base_gravada": 100.00,
                "igv": 18.00,
                "codigo": "PAN001",
                "impuesto_id": 1,
            }
        ],
    }


def _payload_boleta_base() -> dict:
    """Boleta simple: 1 ítem gravado IGV 18 %, receptor con DNI."""
    p = _payload_factura_base()
    p["comprobante"]["tipo"] = "BOLETA"
    p["comprobante"]["serie"] = "B001"
    p["receptor"] = {"tipo_doc": "1", "num_doc": "45678912", "nombre": "JUAN PEREZ"}
    # Ajustar totales a un monto menor
    p["totales"] = {
        "subtotal": 11.80,
        "base_gravada": 10.00,
        "igv": 1.80,
        "exonerado": 0.0,
        "inafecto": 0.0,
        "redondeo": 0.0,
        "total": 11.80,
    }
    p["items"][0]["precio_unitario"] = 11.80
    p["items"][0]["importe"] = 11.80
    p["items"][0]["base_gravada"] = 10.00
    p["items"][0]["igv"] = 1.80
    p["items"][0]["cantidad"] = 1
    return p


def _payload_con_exonerado_inafecto() -> dict:
    """Factura con líneas gravadas, exoneradas e inafectas."""
    p = _payload_factura_base()
    p["totales"] = {
        "subtotal": 200.00,
        "base_gravada": 100.00,
        "igv": 18.00,
        "exonerado": 50.00,
        "inafecto": 30.00,
        "redondeo": 0.0,
        "total": 200.00,  # 100 + 18 + 50 + 30 + 2 redondeo ≈ 198
    }
    p["items"] = [
        {
            "descripcion": "PAN FRANCES (gravado)",
            "cantidad": 50,
            "unidad_sunat": "NIU",
            "precio_unitario": 2.36,
            "importe": 118.00,
            "tipo_afectacion": AFECTACION_GRAVADO,
            "base_gravada": 100.00,
            "igv": 18.00,
            "codigo": "PAN001",
            "impuesto_id": 1,
        },
        {
            "descripcion": "LIBRO EXONERADO",
            "cantidad": 1,
            "unidad_sunat": "NIU",
            "precio_unitario": 50.00,
            "importe": 50.00,
            "tipo_afectacion": AFECTACION_EXONERADO,
            "base_gravada": 0.0,
            "igv": 0.0,
            "codigo": "LIB001",
            "impuesto_id": 2,
        },
        {
            "descripcion": "SERVICIO INAFECTO",
            "cantidad": 1,
            "unidad_sunat": "NIU",
            "precio_unitario": 30.00,
            "importe": 30.00,
            "tipo_afectacion": AFECTACION_INAFECTO,
            "base_gravada": 0.0,
            "igv": 0.0,
            "codigo": "SER001",
            "impuesto_id": 3,
        },
    ]
    return p


# ----------------------------------------------------------------------
# Helpers de aserción sobre el XML parseado
# ----------------------------------------------------------------------


NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _parsear(xml_bytes: bytes) -> etree._Element:
    return etree.fromstring(xml_bytes)


def _xpath_text(root: etree._Element, path: str) -> str:
    el = root.find(path, namespaces=NS)
    return el.text if el is not None else ""


def _xpath_attr(root: etree._Element, path: str, attr: str) -> str:
    el = root.find(path, namespaces=NS)
    return el.get(attr) if el is not None else ""


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_factura_genera_xml_valido() -> None:
    xml = generar_xml_comprobante(_payload_factura_base())
    root = _parsear(xml)

    # Raíz y namespaces
    assert root.tag.endswith("Invoice")
    assert _xpath_text(root, "cbc:UBLVersionID") == "2.1"
    assert _xpath_text(root, "cbc:CustomizationID") == "2.0"

    # Tipo de comprobante = 01 (Factura)
    assert _xpath_text(root, "cbc:InvoiceTypeCode") == TIPO_COMPROBANTE_FACTURA
    assert _xpath_attr(root, "cbc:InvoiceTypeCode", "listAgencyName") == "PE:SUNAT"
    assert _xpath_attr(root, "cbc:InvoiceTypeCode", "listID") == "0101"

    # ID = serie-correlativo (8 dígitos)
    assert _xpath_text(root, "cbc:ID") == "F001-00000001"

    # Moneda
    assert _xpath_text(root, "cbc:DocumentCurrencyCode") == "PEN"

    # Emisor: UBL 2.1 (PartyIdentification + RegistrationAddress), sin residuos UBL 2.0
    assert _xpath_text(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name") == "LA CASA DEL PAN S.A.C."
    assert _xpath_text(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID") == "20603122144"
    assert _xpath_attr(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", "schemeID") == "6"
    sup_doc = root.find(
        "cac:AccountingSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID",
        namespaces=NS,
    )
    assert sup_doc is not None and sup_doc.text == "20603122144"
    assert sup_doc.get("schemeID") == "6"
    assert sup_doc.get("schemeAgencyName") == "PE:SUNAT"
    assert _xpath_text(
        root,
        "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cbc:ID",
    ) == "150107"
    assert _xpath_text(
        root,
        "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cbc:AddressTypeCode",
    ) == "0000"
    assert _xpath_text(
        root,
        "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cac:RegistrationAddress/cac:Country/cbc:IdentificationCode",
    ) == "PE"
    # No deben existir residuos del estilo UBL 2.0 ni PostalAddress suelto
    assert root.find("cac:AccountingSupplierParty/cbc:CustomerAssignedAccountID", namespaces=NS) is None
    assert root.find("cac:AccountingSupplierParty/cbc:AdditionalAccountID", namespaces=NS) is None
    assert root.find("cac:AccountingSupplierParty/cac:Party/cac:PostalAddress", namespaces=NS) is None

    # Receptor (RUC)
    assert _xpath_text(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name") == "PANADERIA LOS ANDES S.A.C."
    assert _xpath_text(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID") == "20123456789"
    cust_doc = root.find(
        "cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID",
        namespaces=NS,
    )
    assert cust_doc is not None and cust_doc.text == "20123456789"
    assert cust_doc.get("schemeID") == "6"

    # Forma de pago (solo facturas)
    assert _xpath_text(root, "cac:PaymentTerms/cbc:ID") == "FormaPago"
    assert _xpath_text(root, "cac:PaymentTerms/cbc:PaymentMeansID") == "Contado"

    # Sin xsi:schemaLocation (UBL 2.1 SUNAT no lo usa)
    assert not any(k.endswith("}schemaLocation") for k in root.attrib)

    # TaxTotal gravado
    tax_amount = _xpath_text(root, "cac:TaxTotal/cbc:TaxAmount")
    assert Decimal(tax_amount) == Decimal("18.00")

    # LegalMonetaryTotal
    assert Decimal(_xpath_text(root, "cac:LegalMonetaryTotal/cbc:PayableAmount")) == Decimal("118.00")

    # Una línea
    lines = root.findall("cac:InvoiceLine", namespaces=NS)
    assert len(lines) == 1
    # Unidad Catálogo 03
    assert _xpath_attr(lines[0], "cbc:InvoicedQuantity", "unitCode") == "NIU"
    assert _xpath_attr(lines[0], "cbc:InvoicedQuantity", "unitCodeListID") == "03"
    # Precio con IGV (precio unitario con impuestos)
    assert Decimal(_xpath_text(lines[0], "cac:PricingReference/cac:AlternativeConditionPrice/cbc:PriceAmount")) == Decimal("2.36")
    # Valor unitario SIN IGV = base_gravada / cantidad (regla SUNAT 3271)
    valor_unit = Decimal(_xpath_text(lines[0], "cac:Price/cbc:PriceAmount"))
    assert valor_unit == Decimal("2.00")
    assert valor_unit * Decimal("50") == Decimal(_xpath_text(lines[0], "cbc:LineExtensionAmount"))

    # Hash reproducible
    hash1 = hashlib.sha256(xml).hexdigest()
    xml2 = generar_xml_comprobante(_payload_factura_base())
    hash2 = hashlib.sha256(xml2).hexdigest()
    assert hash1 == hash2


def test_boleta_genera_xml_valido() -> None:
    xml = generar_xml_comprobante(_payload_boleta_base())
    root = _parsear(xml)

    assert _xpath_text(root, "cbc:InvoiceTypeCode") == TIPO_COMPROBANTE_BOLETA
    assert _xpath_text(root, "cbc:ID") == "B001-00000001"

    # Receptor con DNI (tipo_doc = 1) → PartyIdentification/cbc:ID
    cust_id = root.find(
        "cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID",
        namespaces=NS,
    )
    assert cust_id is not None
    assert cust_id.text == "45678912"
    assert cust_id.get("schemeID") == "1"
    assert cust_id.get("schemeAgencyName") == "PE:SUNAT"
    assert root.find("cac:AccountingCustomerParty/cbc:CustomerAssignedAccountID", namespaces=NS) is None

    # SUNAT exige <cbc:RegistrationName> del receptor (regla 2021)
    assert _xpath_text(
        root, "cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName"
    ) == "JUAN PEREZ"

    # No debe tener PartyTaxScheme el receptor (no es RUC)
    pts = root.find("cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme", namespaces=NS)
    assert pts is None

    # La boleta no lleva PaymentTerms (solo facturas)
    assert root.find("cac:PaymentTerms", namespaces=NS) is None


def test_payload_con_exonerado_inafecto_tax_subtotals() -> None:
    """Verifica que se crean TaxSubtotal para gravado, exonerado e inafecto."""
    xml = generar_xml_comprobante(_payload_con_exonerado_inafecto())
    root = _parsear(xml)

    tax_total = root.find("cac:TaxTotal", namespaces=NS)
    assert tax_total is not None

    subtotals = tax_total.findall("cac:TaxSubtotal", namespaces=NS)
    # Debe haber 3: gravado (10), exonerado (20), inafecto (30)
    assert len(subtotals) == 3

    codes = [
        _xpath_text(st, "cac:TaxCategory/cbc:TaxExemptionReasonCode") for st in subtotals
    ]
    assert AFECTACION_GRAVADO in codes
    assert AFECTACION_EXONERADO in codes
    assert AFECTACION_INAFECTO in codes

    # El TaxAmount total = IGV solo del gravado
    assert Decimal(_xpath_text(root, "cac:TaxTotal/cbc:TaxAmount")) == Decimal("18.00")

    # LineExtensionAmount = base_gravada + exonerado + inafecto = 100 + 50 + 30 = 180
    line_ext = Decimal(_xpath_text(root, "cac:LegalMonetaryTotal/cbc:LineExtensionAmount"))
    assert line_ext == Decimal("180.00")


def test_boleta_sin_documento_tipo_cero() -> None:
    """Boleta a consumidor final sin DNI → tipo_doc = '0', num_doc = ''."""
    p = _payload_boleta_base()
    p["receptor"] = {"tipo_doc": "0", "num_doc": "", "nombre": "CONSUMIDOR FINAL"}

    xml = generar_xml_comprobante(p)
    root = _parsear(xml)

    cust_id = root.find(
        "cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID",
        namespaces=NS,
    )
    assert cust_id is not None
    assert cust_id.get("schemeID") == "0"
    assert cust_id.text == "0"  # el generador pone "0" si está vacío


def test_items_llevan_unidad_sunat_y_afectacion_por_linea() -> None:
    """Cada InvoiceLine lleva su unidad (Cat. 03) y su tipo de afectación (Cat. 07)."""
    p = _payload_con_exonerado_inafecto()
    xml = generar_xml_comprobante(p)
    root = _parsear(xml)

    lines = root.findall("cac:InvoiceLine", namespaces=NS)
    assert len(lines) == 3

    # Línea 1: gravado
    l1 = lines[0]
    assert _xpath_attr(l1, "cbc:InvoicedQuantity", "unitCode") == "NIU"
    assert _xpath_text(l1, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode") == AFECTACION_GRAVADO
    assert _xpath_text(l1, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent") == "18.00"

    # Línea 2: exonerado
    l2 = lines[1]
    assert _xpath_text(l2, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode") == AFECTACION_EXONERADO
    assert _xpath_text(l2, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent") == "0.00"

    # Línea 3: inafecto
    l3 = lines[2]
    assert _xpath_text(l3, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode") == AFECTACION_INAFECTO
    assert _xpath_text(l3, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent") == "0.00"


def test_guardar_xml_comprobante_crea_archivo_y_hash() -> None:
    """guarda el XML en disco y devuelve ruta + hash SHA-256."""
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        ruta, hash_cpe = guardar_xml_comprobante(_payload_factura_base(), carpeta)

        assert Path(ruta).exists()
        assert Path(ruta).name == "20603122144-01-F001-00000001.xml"
        assert len(hash_cpe) == 64  # SHA-256 hex

        # El hash coincide con el contenido
        contenido = Path(ruta).read_bytes()
        assert hashlib.sha256(contenido).hexdigest() == hash_cpe


def test_validar_payload_rechaza_incompleto() -> None:
    with pytest.raises(ValueError, match="falta sección 'emisor'"):
        _validar_payload({"receptor": {}, "comprobante": {}, "totales": {}, "items": []})

    with pytest.raises(ValueError, match="tipo de comprobante desconocido"):
        _validar_payload(
            {
                "emisor": {"ruc": "1", "razon_social": "X"},
                "receptor": {},
                "comprobante": {"tipo": "OTRO", "serie": "X", "correlativo": 1},
                "totales": {},
                "items": [{}],
            }
        )

    with pytest.raises(ValueError, match="al menos un ítem"):
        _validar_payload(
            {
                "emisor": {"ruc": "1", "razon_social": "X"},
                "receptor": {},
                "comprobante": {"tipo": "FACTURA", "serie": "F001", "correlativo": 1},
                "totales": {},
                "items": [],
            }
        )


def test_xml_serializable_y_bien_formado() -> None:
    """El XML generado es well-formed y se puede parsear sin errores."""
    for payload in (_payload_factura_base(), _payload_boleta_base(), _payload_con_exonerado_inafecto()):
        xml = generar_xml_comprobante(payload)
        # No debe lanzar
        etree.fromstring(xml)

        # Declaración XML presente
        assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")


def test_generar_xml_desde_fila_sin_payload() -> None:
    """generar_y_guardar_xml reconstruye payload mínimo desde columnas planas."""
    from src.infraestructura.ubl_generator import generar_y_guardar_xml

    from tempfile import TemporaryDirectory

    fila = {
        "tipo_comprobante": "BOLETA",
        "serie": "B001",
        "correlativo": 5,
        "fecha_emision": "2026-10-18",
        "receptor_tipo_doc": "1",
        "receptor_num_doc": "45678912",
        "receptor_nombre": "JUAN PEREZ",
        "moneda": "PEN",
        "monto_total": 118.00,
        "monto_igv": 18.00,
        "payload": {
            "items": [
                {
                    "descripcion": "PRODUCTO RECONSTRUIDO",
                    "cantidad": 1,
                    "unidad_sunat": "NIU",
                    "precio_unitario": 118.00,
                    "importe": 118.00,
                    "tipo_afectacion": "10",
                    "base_gravada": 100.00,
                    "igv": 18.00,
                    "codigo": "REC001",
                    "impuesto_id": 1,
                }
            ]
        },  # payload con items mínimo
    }

    with TemporaryDirectory() as tmp:
        ruta, hash_cpe = generar_y_guardar_xml(fila, Path(tmp))
        assert Path(ruta).exists()
        assert Path(ruta).name == "20603122144-03-B001-00000005.xml"

        # Verificar que el XML reconstruido es válido
        xml = Path(ruta).read_bytes()
        root = etree.fromstring(xml)
        assert _xpath_text(root, "cbc:InvoiceTypeCode") == "03"
        assert _xpath_text(root, "cbc:ID") == "B001-00000005"


def test_redondeo_en_totales_no_afecta_tax_total() -> None:
    """El redondeo de efectivo va en LegalMonetaryTotal, NO en TaxTotal."""
    p = _payload_factura_base()
    p["totales"]["redondeo"] = 0.10
    p["totales"]["total"] = 118.10

    xml = generar_xml_comprobante(p)
    root = _parsear(xml)

    # TaxAmount sigue siendo solo IGV (18.00), no incluye redondeo
    assert Decimal(_xpath_text(root, "cac:TaxTotal/cbc:TaxAmount")) == Decimal("18.00")

    # PayableAmount incluye el redondeo
    assert Decimal(_xpath_text(root, "cac:LegalMonetaryTotal/cbc:PayableAmount")) == Decimal("118.10")