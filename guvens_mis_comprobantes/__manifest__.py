# -*- coding: utf-8 -*-
{
    'name': 'Cruce Mis Comprobantes ARCA',
    'version': '17.0.3.0.2',
    'category': 'Accounting',
    'summary': 'Importa CSV de ARCA: cruza compras y crea ventas faltantes (FCE MiPyME)',
    'description': """
        Importa CSV de los portales de ARCA en dos modalidades complementarias:

        Compras: importa CSV de "Mis Comprobantes — Recibidos" o "Portal IVA — Compras"
        y cruza automáticamente contra facturas de proveedores en Odoo. Auto-detecta
        el formato del CSV. Portal IVA incluye desglose IVA por alícuota, percepciones
        (IIBB, IVA, municipales, internos), multi-moneda y crédito fiscal.

        Ventas: importa CSV de "Mis Comprobantes — Emitidos" y crea en Odoo los
        comprobantes que se emitieron desde el portal de ARCA y faltan en el sistema
        (típicamente FCE MiPyME). El CAE proviene del CSV; no se vuelve a llamar a WSFE.
        Tres pasos: subir, previsualizar con clasificación por color, confirmar.
    """,
    'author': 'Guvens Consultora',
    'website': '',
    # Por qué: l10n_ar provee document_type, document_number y VAT argentino
    # account provee account.move (facturas)
    'depends': ['account', 'l10n_ar'],
    'data': [
        'security/mis_comprobantes_security.xml',
        'security/ir.model.access.csv',
        'views/wizard_views.xml',
        'views/wizard_emitidos_views.xml',
        'views/mis_comprobantes_views.xml',
        'views/account_move_views.xml',
        'data/menuitem.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
