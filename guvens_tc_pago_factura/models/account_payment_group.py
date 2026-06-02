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

        ADHOC postea los pagos y reconcilia inline dentro de post():
            counterpart_aml = payment_ids.invoice_line_ids (líneas CxC del cobro)
            (counterpart_aml + to_pay_move_line_ids).reconcile()

        Cuando usar_tc_factura está activo, posteamos los pagos nosotros ANTES
        de llamar a super() y ajustamos la línea de cuenta por cobrar del cobro
        para que quede valuada en USD al TC de la factura. Así, cuando super()
        reconcilia, las dos líneas coinciden en pesos Y en dólares → sin CAMBI.

        super() saltea el re-post porque filtra solo pagos en 'draft'.

        Por qué este enfoque (y no forzar el pago a USD): la caja/banco debe
        quedar en pesos (es lo que físicamente se cobró). Solo la contrapartida
        de cuenta por cobrar lleva la info en dólares para cerrar la factura.
        """
        for rec in self:
            if rec.usar_tc_factura:
                rec._ajustar_cobro_a_tc_factura()
        return super().post()

    def _ajustar_cobro_a_tc_factura(self):
        self.ensure_one()
        usd = self.env.ref('base.USD')

        # Facturas en USD que este grupo cancela
        lineas_factura = self.to_pay_move_line_ids.filtered(
            lambda l: l.currency_id == usd
        )
        if not lineas_factura:
            return  # No hay facturas USD: nada que ajustar

        # USD residual total y TC ponderado de las facturas a cancelar.
        # TC factura = ARS residual / USD residual (rate implícito de la factura)
        usd_residual = abs(sum(l.amount_residual_currency for l in lineas_factura))
        ars_residual = abs(sum(l.amount_residual for l in lineas_factura))
        if not usd_residual or not ars_residual:
            return
        tc_factura = ars_residual / usd_residual
        if tc_factura <= 0:
            return

        # Postear los pagos ahora para poder ajustar sus líneas antes de que
        # super().post() reconcilie (super saltea el re-post: filtra solo draft).
        self.payment_ids.filtered(lambda x: x.state == 'draft').action_post()

        # Cantidad de líneas de CxC a ajustar en el conjunto de cobros
        lineas_cxc_total = self.payment_ids.move_id.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable'
            and l.currency_id != usd
        )
        linea_unica = len(lineas_cxc_total) == 1

        for payment in self.payment_ids:
            lineas_cxc = payment.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
                and l.currency_id != usd
            )
            for linea in lineas_cxc:
                ars = abs(linea.balance)
                if not ars:
                    continue
                usd_linea = ars / tc_factura

                # Snap de redondeo: si es la única línea y el USD calculado
                # difiere del residual exacto de la factura en menos de 0,01 USD,
                # usamos el residual exacto para cerrar sin fracción de centavo.
                if linea_unica and abs(usd_linea - usd_residual) < 0.01:
                    usd_linea = usd_residual

                # El signo del amount_currency debe coincidir con el del balance
                # (en un cobro la línea de CxC va al crédito → balance negativo).
                amount_currency = math.copysign(usd_linea, linea.balance)

                # CLAVE: fijar también débito/crédito (importe en pesos) en el
                # mismo write. Si solo se escribe amount_currency, Odoo recalcula
                # el balance usando el TC del DÍA (no el de la factura) y el
                # asiento queda desbalanceado. Al pinear debit/credit con los
                # pesos originales, el TC implícito queda en el de la factura.
                # skip_account_move_synchronization: evita que la sync de
                # account.payment revierta el cambio.
                linea.with_context(
                    check_move_validity=False,
                    skip_account_move_synchronization=True,
                    no_lock_date_check=True,
                ).write({
                    'currency_id': usd.id,
                    'amount_currency': amount_currency,
                    'debit': linea.debit,
                    'credit': linea.credit,
                })
