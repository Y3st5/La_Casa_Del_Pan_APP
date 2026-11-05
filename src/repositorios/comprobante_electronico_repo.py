"""Repositorio de comprobantes electrónicos — capa de REPOSITORIOS.

Puente entre la venta local y la emisión ante SUNAT: guarda el estado del envío,
los archivos (XML/CDR) y un **payload JSON autocontenido** con todo lo necesario
para generar o reenviar el XML UBL 2.1 **después** de que el Cierre del Día haya
borrado la venta y su detalle.

El payload es un *snapshot* inmutable: emisor, receptor, totales y el detalle de
ítems (con unidad SUNAT y desglose por línea). No depende de ``ventas`` ni de
``venta_detalle``, que desaparecen al cerrar el turno.

No contiene reglas de negocio (estas viven en los servicios).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from src.infraestructura.conexion import ConexionBD

logger = logging.getLogger(__name__)

#: Estados posibles (deben coincidir con el ``CHECK`` de la tabla).
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_ENVIADO = "ENVIADO"
ESTADO_ACEPTADO = "ACEPTADO"
ESTADO_RECHAZADO = "RECHAZADO"
ESTADO_ANULADO = "ANULADO"
ESTADOS = (ESTADO_PENDIENTE, ESTADO_ENVIADO, ESTADO_ACEPTADO, ESTADO_RECHAZADO, ESTADO_ANULADO)

SQL_INSERT = (
    "INSERT INTO comprobantes_electronicos ("
    "    venta_id, tipo_comprobante, serie, correlativo, fecha_emision,"
    "    receptor_tipo_doc, receptor_num_doc, receptor_nombre,"
    "    moneda, monto_total, monto_igv, estado, payload_json"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: Columnas que se leen (el payload JSON se devuelve ya decodificado en ``payload``).
SQL_SELECT = (
    "SELECT id, venta_id, tipo_comprobante, serie, correlativo, fecha_emision,"
    "       receptor_tipo_doc, receptor_num_doc, receptor_nombre,"
    "       moneda, monto_total, monto_igv, estado, hash_cpe, ruta_xml, ruta_cdr,"
    "       respuesta_codigo, respuesta_descripcion, intentos, enviado_en,"
    "       creado_en, actualizado_en, payload_json "
    "  FROM comprobantes_electronicos"
)


class ComprobanteElectronicoRepo:
    """Acceso a la tabla ``comprobantes_electronicos``."""

    def __init__(self, conexion_bd: ConexionBD) -> None:
        self._bd = conexion_bd

    # ------------------------------------------------------------------ escritura
    def crear(self, datos: dict[str, Any], cursor: sqlite3.Cursor | None = None) -> int:
        """Inserta un comprobante electrónico y devuelve su ``id``.

        Args:
            datos: claves ``venta_id``, ``tipo_comprobante``, ``serie``,
                ``correlativo``, ``fecha_emision``, ``receptor_tipo_doc``,
                ``receptor_num_doc``, ``receptor_nombre``, ``moneda``,
                ``monto_total``, ``monto_igv``, ``estado`` y ``payload`` (dict).
            cursor: cursor de una transacción en curso. Se usa para crear el
                comprobante **dentro** de la misma transacción de la venta: si
                algo falla, la venta y el comprobante se revierten juntos.
        """
        payload = datos.get("payload")
        parametros = (
            int(datos["venta_id"]),
            str(datos["tipo_comprobante"]),
            str(datos["serie"]),
            int(datos["correlativo"]),
            str(datos["fecha_emision"]),
            str(datos.get("receptor_tipo_doc") or ""),
            str(datos.get("receptor_num_doc") or ""),
            str(datos.get("receptor_nombre") or ""),
            str(datos.get("moneda") or "PEN"),
            round(float(datos.get("monto_total") or 0), 2),
            round(float(datos.get("monto_igv") or 0), 2),
            str(datos.get("estado") or ESTADO_PENDIENTE),
            json.dumps(payload, ensure_ascii=False) if payload is not None else "",
        )
        if cursor is not None:
            cursor.execute(SQL_INSERT, parametros)
            return int(cursor.lastrowid or 0)
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(SQL_INSERT, parametros)
            return int(cur.lastrowid or 0)

    def actualizar_estado(
        self,
        comprobante_id: int,
        estado: str,
        *,
        respuesta_codigo: str | None = None,
        respuesta_descripcion: str | None = None,
        hash_cpe: str | None = None,
        ruta_xml: str | None = None,
        ruta_cdr: str | None = None,
        incrementar_intentos: bool = False,
    ) -> None:
        """Actualiza el estado del envío y la respuesta de SUNAT / OSE.

        Solo se escriben los campos que se pasan (los ``None`` se dejan intactos).
        """
        if estado not in ESTADOS:
            raise ValueError(f"Estado de comprobante no válido: {estado!r}.")

        asignaciones = ["estado = ?", "actualizado_en = CURRENT_TIMESTAMP"]
        parametros: list[Any] = [estado]

        opcionales = (
            ("respuesta_codigo", respuesta_codigo),
            ("respuesta_descripcion", respuesta_descripcion),
            ("hash_cpe", hash_cpe),
            ("ruta_xml", ruta_xml),
            ("ruta_cdr", ruta_cdr),
        )
        for columna, valor in opcionales:
            if valor is not None:
                asignaciones.append(f"{columna} = ?")
                parametros.append(str(valor))

        if incrementar_intentos:
            asignaciones.append("intentos = intentos + 1")
        # ``enviado_en`` marca la primera vez que el comprobante llegó a SUNAT
        # (aceptado o rechazado); un error local de red lo deja intacto.
        if estado in (ESTADO_ENVIADO, ESTADO_ACEPTADO, ESTADO_RECHAZADO):
            asignaciones.append("enviado_en = COALESCE(enviado_en, CURRENT_TIMESTAMP)")

        parametros.append(int(comprobante_id))
        with self._bd.cursor() as (_conexion, cur):
            cur.execute(
                "UPDATE comprobantes_electronicos "
                f"   SET {', '.join(asignaciones)} "
                " WHERE id = ?",
                parametros,
            )

    # ------------------------------------------------------------------ lecturas
    def obtener(self, comprobante_id: int) -> dict[str, Any] | None:
        """Devuelve un comprobante por ``id`` (con ``payload`` ya decodificado)."""
        with self._bd.cursor() as (_conexion, cur):
            fila = cur.execute(SQL_SELECT + " WHERE id = ?", (int(comprobante_id),)).fetchone()
        return self._fila_a_dict(fila)

    def obtener_por_venta(self, venta_id: int) -> dict[str, Any] | None:
        """Devuelve el comprobante fiscal de una venta (o ``None`` si fue ticket)."""
        with self._bd.cursor() as (_conexion, cur):
            fila = cur.execute(
                SQL_SELECT + " WHERE venta_id = ? ORDER BY id DESC LIMIT 1",
                (int(venta_id),),
            ).fetchone()
        return self._fila_a_dict(fila)

    def listar_por_estado(
        self, estado: str = ESTADO_PENDIENTE, limite: int = 100
    ) -> list[dict[str, Any]]:
        """Lista los comprobantes en un estado (por defecto, los pendientes de envío)."""
        with self._bd.cursor() as (_conexion, cur):
            filas = cur.execute(
                SQL_SELECT + " WHERE estado = ? ORDER BY id LIMIT ?",
                (estado, int(limite)),
            ).fetchall()
        return [d for d in (self._fila_a_dict(f) for f in filas) if d is not None]

    def listar_por_fecha(self, fecha: str) -> list[dict[str, Any]]:
        """Lista los comprobantes emitidos en una fecha (``YYYY-MM-DD``)."""
        with self._bd.cursor() as (_conexion, cur):
            filas = cur.execute(
                SQL_SELECT + " WHERE fecha_emision = ? ORDER BY serie, correlativo",
                (str(fecha),),
            ).fetchall()
        return [d for d in (self._fila_a_dict(f) for f in filas) if d is not None]

    def contar_por_estado(self, estado: str = ESTADO_PENDIENTE) -> int:
        """Cuenta los comprobantes en un estado (sin traer las filas)."""
        with self._bd.cursor() as (_conexion, cur):
            fila = cur.execute(
                "SELECT COUNT(*) AS total FROM comprobantes_electronicos WHERE estado = ?",
                (estado,),
            ).fetchone()
        return int(fila["total"]) if fila is not None else 0

    @staticmethod
    def _fila_a_dict(fila: sqlite3.Row | None) -> dict[str, Any] | None:
        """Convierte una fila en dict, decodificando ``payload_json`` a ``payload``."""
        if fila is None:
            return None
        datos = dict(fila)
        crudo = str(datos.pop("payload_json", "") or "")
        if crudo:
            try:
                datos["payload"] = json.loads(crudo)
            except json.JSONDecodeError:
                logger.warning("Payload JSON inválido en comprobante id=%s.", datos.get("id"))
                datos["payload"] = {}
        else:
            datos["payload"] = {}
        return datos
