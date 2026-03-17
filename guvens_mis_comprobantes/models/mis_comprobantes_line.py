# -*- coding: utf-8 -*-
from odoo import models, fields, api


class MisComprobantesLine(models.Model):
    _name = 'guvens.mis.comprobantes.line'
    _description = 'Línea de comprobante importado de ARCA'
    _order = 'date desc, partner_name'
    # Por qué: valida que move_id pertenezca a la misma company que la línea
    _check_company_auto = True

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

    # -- Datos del CSV de ARCA --
    date = fields.Date(string='Fecha emisión')
    doc_type = fields.Char(string='Tipo comprobante')
    # Por qué: código numérico ARCA (1=FA-A, 3=NC-A, 6=FA-B, 11=FA-C, etc.)
    # Solo se llena desde Portal IVA; Mis Comprobantes usa texto libre en doc_type
    afip_code = fields.Char(string='Cód. ARCA')
    pos_number = fields.Char(string='Punto de venta')
    doc_number = fields.Char(string='Número')
    cae = fields.Char(string='CAE')
    partner_vat = fields.Char(string='CUIT emisor')
    partner_name = fields.Char(string='Denominación emisor')
    amount_total = fields.Float(string='Total ARCA', digits=(16, 2))
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
        ('missing_in_afip', 'Falta en ARCA'),
    ], string='Estado', default='missing_in_odoo')

    move_id = fields.Many2one(
        'account.move',
        string='Factura Odoo',
        ondelete='set null',
        check_company=True,
    )

    # Por qué: score 0-100 indica la confianza del cruce por aproximación
    # 100 = match exacto (CUIT + PV + nro + fecha + importe)
    # <100 = match parcial, el usuario decide si corresponde
    match_score = fields.Integer(
        string='Confianza %',
        help='0-100: indica qué tan probable es que el registro ARCA '
             'corresponda a la factura Odoo vinculada',
    )
    # Por qué: detalle legible de qué criterios matchearon y cuáles no
    match_detail = fields.Char(string='Detalle cruce')

    # -- Campos computed para la vista comparativa --

    # Por qué: unificar PV-Nro en un solo string legible para la lista
    display_comprobante = fields.Char(
        string='Comprobante',
        compute='_compute_display_fields',
    )
    # Por qué: mostrar el total de Odoo al lado del total ARCA para comparar
    odoo_amount_total = fields.Float(
        string='Total Odoo',
        compute='_compute_display_fields',
        digits=(16, 2),
    )
    # Por qué: mostrar fecha Odoo al lado de fecha ARCA
    odoo_date = fields.Date(
        string='Fecha Odoo',
        compute='_compute_display_fields',
    )
    # Por qué: mostrar nombre del proveedor en Odoo para comparar con ARCA
    odoo_partner_name = fields.Char(
        string='Proveedor Odoo',
        compute='_compute_display_fields',
    )
    diff_amount = fields.Float(
        string='Diferencia $',
        compute='_compute_display_fields',
        digits=(16, 2),
    )

    @api.depends('pos_number', 'doc_number', 'doc_type',
                 'amount_total', 'move_id', 'move_id.amount_total',
                 'move_id.invoice_date', 'move_id.partner_id')
    def _compute_display_fields(self):
        for rec in self:
            # Comprobante: "FA-A 00001-00000123" o "1 00400-29594"
            pos = (rec.pos_number or '').zfill(5)
            num = (rec.doc_number or '').zfill(8)
            label = rec.doc_type or rec.afip_code or ''
            rec.display_comprobante = '%s %s-%s' % (label, pos, num)

            if rec.move_id:
                rec.odoo_amount_total = abs(rec.move_id.amount_total)
                rec.odoo_date = rec.move_id.invoice_date
                rec.odoo_partner_name = rec.move_id.partner_id.name or ''
                rec.diff_amount = abs(rec.move_id.amount_total) - rec.amount_total
            else:
                rec.odoo_amount_total = 0.0
                rec.odoo_date = False
                rec.odoo_partner_name = ''
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
