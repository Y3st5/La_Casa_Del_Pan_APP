"""Pruebas del servicio de ventas.

Cubren la lógica **pura** del servicio (redondeo de soles) y verifican que la
dependencia usada por el "Cierre de Día" (``fpdf2``) esté disponible. No
requieren interfaz gráfica ni datos previos en la base de datos.
"""

from __future__ import annotations

import pytest

from src.servicios.venta_service import METODOS_PAGO, redondear_total


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (0.0, 0.0),
        (1.0, 1.0),
        (3.50, 3.5),
        (3.54, 3.5),
        (3.55, 3.6),
        (3.56, 3.6),
        (0.04, 0.0),
        (2.999, 3.0),
        (0.25, 0.30),
    ],
)
def test_redondear_total_a_diez_centimos(entrada: float, esperado: float) -> None:
    assert redondear_total(entrada) == esperado


def test_redondeo_siempre_es_multiplo_de_diez_centimos() -> None:
    for valor in (0.01, 1.23, 7.77, 19.99, 123.45):
        resultado = round(redondear_total(valor) * 100)
        assert resultado % 10 == 0


def test_metodos_pago_incluye_efectivo() -> None:
    assert "Efectivo" in METODOS_PAGO


def test_fpdf2_disponible_para_cierre_de_dia() -> None:
    """El Cierre de Día genera PDF: la dependencia debe estar instalada."""
    from src.servicios import venta_service

    assert venta_service.FPDF is not None
