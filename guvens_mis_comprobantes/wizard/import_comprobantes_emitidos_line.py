# -*- coding: utf-8 -*-
from odoo import models, fields


class ImportEmitidosLine(models.TransientModel):
    _name = 'guvens.import.emitidos.line'
    _description = 'Línea de previsualización — Comprobantes Emitidos ARCA'
    _order = 'date, doc_type, pos_number, doc_number'

    wizard_id = fields.Many2one(
        'guvens.import.comprobantes.emitidos',
        string='Asistente',
        ondelete='cascade',
        required=True,
    )

    # -- Datos crudos del CSV de ARCA (Mis Comprobantes Emitidos) --
    date = fields.Date(string='Fecha emisión')
    afip_code = fields.Char(string='Cód. ARCA')
    doc_type_label = fields.Char(string='Tipo comprobante')
    pos_number = fields.Char(string='Punto de venta')
    doc_number = fields.Char(string='Número')
    cae = fields.Char(string='CAE')

    receiver_doc_type = fields.Char(string='Tipo doc. receptor')
    receiver_vat = fields.Char(string='CUIT/DNI receptor')
    receiver_name = fields.Char(string='Denominación receptor')

    currency_code = fields.Char(string='Moneda')
    exchange_rate = fields.Float(string='Tipo cambio', digits=(16, 4))

    amount_neto_0 = fields.Float('Neto 0%', digits=(16, 2))
    amount_iva_25 = fields.Float('IVA 2,5%', digits=(16, 2))
    amount_neto_25 = fields.Float('Neto 2,5%', digits=(16, 2))
    amount_iva_5 = fields.Float('IVA 5%', digits=(16, 2))
    amount_neto_5 = fields.Float('Neto 5%', digits=(16, 2))
    amount_iva_105 = fields.Float('IVA 10,5%', digits=(16, 2))
    amount_neto_105 = fields.Float('Neto 10,5%', digits=(16, 2))
    amount_iva_21 = fields.Float('IVA 21%', digits=(16, 2))
    amount_neto_21 = fields.Float('Neto 21%', digits=(16, 2))
    amount_iva_27 = fields.Float('IVA 27%', digits=(16, 2))
    amount_neto_27 = fields.Float('Neto 27%', digits=(16, 2))

    amount_neto_total = fields.Float('Neto total', digits=(16, 2))
    amount_no_gravado = fields.Float('No gravado', digits=(16, 2))
    amount_exento = fields.Float('Exento', digits=(16, 2))
    amount_otros_tributos = fields.Float('Otros tributos', digits=(16, 2))
    amount_iva_total = fields.Float('IVA total', digits=(16, 2))
    amount_total = fields.Float('Total', digits=(16, 2))

    # -- move_type derivado del código de comprobante (1=FA-A→out_invoice, 3=NC-A→out_refund, etc.) --
    move_type = fields.Selection([
        ('out_invoice', 'Factura/ND cliente'),
        ('out_refund', 'Nota de crédito cliente'),
    ], string='Tipo Odoo')

    # Por qué: ARCA distingue Factura (FA) de Nota de Débito (ND) por código,
    # pero ambas son out_invoice en Odoo y se diferencian por l10n_latam_document_type
    is_debit_note = fields.Boolean(string='Es nota de débito')

    # -- Estado de la previsualización (color en la grilla) --
    state = fields.Selection([
        ('exists', 'Ya existe en Odoo'),
        ('to_create', 'Se va a crear'),
        ('needs_review', 'Requiere revisión'),
        ('invalid', 'No procesable'),
    ], string='Estado', default='to_create')

    # Por qué: detalle textual de qué hay para revisar (cliente nuevo, condición IVA ambigua)
    review_note = fields.Char(string='Nota de revisión')

    # -- Vínculos con datos de Odoo --
    existing_move_id = fields.Many2one(
        'account.move',
        string='Factura existente',
        help='Si la factura ya está en Odoo, este es el registro vinculado.',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente',
        help='Cliente existente que matchea por CUIT, o vacío si se va a crear.',
    )
    partner_will_be_created = fields.Boolean(
        string='Cliente nuevo',
        help='Si está marcado, el cliente no existe y se creará al confirmar.',
    )

    # -- Override del usuario antes de confirmar --
    sale_account_id = fields.Many2one(
        'account.account',
        string='Cuenta de ventas',
        help='Cuenta a la que se imputarán las líneas. Por defecto, la del módulo.',
    )

    # -- Resultado post-confirmación --
    created_move_id = fields.Many2one(
        'account.move',
        string='Factura creada',
        help='Se llena después de confirmar la importación.',
    )

    # -- Display helpers --
    display_comprobante = fields.Char(
        string='Comprobante',
        compute='_compute_display',
    )

    def _compute_display(self):
        for rec in self:
            pos = (rec.pos_number or '').zfill(5)
            num = (rec.doc_number or '').zfill(8)
            label = rec.doc_type_label or rec.afip_code or ''
            rec.display_comprobante = '%s %s-%s' % (label, pos, num)
