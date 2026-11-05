"""Reinicio de arranque del POS: deja la base lista para la primera venta real.

Qué hace, en este orden:

1. **Respaldo** de la base con marca de tiempo en ``db/respaldos/``.
2. **Borra las ventas** (y su detalle y pagos, por cascada).
3. **Cierra los turnos de caja abiertos** (para poder hacer "Inicio del Día").
4. **Pone los correlativos en 0** (``T001``, ``B001``, ``F001``), salvo que se
   indique ``--conservar-correlativos`` (útil después de pruebas contra SUNAT:
   no conviene reusar correlativos que ya se enviaron al WS).
5. **Archiva** tickets, boletas, facturas y reportes en ``_pruebas/<fecha>/``.

Trabaja sobre la **carpeta de datos** de la aplicación: la raíz del proyecto en
desarrollo o ``%PROGRAMDATA%\\LaCasaDelPanApp`` si se define
``LACASADELPAN_DATA`` (o si se ejecuta empaquetado).

No toca el catálogo (productos, proveedores, categorías, impuestos). Las compras
y el stock/kardex **no** se modifican: el POS no revierte stock.

**Cierra la aplicación antes de ejecutarlo.**

Uso::

    python scripts/reiniciar_numeracion.py            # pide confirmación
    python scripts/reiniciar_numeracion.py --simular  # solo muestra qué haría
    python scripts/reiniciar_numeracion.py --si       # sin pedir confirmación

    python scripts/reiniciar_numeracion.py --ruta-bd RUTA  # base alternativa
    python scripts/reiniciar_numeracion.py --no-archivar   # no mover archivos
    python scripts/reiniciar_numeracion.py --conservar-correlativos  # no zerar
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

#: Raíz del proyecto (…/LaCasaDelPanApp), dos niveles por encima de scripts/.
RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from src.infraestructura.rutas import (  # noqa: E402
    ruta_bd_por_defecto,
    ruta_datos,
)

#: Carpetas cuyos archivos de prueba se archivan al arranque.
CARPETAS_PRUEBA = ("tickets", "boletas", "facturas", "reportes")


def _marca_tiempo() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def _archivos_de_prueba() -> list[Path]:
    """Archivos sueltos en las carpetas de salida (no entra en ``_historicas``)."""
    archivos: list[Path] = []
    raiz_datos = ruta_datos()
    for carpeta in CARPETAS_PRUEBA:
        origen = raiz_datos / carpeta
        if origen.exists():
            archivos.extend(p for p in origen.iterdir() if p.is_file())
    return sorted(archivos)


def _leer_estado(cur: sqlite3.Cursor) -> dict[str, object]:
    return {
        "ventas": cur.execute("SELECT COUNT(*) FROM ventas").fetchone()[0],
        "series": cur.execute(
            "SELECT serie, correlativo_actual FROM series_comprobante ORDER BY serie"
        ).fetchall(),
        "turnos_abiertos": cur.execute(
            "SELECT COUNT(*) FROM turnos_caja WHERE estado = 'ABIERTO'"
        ).fetchone()[0],
    }


def _formatear_series(series: object) -> str:
    return " · ".join(f"{serie}={correlativo}" for serie, correlativo in series) or "(vacío)"


def main() -> int:
    analizador = argparse.ArgumentParser(description="Reinicia la numeración para el arranque real.")
    analizador.add_argument(
        "--ruta-bd",
        default=str(ruta_bd_por_defecto()),
        help="Ruta de la base de datos (por defecto: la de la carpeta de datos).",
    )
    analizador.add_argument("--si", action="store_true", help="No pedir confirmación.")
    analizador.add_argument("--simular", action="store_true", help="Solo mostrar qué haría.")
    analizador.add_argument(
        "--no-archivar", action="store_true", help="No mover los archivos de prueba."
    )
    analizador.add_argument(
        "--conservar-correlativos",
        action="store_true",
        help=(
            "No tocar series_comprobante: mantiene los correlativos actuales "
            "(recomendado si ya se enviaron comprobantes al WS de SUNAT)."
        ),
    )
    args = analizador.parse_args()

    ruta_bd = Path(args.ruta_bd)
    if not ruta_bd.exists():
        print(f"[ERROR] No existe la base de datos: {ruta_bd}")
        return 1

    conexion = sqlite3.connect(str(ruta_bd))
    conexion.execute("PRAGMA foreign_keys=ON")
    cur = conexion.cursor()

    estado = _leer_estado(cur)
    archivos = _archivos_de_prueba()

    print(f"Base de datos : {ruta_bd}")
    print(f"Ventas        : {estado['ventas']}  (se borran)")
    destino_correlativos = (
        "sin cambios (--conservar-correlativos)"
        if args.conservar_correlativos
        else "T001=0 · B001=0 · F001=0"
    )
    print(f"Correlativos  : {_formatear_series(estado['series'])}  ->  {destino_correlativos}")
    print(f"Turnos abiertos: {estado['turnos_abiertos']}  (se cierran)")
    print(f"Archivos de prueba: {len(archivos)}  (se archivan en _pruebas/{_marca_tiempo()}/)")

    if args.simular:
        print("\n[simulación] No se modificó nada.")
        conexion.close()
        return 0

    if not args.si:
        respuesta = input("\n¿Confirmar el reinicio? Escribe 'SI' para continuar: ").strip()
        if respuesta.upper() != "SI":
            print("Cancelado. No se modificó nada.")
            conexion.close()
            return 1

    # 1) Respaldo ---------------------------------------------------------
    carpeta_respaldos = ruta_bd.parent / "respaldos"
    carpeta_respaldos.mkdir(parents=True, exist_ok=True)
    respaldo = carpeta_respaldos / f"{ruta_bd.stem}_antes_arranque_{_marca_tiempo()}.db"
    conexion.close()  # soltar el archivo antes de copiarlo
    shutil.copy2(ruta_bd, respaldo)
    print(f"\n[OK] Respaldo: {respaldo}")

    # 2-4) Borrar ventas, cerrar turnos y zerar correlativos --------------
    conexion = sqlite3.connect(str(ruta_bd))
    conexion.execute("PRAGMA foreign_keys=ON")
    cur = conexion.cursor()
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur.execute("DELETE FROM venta_pagos")
        cur.execute("DELETE FROM venta_detalle")
        ventas_borradas = cur.execute("DELETE FROM ventas").rowcount
        turnos_cerrados = cur.execute(
            "UPDATE turnos_caja SET estado='CERRADO', cerrado_en=?, "
            "    monto_cierre_sistema=monto_apertura, monto_cierre_declarado=monto_apertura, "
            "    diferencia=0, observaciones='Cerrado por reinicio de arranque (turno de prueba)' "
            "WHERE estado='ABIERTO'",
            (ahora,),
        ).rowcount
        if not args.conservar_correlativos:
            cur.execute(
                "UPDATE series_comprobante SET correlativo_actual=0, "
                "actualizado_en=CURRENT_TIMESTAMP"
            )
        conexion.commit()
    except Exception as exc:  # noqa: BLE001
        conexion.rollback()
        print(f"[ERROR] Se revirtió todo: {exc}")
        conexion.close()
        return 1
    print(f"[OK] Ventas borradas: {ventas_borradas} | turnos cerrados: {turnos_cerrados}")

    # 5) Archivar los archivos de prueba ----------------------------------
    if args.no_archivar or not archivos:
        print("[OK] Archivos: sin cambios.")
    else:
        destino = ruta_datos() / "_pruebas" / _marca_tiempo()
        for archivo in archivos:
            subcarpeta = destino / archivo.parent.name
            subcarpeta.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archivo), str(subcarpeta / archivo.name))
        print(f"[OK] {len(archivos)} archivo(s) archivado(s) en {destino}")

    final = _leer_estado(cur)
    conexion.close()

    print("\n=== Estado final ===")
    print(f"Ventas       : {final['ventas']}")
    print(f"Correlativos : {_formatear_series(final['series'])}")
    print(f"Turnos abiertos: {final['turnos_abiertos']}")
    if args.conservar_correlativos:
        proximo = " · ".join(f"{serie}-{corr + 1:06d}" for serie, corr in final["series"])
        print(f"\nListo: correlativos conservados; el próximo documento será {proximo}.")
    else:
        print("\nListo: la panadería arranca en T001-000001 · B001-000001 · F001-000001.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
