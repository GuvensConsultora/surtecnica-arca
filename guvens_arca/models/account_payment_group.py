# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountPaymentGroup(models.Model):
    _inherit = 'account.payment.group'

    # Por qué: el módulo account_payment_group define payment_ids con
    # 'states' (deprecado desde v17) y 'ondelete' (inválido en One2many).
    # Redefinimos el campo por herencia para eliminar ambos warnings.
    payment_ids = fields.One2many(
        'account.payment',
        'payment_group_id',
        string='Payment Lines',
        readonly=True,
    )
