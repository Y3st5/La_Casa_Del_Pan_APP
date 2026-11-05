"""Cliente SOAP para envío directo al WS Beta de SUNAT — capa de INFRAESTRUCTURA.

Implementa el envío de comprobantes electrónicos al Web Service Beta de SUNAT
(`sendBill`) usando firma XMLDSig (enveloped, RSA-SHA256), compresión ZIP,
codificación Base64 y WS-Security UsernameToken.

Endpoints:
- Beta: https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService
- Producción: https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService

Credenciales Beta (públicas):
- Usuario: 20603122144MODDATOS
- Clave: MODDATOS
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
from lxml import etree
from signxml import XMLSigner, XMLVerifier, methods
from cryptography.hazmat.primitives.serialization import pkcs12

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Configuración SUNAT
# ----------------------------------------------------------------------

# Endpoints oficiales
WS_BETA_URL = "https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService"
WS_PROD_URL = "https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService"

# Credenciales Beta (públicas, solo para pruebas)
BETA_USER = "20603122144MODDATOS"
BETA_PASS = "MODDATOS"

# Namespaces SOAP / WS-Security / SUNAT
# IMPORTANTE: el billService de SUNAT usa SOAP 1.1 (no 1.2). Con SOAP 1.2
# SUNAT responde "env:Receiver - Internal Error".
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
NS_SUNET = "http://service.sunat.gob.pe"
NS_XSD = "http://www.w3.org/2001/XMLSchema"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

# ----------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CDRResultado:
    """Resultado del procesamiento del CDR (Constancia de Recepción)."""
    estado: str  # "ACEPTADO" | "RECHAZADO" | "ERROR"
    respuesta_codigo: str
    respuesta_descripcion: str
    hash_cpe: str
    xml_firmado_bytes: bytes
    cdr_bytes: bytes
    cdr_zip_bytes: bytes
    ruta_xml: Optional[str] = None
    ruta_cdr: Optional[str] = None


@dataclass(frozen=True)
class SunatConfig:
    """Configuración del cliente SUNAT."""
    ruc: str
    cert_path: str
    cert_pass: str
    url: str = WS_BETA_URL
    usuario: str = BETA_USER
    clave: str = BETA_PASS
    timeout: int = 30


# ----------------------------------------------------------------------
# Utilidades: ZIP, Base64, Hash
# ----------------------------------------------------------------------


def calcular_hash_cpe(xml_firmado: bytes) -> str:
    """Hash oficial (CPE) del comprobante **firmado**.

    Es el ``<ds:DigestValue>`` de la firma XMLDSig: base64 del SHA-256 del XML
    canonicalizado. Es el valor que SUNAT imprime en la representación impresa y
    el que se guarda en ``comprobantes_electronicos.hash_cpe``.

    Si el XML no está firmado (no tiene firma) se usa SHA-256 del XML como
    respaldo, para no romper llamadas previas a la firma.
    """
    try:
        root = etree.fromstring(xml_firmado)
        digest = root.find(
            ".//ds:DigestValue",
            namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"},
        )
        if digest is not None and digest.text:
            return digest.text.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("No se pudo leer ds:DigestValue, usando SHA-256: %s", exc)
    return hashlib.sha256(xml_firmado).hexdigest()


def _zip_xml(xml_bytes: bytes, nombre_zip: str) -> bytes:
    """Comprime el XML firmado en un archivo ZIP en memoria.

    SUNAT espera un ZIP con el XML firmado dentro, nombre:
    {RUC}-{TIPO_DOC}-{SERIE}-{CORRELATIVO_8}.xml
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(nombre_zip, xml_bytes)
    return buffer.getvalue()


def _base64_encode(data: bytes) -> str:
    """Codifica en Base64 estándar (sin saltos de línea)."""
    return base64.b64encode(data).decode("ascii")


# ----------------------------------------------------------------------
# Firma XMLDSig (Enveloped, RSA-SHA256)
# ----------------------------------------------------------------------


def firmar_xml(
    xml_bytes: bytes,
    cert_path: str,
    cert_pass: str,
    ruc: str,
) -> bytes:
    """
    Firma un XML UBL 2.1 con XMLDSig (Enveloped Signature, RSA-SHA256).

    Inserta la firma <ds:Signature> dentro de <ext:ExtensionContent>
    que ya existe en el XML (generado por ubl_generator).

    Args:
        xml_bytes: XML UBL 2.1 sin firmar (con <ext:ExtensionContent> vacío).
        cert_path: Ruta al archivo .pfx (certificado + clave privada).
        cert_pass: Contraseña del .pfx.
        ruc: RUC del emisor (para el Id de la firma).

    Returns:
        XML firmado (bytes UTF-8).
    """
    # Cargar certificado y clave privada desde .pfx
    from cryptography.hazmat.primitives.serialization import pkcs12

    with open(cert_path, "rb") as f:
        pfx_data = f.read()

    private_key, cert, additional_certs = pkcs12.load_key_and_certificates(
        pfx_data, cert_pass.encode("utf-8")
    )

    # Parsear XML
    doc = etree.fromstring(xml_bytes)

    # Localizar <ext:ExtensionContent>
    ns = {
        "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
        "ds": "http://www.w3.org/2000/09/xmldsig#",
    }
    ext_content = doc.find(".//ext:ExtensionContent", namespaces=ns)
    if ext_content is None:
        raise ValueError("No se encontró <ext:ExtensionContent> en el XML")

    # Preparar el signer
    # SUNAT requiere canonicalización exclusiva (exc-c14n) sin comentarios
    # http://www.w3.org/2001/10/xml-exc-c14n#
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )

    # Firmar
    # reference_uri=None -> signxml no agrega Reference URI (documento completo)
    signed_doc = signer.sign(
        doc,
        key=private_key,
        cert=[cert],
        reference_uri=None,  # documento completo sin URI
        key_info=None,
    )

    # Localizar ext:ExtensionContent en el documento FIRMADO
    ext_content_signed = signed_doc.find(
        ".//ext:ExtensionContent",
        namespaces={"ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"}
    )
    if ext_content_signed is None:
        raise ValueError("No se encontró <ext:ExtensionContent> en el XML firmado")

    # Localizar la firma y moverla a ext:ExtensionContent
    signature = signed_doc.find(".//ds:Signature", namespaces={"ds": "http://www.w3.org/2000/09/xmldsig#"})
    if signature is not None:
        parent = signature.getparent()
        if parent is not None:
            parent.remove(signature)
        ext_content_signed = signed_doc.find(
            ".//ext:ExtensionContent",
            namespaces={"ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"}
        )
        if ext_content_signed is not None:
            ext_content_signed.insert(0, signature)
        else:
            raise ValueError("No se pudo localizar ext:ExtensionContent en el documento firmado")

    # Serializar
    return etree.tostring(signed_doc, encoding="UTF-8", xml_declaration=True, pretty_print=True)


# ----------------------------------------------------------------------
# SOAP Client para sendBill
# ----------------------------------------------------------------------


def _construir_soap_sendbill(
    zip_base64: str,
    nombre_zip: str,
    config: SunatConfig,
) -> bytes:
    """Construye el mensaje SOAP 1.1 para sendBill con WS-Security UsernameToken."""
    # Envelope
    envelope = etree.Element(f"{{{NS_SOAP}}}Envelope", nsmap={
        "soap": NS_SOAP,
        "wsse": NS_WSSE,
        "wsu": NS_WSU,
        "xsd": NS_XSD,
        "xsi": NS_XSI,
    })

    # Header con WS-Security
    header = etree.SubElement(envelope, f"{{{NS_SOAP}}}Header")
    security = etree.SubElement(header, f"{{{NS_WSSE}}}Security", nsmap={"wsse": NS_WSSE, "wsu": NS_WSU})

    # UsernameToken
    username_token = etree.SubElement(security, f"{{{NS_WSSE}}}UsernameToken")
    username = etree.SubElement(username_token, f"{{{NS_WSSE}}}Username")
    username.text = config.usuario
    password = etree.SubElement(username_token, f"{{{NS_WSSE}}}Password", Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText")
    password.text = config.clave

    # Body
    body = etree.SubElement(envelope, f"{{{NS_SOAP}}}Body")
    send_bill = etree.SubElement(body, f"{{{NS_SUNET}}}sendBill")

    # fileName (SUNAT espera el nombre del ZIP CON extensión .zip)
    file_name = etree.SubElement(send_bill, "fileName")
    file_name.text = nombre_zip  # CON .zip

    # contentFile (Base64 del ZIP)
    content_file = etree.SubElement(send_bill, "contentFile")
    content_file.text = zip_base64

    return etree.tostring(envelope, encoding="UTF-8", xml_declaration=True)


def _enviar_soap(url: str, soap_bytes: bytes, timeout: int) -> bytes:
    """Envía el SOAP por HTTP POST y retorna la respuesta cruda."""
    headers = {
        # SOAP 1.1 usa text/xml (no application/soap+xml, que es SOAP 1.2).
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "urn:sendBill",
    }
    response = requests.post(url, data=soap_bytes, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        print(f"SOAP Error {response.status_code}: {response.text[:2000]}")
    response.raise_for_status()
    return response.content


def _parsear_cdr(response_bytes: bytes) -> tuple[str, str, bytes]:
    """
    Parsea la respuesta SOAP y extrae el CDR.

    Returns:
        (respuesta_codigo, respuesta_descripcion, cdr_zip_bytes)
    """
    root = etree.fromstring(response_bytes)

    # Buscar applicationResponse (Base64 del ZIP del CDR)
    ns = {"soap": NS_SOAP}
    app_response = root.find(".//applicationResponse", namespaces=ns)
    if app_response is None or not app_response.text:
        # Buscar fault
        fault = root.find(".//soap:Fault", namespaces={"soap": NS_SOAP})
        if fault is not None:
            # En SOAP 1.1 faultcode/faultstring van SIN namespace.
            fault_code = fault.find("faultcode")
            fault_string = fault.find("faultstring")
            codigo = fault_code.text.strip() if fault_code is not None and fault_code.text else "Desconocido"
            detalle = fault_string.text.strip() if fault_string is not None and fault_string.text else "Desconocido"
            raise RuntimeError(f"Error SOAP [{codigo}]: {detalle}")
        raise RuntimeError("No se encontró applicationResponse en la respuesta")

    # Decodificar Base64 del CDR ZIP
    cdr_zip_bytes = base64.b64decode(app_response.text)

    # Descomprimir y leer el XML del CDR
    with zipfile.ZipFile(io.BytesIO(cdr_zip_bytes), "r") as zf:
        # El ZIP contiene un XML: R-{nombre_archivo}.xml
        xml_name = [n for n in zf.namelist() if n.endswith(".xml")][0]
        with zf.open(xml_name) as f:
            cdr_xml = f.read()

    # Parsear CDR para obtener ResponseCode y Description
    cdr_root = etree.fromstring(cdr_xml)
    ns_cdr = {
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    }
    response_code = cdr_root.find(".//cbc:ResponseCode", namespaces=ns_cdr)
    response_desc = cdr_root.find(".//cbc:Description", namespaces=ns_cdr)

    codigo = response_code.text if response_code is not None else "9999"
    descripcion = response_desc.text if response_desc is not None else "Sin descripción"

    return codigo, descripcion, cdr_zip_bytes


# ----------------------------------------------------------------------
# API Principal
# ----------------------------------------------------------------------


def enviar_xml_firmado(
    xml_firmado: bytes,
    config: SunatConfig,
    nombre_zip: str,
) -> CDRResultado:
    """Envía a SUNAT un XML **ya firmado** (no vuelve a firmarlo).

    Args:
        xml_firmado: XML UBL 2.1 con la firma en ``<ext:ExtensionContent>``.
        config: configuración (credenciales, URL, timeout).
        nombre_zip: nombre del ZIP a enviar (``{RUC}-{tipo}-{serie}-{corr}.zip``).

    Returns:
        :class:`CDRResultado` con el estado, los códigos y el ZIP del CDR.
    """
    # 1. Hash oficial del comprobante firmado (ds:DigestValue).
    hash_cpe = calcular_hash_cpe(xml_firmado)

    # 2. Comprimir el XML firmado en ZIP.
    zip_bytes = _zip_xml(xml_firmado, nombre_zip.replace(".zip", ".xml"))
    zip_base64 = _base64_encode(zip_bytes)

    # 3. Construir el SOAP y enviarlo.
    soap_bytes = _construir_soap_sendbill(zip_base64, nombre_zip, config)
    logger.info(f"Enviando a {config.url}...")
    response_bytes = _enviar_soap(config.url, soap_bytes, config.timeout)

    # 4. Parsear el CDR.
    codigo, descripcion, cdr_zip_bytes = _parsear_cdr(response_bytes)

    if codigo == "0":
        estado = "ACEPTADO"
    elif codigo.startswith("2") or codigo.startswith("3"):
        estado = "RECHAZADO"
    else:
        estado = "ERROR"

    logger.info(f"CDR recibido: {codigo} - {descripcion} (estado: {estado})")

    return CDRResultado(
        estado=estado,
        respuesta_codigo=codigo,
        respuesta_descripcion=descripcion,
        hash_cpe=hash_cpe,
        xml_firmado_bytes=xml_firmado,
        cdr_bytes=b"",  # el XML del CDR está dentro del ZIP
        cdr_zip_bytes=cdr_zip_bytes,
    )


def enviar_comprobante(
    xml_bytes: bytes,
    config: SunatConfig,
    nombre_zip: str,
) -> CDRResultado:
    """Flujo completo: firma el XML y lo envía a SUNAT (``sendBill``).

    Atajo de :func:`enviar_xml_firmado` para cuando todavía no se ha firmado el
    XML. El servicio de SUNAT prefiere firmar y guardar el XML él mismo, y usar
    :func:`enviar_xml_firmado`.

    Args:
        xml_bytes: XML UBL 2.1 sin firmar (con ``<ext:ExtensionContent>`` vacío).
        config: Configuración (certificado, credenciales, URL).
        nombre_zip: Nombre del ZIP a enviar (ej. 20603122144-03-B001-00000001.zip).

    Returns:
        CDRResultado con estado, códigos, hash, XML firmado, CDR.
    """
    logger.info(f"Iniciando envío a SUNAT: {nombre_zip}")
    logger.info("Firmando XML con XMLDSig...")
    xml_firmado = firmar_xml(xml_bytes, config.cert_path, config.cert_pass, config.ruc)
    return enviar_xml_firmado(xml_firmado, config, nombre_zip)


# ----------------------------------------------------------------------
# Generador de certificado autofirmado para pruebas Beta
# ----------------------------------------------------------------------


def generar_certificado_prueba(
    ruc: str,
    salida_pfx: str,
    password: str,
    dias_validez: int = 365,
) -> None:
    """
    Genera un certificado autofirmado .pfx para pruebas en el entorno Beta.

    Args:
        ruc: RUC del emisor (ej. "20603122144").
        salida_pfx: Ruta donde guardar el .pfx.
        password: Contraseña del .pfx.
        dias_validez: Días de validez del certificado.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    # Generar clave privada
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Nombre del sujeto
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PE"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "LIMA"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "LIMA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LA CASA DEL PAN S.A.C."),
        x509.NameAttribute(NameOID.COMMON_NAME, f"{ruc}"),
    ])

    # Certificado autofirmado
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc)
    ).not_valid_after(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=dias_validez)
    ).add_extension(
        x509.BasicConstraints(ca=False, path_length=None), critical=True
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=True,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ), critical=True
    ).add_extension(
        x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
    ).sign(key, hashes.SHA256())

    # Exportar a PKCS#12 (.pfx)
    pfx = pkcs12.serialize_key_and_certificates(
        name=b"sunat_test",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )

    with open(salida_pfx, "wb") as f:
        f.write(pfx)

    logger.info(f"Certificado de prueba generado: {salida_pfx}")


# ----------------------------------------------------------------------
# Función de conveniencia para test rápido
# ----------------------------------------------------------------------


def test_ciclo_completo_beta(
    xml_bytes: bytes,
    ruc: str = "20603122144",
    cert_path: Optional[str] = None,
    cert_pass: Optional[str] = None,
    nombre_zip: Optional[str] = None,
) -> CDRResultado:
    """
    Ejecuta el ciclo completo contra el WS Beta de SUNAT.

    Si no se proporciona certificado, genera uno autofirmado temporal.
    """
    if cert_path is None or cert_pass is None:
        # Generar certificado temporal
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="sunat_test_")
        cert_path = os.path.join(tmpdir, "test_cert.pfx")
        cert_pass = "test123"
        generar_certificado_prueba(ruc, cert_path, cert_pass)

    if nombre_zip is None:
        nombre_zip = f"{ruc}-03-B001-00000001.zip"

    config = SunatConfig(
        ruc=ruc,
        cert_path=cert_path,
        cert_pass=cert_pass,
        url=WS_BETA_URL,
    )

    return enviar_comprobante(xml_bytes, config, nombre_zip)


# ----------------------------------------------------------------------
# CLI para pruebas rápidas
# ----------------------------------------------------------------------


if __name__ == "__main__":
    import sys

    # Generar certificado de prueba
    tmpdir = tempfile.mkdtemp(prefix="sunat_cert_")
    cert_path = os.path.join(tmpdir, "test_cert.pfx")
    cert_pass = "test123"
    ruc = "20603122144"

    print("Generando certificado de prueba...")
    generar_certificado_prueba(ruc, cert_path, cert_pass)
    print(f"Certificado generado: {cert_path}")

    # Test con XML dummy
    from src.infraestructura.ubl_generator import generar_xml_comprobante

    demo = {
        "emisor": {
            "razon_social": "LA CASA DEL PAN S.A.C.",
            "nombre_comercial": "LA CASA DEL PAN",
            "ruc": "20603122144",
            "direccion": "AV. NICOLAS AYLLON NRO. 1502 URB. CHACLACAYO",
            "ubigeo": "150107",
            "telefono": "",
        },
        "receptor": {"tipo_doc": "1", "num_doc": "12345678", "nombre": "CLIENTE PRUEBA"},
        "comprobante": {
            "tipo": "BOLETA",
            "serie": "B001",
            "correlativo": 1,
            "fecha_emision": "2026-10-18",
            "hora_emision": "12:00:00",
            "moneda": "PEN",
        },
        "totales": {
            "subtotal": 11.80,
            "base_gravada": 10.00,
            "igv": 1.80,
            "exonerado": 0.0,
            "inafecto": 0.0,
            "redondeo": 0.0,
            "total": 11.80,
        },
        "items": [{
            "descripcion": "PAN FRANCES",
            "cantidad": 1,
            "unidad_sunat": "NIU",
            "precio_unitario": 11.80,
            "importe": 11.80,
            "tipo_afectacion": "10",
            "base_gravada": 10.00,
            "igv": 1.80,
            "codigo": "TEST001",
            "impuesto_id": 1,
        }],
    }

    xml = generar_xml_comprobante(demo)
    print(f"XML generado: {len(xml)} bytes")

    if len(sys.argv) > 1 and sys.argv[1] == "--enviar":
        print("Enviando a WS Beta...")
        resultado = test_ciclo_completo_beta(xml, ruc, cert_path, cert_pass)
        print(f"Estado: {resultado.estado}")
        print(f"Código: {resultado.respuesta_codigo}")
        print(f"Descripción: {resultado.respuesta_descripcion}")
        print(f"Hash CPE: {resultado.hash_cpe}")
    else:
        print("XML generado OK. Ejecuta con --enviar para probar envío real.")