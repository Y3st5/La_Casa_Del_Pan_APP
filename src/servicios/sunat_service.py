"""Servicio de emisión electrónica ante SUNAT — capa de SERVICIOS.

Orquesta, para un comprobante **PENDIENTE** (o todos a la vez), el ciclo
completo de la Opción B (sin Java ni el Facturador SFS):

    1. Lee el *payload* autocontenido de ``comprobantes_electronicos``.
    2. Genera el XML UBL 2.1 (``ubl_generator``).
    3. Lo firma con XMLDSig (``sunat_ws.firmar_xml``) y guarda el XML **firmado**
       en ``xml/``.
    4. Calcula el hash oficial (``ds:DigestValue``) y lo envía por ``sendBill``.
    5. Guarda el ZIP del CDR en ``cdr/`` y actualiza ``comprobantes_electronicos``
       (estado, hash_cpe, ruta_xml, ruta_cdr, respuesta_codigo,
       respuesta_descripcion, intentos, enviado_en).

Reglas de robustez:
    * Un fallo (sin internet, certificado ausente, etc.) **no** pierde el
      comprobante: queda ``PENDIENTE`` para reintentar y se registra el motivo.
    * Un **rechazo** de SUNAT (código 2xxx/3xxx) queda ``RECHAZADO`` y no se
      reintenta automáticamente.
    * La emisión es *best effort*: quien llama (la caja) nunca debe caerse por
      un problema de SUNAT.

Este módulo NO ejecuta SQL directo fuera del repositorio y NO conoce Tk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.infraestructura.config_reader import ConfigReader, ErrorConfiguracion
from src.infraestructura.conexion import ConexionBD
from src.infraestructura.rutas import ruta_datos
from src.infraestructura.sunat_ws import (
    BETA_PASS,
    BETA_USER,
    WS_BETA_URL,
    WS_PROD_URL,
    SunatConfig,
    calcular_hash_cpe,
    enviar_xml_firmado,
    firmar_xml,
    generar_certificado_prueba,
)
from src.infraestructura.ubl_generator import generar_xml_comprobante
from src.repositorios.comprobante_electronico_repo import (
    ESTADO_ACEPTADO,
    ESTADO_ENVIADO,
    ESTADO_PENDIENTE,
    ESTADO_RECHAZADO,
    ComprobanteElectronicoRepo,
)

logger = logging.getLogger(__name__)

#: RUC del emisor si la configuración no lo trae (el real de la panadería).
RUC_POR_DEFECTO = "20603122144"

#: Carpeta (relativa a la raíz de datos) donde se archivan los XML firmados.
CARPETA_XML = "xml"
#: Carpeta donde se archivan los ZIP del CDR (Constancia de Recepción).
CARPETA_CDR = "cdr"

#: Código local (no de SUNAT) cuando falla algo antes/durante el envío.
CODIGO_ERROR_LOCAL = "ERROR_LOCAL"


class ErrorSunat(Exception):
    """Error de configuración o de uso del servicio de emisión electrónica."""


@dataclass(frozen=True)
class ResultadoEnvio:
    """Resultado del envío de un comprobante a SUNAT."""

    comprobante_id: int
    numero: str
    estado: str
    respuesta_codigo: str = ""
    respuesta_descripcion: str = ""
    hash_cpe: str = ""
    ruta_xml: str = ""
    ruta_cdr: str = ""
    #: Motivo cuando el fallo fue local (sin internet, certificado, etc.).
    error: str = ""

    @property
    def aceptado(self) -> bool:
        """``True`` si SUNAT aceptó el comprobante (ResponseCode = 0)."""
        return self.estado == ESTADO_ACEPTADO


def _tipo_documento_sunat(tipo_comprobante: str) -> str:
    """Código de tipo de documento (Catálogo N.º 01) para el nombre del archivo."""
    return "01" if str(tipo_comprobante).upper() == "FACTURA" else "03"


class SunatService:
    """Envía comprobantes electrónicos PENDIENTES a SUNAT (Beta o producción)."""

    def __init__(
        self,
        conexion_bd: ConexionBD,
        comprobante_repo: ComprobanteElectronicoRepo | None = None,
        config: ConfigReader | None = None,
        raiz_proyecto: str | Path | None = None,
    ) -> None:
        """
        Args:
            conexion_bd: fábrica de conexiones SQLite.
            comprobante_repo: repositorio de comprobantes electrónicos.
            config: lector de ``config.ini`` (sección ``[sunat]``).
            raiz_proyecto: raíz donde viven ``xml/`` y ``cdr/`` (útil en tests).
                Si se omite, se usa la carpeta de datos de la aplicación.
        """
        self._bd = conexion_bd
        self._repo = comprobante_repo or ComprobanteElectronicoRepo(conexion_bd)
        self._config = config
        self._raiz = Path(raiz_proyecto) if raiz_proyecto is not None else ruta_datos()

    # ------------------------------------------------------------------ ajustes
    def _ajustes(self) -> dict[str, Any]:
        """Lee la sección ``[sunat]`` de la configuración (con defaults Beta)."""
        ajustes: dict[str, Any] = {
            "habilitado": False,
            "entorno": "beta",
            "cert_path": "config/certificado_prueba.pfx",
            "cert_pass": "test123",
            "usuario_sol": BETA_USER,
            "clave_sol": BETA_PASS,
        }

        lector = self._config
        if lector is None:
            try:
                lector = ConfigReader()
            except ErrorConfiguracion:
                return ajustes  # sin config.ini: emisión deshabilitada

        if not lector.tiene_seccion("sunat"):
            return ajustes

        try:
            ajustes["habilitado"] = lector.obtener_bool("sunat", "habilitado", False)
            ajustes["entorno"] = (
                str(lector.obtener("sunat", "entorno", "beta") or "beta").strip().lower()
            )
            ajustes["cert_path"] = str(
                lector.obtener("sunat", "cert_path", ajustes["cert_path"])
                or ajustes["cert_path"]
            )
            ajustes["cert_pass"] = str(
                lector.obtener("sunat", "cert_pass", ajustes["cert_pass"]) or ""
            )
            ajustes["usuario_sol"] = str(
                lector.obtener("sunat", "usuario_sol", ajustes["usuario_sol"]) or ""
            )
            ajustes["clave_sol"] = str(
                lector.obtener("sunat", "clave_sol", ajustes["clave_sol"]) or ""
            )
        except ErrorConfiguracion as exc:
            logger.warning("Configuración [sunat] inválida: %s", exc)
        return ajustes

    @property
    def habilitado(self) -> bool:
        """``True`` si ``[sunat] habilitado`` está activo."""
        return bool(self._ajustes()["habilitado"])

    @property
    def entorno(self) -> str:
        """``'beta'`` o ``'produccion'``."""
        return str(self._ajustes()["entorno"])

    def contar_pendientes(self) -> int:
        """Cuántos comprobantes están PENDIENTES de envío."""
        return int(self._repo.contar_por_estado(ESTADO_PENDIENTE))

    # ------------------------------------------------------------------ config
    def _ruc_negocio(self) -> str:
        """RUC del emisor según ``[negocio] ruc`` (con valor por defecto)."""
        if self._config is not None:
            try:
                ruc = self._config.obtener("negocio", "ruc", RUC_POR_DEFECTO)
                if ruc:
                    return str(ruc).strip()
            except ErrorConfiguracion:
                pass
        return RUC_POR_DEFECTO

    def _resolver_certificado(self, ajustes: dict[str, Any], ruc: str) -> Path:
        """Devuelve la ruta del ``.pfx``; en Beta lo autogenera si no existe."""
        ruta = Path(str(ajustes["cert_path"]))
        if not ruta.is_absolute():
            ruta = self._raiz / ruta
        if ruta.exists():
            return ruta

        if str(ajustes["entorno"]) != "beta":
            raise ErrorSunat(
                f"No existe el certificado digital ({ruta}). "
                "Configura [sunat] cert_path/cert_pass con el .pfx real."
            )

        ruta.parent.mkdir(parents=True, exist_ok=True)
        generar_certificado_prueba(ruc, str(ruta), str(ajustes["cert_pass"]) or "test123")
        logger.info("Certificado de prueba autogenerado: %s", ruta)
        return ruta

    def config_sunat(self, ruc: str | None = None) -> SunatConfig:
        """Arma la :class:`SunatConfig` desde ``[sunat]`` (URL, credenciales, cert).

        En producción exige credenciales SOL reales; en Beta usa MODDATOS.
        """
        ajustes = self._ajustes()
        if not ajustes["habilitado"]:
            raise ErrorSunat("La emisión electrónica está deshabilitada ([sunat] habilitado=false).")

        ruc = ruc or self._ruc_negocio()
        entorno = str(ajustes["entorno"])
        url = WS_PROD_URL if entorno == "produccion" else WS_BETA_URL

        usuario = str(ajustes["usuario_sol"]) or (BETA_USER if entorno != "produccion" else "")
        clave = str(ajustes["clave_sol"]) or (BETA_PASS if entorno != "produccion" else "")
        if entorno == "produccion" and (not usuario or not clave):
            raise ErrorSunat(
                "Faltan las credenciales SOL ([sunat] usuario_sol/clave_sol) para producción."
            )

        certificado = self._resolver_certificado(ajustes, ruc)
        return SunatConfig(
            ruc=ruc,
            cert_path=str(certificado),
            cert_pass=str(ajustes["cert_pass"]),
            url=url,
            usuario=usuario,
            clave=clave,
        )

    # --------------------------------------------------------- preparación
    def _contexto(self, comprobante: dict[str, Any]) -> dict[str, Any]:
        """Deriva del comprobante los datos/rutas del documento (nombre estándar)."""
        payload = comprobante.get("payload") or {}
        tipo = str(
            comprobante.get("tipo_comprobante")
            or (payload.get("comprobante") or {}).get("tipo")
            or ""
        )
        serie = str(
            comprobante.get("serie") or (payload.get("comprobante") or {}).get("serie") or ""
        )
        correlativo = int(comprobante.get("correlativo") or 0)
        ruc = str((payload.get("emisor") or {}).get("ruc") or self._ruc_negocio())

        # Nombre estándar SUNAT: {RUC}-{TIPO_DOC}-{SERIE}-{CORRELATIVO_8_DIGITOS}
        nombre_base = f"{ruc}-{_tipo_documento_sunat(tipo)}-{serie}-{correlativo:08d}"
        carpeta_xml = self._raiz / CARPETA_XML
        carpeta_cdr = self._raiz / CARPETA_CDR
        return {
            "payload": payload,
            "tipo": tipo,
            "serie": serie,
            "correlativo": correlativo,
            "ruc": ruc,
            "numero": f"{serie}-{correlativo:06d}",
            "nombre_base": nombre_base,
            "carpeta_xml": carpeta_xml,
            "carpeta_cdr": carpeta_cdr,
            "ruta_xml": carpeta_xml / f"{nombre_base}.xml",
            "ruta_cdr": carpeta_cdr / f"R-{nombre_base}.zip",
        }

    def _xml_firmado(
        self,
        comprobante: dict[str, Any],
        contexto: dict[str, Any],
        config: SunatConfig,
    ) -> tuple[bytes, str, Path]:
        """Devuelve ``(xml_firmado, hash_cpe, ruta_xml)``, archivando el XML.

        Si el XML ya fue preparado antes (mismo archivo en disco) lo **reutiliza**
        en vez de volver a firmar, para no recalcular la firma en cada reintento.
        """
        ruta_xml: Path = contexto["ruta_xml"]
        hash_previo = str(comprobante.get("hash_cpe") or "")
        if hash_previo and ruta_xml.exists():
            return ruta_xml.read_bytes(), hash_previo, ruta_xml

        contexto["carpeta_xml"].mkdir(parents=True, exist_ok=True)
        xml = generar_xml_comprobante(contexto["payload"])
        xml_firmado = firmar_xml(
            xml, config.cert_path, config.cert_pass, config.ruc
        )
        ruta_xml.write_bytes(xml_firmado)
        return xml_firmado, calcular_hash_cpe(xml_firmado), ruta_xml

    def preparar(self, comprobante_id: int) -> str:
        """Genera, firma y archiva el XML **sin enviarlo**; devuelve el ``hash_cpe``.

        Es local y rápido (no toca la red): permite imprimir el comprobante con
        su "Resumen" y deja el XML listo para el envío. Persiste ``hash_cpe`` y
        ``ruta_xml``; el estado no cambia (sigue ``PENDIENTE``).

        Raises:
            ErrorSunat: si no existe el comprobante o la configuración es inválida.
        """
        comprobante = self._obtener_o_fallar(comprobante_id)
        contexto = self._contexto(comprobante)
        config = self.config_sunat(contexto["ruc"])
        _xml, hash_cpe, ruta_xml = self._xml_firmado(comprobante, contexto, config)
        self._repo.actualizar_estado(
            comprobante_id=int(comprobante_id),
            estado=ESTADO_PENDIENTE,
            hash_cpe=hash_cpe,
            ruta_xml=str(ruta_xml),
        )
        return hash_cpe

    # ------------------------------------------------------------------ envío
    def procesar(self, comprobante_id: int) -> ResultadoEnvio:
        """Genera, firma y envía **un** comprobante; actualiza su estado.

        Nunca lanza por fallos de red: devuelve un :class:`ResultadoEnvio` con
        ``error`` si el problema fue local (el comprobante queda PENDIENTE).
        """
        comprobante = self._obtener_o_fallar(comprobante_id)
        contexto = self._contexto(comprobante)
        numero = contexto["numero"]
        ruta_xml: Path = contexto["ruta_xml"]
        ruta_cdr: Path = contexto["ruta_cdr"]

        hash_cpe = ""
        try:
            config = self.config_sunat(contexto["ruc"])
            xml_firmado, hash_cpe, ruta_xml = self._xml_firmado(
                comprobante, contexto, config
            )

            resultado = enviar_xml_firmado(
                xml_firmado, config, f"{contexto['nombre_base']}.zip"
            )
            contexto["carpeta_cdr"].mkdir(parents=True, exist_ok=True)
            ruta_cdr.write_bytes(resultado.cdr_zip_bytes)

            estado = self._estado_persistible(resultado.estado)
            self._repo.actualizar_estado(
                comprobante_id=int(comprobante_id),
                estado=estado,
                respuesta_codigo=resultado.respuesta_codigo,
                respuesta_descripcion=resultado.respuesta_descripcion,
                hash_cpe=hash_cpe,
                ruta_xml=str(ruta_xml),
                ruta_cdr=str(ruta_cdr),
                incrementar_intentos=True,
            )
            logger.info(
                "Comprobante %s -> SUNAT %s (%s): %s",
                numero, resultado.estado, resultado.respuesta_codigo,
                resultado.respuesta_descripcion,
            )
            return ResultadoEnvio(
                comprobante_id=int(comprobante_id),
                numero=numero,
                estado=estado,
                respuesta_codigo=resultado.respuesta_codigo,
                respuesta_descripcion=resultado.respuesta_descripcion,
                hash_cpe=hash_cpe,
                ruta_xml=str(ruta_xml),
                ruta_cdr=str(ruta_cdr),
            )
        except Exception as exc:  # noqa: BLE001 - la caja nunca debe caerse por SUNAT
            logger.exception("No se pudo enviar a SUNAT el comprobante %s.", numero)
            self._repo.actualizar_estado(
                comprobante_id=int(comprobante_id),
                estado=ESTADO_PENDIENTE,  # reintentable
                respuesta_codigo=CODIGO_ERROR_LOCAL,
                respuesta_descripcion=str(exc),
                hash_cpe=hash_cpe or None,
                ruta_xml=str(ruta_xml) if ruta_xml.exists() else None,
                incrementar_intentos=True,
            )
            return ResultadoEnvio(
                comprobante_id=int(comprobante_id),
                numero=numero,
                estado=ESTADO_PENDIENTE,
                respuesta_codigo=CODIGO_ERROR_LOCAL,
                respuesta_descripcion=str(exc),
                hash_cpe=hash_cpe,
                ruta_xml=str(ruta_xml) if ruta_xml.exists() else "",
                error=str(exc),
            )

    def _obtener_o_fallar(self, comprobante_id: int) -> dict[str, Any]:
        """Carga el comprobante o lanza :class:`ErrorSunat` si no existe."""
        comprobante = self._repo.obtener(int(comprobante_id))
        if comprobante is None:
            raise ErrorSunat(f"No existe el comprobante electrónico id={comprobante_id}.")
        return comprobante

    def procesar_pendientes(self, limite: int = 100) -> list[ResultadoEnvio]:
        """Envía todos los comprobantes PENDIENTES (de más antiguo a más nuevo).

        Si ``[sunat] habilitado`` es ``false`` no hace nada y devuelve ``[]``.
        """
        if not self.habilitado:
            logger.info("Emisión electrónica deshabilitada ([sunat] habilitado=false).")
            return []

        resultados: list[ResultadoEnvio] = []
        for comprobante in self._repo.listar_por_estado(ESTADO_PENDIENTE, limite=limite):
            resultados.append(self.procesar(int(comprobante["id"])))
        return resultados

    # ------------------------------------------------------------------ apoyo
    @staticmethod
    def _estado_persistible(estado: str) -> str:
        """Mapea el estado del CDR a uno permitido por la tabla.

        ``ERROR`` (código inesperado) se guarda como ``PENDIENTE`` para poder
        reintentarlo; ``ERROR_LOCAL`` no llega aquí.
        """
        if estado in (ESTADO_ACEPTADO, ESTADO_RECHAZADO):
            return estado
        if estado == ESTADO_ENVIADO:
            return ESTADO_ENVIADO
        return ESTADO_PENDIENTE
