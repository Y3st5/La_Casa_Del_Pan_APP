"""Pruebas del dimensionado de la ventana principal.

El piloto corre en mostrador con monitores de 1366x768: la ventana debe abrir
completa (sin quedar por debajo de la barra de tareas) y nunca pedir más ancho
del que ofrece la pantalla.
"""

from __future__ import annotations

import main as app_main


def test_pantalla_grande_usa_tamano_de_diseno() -> None:
    """1920x1080 con barra de tareas: sobra espacio, se usa el diseño."""
    assert app_main.calcular_tamano_inicial(1920, 1040) == (1320, 700)


def test_1366x768_entra_completa() -> None:
    """Caso crítico del piloto: área útil 1366x728."""
    ancho, alto = app_main.calcular_tamano_inicial(1366, 728)
    assert (ancho, alto) == (1320, 680)
    assert ancho <= 1366
    assert alto <= 728


def test_1280x720_se_ajusta_al_ancho() -> None:
    ancho, alto = app_main.calcular_tamano_inicial(1280, 680)
    assert ancho == 1256
    assert ancho <= 1280
    assert alto == 632


def test_pantalla_minuscula_no_desborda() -> None:
    """En pantallas menores al mínimo, la ventana no excede la pantalla."""
    ancho, alto = app_main.calcular_tamano_inicial(1024, 600)
    assert ancho <= 1024
    assert alto <= 600


def test_sin_area_util_reserva_la_barra_de_tareas() -> None:
    """Si no se pudo leer el área útil, se descuenta un margen extra."""
    _ancho, alto = app_main.calcular_tamano_inicial(1366, 768, area_util=False)
    assert alto <= 672
    assert alto >= app_main.MIN_HEIGHT


def test_el_minimo_nunca_supera_el_tamano_inicial() -> None:
    for pantalla in ((1920, 1040), (1366, 728), (1280, 680), (1024, 600)):
        ancho, alto = app_main.calcular_tamano_inicial(*pantalla)
        assert ancho >= min(app_main.MIN_WIDTH, pantalla[0])
        assert alto >= min(app_main.MIN_HEIGHT, pantalla[1])
