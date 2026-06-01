# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountPaymentGroup(models.Model):
    _inherit = 'account.payment.group'

    usar_tc_factura = fields.Boolean(
        string='Usar TC de la factura',
        default=lambda self: self.company_id.usar_tc_factura_cobros,
        help=(
            'Activo: al confirmar el cobro en ARS sobre una factura en USD, '
            'el sistema usa el TC de la factura para convertir, de forma que '
            'la deuda en USD cierra exactamente a cero sin diferencia de cambio.\n'
            'Inactivo: comportamiento estándar de Odoo (TC del día del cobro).'
        ),
    )

    @api.onchange('company_id')
    def _onchange_company_usar_tc(self):
        self.usar_tc_factura = self.company_id.usar_tc_factura_cobros

    def _reconcile_payments(self, writeoff_account_id=False, writeoff_journal_id=False):
        """
        Intercepta la reconciliación para aplicar el TC de la factura.

        El problema: Odoo convierte el pago en ARS a USD usando el TC del día
        del cobro. Si ese TC difiere del TC de la factura, genera un CAMBI que
        deja un crédito flotante en la cuenta del cliente.

        La solución: antes de reconciliar, anotar en la línea de CxC del cobro
        el monto exacto en USD que corresponde a la deuda (a TC de la factura).
        Así Odoo ve coincidencia exacta en USD → no genera CAMBI → cliente $0.
        """
        for pg in self:
            if pg.usar_tc_factura:
                pg._aplicar_tc_factura_en_cobros()
        return super()._reconcile_payments(
            writeoff_account_id=writeoff_account_id,
            writeoff_journal_id=writeoff_journal_id,
        )

    def _aplicar_tc_factura_en_cobros(self):
        """
        Busca el TC de cada factura en to_pay_move_line_ids y lo usa para
        convertir el ARS del cobro a USD antes de reconciliar.

        Fórmula: USD_cobro = ARS_cobro / TC_factura

        Así Odoo ve USD_cobro = USD_deuda → cierre exacto sin diferencia de cambio.

        Para múltiples facturas con distintos TC: se usa el TC ponderado por
        el monto ARS de cada factura (TC promedio del conjunto a cancelar).
        """
        self.ensure_one()
        usd = self.env.ref('base.USD')

        # Líneas de facturas en USD que este cobro cancela
        lineas_usd = self.to_pay_move_line_ids.filtered(
            lambda l: l.currency_id == usd
        )
        if not lineas_usd:
            return

        # TC de la factura: campo l10n_ar_currency_rate en el account.move.
        # Si hay varias facturas con distintos TC, se pondera por monto ARS.
        # Ejemplo: FAC A $100.000 ARS @ TC 1.480 + FAC B $50.000 ARS @ TC 1.500
        #   → TC ponderado = (100.000 × 1.480 + 50.000 × 1.500) / 150.000 = 1.487
        total_ars = 0.0
        suma_ponderada = 0.0
        for linea in lineas_usd:
            factura = linea.move_id
            tc = factura.l10n_ar_currency_rate or 0.0
            if not tc:
                # Fallback: calcular TC implícito desde la propia línea
                # TC = monto_ars / monto_usd (solo si ambos disponibles)
                if linea.amount_currency:
                    tc = abs(linea.balance) / abs(linea.amount_currency)
            monto_ars = abs(linea.amount_residual)
            total_ars += monto_ars
            suma_ponderada += monto_ars * tc

        if not total_ars or not suma_ponderada:
            return

        tc_factura = suma_ponderada / total_ars
        if tc_factura <= 0:
            return  # TC inválido: no intervenir

        # Para cada cobro: USD = ARS_cobro / TC_factura
        for payment in self.payment_ids:
            lineas_cxc = payment.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
                and l.currency_id != usd
            )
            if not lineas_cxc:
                continue

            # ARS total de este cobro sobre cuentas por cobrar
            ars_cobro = abs(sum(l.balance for l in lineas_cxc))
            if not ars_cobro:
                continue

            usd_calculado = ars_cobro / tc_factura

            # Snap de redondeo: si el USD calculado difiere del residual
            # exacto en la factura en menos de 0,01 USD, usamos el residual
            # exacto para evitar que quede cualquier fracción de centavo
            # generando un CAMBI mínimo.
            usd_residual_exacto = abs(sum(
                l.amount_residual_currency for l in lineas_usd
            ))
            if usd_residual_exacto and abs(usd_calculado - usd_residual_exacto) < 0.01:
                usd_calculado = usd_residual_exacto

            # Anotar el USD calculado en la línea del cobro para que
            # la reconciliación use el TC de la factura y no el TC del día.
            # no_lock_date_check=True: necesario en Odoo 17 cuando el período
            # tiene fecha de bloqueo seteada.
            lineas_cxc.with_context(
                check_move_validity=False,
                skip_account_move_synchronization=True,
                no_lock_date_check=True,
            ).write({
                'currency_id': usd.id,
                'amount_currency': -usd_calculado,
            })
