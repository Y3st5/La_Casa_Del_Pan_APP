"""Servicio de ventas — capa de SERVICIOS.

Orquesta el caso de uso **"cobrar una venta"** dentro de **una única
transacción**: registra la venta, su detalle, descuenta el stock y deja
constancia en el kardex (``movimientos_stock``). Si algo falla (por ejemplo,
stock insuficiente), todo se revierte.

Después del ``COMMIT`` se imprime el ticket (Fase 1: archivo simulado de
80 mm). La impresión **nunca** revierte la venta: si falla, solo queda
registrada en el log.

La presentación no ejecuta SQL: llama a este servicio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.dominio.catalogos import (
    DOCUMENTO_IMPRESO,
    ETIQUETA_COMPROBANTE,
    SERIE_POR_TIPO,
    SERIE_TICKET,
    TIPO_BOLETA,
    TIPO_FACTURA,
    TIPO_TICKET,
    codigo_unidad_sunat,
    normalizar_tipo_comprobante,
    validar_compra,
)
from src.dominio.excepciones import CarritoVacio, ErrorVenta, StockInsuficiente
from src.dominio.impuestos import (
    CODIGO_IGV,
    Desglose,
    LineaImpuesto,
    a_decimal,
    calcular_desglose,
    desglosar_lineas,
    dos_decimales,
    linea_desde_impuesto,
)
from src.infraestructura.config_reader import ConfigReader
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.impresora import ImpresoraTicket, LineaTicket, TicketVenta
from src.infraestructura.rutas import ruta_datos
from src.repositorios.compra_repo import METODOS_COMPRA_AFECTAN_CAJA, CompraRepo
from src.repositorios.comprobante_electronico_repo import (
    ESTADO_PENDIENTE,
    ComprobanteElectronicoRepo,
)
from src.repositorios.impuesto_repo import ImpuestoRepo
from src.repositorios.movimiento_repo import MovimientoRepo
from src.repositorios.producto_repo import ProductoRepo
from src.repositorios.serie_repo import SerieRepo
from src.repositorios.venta_repo import VentaRepo
from src.servicios.sunat_service import ResultadoEnvio, SunatService

# PDF para cierre de día
try:
    from fpdf import FPDF
except ImportError:
    FPDF = None  # type: ignore

logger = logging.getLogger(__name__)

#: Raíz de los datos escribibles (reportes PDF). Los tests la parchean a
#: ``tmp_path`` para no escribir en la carpeta real.
RAIZ_PROYECTO = ruta_datos()

#: Métodos de pago disponibles en la caja (Fase 1).
METODOS_PAGO: tuple[str, ...] = (
    "Efectivo",
    "Yape/Plin",
    "Tarjeta",
    "Consumo propio",
    "Fiado",
)

#: Método especial que **NO** representa un ingreso de dinero: la panadería saca
#: producto para uso interno sin cobro. Descuenta stock, pero no entra al arqueo.
METODO_CONSUMO_PROPIO = "Consumo propio"

#: Métodos que **no** ingresan dinero a caja (no entran al arqueo monetario).
METODOS_NO_MONETARIOS: frozenset[str] = frozenset({METODO_CONSUMO_PROPIO, "Fiado"})

#: Serie del comprobante interno (ticket) de control. Cada serie tiene su
#: **propio** correlativo, reservado en la tabla ``series_comprobante`` (que no
#: se limpia en el Cierre del Día). Ver :mod:`src.dominio.catalogos`.

#: Datos del negocio por defecto (si no están en ``[negocio]`` de config.ini).
#: Titular real: LA CASA DEL PAN S.A.C. (RUC 20603122144, RER, afecto a IGV).
EMPRESA_POR_DEFECTO: dict[str, str] = {
    "razon_social": "LA CASA DEL PAN S.A.C.",
    "nombre_comercial": "LA CASA DEL PAN",
    "ruc": "20603122144",
    "direccion": "AV. NICOLAS AYLLON NRO. 1502 URB. CHACLACAYO",
    "ubigeo": "150107",
    "telefono": "",
}

#: Tipo de movimiento de stock para una venta (kardex).
TIPO_MOVIMIENTO_SALIDA_VENTA = "SALIDA_VENTA"

#: Paso del redondeo de efectivo (Perú: moneda de 10 céntimos).
PASO_REDONDEO = 0.10


def metodo_es_monetario(metodo: str) -> bool:
    """``True`` si el método representa dinero recibido (entra al arqueo de caja)."""
    return metodo not in METODOS_NO_MONETARIOS


def redondear_total(total: float, paso: float = PASO_REDONDEO) -> float:
    """Redondea al múltiplo más cercano de ``paso``, con las mitades hacia arriba.

    Se usa para el redondeo en soles al cobrar en efectivo:

        * ``S/ 3.54`` → ``S/ 3.50``
        * ``S/ 3.55`` → ``S/ 3.60``  (mitad: a favor del negocio)

    Se trabaja con céntimos enteros para evitar los errores de coma flotante.
    """
    centimos_paso = int(round(paso * 100))
    if centimos_paso <= 0:
        return round(float(total), 2)
    centimos = int(round(float(total) * 100))
    redondeado = ((centimos + centimos_paso // 2) // centimos_paso) * centimos_paso
    return redondeado / 100


@dataclass(frozen=True)
class ResultadoVenta:
    """Resultado de una venta confirmada."""

    venta_id: int
    serie: str
    correlativo: int
    subtotal: float
    total: float
    vuelto: float
    items: int
    redondeo: float = 0.0
    #: Desglose tributario (IGV) — ``0`` mientras el producto esté exonerado.
    base_gravada: float = 0.0
    igv: float = 0.0
    exonerado: float = 0.0
    inafecto: float = 0.0
    #: Comprobante fiscal emitido. ``TICKET`` = solo ticket interno de control.
    tipo_comprobante: str = TIPO_TICKET
    serie_comprobante: str = ""
    correlativo_comprobante: int = 0
    cliente: str = ""
    dni_cliente: str = ""
    #: ``id`` en ``comprobantes_electronicos`` (0 si fue ticket interno).
    comprobante_electronico_id: int = 0

    @property
    def numero_ticket(self) -> str:
        """Número del ticket interno de control, p. ej. ``T001-000123``.

        Devuelve ``''`` si la venta no es un ticket interno (boleta/factura).
        """
        if not self.serie or self.correlativo == 0:
            return ""
        return f"{self.serie}-{self.correlativo:06d}"

    @property
    def emite_comprobante(self) -> bool:
        """``True`` si la venta emitió boleta o factura (no solo ticket)."""
        return bool(self.serie_comprobante)

    @property
    def numero_comprobante(self) -> str:
        """Número del comprobante fiscal ('' si la venta solo emitió ticket)."""
        if not self.serie_comprobante:
            return ""
        return f"{self.serie_comprobante}-{self.correlativo_comprobante:06d}"


@dataclass(frozen=True)
class LineaVenta:
    """Línea de la venta lista para persistir (producto + importe + impuesto)."""

    producto: dict[str, Any]
    cantidad: float
    importe: Decimal
    impuesto: dict[str, Any] | None = None
    base_gravada: float = 0.0
    igv: float = 0.0

    def a_impuesto(self) -> LineaImpuesto:
        """Devuelve la línea como dato de dominio para el cálculo del IGV."""
        return linea_desde_impuesto(self.importe, self.impuesto)


class VentaService:
    """Casos de uso relacionados con la venta."""

    #: Expuesto a la UI para poblar el selector de métodos de pago.
    METODOS_PAGO = METODOS_PAGO

    def __init__(
        self,
        conexion_bd: ConexionBD,
        producto_repo: ProductoRepo | None = None,
        venta_repo: VentaRepo | None = None,
        movimiento_repo: MovimientoRepo | None = None,
        compra_repo: CompraRepo | None = None,
        impresora: ImpresoraTicket | None = None,
        impuesto_repo: ImpuestoRepo | None = None,
        serie_repo: SerieRepo | None = None,
        comprobante_repo: ComprobanteElectronicoRepo | None = None,
        sunat_service: SunatService | None = None,
        config: ConfigReader | None = None,
    ) -> None:
        self._bd = conexion_bd
        self._producto_repo = producto_repo or ProductoRepo(conexion_bd)
        self._venta_repo = venta_repo or VentaRepo(conexion_bd)
        self._movimiento_repo = movimiento_repo or MovimientoRepo(conexion_bd)
        self._compra_repo = compra_repo or CompraRepo(conexion_bd)
        self._impuesto_repo = impuesto_repo or ImpuestoRepo(conexion_bd)
        self._serie_repo = serie_repo or SerieRepo(conexion_bd)
        # Puente SUNAT: registra la boleta/factura emitida como PENDIENTE.
        self._comprobante_repo = comprobante_repo or ComprobanteElectronicoRepo(conexion_bd)
        # Emisor electrónico (best effort): se intenta enviar a SUNAT al cobrar.
        # Si es ``None`` (p. ej. en tests), la venta solo queda registrada.
        self._sunat = sunat_service
        # La impresión es "best effort": la venta ya está registrada y COMMITeada.
        self._impresora = impresora if impresora is not None else ImpresoraTicket()
        # Datos del negocio para el encabezado de la boleta.
        self._empresa = self._leer_empresa(config)

    @staticmethod
    def _leer_empresa(config: ConfigReader | None) -> dict[str, str]:
        """Lee la razón social, RUC, dirección y teléfono de ``[negocio]``.

        Siempre devuelve valores (los de la config si están, si no los
        predeterminados), para que la boleta nunca quede sin encabezado.
        """
        datos = dict(EMPRESA_POR_DEFECTO)
        if config is None:
            return datos
        for clave in datos:
            valor = config.obtener("negocio", clave, EMPRESA_POR_DEFECTO[clave])
            if valor:
                datos[clave] = str(valor).strip()
        logger.info(
            "Datos del negocio: %s (RUC %s).",
            datos["razon_social"],
            datos["ruc"],
        )
        return datos

    # ------------------------------------------------------------------
    # Consultas (delegadas al repositorio)
    # ------------------------------------------------------------------
    def buscar_por_codigo(self, codigo: str) -> dict[str, Any] | None:
        """Busca un producto por código de barras o código interno."""
        return self._producto_repo.buscar_por_codigo(codigo)

    def buscar_productos(self, texto: str, limite: int = 8) -> list[dict[str, Any]]:
        """Sugiere productos por nombre o código (búsqueda parcial).

        Es la que usa el autocompletado de la caja para buscar por nombre.
        """
        return self._producto_repo.buscar_por_texto(texto, limite=limite)

    @staticmethod
    def calcular_total(carrito: dict[int, dict[str, Any]]) -> float:
        """Calcula el total del carrito (cantidad × precio).

        Redondea **línea por línea** (no el total) porque es lo que exige un
        comprobante electrónico: la suma de los importes de las líneas debe
        coincidir con el total del documento.
        """
        total = Decimal("0.00")
        for item in carrito.values():
            cantidad = a_decimal(item.get("cantidad"))
            precio = a_decimal(item["producto"].get("precio_venta") or 0)
            total += dos_decimales(cantidad * precio)
        return float(total)

    def _mapa_impuestos(self) -> dict[int, dict[str, Any]]:
        """Devuelve ``{id: impuesto}``; si falla, se asume IGV 18 % por defecto."""
        try:
            return self._impuesto_repo.mapa_por_id()
        except Exception:  # noqa: BLE001 - el cobro no debe caer por el catálogo
            logger.exception("No se pudo leer el catálogo de impuestos; se asume IGV 18 %.")
            return {}

    def calcular_lineas(
        self, carrito: dict[int, dict[str, Any]]
    ) -> tuple[Desglose, list[LineaVenta]]:
        """Calcula el desglose tributario del carrito y sus líneas persistibles.

        Returns:
            ``(desglose, lineas)`` en el mismo orden que ``carrito.values()``.
        """
        mapa = self._mapa_impuestos()
        bases: list[LineaVenta] = []
        for item in carrito.values():
            producto = item["producto"]
            cantidad = a_decimal(item.get("cantidad"))
            precio = a_decimal(producto.get("precio_venta") or 0)
            importe = dos_decimales(cantidad * precio)
            impuesto = mapa.get(int(producto.get("impuesto_id") or 0))
            bases.append(
                LineaVenta(
                    producto=producto,
                    cantidad=float(cantidad),
                    importe=importe,
                    impuesto=impuesto,
                )
            )

        desglose = calcular_desglose([linea.a_impuesto() for linea in bases])
        reparto = desglosar_lineas([linea.a_impuesto() for linea in bases], desglose)
        lineas = [
            LineaVenta(
                producto=linea.producto,
                cantidad=linea.cantidad,
                importe=linea.importe,
                impuesto=linea.impuesto,
                base_gravada=float(base),
                igv=float(igv),
            )
            for linea, (base, igv) in zip(bases, reparto)
        ]
        return desglose, lineas

    # ------------------------------------------------------------------
    # Reporte "Ventas del Día"
    # ------------------------------------------------------------------
    def ventas_del_dia(self, fecha: str | None = None) -> dict[str, Any]:
        """Reporte de las ventas de un día con resumen agregado.

        Args:
            fecha: ``'YYYY-MM-DD'``. Si es ``None``, se usa la fecha local de hoy.

        Returns:
            ``{"fecha", "ventas": [...], "resumen": {cantidad, total_vendido, por_metodo}}``
            con cada venta ya formateada (ticket, hora, método, totales e ítems).
        """
        fecha = fecha or datetime.now().strftime("%Y-%m-%d")
        filas = self._venta_repo.listar_por_fecha(fecha)

        ventas = [
            {
                "venta_id": int(v["id"]),
                "numero_ticket": (
                    f"{v['serie']}-{int(v['correlativo']):06d}"
                    if v.get("serie") and v.get("correlativo")
                    else ""
                ),
                "tipo_comprobante": str(v.get("tipo_comprobante") or TIPO_TICKET),
                "numero_comprobante": self._numero_comprobante(v),
                "hora": self._hora_venta(v.get("creado_en")),
                "metodo_pago": v["metodo_pago"],
                "subtotal": round(float(v["subtotal"]), 2),
                "total": round(float(v["total"]), 2),
                "vuelto": round(float(v["vuelto"]), 2),
                "estado": str(v["estado"]),
                "items": int(v.get("items") or 0),
                "primer_producto": v.get("primer_producto"),
                "cliente": (v.get("cliente") or "").strip(),
                "dni_cliente": (v.get("dni_cliente") or "").strip(),
            }
            for v in filas
        ]

        total_vendido = round(sum(v["total"] for v in ventas), 2)
        por_metodo: dict[str, float] = {}
        for v in ventas:
            por_metodo[v["metodo_pago"]] = round(
                por_metodo.get(v["metodo_pago"], 0.0) + v["total"], 2
            )

        # Separación para el arqueo: el consumo propio (uso interno) no entra
        # como dinero recaudado, aunque sí forma parte del total vendido.
        total_monetario = round(
            sum(v["total"] for v in ventas if metodo_es_monetario(v["metodo_pago"])), 2
        )
        total_no_monetario = round(total_vendido - total_monetario, 2)

        return {
            "fecha": fecha,
            "ventas": ventas,
            "resumen": {
                "cantidad": len(ventas),
                "total_vendido": total_vendido,
                "total_monetario": total_monetario,
                "total_no_monetario": total_no_monetario,
                "por_metodo": por_metodo,
                **self._desglose_categorias(fecha, total_vendido),
                "tributario": self._resumen_tributario(filas),
            },
        }

    def _tasa_igv_vigente(self) -> float:
        """Tasa de IGV vigente leída del catálogo de impuestos (18 % por defecto).

        Es el único lugar donde se resuelve la tasa: la usan el reporte del día
        y el PDF del cierre para no quedar desincronizados.
        """
        for impuesto in self._mapa_impuestos().values():
            if str(impuesto.get("codigo_sunat")) == CODIGO_IGV:
                return float(impuesto.get("tasa") or 0.18)
        return 0.18

    def _resumen_tributario(self, filas: list[dict[str, Any]]) -> dict[str, float]:
        """Totaliza el desglose tributario (IGV) de las ventas recibidas.

        Es el resumen que se declara a SUNAT: ``base_gravada + igv + exonerado +
        inafecto`` es el subtotal tributario del día. El redondeo de efectivo no
        forma parte de él y se muestra aparte.
        """
        base_gravada = igv = exonerado = inafecto = 0.0
        for v in filas:
            base_gravada += float(v.get("base_gravada") or 0)
            igv += float(v.get("igv") or 0)
            exonerado += float(v.get("exonerado") or 0)
            inafecto += float(v.get("inafecto") or 0)
        return {
            "tasa_igv": self._tasa_igv_vigente(),
            "base_gravada": round(base_gravada, 2),
            "igv": round(igv, 2),
            "exonerado": round(exonerado, 2),
            "inafecto": round(inafecto, 2),
            "total": round(base_gravada + igv + exonerado + inafecto, 2),
        }

    def _desglose_categorias(self, fecha: str, total_vendido: float) -> dict[str, Any]:
        """Desglose de lo vendido en el día por categoría de producto.

        La suma de los subtotales por categoría proviene del detalle de venta
        (sin redondear a S/ 0.10); el ``ajuste_redondeo`` es la diferencia
        contra el total cobrado para que el desglose cuadre con él.

        Returns:
            ``{"por_categoria": [...], "categorias_total", "ajuste_redondeo"}``.
        """
        try:
            filas = self._venta_repo.totales_por_categoria(fecha)
        except Exception:  # noqa: BLE001 - el reporte no debe caer por el desglose
            logger.exception("No se pudo calcular el desglose por categoría de %s.", fecha)
            filas = []

        por_categoria = [
            {
                "categoria": str(f.get("categoria") or "Sin categoría"),
                "subtotal": round(float(f.get("subtotal") or 0), 2),
                "cantidad": float(f.get("cantidad") or 0),
            }
            for f in filas
        ]
        suma = round(sum(c["subtotal"] for c in por_categoria), 2)
        return {
            "por_categoria": por_categoria,
            "categorias_total": suma,
            "ajuste_redondeo": round(total_vendido - suma, 2),
        }

    def obtener_detalle_venta(self, venta_id: int) -> list[dict[str, Any]]:
        """Líneas de detalle de una venta guardada (para el visor)."""
        return self._venta_repo.listar_detalle(venta_id)

    def imprimir_venta_guardada(self, venta_id: int) -> str | None:
        """Reimprime el ticket y (si existe) el comprobante fiscal de una venta.

        Returns:
            Ruta conjunta de los archivos generados, o ``None`` si no existe.
        """
        venta = self._venta_repo.obtener_por_id(venta_id)
        if venta is None:
            return None

        lineas = self._venta_repo.listar_detalle(venta_id)
        # Leer pagos divididos (si los hay) para reimpresión fiel.
        pagos = self._venta_repo.listar_pagos(venta_id)
        if not pagos:
            # Fallback legacy: construir lista única desde campos de la venta.
            pagos = [{
                "metodo_pago": str(venta["metodo_pago"]),
                "monto": round(float(venta["monto_recibido"]), 2),
                "vuelto": round(float(venta["vuelto"]), 2),
            }]

        subtotal = round(float(venta["subtotal"]), 2)
        total = round(float(venta["total"]), 2)
        cliente = (venta.get("cliente") or "").strip() or "CONSUMIDOR FINAL"
        primer_pago = pagos[0]
        # Desglose tributario guardado (las ventas anteriores al IGV quedan en 0:
        # en ese caso no se muestra y la boleta cae al "Subtotal" de siempre).
        base_gravada = round(float(venta.get("base_gravada") or 0), 2)
        igv = round(float(venta.get("igv") or 0), 2)
        exonerado = round(float(venta.get("exonerado") or 0), 2)
        inafecto = round(float(venta.get("inafecto") or 0), 2)
        tiene_desglose = any(v > 0 for v in (base_gravada, igv, exonerado, inafecto))

        # Comprobante fiscal guardado. Las ventas antiguas (sin tipo) se
        # reimprimen como ticket interno: nunca tuvieron documento fiscal.
        tipo = normalizar_tipo_comprobante(venta.get("tipo_comprobante"))
        serie_doc = str(venta.get("serie_comprobante") or "").strip()
        correlativo_doc = int(venta.get("correlativo_comprobante") or 0)
        emite_fiscal = bool(serie_doc and correlativo_doc)

        # Hash oficial del comprobante (si ya se emitió ante SUNAT) para el
        # "Resumen" de la representación impresa.
        hash_cpe = ""
        if emite_fiscal:
            comprobante = self._comprobante_repo.obtener_por_venta(venta_id)
            if comprobante:
                hash_cpe = str(comprobante.get("hash_cpe") or "")

        ticket = TicketVenta(
            # "Una venta = un solo documento": para boleta/factura NO hay ticket interno
            numero_ticket=(
                f"{venta['serie']}-{int(venta['correlativo']):06d}"
                if venta.get("serie") and venta.get("correlativo")
                else self._numero_comprobante(venta) or "-"
            ),
            fecha=self._fecha_venta(venta.get("creado_en")),
            lineas=[
                LineaTicket(
                    cantidad=float(f["cantidad"]),
                    unidad_medida="",
                    descripcion=str(f["descripcion"]),
                    precio_unitario=float(f["precio_unitario"]),
                    importe=round(float(f["subtotal"]), 2),
                    unidad_sunat=str(f.get("unidad_sunat") or ""),
                )
                for f in lineas
            ],
            total=total,
            metodo_pago=primer_pago["metodo_pago"],
            monto_recibido=primer_pago["monto"],
            vuelto=primer_pago.get("vuelto", 0.0),
            subtotal=subtotal,
            redondeo=round(total - subtotal, 2),
            base_gravada=base_gravada if tiene_desglose else None,
            igv=igv if tiene_desglose else None,
            exonerado=exonerado if tiene_desglose else None,
            inafecto=inafecto if tiene_desglose else None,
            tipo_documento=DOCUMENTO_IMPRESO[tipo],
            numero_documento=(
                f"{serie_doc}-{correlativo_doc:06d}" if emite_fiscal else None
            ),
            cliente=cliente,
            dni_cliente=(venta.get("dni_cliente") or "").strip(),
            razon_social=self._empresa["razon_social"],
            ruc=self._empresa["ruc"],
            direccion=self._empresa["direccion"],
            telefono=self._empresa["telefono"],
            hash_cpe=hash_cpe,
            entorno=self._entorno_sunat(),
        )
        ruta_ticket = self._impresora.imprimir(ticket)
        rutas = [str(ruta_ticket)]
        if emite_fiscal:
            rutas.append(str(self._impresora.imprimir_comprobante(ticket)))
        logger.info("Venta %s reimpresa (%s, %d pagos)",
                    ticket.numero_ticket, " + ".join(rutas), len(pagos))
        return "\n".join(rutas)

    # ------------------------------------------------------------------
    # Cierre de día: generar PDF y eliminar ventas
    # ------------------------------------------------------------------
    def cerrar_dia(self, fecha: str | None = None) -> tuple[str, int, int]:
        """Genera el PDF del día y limpia las ventas y compras de esa fecha.

        Args:
            fecha: ``'YYYY-MM-DD'``. Si es ``None``, usa la fecha local de hoy.

        Returns:
            Tupla ``(ruta_pdf, ventas_eliminadas, compras_eliminadas)``.

        Raises:
            ErrorConexionBD: si falla la base de datos.
            RuntimeError: si fpdf2 no está instalado.
        """
        if FPDF is None:
            raise RuntimeError(
                "La librería fpdf2 no está instalada. "
                "Ejecute: pip install fpdf2"
            )

        fecha = fecha or datetime.now().strftime("%Y-%m-%d")

        # 1. Obtener todas las ventas del día con detalle completo
        filas = self._venta_repo.listar_por_fecha(fecha)
        if not filas:
            raise ValueError(f"No hay ventas registradas para el día {fecha}.")

        # 1b. Compras (ingresos de proveedores) del mismo día, para el reporte.
        compras = self._compra_repo.listar_por_fecha(fecha)

        # 2. Generar PDF
        pdf_path = self._generar_pdf_cierre_dia(fecha, filas, compras)

        # 3. Eliminar ventas (cascade borra detalle y pagos)
        eliminadas = self._venta_repo.eliminar_por_fecha(fecha)

        # 4. Eliminar las compras del día (ya archivadas en el PDF). Así, si se
        #    reabre un turno el mismo día, el arqueo no las vuelve a descontar.
        compras_eliminadas = self._compra_repo.eliminar_por_fecha(fecha)

        logger.info(
            "Cierre de día %s: PDF en %s, %d ventas y %d compras eliminadas.",
            fecha, pdf_path, eliminadas, compras_eliminadas,
        )
        return pdf_path, eliminadas, compras_eliminadas

    def _generar_pdf_cierre_dia(
        self,
        fecha: str,
        filas: list[dict[str, Any]],
        compras: list[dict[str, Any]] | None = None,
    ) -> str:
        """Genera el reporte PDF de cierre de día (ventas + compras)."""
        from pathlib import Path

        class PDF(FPDF):
            def header(self):
                self.set_font("Helvetica", "B", 14)
                self.cell(0, 8, "LA CASA DEL PAN", align="C", new_x="LMARGIN", new_y="NEXT")
                self.set_font("Helvetica", "", 10)
                self.cell(0, 6, "CIERRE DE DÍA", align="C", new_x="LMARGIN", new_y="NEXT")
                self.line(10, self.get_y(), 200, self.get_y())
                self.ln(4)

            def footer(self):
                self.set_y(-15)
                self.set_font("Helvetica", "I", 8)
                self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")

        pdf = PDF()
        # Las fuentes base (Helvetica) usan latin-1 y revientan con caracteres
        # que sí existen en español (tildes, "—", "…"). Windows y los datos del
        # negocio usan cp1252: se declara para que ningún nombre (categoría,
        # producto, cliente) tumbe la generación del PDF.
        pdf.core_fonts_encoding = "cp1252"
        pdf.alias_nb_pages()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=20)

        # Tasa de IGV vigente: se lee del catálogo de impuestos (no va fija).
        tasa_igv = self._tasa_igv_vigente()

        # Datos del negocio
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"Razón Social: {self._empresa['razon_social']}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 5, f"RUC: {self._empresa['ruc']}", new_x="LMARGIN", new_y="NEXT")
        if self._empresa["direccion"]:
            pdf.cell(0, 5, f"Dirección: {self._empresa['direccion']}", new_x="LMARGIN", new_y="NEXT")
        if self._empresa["telefono"]:
            pdf.cell(0, 5, f"Teléfono: {self._empresa['telefono']}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        # Fecha y caja
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, f"Fecha: {fecha[8:10]}/{fecha[5:7]}/{fecha[0:4]}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, "Caja: 1", new_x="LMARGIN", new_y="NEXT")
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)

        # Tabla de ventas. ``Comprobante`` es el documento fiscal emitido
        # (boleta/factura) y ``Ticket`` la copia interna de control.
        pdf.set_font("Helvetica", "B", 8)
        col_w = [24, 18, 12, 44, 14, 20, 22, 22]
        headers = ["Comprobante", "Ticket", "Hora", "Producto", "Items", "Método", "Subtotal", "Total"]
        for w, h in zip(col_w, headers):
            pdf.cell(w, 6, h, border=1, align="C")
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        total_vendido = 0.0
        por_metodo: dict[str, float] = {}
        # Acumuladores del desglose tributario del día (base imponible e IGV).
        total_base_gravada = 0.0
        total_igv = 0.0
        total_exonerado = 0.0
        total_inafecto = 0.0
        # Comprobantes fiscales emitidos (para el resumen diario de SUNAT).
        cuenta_comprobantes: dict[str, int] = {}

        for v in filas:
            # "Una venta = un solo documento": para boleta/factura, serie/correlativo (ticket) son NULL
            # Usamos el comprobante fiscal como identificador principal
            if v.get("serie") and v.get("correlativo"):
                ticket = f"{v['serie']}-{int(v['correlativo']):06d}"
            else:
                ticket = self._numero_comprobante(v) or "-"
            tipo = str(v.get("tipo_comprobante") or TIPO_TICKET)
            comprobante = self._numero_comprobante(v) or "-"
            if comprobante != "-":
                cuenta_comprobantes[tipo] = cuenta_comprobantes.get(tipo, 0) + 1
            hora = (v.get("creado_en") or "")[11:16] if v.get("creado_en") else "—"
            producto = v.get("primer_producto") or "—"
            items = int(v.get("items") or 0)
            metodo = v.get("metodo_pago") or "—"
            subtotal = round(float(v.get("subtotal") or 0), 2)
            total = round(float(v.get("total") or 0), 2)

            total_vendido += total
            por_metodo[metodo] = round(por_metodo.get(metodo, 0.0) + total, 2)
            total_base_gravada += float(v.get("base_gravada") or 0)
            total_igv += float(v.get("igv") or 0)
            total_exonerado += float(v.get("exonerado") or 0)
            total_inafecto += float(v.get("inafecto") or 0)

            # Truncar producto si muy largo
            if len(producto) > 24:
                producto = producto[:23] + "…"

            pdf.cell(col_w[0], 5, comprobante, border=1, align="C")
            pdf.cell(col_w[1], 5, ticket, border=1, align="C")
            pdf.cell(col_w[2], 5, hora, border=1, align="C")
            pdf.cell(col_w[3], 5, producto, border=1, align="L")
            pdf.cell(col_w[4], 5, str(items), border=1, align="C")
            pdf.cell(col_w[5], 5, metodo, border=1, align="C")
            pdf.cell(col_w[6], 5, f"S/ {subtotal:,.2f}", border=1, align="R")
            pdf.cell(col_w[7], 5, f"S/ {total:,.2f}", border=1, align="R")
            pdf.ln()

        # Totales
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)
        total_monetario = round(
            sum(m for met, m in por_metodo.items() if metodo_es_monetario(met)), 2
        )
        total_no_monetario = round(total_vendido - total_monetario, 2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, f"Total Ventas: {len(filas)}", new_x="LMARGIN", new_y="NEXT")
        if cuenta_comprobantes:
            partes = [
                f"{cantidad} {ETIQUETA_COMPROBANTE.get(tipo, tipo).lower()}"
                for tipo, cantidad in sorted(cuenta_comprobantes.items())
            ]
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(
                0, 7,
                "Comprobantes emitidos: " + ", ".join(partes),
                new_x="LMARGIN", new_y="NEXT",
            )
            pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, f"Total Vendido: S/ {total_vendido:,.2f}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 7, f"Recaudado (efectivo + digital): S/ {total_monetario:,.2f}", new_x="LMARGIN", new_y="NEXT")
        if total_no_monetario:
            pdf.cell(0, 7, f"Consumo propio / no monetario: S/ {total_no_monetario:,.2f}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Desglose por método:", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        for metodo, monto in sorted(por_metodo.items()):
            pdf.cell(0, 6, f"  {metodo}: S/ {monto:,.2f}", new_x="LMARGIN", new_y="NEXT")

        # Desglose por categoría (Panes, Abarrotes, Cafetería…). La suma de los
        # subtotales del detalle puede diferir del total cobrado por el redondeo
        # a S/ 0.10, por eso se muestra el ajuste como línea aparte.
        desglose = self._desglose_categorias(fecha, round(total_vendido, 2))
        if desglose["por_categoria"]:
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 6, "Desglose por categoría:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            for cat in desglose["por_categoria"]:
                pdf.cell(
                    0, 6,
                    f"  {cat['categoria']}: S/ {cat['subtotal']:,.2f}",
                    new_x="LMARGIN", new_y="NEXT",
                )
            if abs(desglose["ajuste_redondeo"]) >= 0.01:
                pdf.cell(
                    0, 6,
                    f"  Ajuste por redondeo: S/ {desglose['ajuste_redondeo']:,.2f}",
                    new_x="LMARGIN", new_y="NEXT",
                )
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(
                0, 6, f"  Total: S/ {round(total_vendido, 2):,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )

        # Resumen tributario del día (IGV): es el desglose que se declara a
        # SUNAT. La suma de base + IGV + exonerado + inafecto es el subtotal
        # del día; sumado al redondeo de efectivo debe cuadrar con lo cobrado.
        base_gravada = round(total_base_gravada, 2)
        igv = round(total_igv, 2)
        exonerado = round(total_exonerado, 2)
        inafecto = round(total_inafecto, 2)
        subtotal_tributario = round(base_gravada + igv + exonerado + inafecto, 2)
        redondeo_dia = round(round(total_vendido, 2) - subtotal_tributario, 2)

        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "Resumen tributario (IGV):", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        if base_gravada or igv:
            pdf.cell(
                0, 6, f"  Operaciones gravadas (base imponible): S/ {base_gravada:,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
            pdf.cell(
                0, 6, f"  IGV ({tasa_igv * 100:.0f}%): S/ {igv:,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        if exonerado:
            pdf.cell(
                0, 6, f"  Operaciones exoneradas: S/ {exonerado:,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        if inafecto:
            pdf.cell(
                0, 6, f"  Operaciones inafectas: S/ {inafecto:,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        if not (base_gravada or igv or exonerado or inafecto):
            pdf.cell(
                0, 6, "  Sin información tributaria (ventas anteriores al IGV).",
                new_x="LMARGIN", new_y="NEXT",
            )
        if abs(redondeo_dia) >= 0.01:
            pdf.cell(
                0, 6, f"  Redondeo de efectivo: S/ {redondeo_dia:,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(
            0, 6, f"  Total cobrado: S/ {round(total_vendido, 2):,.2f}",
            new_x="LMARGIN", new_y="NEXT",
        )

        # Compras del día (ingresos de proveedores). No se eliminan al cerrar el
        # día: forman parte del inventario/kardex; aquí quedan como constancia
        # del gasto que afectó (o no) el arqueo de caja.
        compras = compras or []
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Compras del día", new_x="LMARGIN", new_y="NEXT")

        if compras:
            col_c = [22, 83, 45, 45]  # Hora, Proveedor, Método, Total
            enc_c = ["Hora", "Proveedor", "Método", "Total"]
            pdf.set_font("Helvetica", "B", 8)
            for w, h in zip(col_c, enc_c):
                pdf.cell(w, 6, h, border=1, align="C")
            pdf.ln()

            pdf.set_font("Helvetica", "", 8)
            total_compras = 0.0
            compras_afectan_caja = 0.0
            for c in compras:
                creado = c.get("creado_en") or ""
                hora = str(creado)[11:16] if len(str(creado)) >= 16 else "—"
                proveedor = c.get("proveedor_nombre") or f"Proveedor #{c.get('proveedor_id')}"
                if len(proveedor) > 42:
                    proveedor = proveedor[:41] + "..."
                metodo = c.get("metodo_pago") or "—"
                total = round(float(c.get("total") or 0), 2)
                total_compras += total
                if str(c.get("metodo_pago")) in METODOS_COMPRA_AFECTAN_CAJA:
                    compras_afectan_caja += total

                pdf.cell(col_c[0], 5, hora, border=1, align="C")
                pdf.cell(col_c[1], 5, proveedor, border=1, align="L")
                pdf.cell(col_c[2], 5, metodo, border=1, align="C")
                pdf.cell(col_c[3], 5, f"S/ {total:,.2f}", border=1, align="R")
                pdf.ln()

            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(
                0, 7, f"Total compras ({len(compras)}): S/ {round(total_compras, 2):,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
            pdf.cell(
                0, 7,
                f"Compras que afectan caja (efectivo): S/ {round(compras_afectan_caja, 2):,.2f}",
                new_x="LMARGIN", new_y="NEXT",
            )
        else:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 6, "  Sin compras registradas este día.", new_x="LMARGIN", new_y="NEXT")

        # Guardar
        carpeta = RAIZ_PROYECTO / "reportes"
        carpeta.mkdir(parents=True, exist_ok=True)
        nombre_archivo = f"cierre_{fecha.replace('-', '')}.pdf"
        ruta = carpeta / nombre_archivo
        pdf.output(str(ruta))
        return str(ruta)

    @staticmethod
    def _hora_venta(creado_en: Any) -> str:
        """Devuelve ``'HH:MM'`` a partir del timestamp almacenado (hora local)."""
        try:
            return str(creado_en or "").split(" ")[1][:5]
        except (IndexError, AttributeError):
            return ""

    @staticmethod
    def _fecha_venta(creado_en: Any) -> datetime:
        """Convierte el timestamp almacenado a ``datetime`` (con fallback a ahora)."""
        try:
            return datetime.fromisoformat(str(creado_en))
        except ValueError:
            return datetime.now()

    @staticmethod
    def _numero_comprobante(fila: dict[str, Any]) -> str:
        """Número del comprobante fiscal de una venta ('' si solo es ticket)."""
        serie = str(fila.get("serie_comprobante") or "").strip()
        correlativo = int(fila.get("correlativo_comprobante") or 0)
        return f"{serie}-{correlativo:06d}" if serie and correlativo else ""

    # ------------------------------------------------------------------
    # Caso de uso principal
    # ------------------------------------------------------------------
    def confirmar_venta(
        self,
        carrito: dict[int, dict[str, Any]],
        metodo_pago: str,
        total: float,
        monto_recibido: float | None = None,
        serie: str = SERIE_TICKET,
        cliente: str = "",
        dni_cliente: str = "",
        *,
        pagos: list[dict[str, Any]] | None = None,
        tipo_comprobante: str = TIPO_TICKET,
    ) -> ResultadoVenta:
        """Registra la venta, su detalle y descuenta el stock en una transacción.

        Args:
            carrito: ``{id_producto: {"producto": {...}, "cantidad": float}}``.
            metodo_pago: uno de :data:`METODOS_PAGO` (uso legacy si ``pagos`` es None).
            total: total informado por la interfaz. Se valida contra el detalle.
            monto_recibido: monto entregado por el cliente (efectivo). Si es
                ``None`` se asume el total (pago exacto / no efectivo).
            serie: serie del **ticket interno** de control (``T001``).
            cliente: nombre o razón social para la boleta/factura.
            dni_cliente: DNI o RUC del cliente.
            pagos: lista opcional de diccionarios para **pago dividido** (RF-06).
                Cada dict: ``{"metodo_pago": str, "monto": float, "vuelto": float}``.
                Si se provee, ignora ``metodo_pago``/``monto_recibido`` y usa esta lista.
                La suma de ``monto`` debe ser >= ``total_a_pagar``.
            tipo_comprobante: ``TICKET`` (solo ticket de control), ``BOLETA`` o
                ``FACTURA``. Cada tipo reserva su **propio** correlativo.

        Returns:
            :class:`ResultadoVenta` con el número de ticket y, si corresponde, el
            del comprobante fiscal.

        Raises:
            CarritoVacio: si el carrito no tiene ítems.
            ComprobanteInvalido: si faltan datos del comprobante (DNI/RUC).
            ErrorVenta: si el total no coincide, método inválido, o la suma de
                pagos no cubre el total.
            StockInsuficiente: si algún producto no tiene stock suficiente.
            ErrorConexionBD: si falla la base de datos.
        """
        if not carrito:
            raise CarritoVacio("No se puede cobrar: el carrito está vacío.")

        desglose, lineas_venta = self.calcular_lineas(carrito)
        subtotal = float(desglose.subtotal)
        if abs(float(total) - subtotal) > 0.01:
            raise ErrorVenta(
                f"El total informado (S/ {float(total):.2f}) no coincide con el "
                f"detalle del carrito (S/ {subtotal:.2f})."
            )

        # Redondeo de soles: el total a pagar es múltiplo de S/ 0.10.
        total_a_pagar = float(desglose.total)
        redondeo = float(desglose.redondeo)

        # Comprobante fiscal: valida los datos del cliente (DNI/RUC obligatorio
        # según el tipo y el tope de S/ 700) **antes** de abrir la transacción.
        tipo, cliente, dni_cliente = validar_compra(
            tipo_comprobante, total_a_pagar, cliente, dni_cliente
        )
        serie_comprobante = SERIE_POR_TIPO.get(tipo, "")
        if tipo == TIPO_TICKET:
            serie_comprobante = ""

        # --- Validar y normalizar pagos ---
        if pagos is not None:
            if not pagos:
                raise ErrorVenta("La lista de pagos no puede estar vacía.")
            # Validar cada pago
            for i, p in enumerate(pagos):
                mp = p.get("metodo_pago")
                if mp not in METODOS_PAGO:
                    raise ErrorVenta(f"Pago #{i+1}: método no válido '{mp}'.")
                monto = round(float(p.get("monto", 0)), 2)
                if monto <= 0:
                    raise ErrorVenta(f"Pago #{i+1}: el monto debe ser mayor a cero.")
            # La suma de montos debe cubrir el total (permite vuelto en el último pago efectivo)
            suma_montos = sum(round(float(p.get("monto", 0)), 2) for p in pagos)
            if suma_montos + 0.001 < total_a_pagar:
                raise ErrorVenta(
                    f"La suma de pagos (S/ {suma_montos:.2f}) no cubre el total a pagar (S/ {total_a_pagar:.2f})."
                )
            # Calcular vuelto total (solo aplica si hay efectivo y se pasó monto > total)
            # En pago dividido, el vuelto se distribuye en el último pago en efectivo.
            metodo_principal = "MIXTO"
            # Para compatibilidad con campos legacy de la tabla ventas:
            # monto_recibido = suma de montos, vuelto = suma_montos - total_a_pagar
            recibido_total = suma_montos
            vuelto_total = round(suma_montos - total_a_pagar, 2)
        else:
            # Legacy: pago único
            if metodo_pago not in METODOS_PAGO:
                raise ErrorVenta(f"Método de pago no válido: '{metodo_pago}'.")
            recibido_total = total_a_pagar if monto_recibido is None else round(float(monto_recibido), 2)
            vuelto_total = round(recibido_total - total_a_pagar, 2)
            metodo_principal = metodo_pago
            # Construir lista de pagos para inserción unificada
            pagos = [{"metodo_pago": metodo_pago, "monto": recibido_total, "vuelto": vuelto_total}]

        # ---- TRANSACCIÓN ÚNICA ----
        with self._bd.cursor() as (_conexion, cursor):
            # "Una venta = un solo documento":
            # - TICKET: reserva T001 (ticket interno), NO fiscal
            # - BOLETA: reserva B001 (boleta), NO ticket interno
            # - FACTURA: reserva F001 (factura), NO ticket interno
            if tipo == TIPO_TICKET:
                correlativo = self._serie_repo.siguiente_correlativo(SERIE_TICKET, cursor=cursor)
                correlativo_comprobante = 0
                serie_ticket = SERIE_TICKET
                serie_fiscal = ""
            else:
                # BOLETA o FACTURA: solo serie fiscal, ticket interno queda NULL
                correlativo = 0
                correlativo_comprobante = self._serie_repo.siguiente_correlativo(serie_comprobante, cursor=cursor)
                serie_ticket = None  # NULL en BD
                serie_fiscal = serie_comprobante

            # Un único instante de emisión para la venta y su comprobante: la
            # fecha fiscal no debe saltar de día entre dos llamadas a now().
            momento = datetime.now()
            venta_id = self._venta_repo.crear_venta(
                serie=serie_ticket,
                correlativo=correlativo,
                metodo_pago=metodo_principal,
                subtotal=subtotal,
                total=total_a_pagar,
                monto_recibido=recibido_total,
                vuelto=vuelto_total,
                cliente=cliente,
                dni_cliente=dni_cliente,
                cursor=cursor,
                base_gravada=float(desglose.base_gravada),
                igv=float(desglose.igv),
                exonerado=float(desglose.exonerado),
                inafecto=float(desglose.inafecto),
                redondeo=redondeo,
                tipo_comprobante=tipo,
                serie_comprobante=serie_fiscal,
                correlativo_comprobante=correlativo_comprobante,
                creado_en=momento.strftime("%Y-%m-%d %H:%M:%S"),
            )

            for linea in lineas_venta:
                producto = linea.producto
                cantidad = linea.cantidad
                producto_id = int(producto["id"])

                stock_anterior, stock_resultante = self._descontar_stock(
                    producto, producto_id, cantidad, cursor
                )
                self._venta_repo.agregar_detalle(
                    venta_id,
                    producto,
                    cantidad,
                    cursor=cursor,
                    importe=linea.importe,
                    impuesto=linea.impuesto,
                    base_gravada=linea.base_gravada,
                    igv=linea.igv,
                    unidad_sunat=codigo_unidad_sunat(producto.get("unidad_medida")),
                )
                self._movimiento_repo.registrar(
                    producto_id=producto_id,
                    tipo=TIPO_MOVIMIENTO_SALIDA_VENTA,
                    cantidad=-cantidad,
                    stock_anterior=stock_anterior,
                    stock_resultante=stock_resultante,
                    referencia_tipo="VENTA",
                    referencia_id=venta_id,
                    motivo=f"Venta {serie_ticket or serie_fiscal}-{correlativo or correlativo_comprobante:06d}",
                    cursor=cursor,
                )

            # Insertar pagos divididos (uno por método)
            for p in pagos:
                self._venta_repo.agregar_pago(
                    venta_id=venta_id,
                    metodo_pago=p["metodo_pago"],
                    monto=round(float(p["monto"]), 2),
                    vuelto=round(float(p.get("vuelto", 0)), 2),
                    cursor=cursor,
                )

            # Puente SUNAT: una boleta o factura se registra como comprobante
            # electrónico PENDIENTE **dentro** de la misma transacción (si algo
            # falla, la venta y el comprobante se revierten juntos). El ticket
            # interno NO genera registro fiscal. El payload guarda un snapshot
            # autocontenido (receptor + ítems + totales) para poder generar o
            # reenviar el XML UBL 2.1 aunque el Cierre del Día ya haya borrado
            # la venta y su detalle.
            comprobante_id = 0
            if tipo in (TIPO_BOLETA, TIPO_FACTURA):
                comprobante_id = self._comprobante_repo.crear(
                    self._snapshot_comprobante(
                        tipo=tipo,
                        serie=serie_comprobante,
                        correlativo=correlativo_comprobante,
                        venta_id=venta_id,
                        cliente=cliente,
                        dni_cliente=dni_cliente,
                        desglose=desglose,
                        lineas=lineas_venta,
                        total=total_a_pagar,
                        momento=momento,
                    ),
                    cursor=cursor,
                )

        resultado = ResultadoVenta(
            venta_id=venta_id,
            serie=serie_ticket,
            correlativo=correlativo,
            subtotal=subtotal,
            total=total_a_pagar,
            vuelto=vuelto_total,
            items=len(carrito),
            redondeo=redondeo,
            base_gravada=float(desglose.base_gravada),
            igv=float(desglose.igv),
            exonerado=float(desglose.exonerado),
            inafecto=float(desglose.inafecto),
            tipo_comprobante=tipo,
            serie_comprobante=serie_fiscal,
            correlativo_comprobante=correlativo_comprobante,
            cliente=cliente,
            dni_cliente=dni_cliente,
            comprobante_electronico_id=comprobante_id,
        )
        logger.info(
            "Venta registrada %s | %s %s | subtotal=S/ %.2f | base=S/ %.2f | IGV=S/ %.2f | "
            "total=S/ %.2f | redondeo=S/ %.2f | metodo=%s (%d pagos)",
            resultado.numero_ticket,
            tipo,
            resultado.numero_comprobante or "—",
            resultado.subtotal,
            resultado.base_gravada,
            resultado.igv,
            resultado.total,
            resultado.redondeo,
            metodo_principal,
            len(pagos),
        )

        # Emisión electrónica, en tres pasos y tras el COMMIT (todo *best effort*):
        #   1) preparar → genera/firma/archiva el XML (local, sin red) y devuelve
        #      el hash, para poder imprimirlo en el "Resumen" del comprobante;
        #   2) imprimir;
        #   3) enviar a SUNAT (red). Un fallo (sin internet) NO afecta la venta:
        #      el comprobante queda PENDIENTE para reintentar desde "Ventas del Día".
        hash_cpe = self._preparar_sunat(comprobante_id)
        self._imprimir_comprobante(
            resultado, carrito, pagos, cliente, dni_cliente, hash_cpe=hash_cpe
        )
        self._enviar_a_sunat(comprobante_id)

        return resultado

    # ------------------------------------------------------------------
    def _snapshot_comprobante(
        self,
        *,
        tipo: str,
        serie: str,
        correlativo: int,
        venta_id: int,
        cliente: str,
        dni_cliente: str,
        desglose: Desglose,
        lineas: list[LineaVenta],
        total: float,
        momento: datetime,
    ) -> dict[str, Any]:
        """Arma los datos del comprobante electrónico (cabecera + *payload* JSON).

        El **payload** es un snapshot autocontenido (emisor, receptor, totales e
        ítems con unidad SUNAT y desglose por línea). Se guarda tal cual en
        ``comprobantes_electronicos.payload_json`` para poder generar o reenviar
        el XML UBL 2.1 **después** del Cierre del Día, que borra ``ventas`` y
        ``venta_detalle``.

        ``momento`` es el mismo instante con el que se registró la venta, para
        que la fecha de emisión fiscal coincida con la de la operación.
        """
        receptor_tipo = self._tipo_documento_receptor(dni_cliente)
        receptor_num = (dni_cliente or "").strip()

        items: list[dict[str, Any]] = []
        for linea in lineas:
            producto = linea.producto
            impuesto = linea.impuesto or {}
            items.append(
                {
                    "producto_id": int(producto["id"]),
                    "codigo": str(
                        producto.get("codigo_barras") or producto.get("codigo_interno") or ""
                    ),
                    "descripcion": str(producto.get("nombre") or ""),
                    "cantidad": float(linea.cantidad),
                    "unidad_sunat": codigo_unidad_sunat(producto.get("unidad_medida")),
                    "precio_unitario": round(float(producto.get("precio_venta") or 0), 2),
                    "importe": float(linea.importe),
                    "impuesto_id": int(impuesto["id"]) if impuesto.get("id") is not None else None,
                    "tipo_afectacion": str(impuesto.get("tipo_afectacion") or ""),
                    "tasa_impuesto": round(float(impuesto.get("tasa") or 0), 4),
                    "base_gravada": round(float(linea.base_gravada), 2),
                    "igv": round(float(linea.igv), 2),
                }
            )

        payload: dict[str, Any] = {
            "emisor": dict(self._empresa),
            "receptor": {
                "tipo_doc": receptor_tipo,
                "num_doc": receptor_num,
                "nombre": cliente,
            },
            "comprobante": {
                "tipo": tipo,
                "serie": serie,
                "correlativo": int(correlativo),
                "fecha_emision": momento.strftime("%Y-%m-%d"),
                "hora_emision": momento.strftime("%H:%M:%S"),
                "moneda": "PEN",
            },
            "totales": {
                "subtotal": float(desglose.subtotal),
                "base_gravada": float(desglose.base_gravada),
                "igv": float(desglose.igv),
                "exonerado": float(desglose.exonerado),
                "inafecto": float(desglose.inafecto),
                "redondeo": float(desglose.redondeo),
                "total": round(float(total), 2),
            },
            "items": items,
        }

        return {
            "venta_id": int(venta_id),
            "tipo_comprobante": tipo,
            "serie": serie,
            "correlativo": int(correlativo),
            "fecha_emision": payload["comprobante"]["fecha_emision"],
            "receptor_tipo_doc": receptor_tipo,
            "receptor_num_doc": receptor_num,
            "receptor_nombre": cliente,
            "moneda": "PEN",
            "monto_total": round(float(total), 2),
            "monto_igv": float(desglose.igv),
            "estado": ESTADO_PENDIENTE,
            "payload": payload,
        }

    @staticmethod
    def _tipo_documento_receptor(dni_cliente: str) -> str:
        """Tipo de documento del receptor (Catálogo N.º 06 SUNAT).

        ``6`` = RUC, ``1`` = DNI y ``0`` = sin documento (boleta a consumidor
        final que no entrega documento de identidad).
        """
        documento = (dni_cliente or "").strip()
        if not documento:
            return "0"
        if len(documento) == 11:
            return "6"
        return "1"

    # ------------------------------------------------------------------
    def _descontar_stock(
        self,
        producto: dict[str, Any],
        producto_id: int,
        cantidad: float,
        cursor: Any,
    ) -> tuple[float, float]:
        """Descuenta stock enriqueciendo el error con el nombre del producto.

        Returns:
            Tupla ``(stock_anterior, stock_resultante)`` para el kardex.
        """
        try:
            return self._producto_repo.descontar_stock(producto_id, cantidad, cursor=cursor)
        except StockInsuficiente as exc:
            nombre = producto.get("nombre", f"id={producto_id}")
            disponible = exc.disponible if exc.disponible is not None else 0.0
            raise StockInsuficiente(
                producto_id,
                cantidad,
                disponible=disponible,
                mensaje=(
                    f"Stock insuficiente para '{nombre}': "
                    f"se pidieron {cantidad:g} y solo hay {disponible:g}."
                ),
            ) from exc

    # ------------------------------------------------------------------
    def _imprimir_comprobante(
        self,
        resultado: ResultadoVenta,
        carrito: dict[int, dict[str, Any]],
        pagos: list[dict[str, Any]],
        cliente: str = "",
        dni_cliente: str = "",
        hash_cpe: str = "",
    ) -> None:
        """Genera e imprime **un solo documento** según el tipo de comprobante.

        - TICKET: solo ticket interno (T001) → carpeta ``tickets/``
        - BOLETA: solo boleta (B001) → carpeta ``boletas/``
        - FACTURA: solo factura (F001) → carpeta ``facturas/``

        **No** se genera el ticket interno cuando se emite boleta/factura
        (modalidad "Una venta = un solo documento").

        Es *best effort*: cualquier fallo solo se registra en el log; la venta
        ya fue COMMITEADA y el cajero puede seguir con el siguiente cliente.
        """
        try:
            lineas = [
                LineaTicket(
                    cantidad=float(item["cantidad"]),
                    unidad_medida=str(item["producto"].get("unidad_medida") or "UND"),
                    descripcion=str(item["producto"].get("nombre") or ""),
                    precio_unitario=float(item["producto"].get("precio_venta") or 0),
                    importe=float(
                        dos_decimales(
                            a_decimal(item["cantidad"])
                            * a_decimal(item["producto"].get("precio_venta") or 0)
                        )
                    ),
                    unidad_sunat=codigo_unidad_sunat(item["producto"].get("unidad_medida")),
                )
                for item in carrito.values()
            ]
            # Tomamos el primer pago como "principal" para compatibilidad con TicketVenta.
            primer_pago = pagos[0] if pagos else {"metodo_pago": "MIXTO", "monto": resultado.total, "vuelto": resultado.vuelto}

            # Número y tipo de documento a imprimir
            if resultado.tipo_comprobante == TIPO_TICKET:
                # Solo ticket interno
                numero_doc = resultado.numero_ticket
                tipo_doc = TIPO_TICKET
                serie_doc = resultado.serie
                correlativo_doc = resultado.correlativo
            else:
                # Boleta o Factura: usar la serie fiscal
                numero_doc = resultado.numero_comprobante
                tipo_doc = resultado.tipo_comprobante
                serie_doc = resultado.serie_comprobante
                correlativo_doc = resultado.correlativo_comprobante

            ticket = TicketVenta(
                numero_ticket=numero_doc,  # para el nombre del archivo
                fecha=datetime.now(),
                lineas=lineas,
                total=resultado.total,
                metodo_pago=primer_pago["metodo_pago"],
                monto_recibido=primer_pago["monto"],
                vuelto=primer_pago.get("vuelto", 0.0),
                subtotal=resultado.subtotal,
                redondeo=resultado.redondeo,
                base_gravada=resultado.base_gravada,
                igv=resultado.igv,
                exonerado=resultado.exonerado,
                inafecto=resultado.inafecto,
                # Datos del comprobante fiscal
                tipo_documento=DOCUMENTO_IMPRESO[resultado.tipo_comprobante],
                numero_documento=numero_doc,
                cliente=(cliente or "").strip() or "CONSUMIDOR FINAL",
                dni_cliente=(dni_cliente or "").strip(),
                razon_social=self._empresa["razon_social"],
                ruc=self._empresa["ruc"],
                direccion=self._empresa["direccion"],
                telefono=self._empresa["telefono"],
                hash_cpe=hash_cpe,
                entorno=self._entorno_sunat(),
            )

            if resultado.tipo_comprobante == TIPO_TICKET:
                # Ticket interno (T001)
                ruta = self._impresora.imprimir(ticket)
                logger.info("Ticket %s -> %s", resultado.numero_ticket, ruta)
            else:
                # Boleta o Factura (B001 / F001)
                ruta = self._impresora.imprimir_comprobante(ticket)
                logger.info("%s %s -> %s", resultado.tipo_comprobante, numero_doc, ruta)

        except Exception:  # noqa: BLE001 - la impresión no debe tumbar la venta
            logger.exception(
                "No se pudo generar el comprobante %s (la venta ya está registrada).",
                resultado.numero_ticket if resultado.tipo_comprobante == TIPO_TICKET else resultado.numero_comprobante,
            )

    # ------------------------------------------------------------------
    # Emisión electrónica (SUNAT) — delegada al SunatService
    # ------------------------------------------------------------------
    def _preparar_sunat(self, comprobante_id: int) -> str:
        """Genera y firma el XML (local, sin red) y devuelve el ``hash_cpe``.

        Es la primera mitad de la emisión: permite imprimir el comprobante con
        su "Resumen" antes de enviarlo. Silencioso por diseño: devuelve ``""``
        si la emisión está deshabilitada o si falla la preparación (el envío
        posterior lo reintentará).
        """
        if self._sunat is None or not comprobante_id:
            return ""
        try:
            if not self._sunat.habilitado:
                return ""
            return self._sunat.preparar(int(comprobante_id))
        except Exception:  # noqa: BLE001 - nunca debe tumbar la venta
            logger.exception(
                "No se pudo preparar el XML del comprobante id=%s.", comprobante_id
            )
            return ""

    def _entorno_sunat(self) -> str:
        """``beta`` o ``produccion`` (``[sunat] entorno``); por defecto ``beta``."""
        if self._sunat is not None:
            try:
                return str(self._sunat.entorno)
            except Exception:  # noqa: BLE001
                pass
        return "beta"

    def _enviar_a_sunat(self, comprobante_id: int) -> None:
        """Envía a SUNAT el comprobante recién registrado (best effort).

        Silencioso por diseño: la caja no se detiene si falla SUNAT o no hay
        internet; el comprobante queda PENDIENTE y se reintenta luego.
        """
        if self._sunat is None or not comprobante_id:
            return
        try:
            if not self._sunat.habilitado:
                return
            self._sunat.procesar(int(comprobante_id))
        except Exception:  # noqa: BLE001 - SUNAT nunca debe tumbar la venta
            logger.exception("No se pudo enviar el comprobante id=%s a SUNAT.", comprobante_id)

    def sunat_habilitado(self) -> bool:
        """``True`` si la emisión electrónica está activa (``[sunat] habilitado``)."""
        return bool(self._sunat is not None and self._sunat.habilitado)

    def contar_pendientes_sunat(self) -> int:
        """Cuántos comprobantes están pendientes de envío a SUNAT."""
        return self._sunat.contar_pendientes() if self._sunat is not None else 0

    def enviar_pendientes_sunat(self, limite: int = 100) -> list[ResultadoEnvio]:
        """Envía (o reintenta) todos los comprobantes PENDIENTES a SUNAT."""
        if self._sunat is None:
            return []
        return self._sunat.procesar_pendientes(limite=limite)

    def enviar_comprobante_sunat(self, comprobante_id: int) -> ResultadoEnvio:
        """Envía (o reintenta) un comprobante concreto a SUNAT."""
        if self._sunat is None:
            raise ErrorVenta("El servicio de emisión electrónica no está disponible.")
        return self._sunat.procesar(int(comprobante_id))
