{
    'name': 'Fix layout PDF facturas - clearfix totales',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Corrige superposición del pie/nota con la caja de totales cuando el texto de la nota es largo',
    'author': 'Guvens Consultora',
    'license': 'AGPL-3',
    'depends': [
        'account',
    ],
    'data': [
        'views/report_invoice_document.xml',
    ],
    'installable': True,
    'application': False,
}
