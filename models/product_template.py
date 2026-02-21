# -*- coding: utf-8 -*-

from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Por qué: ARCA (RG 5705/2025) requiere desglosar DF/CF por actividad AFIP
    # en el CSV de Apertura de Conceptos del F.2051 (IVA Simple).
    # Cada producto puede corresponder a una actividad económica distinta.
    l10n_ar_afip_activity_code = fields.Char(
        string='Actividad AFIP',
        size=6,
        help='Código de actividad AFIP (6 dígitos). Ej: 620100',
    )
