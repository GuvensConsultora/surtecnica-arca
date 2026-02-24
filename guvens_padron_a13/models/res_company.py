# Autenticación WSAA directa para WS A13 — sin depender de l10n_ar_afipws
# Por qué: el certificado real está en campos Enterprise de res.company
# (l10n_ar_afip_ws_key / l10n_ar_afip_ws_crt). Autenticamos con pyafipws.wsaa
# directamente y cacheamos token/sign en guvens.a13.token.cache.
# Patrón: basado en l10n_ar_afipws/models/res_company.py:authenticate()
from odoo import models, _
from odoo.exceptions import UserError
import base64
import logging

_logger = logging.getLogger(__name__)

# URLs WSAA de AFIP (Login CMS)
WSAA_URL = {
    "production": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
    "homologation": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
}

# URLs del WS A13 (padrón)
A13_WSDL = {
    "production": (
        "https://aws.afip.gov.ar/sr-padron/webservices/"
        "personaServiceA13?WSDL"
    ),
    "homologation": (
        "https://awshomo.afip.gov.ar/sr-padron/webservices/"
        "personaServiceA13?WSDL"
    ),
}

# TTL para el TRA (5 horas, mismo default que l10n_ar_afipws)
DEFAULT_TTL = 60 * 60 * 5


class ResCompany(models.Model):
    _inherit = "res.company"

    def _get_environment_type(self):
        """Detecta environment: production o homologation.
        Por qué: replica la lógica de l10n_ar_afipws para no depender de él."""
        param = self.env["ir.config_parameter"].sudo().get_param(
            "afip.ws.env.type"
        )
        if param in ("production", "homologation"):
            return param
        # Fallback: config file server_mode
        from odoo.tools import config
        server_mode = config.get("server_mode", "")
        if server_mode in ("test", "develop"):
            return "homologation"
        return "production"

    def _get_a13_ws(self):
        """Retorna instancia WSSrPadronA13 autenticada y lista para consultar.

        Flujo:
        1. Busca token cacheado en guvens.a13.token.cache
        2. Si no hay → lee cert Enterprise, autentica WSAA, cachea token
        3. Crea instancia WSSrPadronA13 con Token/Sign/Cuit
        """
        self.ensure_one()
        env_type = self._get_environment_type()
        cache_model = self.env["guvens.a13.token.cache"]

        # Paso 1: buscar token cacheado
        cached = cache_model._get_valid_token(self)
        if cached:
            _logger.info("A13 using cached token for company %s", self.name)
            token = cached.token
            sign = cached.sign
        else:
            # Paso 2: autenticar con WSAA
            token, sign = self._authenticate_wsaa(env_type)

        # Paso 3: armar instancia WSSrPadronA13
        from .ws_sr_padron_a13 import WSSrPadronA13

        ws = WSSrPadronA13()
        ws.HOMO = False
        ws.Conectar("", A13_WSDL[env_type], "")
        ws.Token = token
        ws.Sign = sign
        ws.Cuit = self.vat
        ws.Obs = ""
        ws.Errores = []
        _logger.info(
            "A13 connection ready — env=%s, cuit=%s", env_type, self.vat
        )
        return ws

    def _authenticate_wsaa(self, env_type):
        """Autentica con WSAA usando certificado Enterprise y cachea el resultado.

        Por qué: pyafipws.wsaa maneja toda la criptografía (TRA, CMS, LoginCMS).
        Nosotros solo le pasamos cert + key en PEM y parseamos el ticket."""
        from pyafipws.wsaa import WSAA

        # Leer certificado de campos Enterprise (Binary → base64 → PEM)
        pkey_b64 = self.l10n_ar_afip_ws_key
        crt_b64 = self.l10n_ar_afip_ws_crt
        if not pkey_b64 or not crt_b64:
            raise UserError(_(
                "No se encontró certificado AFIP para la empresa %s.\n"
                "Configure la clave privada y certificado en "
                "Contabilidad → Configuración → Certificado AFIP."
            ) % self.name)

        pkey = base64.b64decode(pkey_b64).decode("ascii")
        cert = base64.b64decode(crt_b64).decode("ascii")
        _logger.info("Using Enterprise certificate for company %s", self.name)

        # Por qué: pyafipws busca "BEGIN RSA PRIVATE KEY", no "BEGIN PRIVATE KEY"
        if pkey.startswith("-----BEGIN PRIVATE KEY-----"):
            pkey = pkey.replace(" PRIVATE KEY", " RSA PRIVATE KEY")

        wsaa = WSAA()
        wsaa.LanzarExcepciones = True

        try:
            # Crear TRA (Ticket de Requerimiento de Acceso) para servicio A13
            tra = wsaa.CreateTRA(service="ws_sr_padron_a13", ttl=DEFAULT_TTL)
            # Firmar TRA con cert+key → CMS (PKCS#7)
            cms = wsaa.SignTRA(tra, cert, pkey)
            # Conectar al WSAA de AFIP
            wsaa.Conectar("", WSAA_URL[env_type], "")
            # Obtener Ticket de Acceso (TA)
            ta = wsaa.LoginCMS(cms)
            if not ta:
                raise RuntimeError("WSAA LoginCMS no retornó ticket")

            # Parsear el TA XML para extraer token, sign y tiempos
            wsaa.AnalizarXml(xml=ta)
            token = wsaa.ObtenerTagXml("token")
            sign = wsaa.ObtenerTagXml("sign")
            expiration = wsaa.ObtenerTagXml("expirationTime")
            generation = wsaa.ObtenerTagXml("generationTime")
        except Exception as e:
            err_msg = wsaa.Excepcion if wsaa.Excepcion else str(e)
            raise UserError(_(
                "Error autenticando con WSAA para A13: %s"
            ) % err_msg)

        # Normalizar timestamps ISO → Datetime de Odoo (quitar T, truncar a 19 chars)
        gen_dt = generation.replace("T", " ")[:19] if generation else False
        exp_dt = expiration.replace("T", " ")[:19] if expiration else False

        # Cachear en DB para reutilizar hasta que expire
        self.env["guvens.a13.token.cache"].sudo().create({
            "company_id": self.id,
            "token": token,
            "sign": sign,
            "generation_time": gen_dt,
            "expiration_time": exp_dt,
        })
        _logger.info("A13 token cached until %s for company %s", exp_dt, self.name)

        return token, sign
