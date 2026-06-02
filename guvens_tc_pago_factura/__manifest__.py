# -*- coding: utf-8 -*-
{
    'name': 'TC de Factura en Cobros USD',
    'version': '17.0.1.1.0',
    'category': 'Accounting',
    'summary': 'Usa el TC de la factura al conciliar cobros en ARS contra facturas en USD',
    'description': """
        Cuando un cliente paga en ARS una factura emitida en USD, Odoo genera una
        diferencia de tipo de cambio (CAMBI) que deja un crédito flotante en la
        cuenta por cobrar del cliente.

        Este módulo agrega un booleano "Usar TC de la factura" al payment group
        (por defecto activo). Cuando está activo, después de que el cobro es
        confirmado, cancela automáticamente el asiento de diferencia de cambio
        y el crédito flotante del cliente, registrando la diferencia como un
        ajuste de TC sin impacto en la cuenta corriente del cliente.

        Por qué: Sur Técnica acuerda comercialmente con sus clientes que los pagos
        son al TC de la factura original, no al TC del día del cobro. Odoo no
        conoce ese acuerdo y genera diferencias de cambio que confunden a los
        operadores (Rayen, junio 2026).
    """,
    'author': 'Guvens Consultora',
    'depends': ['account', 'l10n_ar', 'account_payment_group'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_company_views.xml',
        'views/account_payment_group_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
