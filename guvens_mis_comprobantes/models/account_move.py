# -*- coding: utf-8 -*-
from odoo import models, fields


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Por qué: link inverso para saber desde la factura si fue cruzada con AFIP
    mis_comprobantes_line_ids = fields.One2many(
        'guvens.mis.comprobantes.line',
        'move_id',
        string='Cruce AFIP',
    )
    mis_comprobantes_state = fields.Selection(
        related='mis_comprobantes_line_ids.state',
        string='Estado cruce AFIP',
    )
