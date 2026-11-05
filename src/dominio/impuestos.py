"""Cálculo de impuestos (IGV) — capa de DOMINIO.

Reglas de negocio **puras** (sin SQL ni interfaz): el desglose tributario de una
venta se calcula aquí con :class:`~decimal.Decimal` y ``ROUND_HALF_UP``, de modo
que el resultado coincida *exactamente* con lo que exige un comprobante
electrónico (UBL 2.1) y no arrastre el error de la coma flotante.

Convenio del negocio
--------------------
El **precio de venta ya incluye IGV** (es el precio del cartel y lo que paga el
cliente), por lo que la base se obtiene *quitando* el impuesto::

    base = importe / (1 + tasa)       # 1.18 / 1.18 = 1.00
    igv  = importe - base             # 1.18 - 1.00 = 0.18

Invariantes garantizadas (exactas, al céntimo)
----------------------------------------------
* ``base_gravada + igv + exonerado + inafecto == subtotal``
* ``subtotal + redondeo == total``
* por cada línea: ``base + igv == importe``

La clave para que las dos primeras se cumplan siempre es calcular el **IGV de la
cabecera por resta** (``gravado_total - base_gravada``) y **nunca** sumando los
IGV redondeados de cada línea; el residuo de céntimos se reparte luego con
:func:`desglosar_lineas`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Sequence

#: Un céntimo, la unidad mínima con la que se cuantizan los importes.
CENTIMO = Decimal("0.01")

#: Paso del redondeo de efectivo en Perú (moneda de 10 céntimos).
PASO_REDONDEO = Decimal("0.10")

# --- Catálogo N.º 05 SUNAT: tipos de tributos -----------------------------
CODIGO_IGV = "1000"
CODIGO_EXONERADO = "9997"
CODIGO_INAFECTO = "9998"
CODIGO_ICBPER = "7152"  # Impuesto a las bolsas de plástico (monto fijo por bolsa).

# --- Catálogo N.º 07 SUNAT: tipo de afectación del IGV --------------------
AFECTACION_GRAVADO = "10"     # Gravado - Operación Onerosa
AFECTACION_EXONERADO = "20"   # Exonerado - Operación Onerosa
AFECTACION_INAFECTO = "30"    # Inafecto - Operación Onerosa

#: Tasa del IGV en el Perú (16 % IGV + 2 % IPM = 18 %).
TASA_IGV = Decimal("0.18")


# --------------------------------------------------------------------------
# Conversión y cuantización
# --------------------------------------------------------------------------
def a_decimal(valor: Any, por_defecto: str = "0") -> Decimal:
    """Convierte ``valor`` a ``Decimal`` de forma tolerante.

    Acepta ``None``, cadenas y números. Se construye desde ``str`` para no
    heredar el error binario de los ``float`` (p. ej. ``0.1`` real).
    """
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return Decimal(por_defecto)
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def dos_decimales(valor: Any) -> Decimal:
    """Cuantiza a céntimos con redondeo ``ROUND_HALF_UP`` (mitades hacia arriba)."""
    return a_decimal(valor).quantize(CENTIMO, rounding=ROUND_HALF_UP)


def redondear_soles(total: Any, paso: Any = PASO_REDONDEO) -> Decimal:
    """Redondea al múltiplo más cercano de ``paso``, con las mitades hacia arriba.

    Es el redondeo de efectivo peruano (moneda de S/ 0,10)::

        * S/ 3.54 -> S/ 3.50
        * S/ 3.55 -> S/ 3.60   (la mitad, a favor del negocio)

    Se trabaja con céntimos enteros para no arrastrar el error de la coma
    flotante.
    """
    paso_centimos = int(dos_decimales(paso) * 100)
    if paso_centimos <= 0:
        return dos_decimales(total)
    centimos = int(dos_decimales(total) * 100)
    cociente, resto = divmod(centimos, paso_centimos)
    if resto * 2 >= paso_centimos:
        cociente += 1
    return (Decimal(cociente * paso_centimos) / 100).quantize(CENTIMO)


# --------------------------------------------------------------------------
# Estructuras del desglose
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class LineaImpuesto:
    """Una línea lista para desglosar.

    Attributes:
        importe: importe bruto de la línea (**con** IGV incluido).
        tasa: tasa del impuesto que le corresponde (p. ej. ``0.18``).
        tipo_afectacion: código del Catálogo N.º 07 SUNAT
            (:data:`AFECTACION_GRAVADO`, :data:`AFECTACION_EXONERADO`,
            :data:`AFECTACION_INAFECTO`).
    """

    importe: Decimal
    tasa: Decimal = TASA_IGV
    tipo_afectacion: str = AFECTACION_GRAVADO

    @property
    def es_gravada(self) -> bool:
        """``True`` si la línea está gravada con IGV."""
        return self.tipo_afectacion == AFECTACION_GRAVADO


@dataclass(frozen=True)
class Desglose:
    """Desglose tributario de una venta (todos los valores en ``Decimal``)."""

    subtotal: Decimal
    base_gravada: Decimal
    igv: Decimal
    exonerado: Decimal
    inafecto: Decimal
    redondeo: Decimal = Decimal("0.00")

    @property
    def total(self) -> Decimal:
        """Importe total a pagar (subtotal + redondeo de efectivo)."""
        return self.subtotal + self.redondeo

    @property
    def cuadra(self) -> bool:
        """``True`` si se cumple la identidad tributaria del comprobante."""
        return (
            self.base_gravada + self.igv + self.exonerado + self.inafecto
            == self.subtotal
        )

    def como_flotantes(self) -> dict[str, float]:
        """Devuelve el desglose listo para persistir/mostrar (``float`` de 2 dec.)."""
        return {
            "subtotal": float(self.subtotal),
            "base_gravada": float(self.base_gravada),
            "igv": float(self.igv),
            "exonerado": float(self.exonerado),
            "inafecto": float(self.inafecto),
            "redondeo": float(self.redondeo),
            "total": float(self.total),
        }


# --------------------------------------------------------------------------
# Cálculo
# --------------------------------------------------------------------------
def calcular_desglose(
    lineas: Iterable[LineaImpuesto],
    *,
    redondear_efectivo: bool = True,
) -> Desglose:
    """Calcula el desglose tributario exacto de un conjunto de líneas.

    Args:
        lineas: líneas de la venta (importe con IGV incluido).
        redondear_efectivo: si es ``True`` (por defecto), el total se redondea
            al múltiplo de S/ 0,10 y la diferencia se guarda en ``redondeo``.

    Returns:
        El :class:`Desglose` con la identidad tributaria garantizada:
        ``base_gravada + igv + exonerado + inafecto == subtotal``.
    """
    lista = list(lineas)
    importes = [dos_decimales(linea.importe) for linea in lista]
    subtotal = sum(importes, Decimal("0.00"))

    # Las líneas gravadas se agrupan por tasa (en Perú solo existe el 18 %, pero
    # el modelo admite varias: no se puede dividir la suma por un único factor).
    por_tasa: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0.00"))
    exonerado = Decimal("0.00")
    inafecto = Decimal("0.00")
    for linea, importe in zip(lista, importes):
        if linea.es_gravada:
            por_tasa[dos_decimales(linea.tasa)] += importe
        elif linea.tipo_afectacion == AFECTACION_EXONERADO:
            exonerado += importe
        else:
            inafecto += importe

    base_gravada = Decimal("0.00")
    for tasa, monto in por_tasa.items():
        base_gravada += (monto / (Decimal("1") + tasa)).quantize(
            CENTIMO, rounding=ROUND_HALF_UP
        )
    # El IGV se obtiene por RESTA para que la identidad cuadre al céntimo.
    gravado_total = sum(por_tasa.values(), Decimal("0.00"))
    igv = gravado_total - base_gravada

    total_a_pagar = redondear_soles(subtotal) if redondear_efectivo else subtotal
    redondeo = total_a_pagar - subtotal

    return Desglose(
        subtotal=subtotal,
        base_gravada=base_gravada,
        igv=igv,
        exonerado=exonerado,
        inafecto=inafecto,
        redondeo=redondeo,
    )


def _repartir_centimos(
    importes_cent: Sequence[int],
    base_total_cent: int,
    factor: Decimal,
) -> list[int]:
    """Reparte ``base_total_cent`` entre varias líneas con el *resto mayor*.

    Se parte del valor exacto (``importe / (1 + tasa)``, en céntimos
    fraccionarios), se toma el piso de cada línea y los céntimos que sobran se
    entregan, uno a uno, a las líneas con mayor resto fraccionario. Cada línea
    nunca recibe más céntimos que su propio importe (así el IGV nunca es
    negativo) ni menos de cero.

    Returns:
        Lista de bases en céntimos (enteros) que suma exactamente
        ``base_total_cent``.
    """
    exactos = [Decimal(cent) / factor for cent in importes_cent]
    bases = [int(exacto) for exacto in exactos]
    restos = [exacto - int(exacto) for exacto in exactos]
    capacidades = [cent - base for cent, base in zip(importes_cent, bases)]

    faltante = base_total_cent - sum(bases)
    for indice in sorted(range(len(bases)), key=lambda i: restos[i], reverse=True):
        if faltante <= 0:
            break
        if capacidades[indice] > 0:
            bases[indice] += 1
            capacidades[indice] -= 1
            faltante -= 1

    # Respaldo por si el orden por resto no bastara (empates o recortes): reparte
    # lo que falte mientras quede capacidad, o devuelve céntimos si sobraran.
    while faltante > 0 and any(c > 0 for c in capacidades):
        for indice in range(len(bases)):
            if faltante <= 0:
                break
            if capacidades[indice] > 0:
                bases[indice] += 1
                capacidades[indice] -= 1
                faltante -= 1
    while faltante < 0:
        for indice in sorted(range(len(bases)), key=lambda i: bases[i], reverse=True):
            if faltante == 0:
                break
            if bases[indice] > 0:
                bases[indice] -= 1
                faltante += 1

    return bases


def desglosar_lineas(
    lineas: Sequence[LineaImpuesto],
    desglose: Desglose,
) -> list[tuple[Decimal, Decimal]]:
    """Reparte ``base`` e ``igv`` por línea cuadrando con el desglose de cabecera.

    Redondear la base de cada línea por separado deja un residuo de céntimos
    frente al total (p. ej. dos panes de S/ 0.50: las líneas dan IGV 0.16 y la
    cabecera 0.15; y nueve panes de S/ 0.10 acumulan 4 céntimos de error). Por
    eso la base no se redondea línea a línea: se reparte el total exacto de la
    cabecera con el método del resto mayor, por grupo de tasa. Así:

    * ``sum(base_líneas) == base_gravada`` (exacto)
    * ``sum(igv_líneas) == igv`` (exacto)
    * cada línea sigue cumpliendo ``base + igv == importe``, con ``igv >= 0``

    Returns:
        Lista de ``(base, igv)`` en el mismo orden que ``lineas``.
    """
    lista = list(lineas)
    bases: list[Decimal] = [Decimal("0.00")] * len(lista)
    igvs: list[Decimal] = [Decimal("0.00")] * len(lista)

    grupos: dict[Decimal, list[int]] = defaultdict(list)
    for indice, linea in enumerate(lista):
        importe = dos_decimales(linea.importe)
        if linea.es_gravada and linea.tasa > 0:
            grupos[dos_decimales(linea.tasa)].append(indice)
        else:
            # Exonerado/inafecto: todo el importe es base y no hay IGV.
            bases[indice] = importe

    for tasa, indices in grupos.items():
        factor = Decimal("1") + tasa
        importes_cent = [int(dos_decimales(lista[i].importe) * 100) for i in indices]
        base_total_cent = int(
            (Decimal(sum(importes_cent)) / 100 / factor).quantize(
                CENTIMO, rounding=ROUND_HALF_UP
            )
            * 100
        )
        for i, base_cent in zip(indices, _repartir_centimos(importes_cent, base_total_cent, factor)):
            bases[i] = (Decimal(base_cent) / 100).quantize(CENTIMO)
            igvs[i] = dos_decimales(lista[i].importe) - bases[i]

    return list(zip(bases, igvs))


def linea_desde_impuesto(importe: Any, impuesto: dict[str, Any] | None) -> LineaImpuesto:
    """Construye una :class:`LineaImpuesto` a partir del importe y su impuesto.

    Si no se conoce el impuesto (producto sin asignar), se asume **IGV 18 %
    gravado**, que es el caso por defecto del negocio.
    """
    if not impuesto:
        return LineaImpuesto(importe=dos_decimales(importe))
    return LineaImpuesto(
        importe=dos_decimales(importe),
        tasa=dos_decimales(impuesto.get("tasa") or 0),
        tipo_afectacion=str(impuesto.get("tipo_afectacion") or AFECTACION_GRAVADO),
    )
