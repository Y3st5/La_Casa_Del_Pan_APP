"""Recursos gráficos generados con Pillow — capa de PRESENTACIÓN.

Genera el **logo de la cesta** y un **set de iconos vectoriales** dibujados con
Pillow (ya instalado), sin depender de fuentes de iconos externas ni de
archivos de imagen versionados. Todo se rasteriza con supermuestreo (4× y
reescalado LANCZOS) para que se vea nítido en pantalla.

Uso::

    from src.presentacion.recursos import icono, logo_ctk
    boton = ctk.CTkButton(..., image=icono("mas", 18, estilos.COLOR_TEXTO))

Los resultados se **cachean** (``lru_cache``): pedir el mismo icono dos veces
devuelve el mismo objeto.
"""

from __future__ import annotations

import functools

from PIL import Image, ImageDraw

from src.presentacion.estilos import (
    BORDE,
    COLOR_ACENTO,
    COLOR_TEXTO,
    FAMILIA,
)

#: Factor de supermuestreo (dibujamos grande y reducimos → bordes suaves).
_FACTOR = 4


# ======================================================================
#  Utilidades de dibujo (coordenadas normalizadas 0..100)
# ======================================================================
def _p(valor: float, tam: int) -> float:
    """Convierte una coordenada normalizada (0..100) a píxeles."""
    return valor * tam / 100.0


def _lw(tam: int) -> int:
    """Ancho de trazo proporcional al tamaño."""
    return max(2, round(tam * 0.085))


def _trazo(d: ImageDraw.ImageDraw, tam: int, color: str, puntos, ancho: int | None = None, cerrado: bool = False) -> None:
    coords = [(_p(x, tam), _p(y, tam)) for x, y in puntos]
    if cerrado and coords:
        coords.append(coords[0])
    d.line(coords, fill=color, width=ancho or _lw(tam), joint="curve")


def _elipse(d: ImageDraw.ImageDraw, tam: int, color: str, cx: float, cy: float, r: float, ancho: int | None = None, relleno: str | None = None) -> None:
    caja = [_p(cx - r, tam), _p(cy - r, tam), _p(cx + r, tam), _p(cy + r, tam)]
    d.ellipse(caja, outline=color, width=ancho or _lw(tam), fill=relleno)


def _rect_redondeado(d: ImageDraw.ImageDraw, tam: int, color: str, x0: float, y0: float, x1: float, y1: float, r: float, ancho: int | None = None, relleno: str | None = None) -> None:
    d.rounded_rectangle(
        [_p(x0, tam), _p(y0, tam), _p(x1, tam), _p(y1, tam)],
        radius=_p(r, tam),
        outline=color,
        width=ancho or _lw(tam),
        fill=relleno,
    )


# ======================================================================
#  Iconos (estilo línea)
# ======================================================================
def _ic_cesta(d, tam, c):
    """Cesta de compra (logo y módulo de ventas)."""
    _trazo(d, tam, c, [(17, 46), (83, 46), (72, 87), (28, 87)], cerrado=True)
    d.arc(
        [_p(30, tam), _p(16, tam), _p(70, tam), _p(60, tam)],
        start=200, end=340, fill=c, width=_lw(tam),
    )
    _trazo(d, tam, c, [(43, 55), (39, 79)], ancho=max(2, _lw(tam) - 1))
    _trazo(d, tam, c, [(57, 55), (61, 79)], ancho=max(2, _lw(tam) - 1))


def _ic_carrito(d, tam, c):
    _trazo(d, tam, c, [(9, 17), (24, 17), (34, 57)])
    _trazo(d, tam, c, [(34, 31), (79, 31), (71, 57), (34, 57)], cerrado=True)
    _elipse(d, tam, c, 42, 78, 7, relleno=c)
    _elipse(d, tam, c, 68, 78, 7, relleno=c)


def _ic_caja(d, tam, c):
    """Caja en perspectiva (inventario)."""
    _trazo(d, tam, c, [(50, 16), (84, 35), (50, 54), (16, 35)], cerrado=True)
    _trazo(d, tam, c, [(16, 35), (16, 71)])
    _trazo(d, tam, c, [(84, 35), (84, 71)])
    _trazo(d, tam, c, [(16, 71), (50, 90), (84, 71)])
    _trazo(d, tam, c, [(50, 54), (50, 90)], ancho=max(2, _lw(tam) - 1))


def _ic_camion(d, tam, c):
    _rect_redondeado(d, tam, c, 8, 34, 56, 72, 4)
    _trazo(d, tam, c, [(56, 46), (76, 46), (90, 60), (90, 72), (56, 72)], cerrado=True)
    _elipse(d, tam, c, 27, 78, 9, relleno=c)
    _elipse(d, tam, c, 72, 78, 9, relleno=c)


def _ic_recibo(d, tam, c):
    """Comprobante / recibo (ventas del día)."""
    _trazo(d, tam, c, [
        (27, 15), (73, 15), (73, 80), (65, 86), (57, 80), (49, 86),
        (41, 80), (33, 86), (27, 80),
    ], cerrado=True)
    for y in (31, 43, 55):
        _trazo(d, tam, c, [(37, y), (63, y)], ancho=max(2, _lw(tam) - 1))


def _ic_mas(d, tam, c):
    _trazo(d, tam, c, [(50, 24), (50, 76)], ancho=max(3, round(_lw(tam) * 1.2)))
    _trazo(d, tam, c, [(24, 50), (76, 50)], ancho=max(3, round(_lw(tam) * 1.2)))


def _ic_chevron(d, tam, c):
    """Flecha hacia abajo (menú desplegable)."""
    _trazo(d, tam, c, [(33, 40), (50, 58), (67, 40)], ancho=max(3, round(_lw(tam) * 1.2)))


def _ic_lapiz(d, tam, c):
    """Lápiz (editar)."""
    _trazo(d, tam, c, [(32, 80), (72, 40), (60, 28), (20, 68)], cerrado=True)
    _trazo(d, tam, c, [(54, 34), (66, 46)], ancho=max(2, _lw(tam) - 1))
    d.polygon(
        [_p(20, tam), _p(68, tam), _p(32, tam), _p(80, tam), _p(16, tam), _p(84, tam)],
        fill=c,
    )


def _ic_papelera(d, tam, c):
    _trazo(d, tam, c, [(20, 30), (80, 30)])
    _trazo(d, tam, c, [(40, 30), (40, 19), (60, 19), (60, 30)])
    _trazo(d, tam, c, [(28, 30), (32, 86), (68, 86), (72, 30)])
    for x in (43, 57):
        _trazo(d, tam, c, [(x, 42), (x, 74)], ancho=max(2, _lw(tam) - 1))


def _ic_recargar(d, tam, c):
    w = _lw(tam)
    d.arc([_p(22, tam), _p(22, tam), _p(78, tam), _p(78, tam)], start=40, end=330, fill=c, width=w)
    _trazo(d, tam, c, [(66, 16), (82, 30), (76, 48)], ancho=w)


def _ic_calendario(d, tam, c):
    _rect_redondeado(d, tam, c, 17, 27, 83, 84, 6)
    _trazo(d, tam, c, [(17, 43), (83, 43)])
    _trazo(d, tam, c, [(32, 19), (32, 33)])
    _trazo(d, tam, c, [(68, 19), (68, 33)])
    for cx in (34, 50, 66):
        for cy in (57, 71):
            _elipse(d, tam, c, cx, cy, 3.2, ancho=1, relleno=c)


def _ic_carpeta(d, tam, c):
    _trazo(d, tam, c, [(15, 34), (41, 34), (49, 45), (85, 45), (85, 83), (15, 83)], cerrado=True)


def _ic_lupa(d, tam, c):
    _elipse(d, tam, c, 44, 43, 24)
    _trazo(d, tam, c, [(61, 60), (83, 82)])


def _ic_efectivo(d, tam, c):
    _rect_redondeado(d, tam, c, 12, 31, 88, 69, 6)
    _elipse(d, tam, c, 50, 50, 12)
    _elipse(d, tam, c, 50, 50, 2.6, ancho=1, relleno=c)


def _ic_codigo_barras(d, tam, c):
    """Código de barras estilizado (7 barras, 2 grosores)."""
    barras = [(22, 2.5), (31, 5.0), (40, 2.5), (49, 5.0), (58, 2.5), (67, 5.0), (76, 2.5)]
    for x, grueso in barras:
        ancho = max(1, round(grueso * tam / 100))
        _trazo(d, tam, c, [(x, 26), (x, 74)], ancho=ancho)


def _ic_salir(d, tam, c):
    _trazo(d, tam, c, [(60, 20), (26, 20), (26, 80), (60, 80)])
    _trazo(d, tam, c, [(50, 50), (86, 50)])
    _trazo(d, tam, c, [(72, 38), (86, 50), (72, 62)])


def _ic_poder(d, tam, c):
    """Encendido / apagado (activar-desactivar)."""
    w = _lw(tam)
    d.arc([_p(18, tam), _p(16, tam), _p(82, tam), _p(80, tam)], start=290, end=250, fill=c, width=w)
    _trazo(d, tam, c, [(50, 12), (50, 52)], ancho=max(3, round(w * 1.1)))


def _ic_usuario(d, tam, c):
    _elipse(d, tam, c, 50, 34, 16)
    _trazo(d, tam, c, [(20, 86), (24, 66), (50, 58), (76, 66), (80, 86)])


def _ic_grafico(d, tam, c):
    _trazo(d, tam, c, [(18, 16), (18, 84), (86, 84)])
    _trazo(d, tam, c, [(34, 84), (34, 58)], ancho=max(3, round(_lw(tam) * 1.4)))
    _trazo(d, tam, c, [(52, 84), (52, 40)], ancho=max(3, round(_lw(tam) * 1.4)))
    _trazo(d, tam, c, [(70, 84), (70, 26)], ancho=max(3, round(_lw(tam) * 1.4)))


def _ic_ingreso(d, tam, c):
    """Flecha que baja hacia una caja (ingreso de mercadería / compras)."""
    _trazo(d, tam, c, [(50, 14), (50, 52)])
    _trazo(d, tam, c, [(34, 38), (50, 54), (66, 38)])
    _trazo(d, tam, c, [(20, 62), (20, 84), (80, 84), (80, 62)])


def _ic_etiqueta(d, tam, c):
    """Etiqueta / tag (categorías del catálogo)."""
    _trazo(d, tam, c, [
        (20, 30), (60, 30), (85, 55), (60, 80), (20, 80),
    ], cerrado=True)
    _elipse(d, tam, c, 66, 55, 6, ancho=max(2, _lw(tam) - 1))


_ICONOS = {
    "cesta": _ic_cesta,
    "carrito": _ic_carrito,
    "caja": _ic_caja,
    "camion": _ic_camion,
    "recibo": _ic_recibo,
    "mas": _ic_mas,
    "chevron": _ic_chevron,
    "lapiz": _ic_lapiz,
    "papelera": _ic_papelera,
    "recargar": _ic_recargar,
    "calendario": _ic_calendario,
    "carpeta": _ic_carpeta,
    "lupa": _ic_lupa,
    "efectivo": _ic_efectivo,
    "codigo_barras": _ic_codigo_barras,
    "salir": _ic_salir,
    "poder": _ic_poder,
    "usuario": _ic_usuario,
    "grafico": _ic_grafico,
    "ingreso": _ic_ingreso,
    "etiqueta": _ic_etiqueta,
}

NOMBRES_ICONOS = tuple(_ICONOS)


# ======================================================================
#  API pública
# ======================================================================
@functools.lru_cache(maxsize=512)
def imagen_icono(nombre: str, tamano: int = 22, color: str = COLOR_TEXTO) -> Image.Image:
    """Devuelve un icono como imagen PIL (transparente)."""
    dibujante = _ICONOS.get(nombre)
    if dibujante is None:
        raise KeyError(f"Icono desconocido: {nombre!r}. Disponibles: {NOMBRES_ICONOS}")
    grande = max(16, tamano * _FACTOR)
    lienzo = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
    dibujante(ImageDraw.Draw(lienzo), grande, color)
    return lienzo.resize((tamano, tamano), Image.LANCZOS)


@functools.lru_cache(maxsize=512)
def icono(nombre: str, tamano: int = 22, color: str = COLOR_TEXTO):
    """Devuelve un icono como ``CTkImage`` listo para botones y etiquetas."""
    import customtkinter as ctk

    imagen = imagen_icono(nombre, tamano, color)
    return ctk.CTkImage(light_image=imagen, dark_image=imagen, size=(tamano, tamano))


# ----------------------------------------------------------------------
@functools.lru_cache(maxsize=32)
def logo_pil(tamano: int = 48, fondo: str = COLOR_ACENTO) -> Image.Image:
    """Logo de la casa: cesta de pan sobre una baldosa redondeada ámbar."""
    grande = max(24, tamano * _FACTOR)
    lienzo = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
    d = ImageDraw.Draw(lienzo)

    # Baldosa redondeada de fondo.
    d.rounded_rectangle(
        [_p(3, grande), _p(3, grande), _p(97, grande), _p(97, grande)],
        radius=_p(24, grande),
        fill=fondo,
    )

    # Cesta en blanco, rellena, con "tejido" del color de fondo.
    blanco = "#ffffff"
    cuerpo = [
        (_p(20, grande), _p(46, grande)),
        (_p(80, grande), _p(46, grande)),
        (_p(70, grande), _p(84, grande)),
        (_p(30, grande), _p(84, grande)),
    ]
    d.polygon(cuerpo, fill=blanco)
    d.arc(
        [_p(29, grande), _p(14, grande), _p(71, grande), _p(60, grande)],
        start=200, end=340, fill=blanco, width=max(3, round(grande * 0.055)),
    )
    ancho_tejido = max(2, round(grande * 0.035))
    d.line([_p(38, grande), _p(56, grande), _p(50, grande), _p(82, grande)], fill=fondo, width=ancho_tejido, joint="curve")
    d.line([_p(62, grande), _p(56, grande), _p(50, grande), _p(82, grande)], fill=fondo, width=ancho_tejido, joint="curve")
    d.line([_p(50, grande), _p(50, grande), _p(50, grande), _p(84, grande)], fill=fondo, width=ancho_tejido)
    return lienzo.resize((tamano, tamano), Image.LANCZOS)


@functools.lru_cache(maxsize=32)
def logo_ctk(tamano: int = 40, fondo: str = COLOR_ACENTO):
    """Logo como ``CTkImage`` para la cabecera y el icono de ventana."""
    import customtkinter as ctk

    imagen = logo_pil(tamano, fondo)
    return ctk.CTkImage(light_image=imagen, dark_image=imagen, size=(tamano, tamano))


@functools.lru_cache(maxsize=4)
def foto_logo(tamano: int = 64):
    """Logo como ``PhotoImage`` de Tk (para ``iconphoto`` de la ventana)."""
    from PIL import ImageTk

    return ImageTk.PhotoImage(logo_pil(tamano))
