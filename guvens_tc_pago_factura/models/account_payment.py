# -*- coding: utf-8 -*-
from odoo import models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def _prepare_move_line_default_vals(self, write_off_line_vals=None):
        """
        Intercepta la construcción del asiento del cobro cuando force_tc_factura
        está en el contexto.

        Odoo calcula los importes ARS de las líneas usando el TC del día
        (currency._convert). Aquí sustituimos esos importes por los calculados
        al TC de la factura, de modo que:
          - Línea Efectivo/banco: ARS correcto (lo que físicamente se cobró)
          - Línea CxC:           USD exacto con ARS al TC factura

        El asiento resultante está balanceado (D = C en ARS) y tiene monedas
        consistentes → Odoo lo acepta como multi-moneda nativo, sin restricción
        de reversión.
        """
        vals_list = super()._prepare_move_line_default_vals(write_off_line_vals)

        tc_factura = self._context.get('force_tc_factura')
        if not tc_factura:
            return vals_list

        company_currency = self.company_id.currency_id
        if self.currency_id == company_currency:
            # Pago ya en moneda de la compañía: no hay nada que ajustar
            return vals_list

        # ars_amount = importe ARS que el cliente pagó físicamente
        ars_amount = round(self.amount * tc_factura, 2)

        for vals in vals_list:
            cur_id = vals.get('currency_id')
            if cur_id == self.currency_id.id:
                # Línea de CxC (USD): corregir el balance ARS; amount_currency
                # ya viene bien desde super() (-usd_amount).
                if vals.get('credit', 0) > 0:
                    vals['credit'] = ars_amount
                elif vals.get('debit', 0) > 0:
                    vals['debit'] = ars_amount
            else:
                # Línea de Efectivo/banco (ARS): corregir balance y amount_currency
                if vals.get('debit', 0) > 0:
                    vals['debit'] = ars_amount
                    vals['amount_currency'] = ars_amount
                elif vals.get('credit', 0) > 0:
                    vals['credit'] = ars_amount
                    vals['amount_currency'] = -ars_amount

        return vals_list
