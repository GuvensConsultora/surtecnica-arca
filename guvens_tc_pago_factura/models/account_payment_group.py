# -*- coding: utf-8 -*-
import math
from odoo import api, fields, models


class AccountPaymentGroup(models.Model):
    _inherit = 'account.payment.group'

    usar_tc_factura = fields.Boolean(
        string='Usar TC de la factura',
        default=lambda self: self.env.company.usar_tc_factura_cobros,
        help=(
            'Activo: el cobro entra en pesos (la caja/banco registra ARS) pero '
            'la línea de cuenta por cobrar se valúa en USD al TC de la factura, '
            'de modo que la factura en dólares cierra exactamente sin generar '
            'diferencia de cambio ni crédito flotante en el cliente.\n'
            'Inactivo: comportamiento estándar de Odoo (TC del día del cobro).'
        ),
    )

    @api.onchange('company_id')
    def _onchange_company_usar_tc(self):
        self.usar_tc_factura = self.company_id.usar_tc_factura_cobros

    def post(self):
        """
        Hook sobre el post() de ADHOC (account_payment_group).

        Cuando usar_tc_factura está activo, antes de llamar a super():
          1. Calcula el TC ponderado de las facturas a cancelar.
          2. Convierte cada pago ARS a USD al TC factura.
          3. Postea los pagos con force_tc_factura en el contexto.

        _prepare_move_line_default_vals (en account_payment.py) intercepta ese
        contexto y construye el asiento usando el TC factura en lugar del TC del
        día. El resultado es un asiento multi-moneda nativo de Odoo:
          - Efectivo: ARS (lo que físicamente se cobró)
          - CxC:     USD al TC factura (cierra la factura exacto, sin CAMBI)

        super().post() saltea el re-post (filtra solo draft) y reconcilia
        directamente las CxC del cobro con las de la factura → match exacto en
        USD y ARS, sin diferencia de cambio ni crédito flotante.

        Ventaja sobre el enfoque anterior (parchear líneas post-posted): el
        asiento es válido para Odoo desde el origen, por lo que puede reversarse
        con el botón estándar de la UI.
        """
        for rec in self:
            if rec.usar_tc_factura:
                rec._preparar_pagos_a_tc_factura()
        return super().post()

    def _preparar_pagos_a_tc_factura(self):
        self.ensure_one()
        usd = self.env.ref('base.USD')
        company_currency = self.env.company.currency_id

        # Facturas USD que este grupo cancela
        lineas_factura = self.to_pay_move_line_ids.filtered(
            lambda l: l.currency_id == usd
        )
        if not lineas_factura:
            return

        usd_residual = abs(sum(l.amount_residual_currency for l in lineas_factura))
        ars_residual = abs(sum(l.amount_residual for l in lineas_factura))
        if not usd_residual or not ars_residual:
            return
        tc_factura = ars_residual / usd_residual

        pagos_draft = self.payment_ids.filtered(
            lambda x: x.state == 'draft' and x.currency_id == company_currency
        )
        if not pagos_draft:
            return

        for payment in pagos_draft:
            ars_pago = payment.amount
            usd_pago = round(ars_pago / tc_factura, 2)

            # Snap: si la diferencia es menor a 0,01 USD usamos el residual exacto
            # para que la factura cierre sin fracción de centavo.
            if abs(usd_pago - usd_residual) < 0.01:
                usd_pago = usd_residual

            payment.with_context(
                skip_account_move_synchronization=True,
                check_move_validity=False,
            ).write({
                'currency_id': usd.id,
                'amount': usd_pago,
            })

        # Postear con el TC factura en contexto → _prepare_move_line_default_vals
        # lo usará para construir el asiento con los ARS correctos.
        pagos_draft.with_context(force_tc_factura=tc_factura).action_post()
