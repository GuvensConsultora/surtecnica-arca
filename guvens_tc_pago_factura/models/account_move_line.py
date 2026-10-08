# -*- coding: utf-8 -*-
from odoo import models

# Mismos códigos que excluye account_payment_group._preparar_pagos_a_tc_factura.
CODIGOS_CHEQUE = (
    'check_printing',
    'new_third_party_checks',
    'in_third_party_checks',
    'out_third_party_checks',
)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _prepare_move_line_residual_amounts(self, aml_values, counterpart_currency,
                                            shadowed_aml_values=None, other_aml_values=None):
        """
        Cheques en órdenes de pago a PROVEEDORES con «Usar TC de la factura»:
        concilia al TC de cada factura.

        Por qué: Sur Técnica cancela la deuda en USD con sus proveedores al TC
        de la factura; si el proveedor entiende que hay diferencia, la reclama
        con nota de débito (Candela, 08/10/2026). Odoo, en cambio, pasa los
        pesos del pago a dólares al TC de la fecha del pago y lleva la
        diferencia a un asiento CAMBI.

        Por qué acá y no convirtiendo el pago a USD (como con efectivo/banco):
        el core exige que un pago con cheque tenga la moneda y el importe del
        cheque (l10n_latam_check) y que el asiento del pago esté en una sola
        moneda (account.payment._synchronize_from_moves). Este método decide a
        qué TC se convierte la línea en pesos al conciliarla contra la línea en
        USD de una factura: se reemplaza ese TC por el de la factura y el pago
        queda intacto. Con el TC de cada factura los importes en pesos coinciden
        y no se genera diferencia de cambio.

        No se usa el contexto porque account_payment_group.post() lo limpia
        (with_context({})) antes de conciliar: la condición sale de los datos
        del pago. Cobros de clientes: sin cambios.
        """
        res = super()._prepare_move_line_residual_amounts(
            aml_values, counterpart_currency,
            shadowed_aml_values=shadowed_aml_values, other_aml_values=other_aml_values)

        aml = aml_values['aml']
        other_aml = (other_aml_values or {}).get('aml')
        if (
            not other_aml
            or counterpart_currency not in res
            or counterpart_currency == aml.company_currency_id
            or aml.currency_id != aml.company_currency_id
            or aml.account_id.account_type != 'liability_payable'
            or other_aml.currency_id != counterpart_currency
            or not other_aml.move_id.is_invoice(include_receipts=True)
        ):
            return res

        payment = aml.move_id.payment_id
        group = payment.payment_group_id
        if not (
            group.usar_tc_factura
            and group.partner_type == 'supplier'
            and payment.payment_method_line_id.code in CODIGOS_CHEQUE
        ):
            return res

        # TC contable de la factura: USD por cada peso, como lo espera el core.
        balance = other_aml.balance
        amount_currency = other_aml.amount_currency
        if aml.company_currency_id.is_zero(balance) or counterpart_currency.is_zero(amount_currency):
            return res
        rate = abs(amount_currency / balance)
        res[counterpart_currency] = {
            'residual': counterpart_currency.round(aml_values['amount_residual'] * rate),
            'rate': rate,
        }
        return res
