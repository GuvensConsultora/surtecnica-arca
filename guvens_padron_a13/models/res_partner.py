# Override de check_padron() para usar WS A13 en vez de ws_sr_constancia_inscripcion
# Por qué: A13 es el servicio recomendado por ARCA para consultar datos de contribuyentes.
# La respuesta de A13 es compatible con A5, así que reutilizamos parce_census_vals() sin cambios.
from odoo import models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = "res.partner"

    def check_padron(self):
        """Consulta padrón AFIP usando WS A13 (reemplaza ws_sr_constancia_inscripcion)."""
        self.ensure_one()
        cuit = self.ensure_vat()

        # Por qué: get_connection() ya maneja la búsqueda de certificado internamente
        # via _create_connection → get_key_and_certificate. No necesitamos pre-chequear.
        # El código original de l10n_ar_padron tenía un fallback con bug en el search.
        company = self.env.user.company_id
        padron = company.get_connection('ws_sr_padron_a13').connect()

        error_msg = _(
            'No pudimos actualizar desde padron afip al partner %s (%s).\n'
            'Recomendamos verificar manualmente en la página de AFIP.\n'
            'Obtuvimos este error: %s')
        try:
            padron.Consultar(cuit)
        except Exception as e:
            raise UserError(error_msg % (self.name, cuit, e))

        if not padron.denominacion or padron.denominacion == ', ':
            raise UserError(error_msg % (
                self.name, cuit, 'La afip no devolvió nombre'))

        # Tip: parce_census_vals() viene de l10n_ar_padron, no necesita override
        vals = self.parce_census_vals(padron)
        # Por qué: estos campos no se escriben en el partner desde check_padron()
        # (misma lógica que el módulo original)
        del vals['imp_iva_padron']
        del vals['last_update_census']
        del vals['imp_ganancias_padron']
        self.write(vals)
        return vals
