# -*- coding: utf-8 -*-
from odoo import models, fields, api


class MisComprobantesLine(models.Model):
    _name = 'guvens.mis.comprobantes.line'
    _description = 'Línea de comprobante importado de AFIP'
    _order = 'date desc, partner_name'

    # -- Campos de importación --
    import_date = fields.Date(
        string='Fecha importación',
        default=fields.Date.context_today,
    )
    period = fields.Char(string='Período')
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        default=lambda self: self.env.company,
    )

    # -- Datos del CSV de AFIP --
    date = fields.Date(string='Fecha emisión')
    doc_type = fields.Char(string='Tipo comprobante')
    pos_number = fields.Char(string='Punto de venta')
    doc_number = fields.Char(string='Número')
    cae = fields.Char(string='CAE')
    partner_vat = fields.Char(string='CUIT emisor')
    partner_name = fields.Char(string='Denominación emisor')
    amount_total = fields.Float(string='Total AFIP', digits=(16, 2))
    amount_net = fields.Float(string='Neto gravado', digits=(16, 2))
    amount_exempt = fields.Float(string='Exento', digits=(16, 2))
    amount_untaxed = fields.Float(string='No gravado', digits=(16, 2))
    amount_iva = fields.Float(string='IVA', digits=(16, 2))

    # -- Resultado del cruce --
    state = fields.Selection([
        ('match', 'Coincide'),
        ('mismatch', 'Diferencia importe'),
        ('missing_in_odoo', 'Falta en Odoo'),
        ('missing_in_afip', 'Falta en AFIP'),
    ], string='Estado', default='missing_in_odoo')

    move_id = fields.Many2one(
        'account.move',
        string='Factura Odoo',
        ondelete='set null',
    )

    # Por qué: computed para mostrar la diferencia sin almacenarla
    # Patrón: campo computed no stored — se calcula on-the-fly
    diff_amount = fields.Float(
        string='Diferencia',
        compute='_compute_diff_amount',
        digits=(16, 2),
    )

    @api.depends('amount_total', 'move_id.amount_total', 'state')
    def _compute_diff_amount(self):
        for rec in self:
            if rec.move_id:
                rec.diff_amount = abs(rec.move_id.amount_total) - rec.amount_total
            else:
                rec.diff_amount = 0.0

    def action_open_move(self):
        """Abre la factura linkeada en Odoo."""
        self.ensure_one()
        if not self.move_id:
            return
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
