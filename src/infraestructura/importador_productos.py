"""Importador de productos desde Excel/CSV.

Lee archivos .xlsx, .xls, .csv y devuelve filas normalizadas listas
para validar en el servicio de productos.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

UNIDADES_VALIDAS = {"UND", "KG", "GR", "LT", "ML", "DOCENA", "PAQUETE"}


def leer_archivo(ruta: str | os.PathLike) -> List[Dict[str, Any]]:
    """Lee un archivo Excel o CSV y devuelve lista de dicts normalizados."""
    ruta_str = str(ruta)
    ext = Path(ruta_str).suffix.lower()

    if ext in (".xlsx", ".xls"):
        return _leer_excel(ruta_str)
    if ext == ".csv":
        return _leer_csv(ruta_str)
    # Intentar por extensión
    if ext in (".xlsx", ".xls"):
        return _leer_excel(ruta_str)
    return _leer_csv(ruta_str)


def _leer_excel(ruta: str) -> List[Dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "openpyxl no está instalado. Añade openpyxl al requirements.txt"
        ) from exc

    wb = load_workbook(filename=ruta, read_only=True, data_only=True)
    ws = wb.active
    filas = list(ws.rows)
    if not filas:
        return []

    # Cabeceras
    cabeceras = []
    for c in filas[0]:
        val = c.value
        if val is None:
            cabeceras.append("")
            continue
        cabeceras.append(str(val).strip().lower().replace(" ", "_"))

    mapeo = _mapear_columnas(cabeceras)

    resultado: List[Dict[str, Any]] = []
    for i, fila in enumerate(filas[1:], start=2):
        datos: Dict[str, Any] = {}
        for j, c in enumerate(fila):
            if j >= len(cabeceras):
                continue
            campo = mapeo.get(j)
            if not campo:
                continue
            datos[campo] = c.value
        if _fila_vacia(datos):
            continue
        resultado.append(datos)
    wb.close()
    return resultado


def _leer_csv(ruta: str) -> List[Dict[str, Any]]:
    with open(ruta, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            cabeceras_raw = next(reader)
        except StopIteration:
            return []

        cabeceras = []
        for val in cabeceras_raw:
            if val is None:
                cabeceras.append("")
                continue
            cabeceras.append(str(val).strip().lower().replace(" ", "_"))

        mapeo = _mapear_columnas(cabeceras)

        resultado: List[Dict[str, Any]] = []
        for i, fila in enumerate(reader, start=2):
            datos: Dict[str, Any] = {}
            for j, val in enumerate(fila):
                if j >= len(cabeceras):
                    continue
                campo = mapeo.get(j)
                if not campo:
                    continue
                datos[campo] = val
            if _fila_vacia(datos):
                continue
            resultado.append(datos)
        return resultado


def _mapear_columnas(cabeceras: List[str]) -> Dict[int, str]:
    alias: Dict[str, str] = {
        "codigo_barras": "codigo_barras",
        "codbarras": "codigo_barras",
        "cod_barras": "codigo_barras",
        "bar_code": "codigo_barras",
        "barcode": "codigo_barras",
        "codigo_interno": "codigo_interno",
        "codinterno": "codigo_interno",
        "cod_interno": "codigo_interno",
        "sku": "codigo_interno",
        "nombre": "nombre",
        "descripcion": "descripcion",
        "desc": "descripcion",
        "categoria": "categoria",
        "categoria_id": "categoria",
        "unidad_medida": "unidad_medida",
        "unidad": "unidad_medida",
        "um": "unidad_medida",
        "precio_venta": "precio_venta",
        "precio": "precio_venta",
        "pvp": "precio_venta",
        "precio_venta_soles": "precio_venta",
        "costo": "costo",
        "precio_costo": "costo",
        "es_pesable": "es_pesable",
        "pesable": "es_pesable",
        "controla_stock": "controla_stock",
        "controlastock": "controla_stock",
        "stock_actual": "stock_actual",
        "stock": "stock_actual",
        "existencia": "stock_actual",
        "stock_minimo": "stock_minimo",
        "minimo": "stock_minimo",
        "stock_min": "stock_minimo",
        "activo": "activo",
        "estado": "activo",
    }

    mapeo: Dict[int, str] = {}
    for i, c in enumerate(cabeceras):
        if not c:
            continue
        # limpiar
        c2 = c.replace("-", "_")
        mapeo[i] = alias.get(c2, c2)
    return mapeo


def _fila_vacia(datos: Dict[str, Any]) -> bool:
    for v in datos.values():
        if v is None:
            continue
        if str(v).strip() != "":
            return False
    return True
