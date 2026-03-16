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

    # Por qué: distinguir el origen del CSV para saber qué parser se usó
    # y qué campos tienen datos válidos
    source = fields.Selection([
        ('mis_comprobantes', 'Mis Comprobantes'),
        ('portal_iva', 'Portal IVA'),
    ], string='Origen', default='mis_comprobantes')

    # -- Datos del CSV de AFIP --
    date = fields.Date(string='Fecha emisión')
    doc_type = fields.Char(string='Tipo comprobante')
    # Por qué: código numérico AFIP (1=FA-A, 3=NC-A, 6=FA-B, 11=FA-C, etc.)
    # Solo se llena desde Portal IVA; Mis Comprobantes usa texto libre en doc_type
    afip_code = fields.Char(string='Cód. AFIP')
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

    # -- Multi-moneda (solo Portal IVA) --
    currency_code = fields.Char(string='Moneda')
    exchange_rate = fields.Float(string='Tipo cambio', digits=(16, 4))

    # -- Percepciones (solo Portal IVA) --
    amount_perc_iibb = fields.Float('Perc. IIBB', digits=(16, 2))
    amount_perc_iva = fields.Float('Perc. IVA', digits=(16, 2))
    amount_perc_municipal = fields.Float('Imp. Municipal', digits=(16, 2))
    amount_perc_internos = fields.Float('Imp. Internos', digits=(16, 2))
    amount_perc_otros_nac = fields.Float('Perc. Otros Nac.', digits=(16, 2))
    amount_otros_tributos = fields.Float('Otros Tributos', digits=(16, 2))

    # -- IVA desglosado por alícuota (solo Portal IVA) --
    neto_iva_0 = fields.Float('Neto 0%', digits=(16, 2))
    neto_iva_25 = fields.Float('Neto 2.5%', digits=(16, 2))
    iva_25 = fields.Float('IVA 2.5%', digits=(16, 2))
    neto_iva_5 = fields.Float('Neto 5%', digits=(16, 2))
    iva_5 = fields.Float('IVA 5%', digits=(16, 2))
    neto_iva_105 = fields.Float('Neto 10.5%', digits=(16, 2))
    iva_105 = fields.Float('IVA 10.5%', digits=(16, 2))
    neto_iva_21 = fields.Float('Neto 21%', digits=(16, 2))
    iva_21 = fields.Float('IVA 21%', digits=(16, 2))
    neto_iva_27 = fields.Float('Neto 27%', digits=(16, 2))
    iva_27 = fields.Float('IVA 27%', digits=(16, 2))
    amount_no_gravado = fields.Float('No Gravado (IVA)', digits=(16, 2))

    # Por qué: crédito fiscal = total IVA computable del portal IVA
    credito_fiscal = fields.Float('Crédito Fiscal', digits=(16, 2))

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

    # Por qué: score 0-100 indica la confianza del cruce por aproximación
    # 100 = match exacto (CUIT + nro + fecha + importe)
    # <100 = match parcial, el usuario decide si corresponde
    match_score = fields.Integer(
        string='Confianza %',
        help='0-100: indica qué tan probable es que el registro AFIP '
             'corresponda a la factura Odoo vinculada',
    )
    # Por qué: detalle legible de qué criterios matchearon y cuáles no
    match_detail = fields.Char(string='Detalle cruce')

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
