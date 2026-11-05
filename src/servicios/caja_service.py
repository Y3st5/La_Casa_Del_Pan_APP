"""Servicio de caja / turnos — capa de SERVICIOS.

Orquesta la apertura y cierre de turnos (arranque y arqueo de caja).
No ejecuta SQL directamente: delega en ``TurnoRepo`` y ``VentaRepo``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.infraestructura.conexion import ConexionBD
from src.repositorios.compra_repo import METODOS_COMPRA_AFECTAN_CAJA, CompraRepo
from src.repositorios.turno_repo import TurnoRepo
from src.repositorios.venta_repo import VentaRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResumenTurno:
    """Resumen de un turno para mostrar en la UI."""
    turno_id: int
    caja_id: int
    estado: str
    abierto_en: str
    cerrado_en: str | None
    monto_apertura: float
    monto_cierre_declarado: float | None
    monto_cierre_sistema: float | None
    diferencia: float | None
    usuario_apertura_id: int | None
    usuario_cierre_id: int | None
    observaciones: str | None


@dataclass(frozen=True)
class ResultadoApertura:
    """Resultado de la apertura de un turno."""
    turno_id: int
    monto_apertura: float
    caja_id: int
    abierto_en: str


@dataclass(frozen=True)
class ResultadoCierre:
    """Resultado del cierre de un turno (arqueo)."""
    turno_id: int
    monto_apertura: float
    monto_cierre_declarado: float
    monto_cierre_sistema: float
    diferencia: float
    total_ventas_efectivo: float
    ventas_efectivo: int
    cerrado_en: str
    total_compras_efectivo: float = 0.0


class CajaService:
    """Casos de uso de caja: abrir turno (inicio día), cerrar turno (arqueo)."""

    def __init__(
        self,
        conexion_bd: ConexionBD,
        turno_repo: TurnoRepo | None = None,
        venta_repo: VentaRepo | None = None,
        compra_repo: CompraRepo | None = None,
    ) -> None:
        self._bd = conexion_bd
        self._turno_repo = turno_repo or TurnoRepo(conexion_bd)
        self._venta_repo = venta_repo or VentaRepo(conexion_bd)
        self._compra_repo = compra_repo or CompraRepo(conexion_bd)
        # Asegurar que la tabla existe
        self._turno_repo.asegurar_tabla()

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def obtener_turno_abierto(self, caja_id: int) -> ResumenTurno | None:
        """Devuelve el turno ABIERTO actual de una caja, o ``None``."""
        fila = self._turno_repo.obtener_turno_abierto(caja_id)
        if not fila:
            return None
        return self._a_resumen(fila)

    def obtener_ultimo_cerrado(self, caja_id: int) -> ResumenTurno | None:
        """Devuelve el último turno CERRADO de una caja (para inicio del día)."""
        fila = self._turno_repo.obtener_ultimo_cerrado(caja_id)
        if not fila:
            return None
        return self._a_resumen(fila)

    def hay_turno_abierto(self, caja_id: int) -> bool:
        """``True`` si ya hay un turno ABIERTO para la caja."""
        return self._turno_repo.obtener_turno_abierto(caja_id) is not None

    def total_compras_afectan_caja(self, fecha: str | None = None) -> float:
        """Total de compras del día que egresan efectivo físico de la caja.

        Solo las compras pagadas en "Efectivo" reducen el efectivo del arqueo:
        las de "Yape/Plin" y "Transferencia" salen por canales digitales y las
        de "Cuenta corriente" (crédito) no mueven dinero.
        """
        fecha = fecha or datetime.now().strftime("%Y-%m-%d")
        filas = self._compra_repo.listar_por_fecha(fecha)
        total = sum(
            round(float(f["total"]), 2)
            for f in filas
            if str(f.get("metodo_pago")) in METODOS_COMPRA_AFECTAN_CAJA
        )
        return round(total, 2)

    def _a_resumen(self, fila: dict[str, Any]) -> ResumenTurno:
        return ResumenTurno(
            turno_id=int(fila["id"]),
            caja_id=int(fila["caja_id"]),
            estado=str(fila["estado"]),
            abierto_en=str(fila["abierto_en"]),
            cerrado_en=str(fila["cerrado_en"]) if fila.get("cerrado_en") else None,
            monto_apertura=round(float(fila.get("monto_apertura") or 0), 2),
            monto_cierre_declarado=round(float(fila["monto_cierre_declarado"]), 2)
            if fila.get("monto_cierre_declarado") is not None
            else None,
            monto_cierre_sistema=round(float(fila["monto_cierre_sistema"]), 2)
            if fila.get("monto_cierre_sistema") is not None
            else None,
            diferencia=round(float(fila["diferencia"]), 2)
            if fila.get("diferencia") is not None
            else None,
            usuario_apertura_id=int(fila["usuario_apertura_id"])
            if fila.get("usuario_apertura_id") is not None
            else None,
            usuario_cierre_id=int(fila["usuario_cierre_id"])
            if fila.get("usuario_cierre_id") is not None
            else None,
            observaciones=fila.get("observaciones"),
        )

    # ------------------------------------------------------------------
    # Casos de uso
    # ------------------------------------------------------------------
    def abrir_turno(
        self, caja_id: int, monto_apertura: float, usuario_apertura_id: int | None = None
    ) -> ResultadoApertura:
        """Abre un nuevo turno (Inicio del Día).

        Args:
            caja_id: identificador de la caja (de ``config.ini``).
            monto_apertura: fondo de caja inicial (efectivo físico contado).
            usuario_apertura_id: id del usuario que abre (opcional).

        Returns:
            :class:`ResultadoApertura` con el id del turno creado.

        Raises:
            ValueError: si ya hay un turno abierto para esa caja.
        """
        with self._bd.cursor() as (_conexion, cursor):
            turno_id = self._turno_repo.abrir_turno(
                caja_id=caja_id,
                monto_apertura=monto_apertura,
                usuario_apertura_id=usuario_apertura_id,
                cursor=cursor,
            )
        logger.info(
            "Turno #%d ABIERTO (caja=%d, apertura=S/ %.2f, usuario=%s)",
            turno_id,
            caja_id,
            monto_apertura,
            usuario_apertura_id,
        )
        return ResultadoApertura(
            turno_id=turno_id,
            monto_apertura=round(monto_apertura, 2),
            caja_id=caja_id,
            abierto_en=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    def cerrar_turno(
        self,
        caja_id: int,
        monto_cierre_declarado: float,
        usuario_cierre_id: int | None = None,
        observaciones: str | None = None,
    ) -> ResultadoCierre:
        """Cierra el turno ABIERTO actual (Arqueo / Cierre de Día).

        Calcula el efectivo esperado por el sistema (apertura + ventas en efectivo)
        y lo compara con lo declarado por el cajero.

        Args:
            caja_id: identificador de la caja.
            monto_cierre_declarado: efectivo contado físicamente por el cajero.
            usuario_cierre_id: id del usuario que cierra.
            observaciones: notas libres (diferencias, novedades).

        Returns:
            :class:`ResultadoCierre` con el arqueo completo.

        Raises:
            ValueError: si no hay turno abierto para esa caja.
        """
        # 1. Obtener turno abierto
        turno = self._turno_repo.obtener_turno_abierto(caja_id)
        if not turno:
            raise ValueError(f"No hay turno abierto para la caja {caja_id}.")

        turno_id = int(turno["id"])
        monto_apertura = round(float(turno.get("monto_apertura") or 0), 2)

        # 2. Calcular efectivo esperado por el sistema:
        #    apertura + ventas en efectivo de ESTE turno.
        #    Solo el efectivo físico entra al cajón: Yape/Plin, tarjeta, consumo
        #    propio y fiado NO forman parte del arqueo de efectivo.
        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
        ventas_hoy = self._venta_repo.listar_por_fecha(fecha_hoy)

        total_ventas_efectivo = 0.0
        ventas_efectivo = 0

        for v in ventas_hoy:
            metodo = str(v.get("metodo_pago") or "")
            if metodo == "Efectivo":
                total = round(float(v.get("total") or 0), 2)
                total_ventas_efectivo += total
                ventas_efectivo += 1

        # Las compras pagadas al contado (efectivo) reducen el efectivo
        # esperado en caja.
        total_compras_efectivo = self.total_compras_afectan_caja(fecha_hoy)

        monto_cierre_sistema = round(
            monto_apertura + total_ventas_efectivo - total_compras_efectivo, 2
        )

        # 3. Cerrar turno en BD
        with self._bd.cursor() as (_conexion, cursor):
            self._turno_repo.cerrar_turno(
                turno_id=turno_id,
                usuario_cierre_id=usuario_cierre_id,
                monto_cierre_declarado=monto_cierre_declarado,
                monto_cierre_sistema=monto_cierre_sistema,
                observaciones=observaciones,
                cursor=cursor,
            )

        diferencia = round(float(monto_cierre_declarado) - monto_cierre_sistema, 2)
        cerrado_en = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logger.info(
            "Turno #%d CERRADO (caja=%d) | apertura=S/ %.2f | efectivo_ventas=S/ %.2f | "
            "compras=S/ %.2f | sistema=S/ %.2f | declarado=S/ %.2f | diff=S/ %.2f | usuario=%s",
            turno_id,
            caja_id,
            monto_apertura,
            total_ventas_efectivo,
            total_compras_efectivo,
            monto_cierre_sistema,
            monto_cierre_declarado,
            diferencia,
            usuario_cierre_id,
        )

        return ResultadoCierre(
            turno_id=turno_id,
            monto_apertura=monto_apertura,
            monto_cierre_declarado=round(monto_cierre_declarado, 2),
            monto_cierre_sistema=monto_cierre_sistema,
            diferencia=diferencia,
            total_ventas_efectivo=round(total_ventas_efectivo, 2),
            ventas_efectivo=ventas_efectivo,
            cerrado_en=cerrado_en,
            total_compras_efectivo=total_compras_efectivo,
        )