"""Impresión de tickets — capa de INFRAESTRUCTURA.

Fase 1 (RF-07): **simulación** de la impresora térmica de 80 mm. En lugar de
enviar comandos ESC/POS al puerto USB, se genera un archivo ``.txt`` en la
carpeta ``tickets/`` con el mismo diseño en texto que tendría el papel
(ancho fijo de ~42 caracteres, que es lo que cabe en 80 mm con la fuente por
defecto).

En la Fase 2 bastará con sustituir la escritura del archivo por el envío
ESC/POS (``python-escpos``), manteniendo intacto el resto del sistema: el
servicio solo conoce :class:`ImpresoraTicket` y sus DTO.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.dominio.catalogos import codigo_unidad_sunat
from src.infraestructura.rutas import ruta_datos

logger = logging.getLogger(__name__)

#: Raíz de los datos escribibles. Los tests lo parchean a ``tmp_path`` para
#: aislar tickets, boletas y facturas.
RAIZ_PROYECTO = ruta_datos()

#: Ancho en caracteres de un ticket de 80 mm con la fuente por defecto.
ANCHO_TICKET = 42

#: Ancho en caracteres de la BOLETA DE VENTA (documento formal, más ancho).
ANCHO_BOLETA = 60

#: Carpeta y prefijo del archivo según el tipo de comprobante fiscal.
CARPETAS_COMPROBANTE: dict[str, tuple[str, str]] = {
    "BOLETA": ("boletas", "boleta"),
    "FACTURA": ("facturas", "factura"),
}


@dataclass(frozen=True)
class LineaTicket:
    """Una línea del ticket (snapshot del producto al momento de la venta)."""

    cantidad: float
    unidad_medida: str
    descripcion: str
    precio_unitario: float
    importe: float
    #: Código del Catálogo N.º 03 (``NIU``, ``KGM``…). La boleta/factura lo
    #: imprime; el ticket interno usa la unidad "de barrio" (``kg``).
    unidad_sunat: str = ""


@dataclass(frozen=True)
class TicketVenta:
    """Datos necesarios para componer un ticket."""

    numero_ticket: str
    fecha: datetime
    lineas: list[LineaTicket]
    total: float
    metodo_pago: str
    monto_recibido: float | None = None
    vuelto: float | None = None
    subtotal: float | None = None
    redondeo: float | None = None
    # --- Desglose tributario (boleta: OP. GRAVADA / IGV / TOTAL) -----------
    base_gravada: float | None = None
    igv: float | None = None
    exonerado: float | None = None
    inafecto: float | None = None
    tasa_igv: float = 0.18
    encabezado: str = "LA CASA DEL PAN S.A.C."
    pie: str = "¡Gracias por su compra!"
    caja: str = "Caja 1"
    moneda: str = "S/"
    # --- Datos de la BOLETA DE VENTA (documento formal) -------------------
    tipo_documento: str = "BOLETA"
    numero_documento: str | None = None
    cliente: str = "CONSUMIDOR FINAL"
    dni_cliente: str = ""
    razon_social: str = "LA CASA DEL PAN S.A.C."
    ruc: str = "20603122144"
    direccion: str = "AV. NICOLAS AYLLON NRO. 1502 URB. CHACLACAYO"
    telefono: str = ""
    # --- Emisión electrónica (SUNAT) --------------------------------------
    #: Hash oficial del comprobante (``ds:DigestValue`` del XML firmado). Se
    #: imprime como "Resumen: <hash>"; vacío mientras no se haya emitido.
    hash_cpe: str = ""
    #: ``beta`` → aviso de demostración; ``produccion`` → leyenda oficial.
    entorno: str = "beta"


class ImpresoraTicket:
    """Genera el ticket de venta como texto de 80 mm (Fase 1: archivo)."""

    def __init__(self, carpeta: str | Path | None = None) -> None:
        """Inicializa la "impresora".

        Args:
            carpeta: destino de los ``.txt``. Por defecto ``tickets/`` dentro
                de la carpeta de datos de la aplicación.
        """
        self._carpeta = Path(carpeta) if carpeta is not None else RAIZ_PROYECTO / "tickets"

    @property
    def carpeta(self) -> Path:
        """Carpeta donde se guardan los tickets."""
        return self._carpeta

    # ------------------------------------------------------------------
    def imprimir(self, ticket: TicketVenta) -> Path:
        """Compone el ticket y lo guarda como ``.txt``.

        Args:
            ticket: datos de la venta a imprimir.

        Returns:
            Ruta del archivo generado (``tickets/ticket_<numero>.txt``).

        Raises:
            OSError: si no se puede escribir el archivo.
        """
        contenido = self.render(ticket)
        self._carpeta.mkdir(parents=True, exist_ok=True)
        ruta = self._carpeta / f"ticket_{ticket.numero_ticket}.txt"
        ruta.write_text(contenido, encoding="utf-8")
        logger.info("Ticket generado: %s", ruta)
        return ruta

    # ------------------------------------------------------------------
    def render(self, ticket: TicketVenta) -> str:
        """Devuelve el ticket como texto (sin tocar el disco)."""
        separador = "=" * ANCHO_TICKET
        linea_fina = "-" * ANCHO_TICKET

        partes: list[str] = [
            separador,
            self._centrar(ticket.encabezado.upper()),
            self._centrar("Ticket de venta"),
            self._centrar("RUC 00000000000  ·  Documento interno"),
            linea_fina,
            f"Ticket : {ticket.numero_ticket}",
            f"Fecha  : {ticket.fecha:%d/%m/%Y %H:%M:%S}",
            f"Caja   : {ticket.caja}",
            linea_fina,
            self._cabecera_items(),
            linea_fina,
        ]

        for linea in ticket.lineas:
            partes.append(self._fila_item(linea, ticket.moneda))

        partes.append(linea_fina)
        if ticket.subtotal is not None:
            partes.append(self._dos_columnas("Subtotal        ", self._monto(ticket.subtotal, ticket.moneda)))
        if ticket.redondeo is not None and abs(ticket.redondeo) >= 0.005:
            signo = "+" if ticket.redondeo > 0 else ""
            partes.append(self._dos_columnas("Redondeo        ", f"{signo}{self._monto(ticket.redondeo, ticket.moneda)}"))
        partes.append(self._dos_columnas("TOTAL A PAGAR   ", self._monto(ticket.total, ticket.moneda)))
        partes.append(f"Metodo de pago : {ticket.metodo_pago}")

        if ticket.monto_recibido is not None:
            recibido = self._monto(ticket.monto_recibido, ticket.moneda)
            vuelto = self._monto(ticket.vuelto or 0.0, ticket.moneda)
            partes.append(self._dos_columnas(f"Recibido       : {recibido}", f"Vuelto: {vuelto}"))

        partes.append(separador)
        partes.append(self._centrar(ticket.pie))
        partes.append(separador)
        return "\n".join(partes) + "\n"

    # -------------------------------------------------------------- Boleta
    def imprimir_comprobante(self, ticket: TicketVenta) -> Path:
        """Compone y guarda el comprobante fiscal (boleta o factura).

        El tipo lo determina ``ticket.tipo_documento``:

        * ``BOLETA`` → ``boletas/boleta_<serie-numero>.txt``
        * ``FACTURA`` → ``facturas/factura_<serie-numero>.txt``

        En Fase 1 se simula como archivo, igual que el ticket térmico.

        Returns:
            Ruta del archivo generado.
        """
        contenido = self.render_boleta(ticket)
        tipo = (ticket.tipo_documento or "BOLETA").strip().upper()
        carpeta_nombre, prefijo = CARPETAS_COMPROBANTE.get(tipo, ("boletas", "boleta"))
        carpeta = RAIZ_PROYECTO / carpeta_nombre
        carpeta.mkdir(parents=True, exist_ok=True)
        numero = (ticket.numero_documento or ticket.numero_ticket).replace("/", "-")
        ruta = carpeta / f"{prefijo}_{numero}.txt"
        ruta.write_text(contenido, encoding="utf-8")
        logger.info("%s generada: %s", tipo.capitalize(), ruta)
        return ruta

    def imprimir_boleta(self, ticket: TicketVenta) -> Path:
        """Compatibilidad: emite el comprobante fiscal del ticket."""
        return self.imprimir_comprobante(ticket)

    # ------------------------------------------------------------------
    def render_boleta(self, ticket: TicketVenta) -> str:
        """Devuelve el comprobante fiscal como texto de ancho ``ANCHO_BOLETA``."""
        ancho = ANCHO_BOLETA
        separador = "=" * ancho
        linea_fina = "-" * ancho
        numero = ticket.numero_documento or ticket.numero_ticket
        es_factura = (ticket.tipo_documento or "").strip().upper() == "FACTURA"

        partes: list[str] = [
            separador,
            self._centrar((ticket.razon_social or ticket.encabezado).upper(), ancho),
            self._centrar(f"RUC: {ticket.ruc}", ancho) if ticket.ruc else "",
            self._centrar(ticket.direccion, ancho) if ticket.direccion else "",
            self._centrar(f"TEL: {ticket.telefono}", ancho) if ticket.telefono else "",
            separador,
            self._centrar(f"{ticket.tipo_documento} DE VENTA", ancho),
            self._centrar(f"Nº {numero}", ancho),
            linea_fina,
            f"Fecha   : {ticket.fecha:%d/%m/%Y %H:%M:%S}",
            f"Caja    : {ticket.caja}",
        ]
        if es_factura:
            # La factura exige razón social y RUC del adquiriente.
            partes.append(f"Señor(es): {ticket.cliente or ''}")
            partes.append(f"RUC     : {ticket.dni_cliente or ''}")
        else:
            partes.append(f"Cliente : {ticket.cliente or 'CONSUMIDOR FINAL'}")
            if ticket.dni_cliente:
                partes.append(f"DNI/RUC : {ticket.dni_cliente}")
        partes.append(linea_fina)

        # Cabecera de ítems del ancho del comprobante. La columna de cantidad se
        # ensancha para dar cabida al código SUNAT de la unidad ("1.5 KGM").
        partes.append(f"{'CANT':>8} {'DESCRIPCION':<22} {'P.UNIT':>9} {'IMPORTE':>11}")
        partes.append(linea_fina)
        for linea in ticket.lineas:
            cantidad = self._cantidad_catalogo(linea)
            descripcion = (linea.descripcion or "").strip()[:22]
            partes.append(
                f"{cantidad[:8]:>8} {descripcion:<22} "
                f"{linea.precio_unitario:>9,.2f} {linea.importe:>11,.2f}"
            )
        partes.append(linea_fina)

        # Desglose tributario del comprobante (OP. GRAVADA / IGV / TOTAL).
        # Solo aparece el bloque de IGV si la venta está gravada; el exonerado y
        # el inafecto se muestran únicamente cuando tienen importe.
        tiene_desglose = any(
            valor is not None
            for valor in (ticket.base_gravada, ticket.igv, ticket.exonerado, ticket.inafecto)
        )
        if tiene_desglose:
            if (ticket.base_gravada or 0) > 0 or (ticket.igv or 0) > 0:
                partes.append(
                    self._dos_columnas("OP. GRAVADA", self._monto(ticket.base_gravada or 0.0, ticket.moneda), ancho)
                )
                porcentaje = f"{ticket.tasa_igv * 100:.0f}%"
                partes.append(
                    self._dos_columnas(f"IGV ({porcentaje})", self._monto(ticket.igv or 0.0, ticket.moneda), ancho)
                )
            if ticket.exonerado:
                partes.append(
                    self._dos_columnas("OP. EXONERADA", self._monto(ticket.exonerado, ticket.moneda), ancho)
                )
            if ticket.inafecto:
                partes.append(
                    self._dos_columnas("OP. INAFECTA", self._monto(ticket.inafecto, ticket.moneda), ancho)
                )
        elif ticket.subtotal is not None:
            partes.append(self._dos_columnas("Subtotal", self._monto(ticket.subtotal, ticket.moneda), ancho))

        if ticket.redondeo is not None and abs(ticket.redondeo) >= 0.005:
            signo = "+" if ticket.redondeo > 0 else ""
            partes.append(self._dos_columnas("Redondeo", f"{signo}{self._monto(ticket.redondeo, ticket.moneda)}", ancho))
        partes.append(self._dos_columnas("TOTAL A PAGAR", self._monto(ticket.total, ticket.moneda), ancho))
        partes.append(f"Metodo de pago : {ticket.metodo_pago}")
        if ticket.monto_recibido is not None:
            recibido = self._monto(ticket.monto_recibido, ticket.moneda)
            vuelto = self._monto(ticket.vuelto or 0.0, ticket.moneda)
            partes.append(self._dos_columnas(f"Recibido: {recibido}", f"Vuelto: {vuelto}", ancho))

        partes.append(linea_fina)
        # Leyenda legal del comprobante. En Beta se advierte que es una
        # representación de demostración; en producción se declara la
        # "Representación impresa ... de Venta Electrónica" que exige SUNAT.
        if (ticket.entorno or "").strip().lower() == "produccion":
            nombre_doc = "Factura" if es_factura else "Boleta"
            partes.append(
                self._centrar(
                    f"Representación impresa de la {nombre_doc} de Venta Electrónica", ancho
                )
            )
        else:
            partes.append(self._centrar("Representación impresa del comprobante", ancho))
            partes.append(
                self._centrar("para fines de demostración (sin valor tributario).", ancho)
            )
        # "Resumen": hash oficial del XML firmado (ds:DigestValue, Base64).
        if ticket.hash_cpe:
            partes.append(self._centrar(f"Resumen: {ticket.hash_cpe}", ancho))
        partes.append(separador)
        partes.append(self._centrar(ticket.pie, ancho))
        partes.append(separador)
        return "\n".join(p for p in partes if p != "") + "\n"

    # ---------------------------------------------------------- Utilidades
    @staticmethod
    def _monto(valor: float, moneda: str = "S/") -> str:
        return f"{moneda} {valor:,.2f}"

    def _centrar(self, texto: str, ancho: int = ANCHO_TICKET) -> str:
        return texto[:ancho].center(ancho).rstrip()

    @staticmethod
    def _dos_columnas(izquierda: str, derecha: str, ancho: int = ANCHO_TICKET) -> str:
        relleno = max(1, ancho - len(izquierda) - len(derecha))
        return f"{izquierda}{' ' * relleno}{derecha}"

    @staticmethod
    def _cabecera_items() -> str:
        # Cols: cantidad (6) | descripción (19) | P.U. (7) | importe (7) + 3 espacios.
        return f"{'CANT':>6} {'DESCRIPCION':<19} {'P.U.':>7} {'IMPORTE':>7}"

    @staticmethod
    def _fila_item(linea: LineaTicket, moneda: str = "S/") -> str:
        cantidad = ImpresoraTicket._cantidad_corta(linea.cantidad, linea.unidad_medida)
        descripcion = (linea.descripcion or "").strip()[:19]
        return (
            f"{cantidad[:6]:>6} {descripcion:<19} "
            f"{linea.precio_unitario:>7,.2f} {linea.importe:>7,.2f}"
        )

    @staticmethod
    def _cantidad_corta(cantidad: float, unidad_medida: str) -> str:
        """``1.5`` + ``KG`` → ``1.5kg``; ``2`` + ``UND`` → ``2``."""
        numero = f"{cantidad:g}"
        unidad = (unidad_medida or "UND").upper()
        return f"{numero}{unidad.lower()}" if unidad != "UND" else numero

    @staticmethod
    def _cantidad_catalogo(linea: LineaTicket) -> str:
        """Cantidad con el código del Catálogo N.º 03: ``1.5`` + KGM → ``1.5 KGM``.

        Se usa en la boleta/factura (documento fiscal). El ticket interno sigue
        usando :meth:`_cantidad_corta`, más legible para el mostrador.
        """
        numero = f"{linea.cantidad:g}"
        codigo = (linea.unidad_sunat or codigo_unidad_sunat(linea.unidad_medida)).strip().upper()
        return f"{numero} {codigo}" if codigo else numero

