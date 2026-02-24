# Cache de tokens WSAA para WS A13
# Por qué: evita re-autenticar con WSAA en cada consulta al padrón.
# AFIP otorga tokens con TTL de ~12hs, los cacheamos en DB para reutilizar.
# Patrón: similar a afipws.connection pero sin depender de ese modelo.
from odoo import fields, models, api
import logging

_logger = logging.getLogger(__name__)


class A13TokenCache(models.Model):
    _name = "guvens.a13.token.cache"
    _description = "Cache de tokens WSAA para WS A13"
    _order = "expiration_time desc"

    company_id = fields.Many2one(
        "res.company", required=True, ondelete="cascade", index=True,
    )
    token = fields.Text(required=True)
    sign = fields.Text(required=True)
    generation_time = fields.Datetime()
    expiration_time = fields.Datetime(required=True, index=True)

    @api.model
    def _get_valid_token(self, company):
        """Busca token no expirado para la company dada.
        Retorna recordset (vacío si no hay token válido)."""
        return self.sudo().search([
            ("company_id", "=", company.id),
            ("expiration_time", ">", fields.Datetime.now()),
        ], limit=1)
