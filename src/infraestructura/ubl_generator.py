"""Generador de XML UBL 2.1 para SUNAT — capa de INFRAESTRUCTURA.

Convierte el *payload* autocontenido (guardado en ``comprobantes_electronicos.payload_json``)
en un XML válido según la **Especificación Técnica SUNAT**:
- **CustomizationID**: ``2.0``
- **UBLVersionID**: ``2.1``
- **InvoiceTypeCode** (Catálogo N.º 01): ``01`` = Factura, ``03`` = Boleta
- **DocumentCurrencyCode**: ``PEN``
- Catálogo N.º 03 (unidad de medida) en cada línea
- Catálogo N.º 06 (tipo de documento) en el receptor
- Catálogo N.º 05 (tributo): ``1000`` = IGV
- Catálogo N.º 07 (afectación): ``10`` = gravado, ``20`` = exonerado, ``30`` = inafecto

El generador **no** depende de ``ventas`` ni ``venta_detalle``; todo sale del *payload*.
Así el XML se puede regenerar **aun después del Cierre del Día**.

No contiene reglas de negocio ni acceso a BD; solo transforma el dict → XML.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from lxml import etree

# ----------------------------------------------------------------------
# Constantes y catálogos SUNAT (subconjunto usado)
# ----------------------------------------------------------------------

#: Catálogo N.º 01 — Tipo de comprobante
TIPO_COMPROBANTE_FACTURA = "01"
TIPO_COMPROBANTE_BOLETA = "03"

#: Catálogo N.º 05 — Tipo de tributo
CODIGO_IGV = "1000"
NOMBRE_IGV = "IGV"
CODIGO_TRIBUTO_INTERNACIONAL = "VAT"

#: Catálogo N.º 07 — Tipo de afectación del IGV
AFECTACION_GRAVADO = "10"      # Gravado - Operación Onerosa
AFECTACION_EXONERADO = "20"    # Exonerado - Operación Onerosa
AFECTACION_INAFECTO = "30"     # Inafecto - Operación Onerosa

#: Formato de cantidad (Catálogo N.º 03 ya viene en el payload)
UNIDAD_POR_DEFECTO = "NIU"  # unidad (bienes)

#: Precisión monetaria SUNAT (2 decimales)
Q2 = Decimal("0.01")


def _d2(v: float | Decimal | str | None) -> Decimal:
    """Quantize a 2 decimales (ROUND_HALF_UP)."""
    if v is None or v == "":
        return Decimal("0.00")
    return Decimal(str(v)).quantize(Q2, rounding=ROUND_HALF_UP)


def _d4(v: float | Decimal | str | None) -> Decimal:
    """Quantize a 4 decimales (precios unitarios)."""
    if v is None or v == "":
        return Decimal("0.0000")
    return Decimal(str(v)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


#: Precisión para el valor unitario sin IGV (SUNAT admite hasta 10 decimales)
Q10 = Decimal("0.0000000001")


def _valor_unitario(
    base: float | Decimal | str | None,
    cantidad: float | Decimal | str | None,
) -> Decimal:
    """Valor unitario SIN IGV = base_gravada / cantidad.

    SUNAT valida que `cac:Price/cbc:PriceAmount` x cantidad coincida con
    `LineExtensionAmount` (regla 3271), por eso se conserva alta precisión.
    """
    b = _d2(base)
    try:
        q = Decimal(str(cantidad))
    except Exception:  # noqa: BLE001
        q = Decimal("0")
    if q <= 0:
        return b
    return (b / q).quantize(Q10, rounding=ROUND_HALF_UP)


def _fmt_precio(v: Decimal) -> str:
    """Formatea un valor unitario con mínimo 2 decimales y sin ceros sobrantes."""
    s = format(v.normalize(), "f")
    if "." not in s:
        return s + ".00"
    entero, dec = s.split(".")
    if len(dec) < 2:
        return f"{entero}.{dec.ljust(2, '0')}"
    return s


@dataclass(frozen=True)
class ResultadoUBL:
    """Resultado de la generación del XML."""
    xml_bytes: bytes
    nombre_archivo: str
    hash_cpe: str  # digest SHA-256 del XML (para firmar / CDR)


# ----------------------------------------------------------------------
# Generador principal
# ----------------------------------------------------------------------


def generar_xml_comprobante(payload: dict[str, Any]) -> bytes:
    """
    Genera el XML UBL 2.1 (CustomizationID 2.0) a partir del *payload*.

    Args:
        payload: dict con la estructura guardada en ``payload_json``:
            - emisor: {razon_social, nombre_comercial, ruc, direccion, ubigeo, telefono}
            - receptor: {tipo_doc, num_doc, nombre}
            - comprobante: {tipo, serie, correlativo, fecha_emision, hora_emision, moneda}
            - totales: {subtotal, base_gravada, igv, exonerado, inafecto, redondeo, total}
            - items: lista de {descripcion, cantidad, unidad_sunat, precio_unitario,
              importe, tipo_afectacion, base_gravada, igv, impuesto_id, codigo}

    Returns:
        bytes del XML codificado en UTF-8 (con declaración XML).

    Raises:
        ValueError: si faltan datos obligatorios o el tipo de comprobante no es 01/03.
    """
    _validar_payload(payload)

    emisor = payload["emisor"]
    receptor = payload["receptor"]
    comp = payload["comprobante"]
    totales = payload["totales"]
    items = payload["items"]

    tipo = comp["tipo"]  # "BOLETA" o "FACTURA"
    tipo_codigo = TIPO_COMPROBANTE_FACTURA if tipo == "FACTURA" else TIPO_COMPROBANTE_BOLETA
    serie = comp["serie"]
    correlativo = int(comp["correlativo"])
    fecha = comp["fecha_emision"]  # YYYY-MM-DD
    moneda = comp.get("moneda", "PEN")

    # Nombre del archivo SUNAT oficial: {RUC}-{TIPO_DOC}-{SERIE}-{CORRELATIVO_8_DIGITOS}.xml
    # TIPO_DOC: 01 = Factura, 03 = Boleta (Catálogo N.º 01)
    ruc = payload["emisor"]["ruc"]
    tipo_doc = "01" if comp["tipo"] == "FACTURA" else "03"
    nombre_archivo = f"{ruc}-{tipo_doc}-{serie}-{correlativo:08d}.xml"

    # ============================================================
    # Construcción del árbol XML con lxml (namespaces SUNAT/UBL)
    # ============================================================
    NS = {
        # UBL 2.1
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        # Invoice root namespace (UBL 2.1)
        "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
        # Extensiones (para firma / CDR)
        "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
        # SUNAT / DS
        "ds": "http://www.w3.org/2000/09/xmldsig#",
        "sac": "urn:sunat:names:specification:ubl:peru:schema:xsd:SunatAggregateComponents-1",
        # XSI para schemaLocation
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    }

    # Elemento raíz: Invoice (tanto para factura como para boleta en UBL Perú)
    # Namespace correcto: urn:oasis:names:specification:ubl:schema:xsd:Invoice-2
    root = etree.Element(
        f"{{{NS['inv']}}}Invoice",
        nsmap={
            None: NS["inv"],  # default = Invoice namespace
            "cbc": NS["cbc"],
            "cac": NS["cac"],
            "ext": NS["ext"],
            "ds": NS["ds"],
            "sac": NS["sac"],
        },
    )
    # NOTA: no se agrega xsi:schemaLocation. SUNAT (UBL 2.1) no lo usa y
    # apuntar al XSD 2.0 dentro de un documento 2.1 confunde al validador.

    # ---- 0. ext:UBLExtensions (contenedor para la firma digital) ----
    # SUNAT requiere este contenedor para alojar la firma XMLDSig dentro de
    # <ext:ExtensionContent>. La firma real (XMLDSig) se insertará aquí
    # al firmar el documento.
    ubl_extensions = etree.SubElement(root, f"{{{NS['ext']}}}UBLExtensions")
    ubl_extension = etree.SubElement(ubl_extensions, f"{{{NS['ext']}}}UBLExtension")
    extension_content = etree.SubElement(ubl_extension, f"{{{NS['ext']}}}ExtensionContent")
    # La firma XMLDSig (<ds:Signature>) se insertará aquí por el firmante.

    # Helper para crear elementos cbc con texto
    def cbc(tag: str, text: str, **attrs) -> etree._Element:
        el = etree.SubElement(root, f"{{{NS['cbc']}}}{tag}")
        el.text = text
        for k, v in attrs.items():
            el.set(k, v)
        return el

    # Helper para crear elementos cac (contenedores)
    def cac(tag: str) -> etree._Element:
        return etree.SubElement(root, f"{{{NS['cac']}}}{tag}")

    # ---- 1. UBLVersionID / CustomizationID ----
    cbc("UBLVersionID", "2.1")
    cbc("CustomizationID", "2.0")

    # ---- 2. ID del comprobante (serie-correlativo, 8 dígitos) ----
    cbc("ID", f"{serie}-{correlativo:08d}")

    # ---- 3. IssueDate / IssueTime (DEBEN ir ANTES de InvoiceTypeCode según UBL 2.1) ----
    cbc("IssueDate", fecha)
    cbc("IssueTime", comp.get("hora_emision", "00:00:00"))

    # ---- 3b. InvoiceTypeCode (Catálogo 01) ----
    cbc(
        "InvoiceTypeCode",
        tipo_codigo,
        listID="0101",  # Operación interna (venta interna)
        listAgencyName="PE:SUNAT",
        listName="Tipo de Comprobante",
        listURI="urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo01",
    )

    # ---- 4. Note (opcional; va entre InvoiceTypeCode y DocumentCurrencyCode) ----
    _nota = comp.get("note") or comp.get("observacion")
    if _nota:
        cbc("Note", str(_nota))

    # ---- 5. DocumentCurrencyCode (PEN) ----
    cbc("DocumentCurrencyCode", moneda, listID="ISO 4217 Alpha")

    # ---- 5. Signature (firma del emisor) ----
    # Obligatorio para SUNAT: <cac:Signature> antes de AccountingSupplierParty.
    # Contiene la información del firmante; la firma XMLDSig real
    # (<ds:Signature>) se inserta en <ext:ExtensionContent> por el firmante.
    signature = cac("Signature")
    sig_id = etree.SubElement(signature, f"{{{NS['cbc']}}}ID")
    sig_id.text = f"Sign{emisor['ruc']}"
    signatory = etree.SubElement(signature, f"{{{NS['cac']}}}SignatoryParty")
    party_id = etree.SubElement(signatory, f"{{{NS['cac']}}}PartyIdentification")
    cbc_id = etree.SubElement(party_id, f"{{{NS['cbc']}}}ID")
    cbc_id.text = emisor["ruc"]
    cbc_id.set("schemeID", "6")  # RUC
    party_name = etree.SubElement(signatory, f"{{{NS['cac']}}}PartyName")
    cbc_name = etree.SubElement(party_name, f"{{{NS['cbc']}}}Name")
    cbc_name.text = emisor["razon_social"]
    # DigitalSignatureAttachment (placeholder)
    ds_att = etree.SubElement(signature, f"{{{NS['cac']}}}DigitalSignatureAttachment")
    ext_content = etree.SubElement(ds_att, f"{{{NS['cac']}}}ExternalReference")
    mime = etree.SubElement(ext_content, f"{{{NS['cbc']}}}MimeCode")
    mime.text = "application/xml"

    # ---- 6. AccountingSupplierParty (Emisor) ----
    # UBL 2.1 + perfil SUNAT: el único hijo directo es <cac:Party>.
    # El RUC va en <cac:PartyIdentification>/<cbc:ID> y la dirección fiscal
    # dentro de <cac:PartyLegalEntity><cac:RegistrationAddress>.
    supplier = cac("AccountingSupplierParty")
    party = etree.SubElement(supplier, f"{{{NS['cac']}}}Party")

    # 1) PartyIdentification (RUC del emisor, Catálogo 06 SUNAT)
    party_ident = etree.SubElement(party, f"{{{NS['cac']}}}PartyIdentification")
    party_ident_id = etree.SubElement(party_ident, f"{{{NS['cbc']}}}ID")
    party_ident_id.text = emisor["ruc"]
    party_ident_id.set("schemeID", "6")
    party_ident_id.set("schemeName", "Documento de Identidad")
    party_ident_id.set("schemeAgencyName", "PE:SUNAT")
    party_ident_id.set("schemeURI", "urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo06")

    # 2) PartyName (razón social)
    party_name = etree.SubElement(party, f"{{{NS['cac']}}}PartyName")
    cbc_name = etree.SubElement(party_name, f"{{{NS['cbc']}}}Name")
    cbc_name.text = emisor["razon_social"]

    # 3) PartyTaxScheme (RUC + tributo IGV)
    pts = etree.SubElement(party, f"{{{NS['cac']}}}PartyTaxScheme")
    reg_name = etree.SubElement(pts, f"{{{NS['cbc']}}}RegistrationName")
    reg_name.text = emisor["razon_social"]
    company_id = etree.SubElement(pts, f"{{{NS['cbc']}}}CompanyID")
    company_id.text = emisor["ruc"]
    company_id.set("schemeID", "6")
    tax_scheme = etree.SubElement(pts, f"{{{NS['cac']}}}TaxScheme")
    tax_id = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}ID")
    tax_id.text = CODIGO_IGV
    tax_name = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}Name")
    tax_name.text = NOMBRE_IGV
    tax_type = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}TaxTypeCode")
    tax_type.text = CODIGO_TRIBUTO_INTERNACIONAL

    # 4) PartyLegalEntity (razón social + dirección fiscal registrada)
    legal_entity = etree.SubElement(party, f"{{{NS['cac']}}}PartyLegalEntity")
    legal_name = etree.SubElement(legal_entity, f"{{{NS['cbc']}}}RegistrationName")
    legal_name.text = emisor["razon_social"]
    reg_addr = etree.SubElement(legal_entity, f"{{{NS['cac']}}}RegistrationAddress")
    if emisor.get("ubigeo"):
        reg_addr_id = etree.SubElement(reg_addr, f"{{{NS['cbc']}}}ID")
        reg_addr_id.text = str(emisor["ubigeo"])
    addr_type = etree.SubElement(reg_addr, f"{{{NS['cbc']}}}AddressTypeCode")
    addr_type.text = "0000"
    if emisor.get("direccion"):
        addr_line = etree.SubElement(reg_addr, f"{{{NS['cac']}}}AddressLine")
        addr_line_txt = etree.SubElement(addr_line, f"{{{NS['cbc']}}}Line")
        addr_line_txt.text = emisor["direccion"]
    addr_country = etree.SubElement(reg_addr, f"{{{NS['cac']}}}Country")
    addr_country_code = etree.SubElement(addr_country, f"{{{NS['cbc']}}}IdentificationCode")
    addr_country_code.text = "PE"

    # ---- 8. AccountingCustomerParty (Receptor / Cliente) ----
    # UBL 2.1: el único hijo directo es <cac:Party>; el documento del receptor
    # va en <cac:PartyIdentification>/<cbc:ID> (schemeID = Catálogo 06).
    customer = cac("AccountingCustomerParty")
    party = etree.SubElement(customer, f"{{{NS['cac']}}}Party")

    # PartyIdentification (DNI=1, RUC=6, sin documento=0)
    cust_doc = receptor.get("num_doc", "") or "0"
    cust_tipo = receptor.get("tipo_doc", "0") or "0"
    cust_ident = etree.SubElement(party, f"{{{NS['cac']}}}PartyIdentification")
    cust_ident_id = etree.SubElement(cust_ident, f"{{{NS['cbc']}}}ID")
    cust_ident_id.text = cust_doc
    cust_ident_id.set("schemeID", cust_tipo)
    cust_ident_id.set("schemeName", "Documento de Identidad")
    cust_ident_id.set("schemeAgencyName", "PE:SUNAT")
    cust_ident_id.set("schemeURI", "urn:pe:gob:sunat:cpe:see:gem:catalogos:catalogo06")

    party_name = etree.SubElement(party, f"{{{NS['cac']}}}PartyName")
    cbc_name = etree.SubElement(party_name, f"{{{NS['cbc']}}}Name")
    cbc_name.text = receptor.get("nombre", "CONSUMIDOR FINAL") or "CONSUMIDOR FINAL"
    # PartyTaxScheme del receptor (solo si tiene RUC)
    if cust_tipo == "6" and cust_doc:
        pts = etree.SubElement(party, f"{{{NS['cac']}}}PartyTaxScheme")
        reg_name = etree.SubElement(pts, f"{{{NS['cbc']}}}RegistrationName")
        reg_name.text = receptor.get("nombre", "")
        company_id = etree.SubElement(pts, f"{{{NS['cbc']}}}CompanyID")
        company_id.text = cust_doc
        company_id.set("schemeID", "6")
        tax_scheme = etree.SubElement(pts, f"{{{NS['cac']}}}TaxScheme")
        tax_id = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}ID")
        tax_id.text = CODIGO_IGV
        tax_name = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}Name")
        tax_name.text = NOMBRE_IGV
        tax_type = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}TaxTypeCode")
        tax_type.text = CODIGO_TRIBUTO_INTERNACIONAL

    # PartyLegalEntity del receptor: SUNAT EXIGE <cbc:RegistrationName> del
    # receptor (regla 2021). Es el nombre del cliente.
    cust_legal = etree.SubElement(party, f"{{{NS['cac']}}}PartyLegalEntity")
    cust_legal_name = etree.SubElement(cust_legal, f"{{{NS['cbc']}}}RegistrationName")
    cust_legal_name.text = cbc_name.text

    # ---- 8b. PaymentTerms (forma de pago) — solo Facturas ----
    # SUNAT exige declarar la forma de pago en las facturas.
    if comp["tipo"] == "FACTURA":
        payment_terms = cac("PaymentTerms")
        pt_id = etree.SubElement(payment_terms, f"{{{NS['cbc']}}}ID")
        pt_id.text = "FormaPago"
        pt_means = etree.SubElement(payment_terms, f"{{{NS['cbc']}}}PaymentMeansID")
        pt_means.text = "Contado"

    # ---- 9. TaxTotal (Totales de tributos) ----
    # Orden UBL 2.1: cbc:TaxAmount -> cac:TaxSubtotal*
    tax_total = cac("TaxTotal")
    cbc_tax_amount = etree.SubElement(tax_total, f"{{{NS['cbc']}}}TaxAmount")
    cbc_tax_amount.text = str(_d2(totales.get("igv", 0)))
    cbc_tax_amount.set("currencyID", moneda)

    # Subtotal gravado (si hay base_gravada > 0)
    base_grav = _d2(totales.get("base_gravada", 0))
    if base_grav > 0:
        tax_sub = etree.SubElement(tax_total, f"{{{NS['cac']}}}TaxSubtotal")
        # Orden UBL 2.1: TaxableAmount -> TaxAmount -> TaxCategory
        cbc_base = etree.SubElement(tax_sub, f"{{{NS['cbc']}}}TaxableAmount")
        cbc_base.text = str(base_grav)
        cbc_base.set("currencyID", moneda)
        cbc_amt = etree.SubElement(tax_sub, f"{{{NS['cbc']}}}TaxAmount")
        cbc_amt.text = str(_d2(totales.get("igv", 0)))
        cbc_amt.set("currencyID", moneda)
        tax_cat = etree.SubElement(tax_sub, f"{{{NS['cac']}}}TaxCategory")
        # Orden TaxCategory: Percent -> TaxExemptionReasonCode -> TaxScheme
        cbc_percent = etree.SubElement(tax_cat, f"{{{NS['cbc']}}}Percent")
        cbc_percent.text = "18.00"
        cbc_cat = etree.SubElement(tax_cat, f"{{{NS['cbc']}}}TaxExemptionReasonCode")
        cbc_cat.text = AFECTACION_GRAVADO
        tax_scheme = etree.SubElement(tax_cat, f"{{{NS['cac']}}}TaxScheme")
        tax_id = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}ID")
        tax_id.text = CODIGO_IGV
        tax_name = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}Name")
        tax_name.text = NOMBRE_IGV
        tax_type = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}TaxTypeCode")
        tax_type.text = CODIGO_TRIBUTO_INTERNACIONAL

    # Exonerado (si hay monto > 0)
    exo = _d2(totales.get("exonerado", 0))
    if exo > 0:
        tax_sub = etree.SubElement(tax_total, f"{{{NS['cac']}}}TaxSubtotal")
        cbc_base = etree.SubElement(tax_sub, f"{{{NS['cbc']}}}TaxableAmount")
        cbc_base.text = str(exo)
        cbc_base.set("currencyID", moneda)
        cbc_amt = etree.SubElement(tax_sub, f"{{{NS['cbc']}}}TaxAmount")
        cbc_amt.text = "0.00"
        cbc_amt.set("currencyID", moneda)
        tax_cat = etree.SubElement(tax_sub, f"{{{NS['cac']}}}TaxCategory")
        cbc_percent = etree.SubElement(tax_cat, f"{{{NS['cbc']}}}Percent")
        cbc_percent.text = "0.00"
        cbc_cat = etree.SubElement(tax_cat, f"{{{NS['cbc']}}}TaxExemptionReasonCode")
        cbc_cat.text = AFECTACION_EXONERADO
        tax_scheme = etree.SubElement(tax_cat, f"{{{NS['cac']}}}TaxScheme")
        tax_id = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}ID")
        tax_id.text = CODIGO_IGV
        tax_name = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}Name")
        tax_name.text = NOMBRE_IGV
        tax_type = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}TaxTypeCode")
        tax_type.text = CODIGO_TRIBUTO_INTERNACIONAL

    # Inafecto (si hay monto > 0)
    ina = _d2(totales.get("inafecto", 0))
    if ina > 0:
        tax_sub = etree.SubElement(tax_total, f"{{{NS['cac']}}}TaxSubtotal")
        cbc_base = etree.SubElement(tax_sub, f"{{{NS['cbc']}}}TaxableAmount")
        cbc_base.text = str(ina)
        cbc_base.set("currencyID", moneda)
        cbc_amt = etree.SubElement(tax_sub, f"{{{NS['cbc']}}}TaxAmount")
        cbc_amt.text = "0.00"
        cbc_amt.set("currencyID", moneda)
        tax_cat = etree.SubElement(tax_sub, f"{{{NS['cac']}}}TaxCategory")
        cbc_percent = etree.SubElement(tax_cat, f"{{{NS['cbc']}}}Percent")
        cbc_percent.text = "0.00"
        cbc_cat = etree.SubElement(tax_cat, f"{{{NS['cbc']}}}TaxExemptionReasonCode")
        cbc_cat.text = AFECTACION_INAFECTO
        tax_scheme = etree.SubElement(tax_cat, f"{{{NS['cac']}}}TaxScheme")
        tax_id = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}ID")
        tax_id.text = CODIGO_IGV
        tax_name = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}Name")
        tax_name.text = NOMBRE_IGV
        tax_type = etree.SubElement(tax_scheme, f"{{{NS['cbc']}}}TaxTypeCode")
        tax_type.text = CODIGO_TRIBUTO_INTERNACIONAL

    # ---- 10. LegalMonetaryTotal ----
    legal_mon = cac("LegalMonetaryTotal")
    # LineExtensionAmount = suma de bases gravadas + exonerado + inafecto
    line_ext = _d2(totales.get("base_gravada", 0)) + _d2(totales.get("exonerado", 0)) + _d2(totales.get("inafecto", 0))
    cbc_line = etree.SubElement(legal_mon, f"{{{NS['cbc']}}}LineExtensionAmount")
    cbc_line.text = str(line_ext)
    cbc_line.set("currencyID", moneda)
    # TaxInclusiveAmount = total con IGV
    cbc_tax_inc = etree.SubElement(legal_mon, f"{{{NS['cbc']}}}TaxInclusiveAmount")
    cbc_tax_inc.text = str(_d2(totales.get("total", 0)))
    cbc_tax_inc.set("currencyID", moneda)
    # PayableAmount = total a pagar
    cbc_pay = etree.SubElement(legal_mon, f"{{{NS['cbc']}}}PayableAmount")
    cbc_pay.text = str(_d2(totales.get("total", 0)))
    cbc_pay.set("currencyID", moneda)

    # ---- 11. InvoiceLine (ítems) ----
    for idx, item in enumerate(items, start=1):
        inv_line = cac("InvoiceLine")
        cbc_id = etree.SubElement(inv_line, f"{{{NS['cbc']}}}ID")
        cbc_id.text = str(idx)

        # InvoicedQuantity (con unidad Catálogo 03)
        qty = etree.SubElement(inv_line, f"{{{NS['cbc']}}}InvoicedQuantity")
        qty.text = str(_d2(item.get("cantidad", 1)))
        qty.set("unitCode", item.get("unidad_sunat", UNIDAD_POR_DEFECTO))
        qty.set("unitCodeListID", "03")
        qty.set("unitCodeListAgencyName", "PE:SUNAT")

        # LineExtensionAmount = base de la línea (sin IGV)
        line_ext_amt = _d2(item.get("base_gravada", 0))
        cbc_line_amt = etree.SubElement(inv_line, f"{{{NS['cbc']}}}LineExtensionAmount")
        cbc_line_amt.text = str(line_ext_amt)
        cbc_line_amt.set("currencyID", moneda)

        # PricingReference / AlternativeConditionPrice / PriceAmount = precio con IGV
        pricing = etree.SubElement(inv_line, f"{{{NS['cac']}}}PricingReference")
        alt_price = etree.SubElement(pricing, f"{{{NS['cac']}}}AlternativeConditionPrice")
        price_amt = etree.SubElement(alt_price, f"{{{NS['cbc']}}}PriceAmount")
        price_amt.text = str(_d2(item.get("precio_unitario", 0)))
        price_amt.set("currencyID", moneda)
        price_type = etree.SubElement(alt_price, f"{{{NS['cbc']}}}PriceTypeCode")
        price_type.text = "01"  # precio unitario incluido impuestos

        # TaxTotal por línea (orden UBL 2.1)
        line_tax = etree.SubElement(inv_line, f"{{{NS['cac']}}}TaxTotal")
        cbc_line_tax_amt = etree.SubElement(line_tax, f"{{{NS['cbc']}}}TaxAmount")
        cbc_line_tax_amt.text = str(_d2(item.get("igv", 0)))
        cbc_line_tax_amt.set("currencyID", moneda)
        line_tax_sub = etree.SubElement(line_tax, f"{{{NS['cac']}}}TaxSubtotal")
        # Orden: TaxableAmount -> TaxAmount -> TaxCategory
        cbc_line_tax_base = etree.SubElement(line_tax_sub, f"{{{NS['cbc']}}}TaxableAmount")
        cbc_line_tax_base.text = str(_d2(item.get("base_gravada", 0)))
        cbc_line_tax_base.set("currencyID", moneda)
        cbc_line_tax_sub_amt = etree.SubElement(line_tax_sub, f"{{{NS['cbc']}}}TaxAmount")
        cbc_line_tax_sub_amt.text = str(_d2(item.get("igv", 0)))
        cbc_line_tax_sub_amt.set("currencyID", moneda)
        line_tax_cat = etree.SubElement(line_tax_sub, f"{{{NS['cac']}}}TaxCategory")
        # Orden TaxCategory: Percent -> TaxExemptionReasonCode -> TaxScheme
        afect = item.get("tipo_afectacion", AFECTACION_GRAVADO)
        cbc_pct = etree.SubElement(line_tax_cat, f"{{{NS['cbc']}}}Percent")
        cbc_pct.text = "18.00" if afect == AFECTACION_GRAVADO else "0.00"
        cbc_line_cat = etree.SubElement(line_tax_cat, f"{{{NS['cbc']}}}TaxExemptionReasonCode")
        cbc_line_cat.text = afect
        line_tax_scheme = etree.SubElement(line_tax_cat, f"{{{NS['cac']}}}TaxScheme")
        tax_id = etree.SubElement(line_tax_scheme, f"{{{NS['cbc']}}}ID")
        tax_id.text = CODIGO_IGV
        tax_name = etree.SubElement(line_tax_scheme, f"{{{NS['cbc']}}}Name")
        tax_name.text = NOMBRE_IGV
        tax_type = etree.SubElement(line_tax_scheme, f"{{{NS['cbc']}}}TaxTypeCode")
        tax_type.text = CODIGO_TRIBUTO_INTERNACIONAL

        # Item
        item_el = etree.SubElement(inv_line, f"{{{NS['cac']}}}Item")
        desc = etree.SubElement(item_el, f"{{{NS['cbc']}}}Description")
        desc.text = item.get("descripcion", "")
        # SellersItemIdentification = código interno / barra
        if item.get("codigo"):
            sid = etree.SubElement(item_el, f"{{{NS['cac']}}}SellersItemIdentification")
            sid_id = etree.SubElement(sid, f"{{{NS['cbc']}}}ID")
            sid_id.text = item["codigo"]

        # Price: valor unitario SIN IGV = base_gravada / cantidad (regla 3271)
        price_el = etree.SubElement(inv_line, f"{{{NS['cac']}}}Price")
        price_amt2 = etree.SubElement(price_el, f"{{{NS['cbc']}}}PriceAmount")
        price_amt2.text = _fmt_precio(
            _valor_unitario(item.get("base_gravada", 0), item.get("cantidad", 1))
        )
        price_amt2.set("currencyID", moneda)

    # Serializar a bytes (UTF-8, declaración XML)
    xml_bytes = etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )
    return xml_bytes


def _validar_payload(payload: dict[str, Any]) -> None:
    """Valida que el payload tenga la estructura mínima requerida."""
    obligatorios = ("emisor", "receptor", "comprobante", "totales", "items")
    for k in obligatorios:
        if k not in payload:
            raise ValueError(f"payload inválido: falta sección '{k}'")

    comp = payload["comprobante"]
    if comp.get("tipo") not in ("FACTURA", "BOLETA"):
        raise ValueError(f"tipo de comprobante desconocido: {comp.get('tipo')}")
    if not comp.get("serie") or not comp.get("correlativo"):
        raise ValueError("falta serie o correlativo en el comprobante")
    if not payload["emisor"].get("ruc") or not payload["emisor"].get("razon_social"):
        raise ValueError("emisor incompleto (falta RUC o razón social)")
    if not payload["items"]:
        raise ValueError("el comprobante debe tener al menos un ítem")


def guardar_xml_comprobante(
    payload: dict[str, Any],
    carpeta_xml: Path | str,
) -> tuple[str, str]:
    """
    Genera el XML y lo guarda en disco.

    Args:
        payload: diccionario del *payload* (igual que ``generar_xml_comprobante``).
        carpeta_xml: carpeta destino (p.ej. ``Path('xml')``).

    Returns:
        Tupla ``(ruta_archivo, hash_cpe)``.
    """
    carpeta = Path(carpeta_xml)
    carpeta.mkdir(parents=True, exist_ok=True)

    xml_bytes = generar_xml_comprobante(payload)

    comp = payload["comprobante"]
    tipo = comp["tipo"]
    serie = comp["serie"]
    correlativo = int(comp["correlativo"])
    ruc = payload["emisor"]["ruc"]
    tipo_doc = "01" if tipo == "FACTURA" else "03"
    nombre = f"{ruc}-{tipo_doc}-{serie}-{correlativo:08d}.xml"
    ruta = carpeta / nombre

    # Hash SHA-256 del XML (CPE = Comprobante de Pago Electrónico)
    import hashlib

    hash_cpe = hashlib.sha256(xml_bytes).hexdigest()

    ruta.write_bytes(xml_bytes)
    return str(ruta), hash_cpe


# ----------------------------------------------------------------------
# Función de alto nivel para usar desde el servicio / job de envío
# ----------------------------------------------------------------------


def generar_y_guardar_xml(
    comprobante: dict[str, Any],
    carpeta_xml: Path | str,
) -> tuple[str, str]:
    """
    Genera y guarda el XML a partir de una fila de ``comprobantes_electronicos``.

    Args:
        comprobante: fila de la tabla (con clave ``payload`` ya decodificada).
        carpeta_xml: carpeta donde guardar el XML.

    Returns:
        ``(ruta_xml, hash_cpe)``.
    """
    payload = comprobante.get("payload") or {}
    # Si el payload está incompleto, reconstruir las secciones faltantes
    # desde las columnas planas de la fila (compatibilidad con bases antiguas).
    requeridas = ("emisor", "receptor", "comprobante", "totales", "items")
    if not all(k in payload for k in requeridas):
        base = _reconstruir_payload_desde_fila(comprobante)
        base.update(payload)  # lo que venga en payload tiene prioridad
        payload = base
    return guardar_xml_comprobante(payload, carpeta_xml)


def _reconstruir_payload_desde_fila(fila: dict[str, Any]) -> dict[str, Any]:
    """
    Reconstruye un *payload* compatible a partir de las columnas planas
    (para bases antiguas que no tengan payload_json).
    """
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
            "tipo_doc": fila.get("receptor_tipo_doc", "0"),
            "num_doc": fila.get("receptor_num_doc", ""),
            "nombre": fila.get("receptor_nombre", ""),
        },
        "comprobante": {
            "tipo": fila.get("tipo_comprobante", "BOLETA"),
            "serie": fila.get("serie", "B001"),
            "correlativo": int(fila.get("correlativo", 0)),
            "fecha_emision": fila.get("fecha_emision", ""),
            "hora_emision": "00:00:00",
            "moneda": fila.get("moneda", "PEN"),
        },
        "totales": {
            "subtotal": float(fila.get("monto_total", 0)) / 1.18,  # aprox
            "base_gravada": float(fila.get("monto_total", 0)) / 1.18,
            "igv": float(fila.get("monto_igv", 0)),
            "exonerado": 0.0,
            "inafecto": 0.0,
            "redondeo": 0.0,
            "total": float(fila.get("monto_total", 0)),
        },
        "items": [],
    }


# ----------------------------------------------------------------------
# Ejecución directa para pruebas rápidas (python -m src.infraestructura.ubl_generator)
# ----------------------------------------------------------------------

if __name__ == "__main__":
    from pathlib import Path

    # Payload de prueba (factura)
    demo = {
        "emisor": {
            "razon_social": "LA CASA DEL PAN S.A.C.",
            "nombre_comercial": "LA CASA DEL PAN",
            "ruc": "20603122144",
            "direccion": "AV. NICOLAS AYLLON NRO. 1502 URB. CHACLACAYO",
            "ubigeo": "150107",
            "telefono": "",
        },
        "receptor": {"tipo_doc": "6", "num_doc": "20123456789", "nombre": "CLIENTE PRUEBA SAC"},
        "comprobante": {
            "tipo": "FACTURA",
            "serie": "F001",
            "correlativo": 1,
            "fecha_emision": "2026-10-18",
            "hora_emision": "12:00:00",
            "moneda": "PEN",
        },
        "totales": {
            "subtotal": 100.00,
            "base_gravada": 84.75,
            "igv": 15.25,
            "exonerado": 0.0,
            "inafecto": 0.0,
            "redondeo": 0.0,
            "total": 100.00,
        },
        "items": [
            {
                "descripcion": "PAN FRANCES",
                "cantidad": 50,
                "unidad_sunat": "NIU",
                "precio_unitario": 2.00,
                "importe": 100.00,
                "tipo_afectacion": "10",
                "base_gravada": 84.75,
                "igv": 15.25,
                "codigo": "PAN001",
                "impuesto_id": 1,
            }
        ],
    }

    xml = generar_xml_comprobante(demo)
    print(xml.decode("utf-8"))
    print("--- OK ---")
    Path("test_ubl.xml").write_bytes(xml)
    print("Guardado en test_ubl.xml")