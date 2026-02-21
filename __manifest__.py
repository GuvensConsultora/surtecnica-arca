# -*- coding: utf-8 -*-
{
    'name': 'Libro IVA Digital - ARCA',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Libro IVA Digital y Apertura de Conceptos F.2051 para ARCA',
    'description': """
        Genera los archivos TXT de posición fija para presentar el Libro IVA Digital
        ante ARCA según el Anexo I - Diseños de Registros oficial.

        Archivos generados:
        - LIBRO_IVA_DIGITAL_VENTAS_CBTE (266 chars)
        - LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS (62 chars)
        - LIBRO_IVA_DIGITAL_COMPRAS_CBTE (325 chars)
        - LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS (84 chars)

        Apertura de Conceptos (CSV) para IVA Simple F.2051:
        - Débito Fiscal / Restitución DF
        - Crédito Fiscal / Restitución CF
    """,
    'author': 'Surtecnica',
    'website': '',
    # Por qué: l10n_ar provee CUIT, tipos de comprobante AFIP, códigos de moneda
    # y toda la estructura fiscal argentina necesaria para el Libro IVA Digital
    'depends': ['account', 'l10n_ar'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/libro_iva_digital_wizard_views.xml',
        'wizard/apertura_conceptos_wizard_views.xml',
        'views/product_template_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
