# -*- coding: utf-8 -*-

import base64
from datetime import date
from collections import defaultdict

from odoo import models, fields
from odoo.exceptions import UserError


class AperturaConceptosWizard(models.TransientModel):
    _name = 'apertura.conceptos.wizard'
    _description = 'Apertura de Conceptos F.2051 - IVA Simple'

    # -------------------------------------------------------------------------
    # CAMPOS
    # -------------------------------------------------------------------------

    date_from = fields.Date(
        string='Desde', required=True,
        default=lambda self: date.today().replace(day=1),
    )
    date_to = fields.Date(
        string='Hasta', required=True,
        default=fields.Date.today,
    )
    # Por qué: Actividad AFIP por defecto para productos que no tengan código asignado.
    # Evita tener que cargar la actividad en cada producto si la empresa tiene una sola.
    actividad_default = fields.Char(
        string='Actividad AFIP por defecto', size=6,
        help='Código de 6 dígitos. Se usa cuando el producto no tiene actividad asignada.',
    )
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('done', 'Generado'),
    ], default='draft')

    # Archivos CSV de salida (4 archivos)
    debito_file = fields.Binary('Débito Fiscal')
    debito_name = fields.Char()
    rest_debito_file = fields.Binary('Restitución Débito Fiscal')
    rest_debito_name = fields.Char()
    credito_file = fields.Binary('Crédito Fiscal')
    credito_name = fields.Char()
    rest_credito_file = fields.Binary('Restitución Crédito Fiscal')
    rest_credito_name = fields.Char()

    resumen = fields.Text(string='Resumen', readonly=True)

    # -------------------------------------------------------------------------
    # CONSTANTES
    # -------------------------------------------------------------------------

    # Por qué: ARCA usa un código de 1 dígito distinto al código AFIP de 4 dígitos
    # del Libro IVA Digital. Este mapeo convierte código AFIP → código IVA Simple.
    ALIC_CODE_MAP = {
        '0003': '3',  # 0%
        '0004': '4',  # 10.5%
        '0005': '5',  # 21%
        '0006': '6',  # 27%
        '0008': '8',  # 5%
        '0009': '9',  # 2.5%
    }

    # Códigos AFIP que son alícuotas de IVA gravado
    IVA_GRAVADO_CODES = ('0003', '0004', '0005', '0006', '0008', '0009')

    # Fallback: mapear por monto del impuesto si no tiene código AFIP
    IVA_AMOUNT_MAP = {
        0: '0003', 2.5: '0009', 5: '0008',
        10.5: '0004', 21: '0005', 27: '0006',
    }

    # Nombres legibles para el resumen
    IVA_NAMES = {
        '3': '0%', '4': '10.5%', '5': '21%',
        '6': '27%', '8': '5%', '9': '2.5%',
    }

    # Por qué: l10n_ar.afip.responsibility.type → código IVA Simple para tipo sujeto comprador.
    # AFIP ID 1=RI, 6=Monotributo, 5=CF, 4=Exento, etc.
    TIPO_SUJETO_MAP = {
        1: '1',   # IVA Responsable Inscripto → RI
        11: '1',  # RI Agente Percepción → RI
        6: '2',   # Monotributista → Mono
        12: '2',  # Pequeño Contribuyente Eventual → Mono
        13: '2',  # Monotributista Social → Mono
        14: '2',  # Pequeño Contribuyente Eventual Social → Mono
        5: '3',   # Consumidor Final → CF
        7: '3',   # Sujeto No Categorizado → CF
        4: '4',   # IVA Sujeto Exento → Exento
        10: '4',  # IVA Liberado → Exento
    }

    TIPO_SUJETO_NAMES = {
        '1': 'RI', '2': 'Mono', '3': 'CF', '4': 'Exento', '5': 'Otros',
    }

    # -------------------------------------------------------------------------
    # ACCIÓN PRINCIPAL
    # -------------------------------------------------------------------------

    def action_generar(self):
        """Genera los 4 CSV de Apertura de Conceptos para el F.2051."""
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError('La fecha "Desde" no puede ser posterior a "Hasta".')

        # Buscar moves posted del período
        ventas = self._get_moves('out_invoice')
        ventas_nc = self._get_moves('out_refund')
        compras = self._get_moves('in_invoice')
        compras_nc = self._get_moves('in_refund')

        # Por qué: Solo compras A/M generan crédito fiscal computable
        compras = compras.filtered(
            lambda m: m.l10n_latam_document_type_id.l10n_ar_letter in ('A', 'M')
        )
        compras_nc = compras_nc.filtered(
            lambda m: m.l10n_latam_document_type_id.l10n_ar_letter in ('A', 'M')
        )

        # Agregar líneas por clave (actividad, tipo_op, tipo_sujeto, alicuota)
        data_df = self._agregar_moves(ventas, 'ventas')
        data_rdf = self._agregar_moves(ventas_nc, 'ventas')
        data_cf = self._agregar_moves(compras, 'compras')
        data_rcf = self._agregar_moves(compras_nc, 'compras')

        # Generar CSV
        periodo = self.date_from.strftime('%Y%m')
        vals = {
            'state': 'done',
            'debito_file': self._generar_csv(data_df, 'ventas'),
            'debito_name': f'DF_apertura_{periodo}.csv',
            'rest_debito_file': self._generar_csv(data_rdf, 'ventas'),
            'rest_debito_name': f'RDF_apertura_{periodo}.csv',
            'credito_file': self._generar_csv(data_cf, 'compras'),
            'credito_name': f'CF_apertura_{periodo}.csv',
            'rest_credito_file': self._generar_csv(data_rcf, 'compras'),
            'rest_credito_name': f'RCF_apertura_{periodo}.csv',
            'resumen': self._generar_resumen(data_df, data_rdf, data_cf, data_rcf),
        }
        self.write(vals)

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # -------------------------------------------------------------------------
    # BÚSQUEDA DE MOVES
    # -------------------------------------------------------------------------

    def _get_moves(self, move_type):
        """Busca facturas/NC posted del período por tipo."""
        return self.env['account.move'].search([
            ('state', '=', 'posted'),
            ('move_type', '=', move_type),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            ('l10n_latam_document_type_id', '!=', False),
        ], order='invoice_date, name')

    # -------------------------------------------------------------------------
    # AGREGACIÓN POR LÍNEA DE FACTURA
    # -------------------------------------------------------------------------

    def _agregar_moves(self, moves, tipo):
        """Agrega importes por clave (actividad, tipo_op, tipo_sujeto/None, alic).

        Args:
            moves: recordset de account.move
            tipo: 'ventas' o 'compras'
        Returns:
            dict: {(actividad, tipo_op, tipo_sujeto, alic_code): {neto, iva, exento}}
            Para compras, tipo_sujeto es siempre None (no se usa en el CSV).
        """
        # Por qué: defaultdict simplifica la acumulación sin verificar existencia de clave
        data = defaultdict(lambda: {'neto': 0.0, 'iva': 0.0, 'exento': 0.0})
        errores = []

        for move in moves:
            # Tipo sujeto: solo para ventas
            tipo_sujeto = None
            if tipo == 'ventas':
                tipo_sujeto = self._get_tipo_sujeto(move.commercial_partner_id)

            # Procesar líneas de producto
            for line in move.invoice_line_ids.filtered(
                lambda l: l.display_type == 'product'
            ):
                try:
                    self._procesar_linea(line, move, tipo, tipo_sujeto, data)
                except UserError as e:
                    errores.append(str(e))

        if errores:
            raise UserError(
                'Errores al procesar comprobantes:\n' + '\n'.join(errores)
            )
        return dict(data)

    def _procesar_linea(self, line, move, tipo, tipo_sujeto, data):
        """Procesa una línea de factura y acumula en data.

        Por qué: Cada línea puede tener distinta actividad, alícuota y clasificación.
        Se agrupa por clave compuesta para generar una fila CSV por combinación.
        """
        # Obtener actividad AFIP del producto o fallback al default del wizard
        actividad = (
            line.product_id.l10n_ar_afip_activity_code
            or self.actividad_default
            or ''
        ).strip()
        if not actividad:
            raise UserError(
                f'{move.name}: producto "{line.product_id.name or line.name}" '
                f'sin código de Actividad AFIP (y no hay actividad por defecto).'
            )

        # Clasificar línea: gravado / exento / no_gravado
        line_class, afip_code = self._classify_line(line)

        if line_class in ('exento', 'no_gravado'):
            # Por qué: Líneas exentas/no gravadas van con tipo_op=3, alícuota=3 (0%)
            tipo_op = '3'
            alic_code = '3'
            key = (actividad, tipo_op, tipo_sujeto, alic_code)
            data[key]['exento'] += line.price_subtotal
        else:
            # Línea gravada: tipo_op=1 (venta bienes/servicios o compra ordinaria)
            tipo_op = '1'
            alic_code = self.ALIC_CODE_MAP.get(afip_code, '5')  # Default 21%
            key = (actividad, tipo_op, tipo_sujeto, alic_code)
            data[key]['neto'] += line.price_subtotal
            # Obtener monto IVA de esta línea desde tax lines
            iva_amount = self._get_line_iva_amount(line, afip_code)
            data[key]['iva'] += iva_amount

    def _classify_line(self, line):
        """Clasifica una línea: (tipo, afip_code_4_digitos).

        Returns:
            tuple: ('gravado'|'exento'|'no_gravado', afip_code_str o None)
        """
        for tax in line.tax_ids:
            code = self._get_vat_afip_code(tax)
            if code == '0002':
                return 'exento', code
            elif code == '0001':
                return 'no_gravado', code
            elif code in self.IVA_GRAVADO_CODES:
                return 'gravado', code
        return 'no_gravado', None

    def _get_line_iva_amount(self, line, afip_code):
        """Calcula el monto de IVA de una línea para un código de alícuota.

        Por qué: No hay relación directa línea→tax_line en Odoo.
        Se calcula como price_subtotal * tasa IVA para consistencia.
        """
        rates = {
            '0003': 0.0, '0004': 0.105, '0005': 0.21,
            '0006': 0.27, '0008': 0.05, '0009': 0.025,
        }
        rate = rates.get(afip_code, 0.21)
        return round(line.price_subtotal * rate, 2)

    # -------------------------------------------------------------------------
    # HELPERS: CLASIFICACIÓN
    # -------------------------------------------------------------------------

    def _get_vat_afip_code(self, tax):
        """Obtiene código AFIP de alícuota IVA (4 dígitos) de un impuesto.

        Por qué: Mismo patrón que libro_iva_digital_wizard._get_vat_afip_code.
        l10n_ar guarda '5' → se normaliza a '0005'.
        """
        tax_group = tax.tax_group_id
        code = getattr(tax_group, 'l10n_ar_vat_afip_code', False)
        if code:
            return str(code).zfill(4)

        # Si tiene código tributo (percepciones, IIBB, etc.) NO es IVA
        tribute_code = getattr(tax_group, 'l10n_ar_tribute_afip_code', None)
        if tribute_code:
            return False

        # Fallback por monto
        return self.IVA_AMOUNT_MAP.get(abs(tax.amount), False)

    def _get_tipo_sujeto(self, partner):
        """Obtiene código tipo sujeto comprador para IVA Simple.

        Por qué: El CSV de débito fiscal desglosa por tipo de comprador
        (RI, Mono, CF, Exento, Otros) según la responsabilidad AFIP del partner.
        """
        resp_type = partner.l10n_ar_afip_responsibility_type_id
        if not resp_type:
            return '5'  # Otros
        # Por qué: l10n_ar_afip_code es el ID interno de AFIP del tipo de responsabilidad
        afip_code = getattr(resp_type, 'l10n_ar_afip_code', False)
        if not afip_code:
            return '5'
        return self.TIPO_SUJETO_MAP.get(int(afip_code), '5')

    # -------------------------------------------------------------------------
    # GENERACIÓN CSV
    # -------------------------------------------------------------------------

    def _generar_csv(self, data, tipo):
        """Genera contenido CSV en base64.

        Args:
            data: dict {(actividad, tipo_op, tipo_sujeto, alic): {neto, iva, exento}}
            tipo: 'ventas' (8 campos) o 'compras' (7 campos)
        Returns:
            base64 encoded string o False si no hay datos
        """
        if not data:
            return False

        lines = []
        # Por qué: Ordenar por clave para consistencia en el archivo
        for key in sorted(data.keys()):
            actividad, tipo_op, tipo_sujeto, alic_code = key
            vals = data[key]
            neto = self._fmt_decimal(vals['neto'])
            iva = self._fmt_decimal(vals['iva'])
            dacion = '0,00'
            exento = self._fmt_decimal(vals['exento'])

            if tipo == 'ventas':
                # 8 campos: actividad;tipo_op;tipo_sujeto;alic;neto;df;dacion;exento
                line = f'{actividad};{tipo_op};{tipo_sujeto};{alic_code};{neto};{iva};{dacion};{exento}'
            else:
                # 7 campos: actividad;tipo_op;alic;neto;cf;dacion;exento
                line = f'{actividad};{tipo_op};{alic_code};{neto};{iva};{dacion};{exento}'
            lines.append(line)

        # Por qué: ARCA espera CRLF, encoding ANSI (latin-1), sin header
        content = '\r\n'.join(lines)
        return base64.b64encode(content.encode('latin-1', errors='replace'))

    def _fmt_decimal(self, amount):
        """Formatea importe con coma decimal para CSV ARCA.

        Por qué: ARCA exige separador decimal coma (no punto).
        Ejemplo: 1500.00 → '1500,00'
        """
        return f'{amount:.2f}'.replace('.', ',')

    # -------------------------------------------------------------------------
    # RESUMEN
    # -------------------------------------------------------------------------

    def _generar_resumen(self, data_df, data_rdf, data_cf, data_rcf):
        """Genera resumen de cuadratura con totales por archivo."""
        def fmt(amount):
            sign = '-' if amount < 0 else ''
            abs_val = abs(amount)
            return f'{sign}{abs_val:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

        def tabla_datos(data, label_fiscal, tipo):
            """Genera tabla de totales por alícuota."""
            # Acumular por alícuota
            por_alic = defaultdict(lambda: {'neto': 0.0, 'iva': 0.0, 'exento': 0.0})
            for key, vals in data.items():
                alic = key[3]  # posición 3 = código alícuota
                por_alic[alic]['neto'] += vals['neto']
                por_alic[alic]['iva'] += vals['iva']
                por_alic[alic]['exento'] += vals['exento']

            lines = []
            total_neto = 0.0
            total_fiscal = 0.0
            total_exento = 0.0
            for alic in sorted(por_alic.keys()):
                v = por_alic[alic]
                name = self.IVA_NAMES.get(alic, alic)
                lines.append(
                    f'  Alíc {name:>5}  '
                    f'Neto: {fmt(v["neto"]):>14}  '
                    f'{label_fiscal}: {fmt(v["iva"]):>14}  '
                    f'Exento: {fmt(v["exento"]):>14}'
                )
                total_neto += v['neto']
                total_fiscal += v['iva']
                total_exento += v['exento']
            lines.append(f'  {"─" * 70}')
            lines.append(
                f'  TOTAL    '
                f'Neto: {fmt(total_neto):>14}  '
                f'{label_fiscal}: {fmt(total_fiscal):>14}  '
                f'Exento: {fmt(total_exento):>14}'
            )
            n_filas = len(data)
            return '\n'.join(lines), total_neto, total_fiscal, total_exento, n_filas

        r = (
            f'{"═" * 60}\n'
            f'  APERTURA DE CONCEPTOS - F.2051 (IVA Simple)\n'
            f'{"═" * 60}\n'
            f'Período: {self.date_from.strftime("%d/%m/%Y")} - '
            f'{self.date_to.strftime("%d/%m/%Y")}\n'
            f'Empresa: {self.env.company.name}\n'
            f'CUIT: {self.env.company.vat or "Sin configurar"}\n'
            f'Actividad por defecto: {self.actividad_default or "No definida"}\n'
        )

        # Débito Fiscal
        t_df, neto_df, iva_df, ex_df, n_df = tabla_datos(data_df, 'DF', 'ventas')
        r += (
            f'\n{"─" * 60}\n'
            f'  DÉBITO FISCAL ({n_df} filas CSV)\n'
            f'{"─" * 60}\n'
            f'{t_df}\n'
        )

        # Restitución DF
        t_rdf, neto_rdf, iva_rdf, ex_rdf, n_rdf = tabla_datos(data_rdf, 'DF', 'ventas')
        r += (
            f'\n{"─" * 60}\n'
            f'  RESTITUCIÓN DÉBITO FISCAL ({n_rdf} filas CSV)\n'
            f'{"─" * 60}\n'
            f'{t_rdf}\n'
        )

        # Crédito Fiscal
        t_cf, neto_cf, iva_cf, ex_cf, n_cf = tabla_datos(data_cf, 'CF', 'compras')
        r += (
            f'\n{"─" * 60}\n'
            f'  CRÉDITO FISCAL ({n_cf} filas CSV)\n'
            f'{"─" * 60}\n'
            f'{t_cf}\n'
        )

        # Restitución CF
        t_rcf, neto_rcf, iva_rcf, ex_rcf, n_rcf = tabla_datos(data_rcf, 'CF', 'compras')
        r += (
            f'\n{"─" * 60}\n'
            f'  RESTITUCIÓN CRÉDITO FISCAL ({n_rcf} filas CSV)\n'
            f'{"─" * 60}\n'
            f'{t_rcf}\n'
        )

        # Balance
        saldo = (iva_df - iva_rdf) - (iva_cf - iva_rcf)
        estado = 'A PAGAR' if saldo >= 0 else 'A FAVOR'
        r += (
            f'\n{"═" * 60}\n'
            f'  BALANCE F.2051\n'
            f'{"═" * 60}\n'
            f'  DF neto:              {fmt(iva_df - iva_rdf):>16}\n'
            f'  CF neto:              {fmt(iva_cf - iva_rcf):>16}\n'
            f'  {"─" * 42}\n'
            f'  SALDO:                {fmt(saldo):>16}  ({estado})\n'
        )

        return r
