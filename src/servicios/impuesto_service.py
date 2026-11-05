"""Servicio de impuestos — capa de SERVICIOS.

Expone el catálogo de impuestos a la interfaz (selector del formulario de
producto) y el mapa de tasas que usa el cálculo del desglose tributario.
La aritmética del IGV vive en el dominio (:mod:`src.dominio.impuestos`).
"""

from __future__ import annotations

import logging
from typing import Any

from src.infraestructura.conexion import ConexionBD
from src.repositorios.impuesto_repo import ImpuestoRepo

logger = logging.getLogger(__name__)


class ImpuestoService:
    """Casos de uso de lectura del catálogo de impuestos."""

    def __init__(
        self,
        conexion_bd: ConexionBD,
        impuesto_repo: ImpuestoRepo | None = None,
    ) -> None:
        self._bd = conexion_bd
        self._impuesto_repo = impuesto_repo or ImpuestoRepo(conexion_bd)

    def listar(self) -> list[dict[str, Any]]:
        """Devuelve los impuestos activos (para el selector del formulario)."""
        return self._impuesto_repo.listar()

    def mapa_por_id(self) -> dict[int, dict[str, Any]]:
        """Devuelve ``{id: impuesto}`` con todos los impuestos (activos e inactivos)."""
        return self._impuesto_repo.mapa_por_id()

    def por_defecto(self) -> dict[str, Any] | None:
        """Devuelve el impuesto por defecto: el IGV 18 % gravado.

        Se usa al crear productos o al asignar impuesto a los que no tienen.
        Si por alguna razón no existiera, devuelve el primero activo.
        """
        activos = self._impuesto_repo.listar()
        if not activos:
            return None
        for impuesto in activos:
            if str(impuesto.get("tipo_afectacion")) == "10" and float(impuesto.get("tasa") or 0) > 0:
                return impuesto
        return activos[0]
