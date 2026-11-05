"""Catálogos de SUNAT — capa de DOMINIO.

Traduce los valores internos del sistema a los códigos oficiales que exige un
comprobante electrónico (XML UBL 2.1). No depende de la base de datos ni de la
interfaz: son tablas de referencia puras.

Catálogos usados:

* **Catálogo N.º 03** — unidad de medida (``NIU``, ``KGM``…), Anexo N.º 8.
* **Catálogo N.º 01** — tipo de comprobante (``01`` factura, ``03`` boleta).

Además centraliza las **reglas de identificación del cliente** que exige el
Reglamento de Comprobantes de Pago (DNI/RUC obligatorios).
"""

from __future__ import annotations

from src.dominio.excepciones import ComprobanteInvalido

# ----------------------------------------------------------------------
# Catálogo N.º 03 — Unidades de medida
# ----------------------------------------------------------------------
#: Mapa ``unidad interna → código SUNAT``. Las claves son las que ya usa el
#: catálogo de productos (:data:`src.servicios.producto_service.UNIDADES_MEDIDA`).
CATALOGO_03_UNIDADES: dict[str, str] = {
    "UND": "NIU",       # unidad (bienes)
    "KG": "KGM",        # kilogramo
    "GR": "GRM",        # gramo
    "LT": "LTR",        # litro
    "ML": "MLT",        # mililitro
    "DOCENA": "DZN",    # docena
    "PAQUETE": "PK",    # paquete
}

#: Código de respaldo cuando la unidad no está en el catálogo (``ZZ`` = servicio,
#: que SUNAT admite para conceptos sin unidad física).
CODIGO_UNIDAD_POR_DEFECTO = "ZZ"


def codigo_unidad_sunat(unidad: str | None) -> str:
    """Devuelve el código del Catálogo N.º 03 para una unidad interna.

    ``"KG"`` → ``"KGM"``; ``"UND"`` → ``"NIU"``. Si la unidad es desconocida
    devuelve :data:`CODIGO_UNIDAD_POR_DEFECTO`.
    """
    clave = str(unidad or "").strip().upper()
    return CATALOGO_03_UNIDADES.get(clave, CODIGO_UNIDAD_POR_DEFECTO)


# ----------------------------------------------------------------------
# Catálogo N.º 01 — Tipo de comprobante
# ----------------------------------------------------------------------
TIPO_TICKET = "TICKET"
TIPO_BOLETA = "BOLETA"
TIPO_FACTURA = "FACTURA"

#: Tipos admitidos en la caja, en el orden en que se ofrecen.
TIPOS_COMPROBANTE: tuple[str, ...] = (TIPO_TICKET, TIPO_BOLETA, TIPO_FACTURA)

#: Etiqueta visible para cada tipo (lo que ve el cajero en el selector).
ETIQUETA_COMPROBANTE: dict[str, str] = {
    TIPO_TICKET: "Ticket interno",
    TIPO_BOLETA: "Boleta",
    TIPO_FACTURA: "Factura",
}

#: Serie por defecto de cada tipo. Cada serie lleva su **propio** correlativo.
SERIE_TICKET = "T001"
SERIE_BOLETA = "B001"
SERIE_FACTURA = "F001"

SERIE_POR_TIPO: dict[str, str] = {
    TIPO_TICKET: SERIE_TICKET,
    TIPO_BOLETA: SERIE_BOLETA,
    TIPO_FACTURA: SERIE_FACTURA,
}

TIPO_POR_SERIE: dict[str, str] = {v: k for k, v in SERIE_POR_TIPO.items()}

#: Código del Catálogo N.º 01 (el que va en el XML UBL 2.1). El ticket interno
#: no es comprobante electrónico: ``"00"`` solo aplica al ticket POS del
#: SEE-Consumidor Final.
CATALOGO_01_COMPROBANTE: dict[str, str] = {
    TIPO_TICKET: "00",
    TIPO_BOLETA: "03",
    TIPO_FACTURA: "01",
}

#: Nombre en MAYÚSCULAS para la representación impresa.
DOCUMENTO_IMPRESO: dict[str, str] = {
    TIPO_TICKET: "TICKET",
    TIPO_BOLETA: "BOLETA",
    TIPO_FACTURA: "FACTURA",
}

# ----------------------------------------------------------------------
# Reglas de identificación del cliente
# ----------------------------------------------------------------------
#: Importe **desde** el cual la boleta exige identificar al cliente (DNI o RUC).
#: El umbral es **inclusivo**: una boleta de S/ 700,00 exactos ya requiere
#: documento (criterio algo más estricto que el mínimo de SUNAT, que solo lo
#: exige por encima del tope).
MONTO_MINIMO_BOLETA_CON_DOCUMENTO = 700.0

DIGITOS_DNI = 8
DIGITOS_RUC = 11


def normalizar_tipo_comprobante(tipo: str | None) -> str:
    """Normaliza el tipo de comprobante recibido de la interfaz.

    Un valor vacío cae al ticket interno (lo más conservador: no emite
    documento fiscal). Un valor **desconocido** es un error de programación y
    se rechaza en lugar de convertirse silenciosamente en otra cosa.

    Raises:
        ComprobanteInvalido: si el tipo no es uno de los admitidos.
    """
    valor = str(tipo or "").strip().upper()
    if not valor:
        return TIPO_TICKET
    if valor not in TIPOS_COMPROBANTE:
        raise ComprobanteInvalido(
            f"Tipo de comprobante no válido: '{tipo}'. "
            f"Use uno de: {', '.join(TIPOS_COMPROBANTE)}."
        )
    return valor


def solo_digitos(texto: str | None) -> str:
    """Extrae solo los dígitos de un documento (tolera guiones y espacios)."""
    return "".join(caracter for caracter in str(texto or "") if caracter.isdigit())


def es_dni_valido(documento: str | None) -> bool:
    """``True`` si el documento tiene los 8 dígitos de un DNI."""
    return len(solo_digitos(documento)) == DIGITOS_DNI


def es_ruc_valido(documento: str | None) -> bool:
    """``True`` si el documento tiene los 11 dígitos de un RUC."""
    return len(solo_digitos(documento)) == DIGITOS_RUC


def boleta_exige_documento(total: float) -> bool:
    """``True`` si una boleta de ese importe exige identificar al cliente.

    El umbral (:data:`MONTO_MINIMO_BOLETA_CON_DOCUMENTO`) es **inclusivo**: se
    compara el total ya redondeado a 2 decimales.
    """
    return round(float(total), 2) >= MONTO_MINIMO_BOLETA_CON_DOCUMENTO


def validar_compra(
    tipo_comprobante: str | None,
    total: float,
    cliente: str = "",
    documento: str = "",
) -> tuple[str, str, str]:
    """Valida los datos del comprobante y los devuelve normalizados.

    Reglas (Reglamento de Comprobantes de Pago):

    * **Ticket interno**: no lleva datos fiscales; se descartan.
    * **Boleta**: desde :data:`MONTO_MINIMO_BOLETA_CON_DOCUMENTO` (S/ 700,
      umbral inclusivo) el DNI/RUC es obligatorio. El nombre es opcional.
    * **Factura**: razón social y RUC (11 dígitos) obligatorios.

    Args:
        tipo_comprobante: ``TICKET``, ``BOLETA`` o ``FACTURA``.
        total: total del comprobante (ya redondeado).
        cliente: nombre o razón social.
        documento: DNI o RUC.

    Returns:
        ``(tipo, cliente, documento)`` normalizados.

    Raises:
        ComprobanteInvalido: si falta un dato obligatorio o el documento no
            tiene el formato correcto.
    """
    tipo = normalizar_tipo_comprobante(tipo_comprobante)
    cliente = str(cliente or "").strip()
    documento = solo_digitos(documento)

    if tipo == TIPO_TICKET:
        return tipo, "", ""

    if tipo == TIPO_FACTURA:
        if not cliente:
            raise ComprobanteInvalido("La factura exige la razón social del cliente.")
        if not es_ruc_valido(documento):
            raise ComprobanteInvalido(
                f"La factura exige un RUC de {DIGITOS_RUC} dígitos "
                f"(recibido: '{documento or '—'}')."
            )
        return tipo, cliente, documento

    # Boleta: el documento solo es obligatorio por encima del tope.
    if documento and not (es_dni_valido(documento) or es_ruc_valido(documento)):
        raise ComprobanteInvalido(
            f"El documento de la boleta debe tener {DIGITOS_DNI} dígitos (DNI) "
            f"u {DIGITOS_RUC} (RUC); se recibió '{documento}'."
        )
    if not documento and boleta_exige_documento(total):
        raise ComprobanteInvalido(
            f"Las boletas de S/ {MONTO_MINIMO_BOLETA_CON_DOCUMENTO:,.2f} o más "
            "exigen el DNI o RUC del cliente."
        )
    return tipo, cliente, documento
