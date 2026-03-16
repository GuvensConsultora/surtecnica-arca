# -*- coding: utf-8 -*-
{
    'name': 'Cruce Mis Comprobantes AFIP',
    'version': '17.0.2.0.0',
    'category': 'Accounting',
    'summary': 'Importa CSV de Mis Comprobantes y Portal IVA (AFIP) y cruza contra facturas',
    'description': """
        Importa CSV de "Mis Comprobantes" o "Portal IVA — Compras" de AFIP y cruza
        automáticamente contra facturas de proveedores en Odoo.
        Auto-detecta el formato del CSV. Portal IVA incluye desglose IVA por alícuota,
        percepciones (IIBB, IVA, municipales, internos), multi-moneda y crédito fiscal.
    """,
    'author': 'Guvens Consultora',
    'website': '',
    # Por qué: l10n_ar provee document_type, document_number y VAT argentino
    # account provee account.move (facturas)
    'depends': ['account', 'l10n_ar'],
    'data': [
        'security/ir.model.access.csv',
        'views/wizard_views.xml',
        'views/mis_comprobantes_views.xml',
        'views/account_move_views.xml',
        'data/menuitem.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
