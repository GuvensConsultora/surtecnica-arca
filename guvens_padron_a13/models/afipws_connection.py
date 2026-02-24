# Extensión de afipws.connection para registrar el WS A13
# Por qué: seguimos el mismo patrón de extensión que usa l10n_ar_padron
# para ws_sr_constancia_inscripcion — selection_add + override de _get_ws/get_afip_ws_url.
from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)


class AfipwsConnection(models.Model):
    _inherit = "afipws.connection"

    # Tip: selection_add extiende el campo sin pisar las opciones existentes
    afip_ws = fields.Selection(
        selection_add=[
            ('ws_sr_padron_a13', 'Servicio de Consulta de Padrón Alcance 13'),
        ],
        ondelete={'ws_sr_padron_a13': 'cascade'},
    )

    @api.model
    def get_afip_ws_url(self, afip_ws, environment_type):
        """Agrega URLs del endpoint A13 (producción y homologación)."""
        if afip_ws == 'ws_sr_padron_a13':
            if environment_type == 'production':
                return (
                    "https://aws.afip.gov.ar/sr-padron/webservices/"
                    "personaServiceA13?WSDL")
            else:
                return (
                    "https://awshomo.afip.gov.ar/sr-padron/webservices/"
                    "personaServiceA13?WSDL")
        return super().get_afip_ws_url(afip_ws, environment_type)

    @api.model
    def _get_ws(self, afip_ws):
        """Instancia WSSrPadronA13 para conexiones de tipo ws_sr_padron_a13."""
        if afip_ws == 'ws_sr_padron_a13':
            from .ws_sr_padron_a13 import WSSrPadronA13
            return WSSrPadronA13()
        return super()._get_ws(afip_ws)

    def connect(self):
        """Setea HOMO=False para A13 (mismo parche que A4/A5 en el módulo base)."""
        self.ensure_one()
        if self.afip_ws == 'ws_sr_padron_a13':
            ws = self._get_ws(self.afip_ws)
            ws.HOMO = False
            wsdl = self.afip_ws_url
            ws.Conectar("", wsdl or "", "")
            ws.Cuit = self.company_id.vat
            ws.Token = self.token
            ws.Sign = self.sign
            ws.Obs = ''
            ws.Errores = []
            _logger.info('Connection A13 with url "%s", cuit "%s"', wsdl, ws.Cuit)
            return ws
        return super().connect()
