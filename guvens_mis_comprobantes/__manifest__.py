# -*- coding: utf-8 -*-
{
    'name': 'Cruce Mis Comprobantes AFIP',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Importa CSV de Mis Comprobantes AFIP y cruza contra facturas de proveedores',
    'description': """
        Permite importar el CSV descargado del portal "Mis Comprobantes" de AFIP
        y cruzarlo automáticamente contra las facturas de proveedores cargadas en Odoo.
        Detecta: coincidencias, diferencias de importe, faltantes en Odoo y faltantes en AFIP.
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
