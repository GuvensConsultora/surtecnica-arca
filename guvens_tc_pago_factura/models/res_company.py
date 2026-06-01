# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    usar_tc_factura_cobros = fields.Boolean(
        string='Usar TC de la factura en cobros USD',
        default=True,
        help='Cuando está activo, al confirmar un cobro sobre una factura en USD '
             'el sistema cancela automáticamente la diferencia de tipo de cambio '
             'generada por Odoo, dejando la cuenta corriente del cliente en cero. '
             'Aplica cuando el acuerdo comercial es cobrar al TC de la factura '
             'original, no al TC del día del cobro.',
    )
