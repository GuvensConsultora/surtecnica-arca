# Puente entre odoo-argentina (afipws.certificate) y Odoo Enterprise (l10n_ar)
# Por qué: get_key_and_certificate() de l10n_ar_afipws busca en afipws.certificate,
# pero Odoo Enterprise guarda el certificado en campos Binary de res.company
# (l10n_ar_afip_ws_key / l10n_ar_afip_ws_crt). Este override agrega ese fallback.
from odoo import models
import base64
import logging

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    def get_key_and_certificate(self, environment_type):
        """Extiende búsqueda de certificado: si odoo-argentina no lo encuentra,
        intenta leer de los campos estándar de Odoo Enterprise (l10n_ar)."""
        try:
            return super().get_key_and_certificate(environment_type)
        except Exception:
            # Fallback: campos Binary de Odoo Enterprise
            pkey = self.l10n_ar_afip_ws_key
            crt = self.l10n_ar_afip_ws_crt
            if pkey and crt:
                _logger.info('Using Odoo Enterprise certificate for company %s', self.name)
                # Campos Binary se almacenan en base64 → decodificar a PEM text
                return (
                    base64.b64decode(pkey).decode('ascii'),
                    base64.b64decode(crt).decode('ascii'),
                )
            raise
