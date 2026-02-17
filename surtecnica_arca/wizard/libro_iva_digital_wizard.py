# -*- coding: utf-8 -*-

import base64
import io
import zipfile
from datetime import date

from odoo import models, fields, api
from odoo.exceptions import UserError


class LibroIvaDigitalWizard(models.TransientModel):
    _name = 'libro.iva.digital.wizard'
    _description = 'Libro IVA Digital - ARCA'

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
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('done', 'Generado'),
    ], default='draft')

    # Archivos de salida (4 TXT del Libro IVA Digital)
    ventas_cbte_file = fields.Binary('Ventas Cabecera')
    ventas_cbte_name = fields.Char()
    ventas_alic_file = fields.Binary('Ventas Alícuotas')
    ventas_alic_name = fields.Char()
    compras_cbte_file = fields.Binary('Compras Cabecera')
    compras_cbte_name = fields.Char()
    compras_alic_file = fields.Binary('Compras Alícuotas')
    compras_alic_name = fields.Char()

    resumen = fields.Text(string='Resumen', readonly=True)

    # Por qué: HTML renderizado que simula cómo debe cargarse la DDJJ IVA
    # en el portal ARCA (F.2002), para verificación antes de presentar
    ddjj_iva_html = fields.Html(
        string='DDJJ IVA', readonly=True, sanitize=False,
    )

    # -------------------------------------------------------------------------
    # CONSTANTES AFIP
    # -------------------------------------------------------------------------

    # Mapeo alícuota IVA por monto del tax → código AFIP
    # Por qué: Fallback si l10n_ar_vat_afip_code no está disponible en tax.group
    # Los códigos son single-digit porque así los almacena l10n_ar en Odoo 17
    # El zero-padding a 4 dígitos (0005) se hace al formatear la línea con _fmt_num
    IVA_AMOUNT_MAP = {
        0: '3', 2.5: '9', 5: '8',
        10.5: '4', 21: '5', 27: '6',
    }

    # Códigos AFIP que son alícuotas de IVA gravado (van al archivo de alícuotas)
    # Por qué: l10n_ar_vat_afip_code en Odoo 17 usa códigos sin zero-pad ('5', no '0005')
    IVA_GRAVADO_CODES = ('3', '4', '5', '6', '8', '9')

    # Códigos moneda AFIP - fallback si no existe l10n_ar_afip_code en currency
    MONEDA_MAP = {
        'ARS': 'PES', 'USD': 'DOL', 'EUR': '060', 'BRL': '012',
        'GBP': '021', 'UYU': '011', 'CLP': '033', 'MXN': '010',
    }

    # Mapeo código AFIP alícuota IVA → label para display en DDJJ
    IVA_CODE_LABEL = {
        '3': '0%', '9': '2,50%', '8': '5%',
        '4': '10,50%', '5': '21%', '6': '27%',
    }

    # Mapeo inverso: código AFIP → tasa porcentual
    # Por qué: ARCA valida que IVA = base × alícuota exactamente.
    # Se usa para recalcular el IVA y evitar diferencias de centavos
    # por redondeo cuando Odoo suma IVA por línea de producto.
    IVA_CODE_RATE = {
        '3': 0.0, '9': 2.5, '8': 5.0,
        '4': 10.5, '5': 21.0, '6': 27.0,
    }

    # -------------------------------------------------------------------------
    # ACCIÓN PRINCIPAL
    # -------------------------------------------------------------------------

    def action_generar(self):
        """Genera los 4 archivos TXT del Libro IVA Digital."""
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError('La fecha "Desde" no puede ser posterior a "Hasta".')

        # Buscar facturas de venta y compra en el período
        ventas = self._get_moves('out')
        compras = self._get_moves('in')

        # Generar líneas para cada archivo
        v_cbte, v_alic = self._procesar_moves(ventas, 'ventas')
        c_cbte, c_alic = self._procesar_moves(compras, 'compras')

        # Codificar archivos
        vals = {
            'state': 'done',
            'ventas_cbte_file': self._encode_lines(v_cbte),
            'ventas_cbte_name': 'LIBRO_IVA_DIGITAL_VENTAS_CBTE.txt',
            'ventas_alic_file': self._encode_lines(v_alic),
            'ventas_alic_name': 'LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS.txt',
            'compras_cbte_file': self._encode_lines(c_cbte),
            'compras_cbte_name': 'LIBRO_IVA_DIGITAL_COMPRAS_CBTE.txt',
            'compras_alic_file': self._encode_lines(c_alic),
            'compras_alic_name': 'LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS.txt',
            'resumen': self._generar_resumen(
                ventas, compras, v_cbte, c_cbte, v_alic, c_alic
            ),
            'ddjj_iva_html': self._compute_ddjj_iva_html(ventas, compras),
        }
        self.write(vals)

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_descargar_zip(self):
        """Descarga todos los archivos en un ZIP."""
        self.ensure_one()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname, fdata in [
                (self.ventas_cbte_name, self.ventas_cbte_file),
                (self.ventas_alic_name, self.ventas_alic_file),
                (self.compras_cbte_name, self.compras_cbte_file),
                (self.compras_alic_name, self.compras_alic_file),
            ]:
                if fdata:
                    zf.writestr(fname, base64.b64decode(fdata))

        # Guardar ZIP en un attachment para descarga
        zip_data = base64.b64encode(buf.getvalue())
        periodo = self.date_from.strftime('%Y%m')
        attachment = self.env['ir.attachment'].create({
            'name': f'LIBRO_IVA_DIGITAL_{periodo}.zip',
            'type': 'binary',
            'datas': zip_data,
            'mimetype': 'application/zip',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # -------------------------------------------------------------------------
    # BÚSQUEDA DE MOVES
    # -------------------------------------------------------------------------

    def _get_moves(self, tipo):
        """Busca facturas posted del período.

        Args:
            tipo: 'out' para ventas, 'in' para compras.
        """
        move_types = ['out_invoice', 'out_refund'] if tipo == 'out' \
            else ['in_invoice', 'in_refund']
        return self.env['account.move'].search([
            ('state', '=', 'posted'),
            ('move_type', 'in', move_types),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            # Por qué: Solo facturas con documento fiscal argentino
            ('l10n_latam_document_type_id', '!=', False),
        ], order='invoice_date, name')

    # -------------------------------------------------------------------------
    # PROCESAMIENTO DE MOVES
    # -------------------------------------------------------------------------

    def _procesar_moves(self, moves, tipo):
        """Procesa moves y genera líneas de cabecera + alícuotas.

        Args:
            tipo: 'ventas' o 'compras' (determina el formato de salida).
        Returns:
            tuple: (lista_cabecera, lista_alicuotas)
        """
        cbte_lines = []
        alic_lines = []
        errores = []

        for move in moves:
            try:
                data = self._extract_move_data(move)

                if tipo == 'ventas':
                    cbte_lines.append(self._fmt_ventas_cbte(move, data))
                    for alic in data['iva_alicuotas']:
                        alic_lines.append(self._fmt_ventas_alic(move, alic))
                else:
                    cbte_lines.append(self._fmt_compras_cbte(move, data))
                    for alic in data['iva_alicuotas']:
                        alic_lines.append(self._fmt_compras_alic(move, alic))
            except Exception as e:
                errores.append(f'{move.name}: {str(e)}')

        if errores:
            raise UserError(
                'Errores al procesar comprobantes:\n' + '\n'.join(errores)
            )
        return cbte_lines, alic_lines

    # -------------------------------------------------------------------------
    # EXTRACCIÓN DE DATOS DE UN MOVE
    # -------------------------------------------------------------------------

    def _extract_move_data(self, move):
        """Extrae y clasifica todos los importes de un comprobante.

        Por qué: Separa IVA gravado (→ archivo alícuotas), no gravado,
        exento, percepciones y otros tributos (→ archivo cabecera).
        El signo se invierte para NC/ND negativas.
        """
        # Por qué: NC tienen importes positivos en Odoo, pero negativos en el TXT
        sign = -1 if move.move_type in ('out_refund', 'in_refund') else 1

        result = {
            'total': 0.0,  # Se recalcula en Paso 4 como suma de partes
            'no_gravado': 0.0,
            'exento': 0.0,
            'perc_no_categ': 0.0,      # Ventas campo 11
            'perc_iva': 0.0,           # Compras campo 12
            'perc_nacionales': 0.0,
            'perc_iibb': 0.0,
            'perc_mun': 0.0,
            'imp_internos': 0.0,
            'otros_tributos': 0.0,
            'iva_alicuotas': [],       # [{code, base, amount}]
        }

        # Paso 1: Clasificar líneas de producto → no_gravado / exento
        for line in move.invoice_line_ids.filtered(lambda l: not l.display_type):
            line_class = self._classify_line_iva(line)
            if line_class == 'no_gravado':
                result['no_gravado'] += line.price_subtotal * sign
            elif line_class == 'exento':
                result['exento'] += line.price_subtotal * sign

        # Paso 2: IVA gravado desde tax lines → alícuotas
        iva_by_code = {}
        for line in move.line_ids.filtered(lambda l: l.tax_line_id):
            tax = line.tax_line_id
            vat_code = self._get_vat_afip_code(tax)

            if vat_code and vat_code in self.IVA_GRAVADO_CODES:
                # IVA gravado → archivo alícuotas
                if vat_code not in iva_by_code:
                    iva_by_code[vat_code] = {'code': vat_code, 'base': 0.0, 'amount': 0.0}
                iva_by_code[vat_code]['base'] += abs(line.tax_base_amount) * sign
                iva_by_code[vat_code]['amount'] += abs(line.balance) * sign
            elif vat_code in ('1', '2'):
                # No gravado / exento ya computados en paso 1
                pass
            else:
                # Impuesto no-IVA → clasificar en cabecera
                cat = self._classify_non_iva_tax(tax.tax_group_id)
                result[cat] += abs(line.balance) * sign

        # Paso 3: Ajustar IVA para consistencia matemática con ARCA
        # Por qué: ARCA valida que impuesto = base × alícuota exactamente.
        # Odoo calcula IVA por línea de producto y suma, generando diferencias
        # de centavos. Recalculamos para que la validación no falle.
        for code, alic in iva_by_code.items():
            rate = self.IVA_CODE_RATE.get(code)
            if rate is not None and rate > 0:
                alic['amount'] = round(alic['base'] * rate / 100, 2)

        result['iva_alicuotas'] = list(iva_by_code.values())

        # Paso 4: Computar total como suma de partes
        # Por qué: ARCA valida que Total = suma de todos los campos de importe.
        # Usar move.amount_total genera diferencias cuando algún importe no se
        # clasifica o hay redondeo. Calculando desde las partes, la suma siempre cuadra.
        gravado = sum(a['base'] for a in result['iva_alicuotas'])
        iva = sum(a['amount'] for a in result['iva_alicuotas'])
        result['total'] = (
            gravado + iva
            + result['no_gravado'] + result['exento']
            + result['perc_no_categ'] + result['perc_iva']
            + result['perc_nacionales'] + result['perc_iibb']
            + result['perc_mun'] + result['imp_internos']
            + result['otros_tributos']
        )
        return result

    def _classify_line_iva(self, line):
        """Clasifica una línea de factura: gravado / exento / no_gravado.

        Por qué: Se determina por el tipo de IVA aplicado a la línea.
        Si no tiene IVA → no_gravado (concepto sin impuesto).
        """
        for tax in line.tax_ids:
            code = self._get_vat_afip_code(tax)
            if code == '2':
                return 'exento'
            elif code == '1':
                return 'no_gravado'
            elif code in self.IVA_GRAVADO_CODES:
                return 'gravado'
        return 'no_gravado'

    def _get_vat_afip_code(self, tax):
        """Obtiene el código AFIP de alícuota IVA de un impuesto.

        Por qué: Intenta primero el campo estándar l10n_ar_vat_afip_code.
        El fallback por monto SOLO se aplica si el tax group NO tiene
        l10n_ar_tribute_afip_code y NO parece percepción/retención por nombre.
        Bug anterior: percepciones/retenciones con tasa coincidente (5%, 21%)
        se clasificaban como IVA gravado, inflando el neto gravado con su
        tax_base_amount (importe total de la factura).
        """
        tax_group = tax.tax_group_id

        # Paso 1: código IVA explícito → es IVA seguro
        code = getattr(tax_group, 'l10n_ar_vat_afip_code', False)
        if code:
            return code

        # Paso 2: si tiene código tributo AFIP → NO es IVA (percepción/retención)
        tribute_code = getattr(tax_group, 'l10n_ar_tribute_afip_code', None)
        if tribute_code:
            return False

        # Paso 3: descartar por nombre del grupo (percepciones, retenciones, IIBB, etc.)
        name = (tax_group.name or '').lower()
        if any(k in name for k in (
            'percep', 'reten', 'withhold', 'iibb', 'ingr', 'brut',
            'munic', 'intern', 'ganan',
        )):
            return False

        # Paso 4: fallback por monto solo si no se descartó como tributo
        return self.IVA_AMOUNT_MAP.get(abs(tax.amount), False)

    def _classify_non_iva_tax(self, tax_group):
        """Clasifica un impuesto no-IVA en categoría de cabecera.

        Por qué: Usa l10n_ar_tribute_afip_code si existe, sino clasifica
        por nombre del grupo de impuesto.
        """
        # Intentar código AFIP de tributo
        tribute_code = getattr(tax_group, 'l10n_ar_tribute_afip_code', None)
        if tribute_code:
            mapping = {
                '06': 'perc_iva', '07': 'perc_iibb', '08': 'perc_mun',
                '09': 'otros_tributos', '04': 'imp_internos',
                '01': 'perc_nacionales', '02': 'perc_iibb', '03': 'perc_mun',
            }
            return mapping.get(tribute_code, 'otros_tributos')

        # Fallback: clasificar por nombre
        name = (tax_group.name or '').lower()
        if 'percep' in name:
            if 'iva' in name:
                return 'perc_iva'
            elif any(k in name for k in ('iibb', 'ingr', 'brut', 'provincial')):
                return 'perc_iibb'
            elif 'munic' in name:
                return 'perc_mun'
            return 'perc_nacionales'
        elif 'intern' in name:
            return 'imp_internos'
        return 'otros_tributos'

    # -------------------------------------------------------------------------
    # FORMATEO VENTAS CABECERA (266 chars, 22 campos)
    # -------------------------------------------------------------------------

    def _fmt_ventas_cbte(self, move, data):
        """Genera una línea del archivo LIBRO_IVA_DIGITAL_VENTAS_CBTE."""
        partner = move.commercial_partner_id
        pv, num = self._get_doc_parts(move)
        doc_code, doc_num = self._get_partner_doc(partner)
        cur_code, cur_rate = self._get_currency_info(move)
        op_code = self._get_operation_code(data)
        n_alic = len(data['iva_alicuotas'])
        fecha_vto = move.invoice_date_due or move.invoice_date

        line = (
            self._fmt_date(move.invoice_date)                    #  1: Fecha cbte (8)
            + self._fmt_num(move.l10n_latam_document_type_id.code, 3)  #  2: Tipo cbte (3)
            + self._fmt_num(pv, 5)                               #  3: Pto venta (5)
            + self._fmt_num(num, 20)                             #  4: Nro cbte desde (20)
            + self._fmt_num(num, 20)                             #  5: Nro cbte hasta (20)
            + self._fmt_num(doc_code, 2)                         #  6: Cod doc comprador (2)
            + self._fmt_num(doc_num, 20)                         #  7: Nro doc comprador (20)
            + self._fmt_text(partner.name or '', 30)             #  8: Nombre comprador (30)
            + self._fmt_amount(data['total'])                    #  9: Importe total (15)
            + self._fmt_amount(data['no_gravado'])               # 10: No gravado (15)
            + self._fmt_amount(data['perc_no_categ'])            # 11: Perc no categ (15)
            + self._fmt_amount(data['exento'])                   # 12: Exentas (15)
            + self._fmt_amount(data['perc_nacionales'])          # 13: Perc nacionales (15)
            + self._fmt_amount(data['perc_iibb'])                # 14: Perc IIBB (15)
            + self._fmt_amount(data['perc_mun'])                 # 15: Perc municipales (15)
            + self._fmt_amount(data['imp_internos'])             # 16: Imp internos (15)
            + self._fmt_text(cur_code, 3)                        # 17: Cod moneda (3)
            + self._fmt_rate(cur_rate)                           # 18: Tipo cambio (10)
            + str(n_alic)                                        # 19: Cant alícuotas (1)
            + self._fmt_text(op_code, 1)                         # 20: Cod operación (1)
            + self._fmt_amount(data['otros_tributos'])           # 21: Otros tributos (15)
            + self._fmt_date(fecha_vto)                          # 22: Fecha vto (8)
        )

        if len(line) != 266:
            raise UserError(
                f'Ventas Cbte {move.name}: línea de {len(line)} chars, se esperan 266.'
            )
        return line

    # -------------------------------------------------------------------------
    # FORMATEO VENTAS ALÍCUOTAS (62 chars, 6 campos)
    # -------------------------------------------------------------------------

    def _fmt_ventas_alic(self, move, alic):
        """Genera una línea del archivo LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS."""
        pv, num = self._get_doc_parts(move)

        line = (
            self._fmt_num(move.l10n_latam_document_type_id.code, 3)  # 1: Tipo cbte (3)
            + self._fmt_num(pv, 5)                                   # 2: Pto venta (5)
            + self._fmt_num(num, 20)                                 # 3: Nro cbte (20)
            + self._fmt_amount(alic['base'])                         # 4: Neto gravado (15)
            + self._fmt_num(alic['code'], 4)                         # 5: Alícuota IVA (4)
            + self._fmt_amount(alic['amount'])                       # 6: Impuesto liq (15)
        )

        if len(line) != 62:
            raise UserError(
                f'Ventas Alic {move.name}: línea de {len(line)} chars, se esperan 62.'
            )
        return line

    # -------------------------------------------------------------------------
    # FORMATEO COMPRAS CABECERA (325 chars, 25 campos)
    # -------------------------------------------------------------------------

    def _fmt_compras_cbte(self, move, data):
        """Genera una línea del archivo LIBRO_IVA_DIGITAL_COMPRAS_CBTE."""
        partner = move.commercial_partner_id
        pv, num = self._get_doc_parts(move)
        doc_code, doc_num = self._get_partner_doc(partner)
        cur_code, cur_rate = self._get_currency_info(move)
        op_code = self._get_operation_code(data)
        n_alic = len(data['iva_alicuotas'])
        # Crédito fiscal = suma de IVA de todas las alícuotas
        credito_fiscal = sum(a['amount'] for a in data['iva_alicuotas'])

        line = (
            self._fmt_date(move.invoice_date)                    #  1: Fecha cbte (8)
            + self._fmt_num(move.l10n_latam_document_type_id.code, 3)  #  2: Tipo cbte (3)
            + self._fmt_num(pv, 5)                               #  3: Pto venta (5)
            + self._fmt_num(num, 20)                             #  4: Nro cbte (20)
            + self._fmt_text('', 16)                             #  5: Despacho import (16)
            + self._fmt_num(doc_code, 2)                         #  6: Cod doc vendedor (2)
            + self._fmt_num(doc_num, 20)                         #  7: Nro doc vendedor (20)
            + self._fmt_text(partner.name or '', 30)             #  8: Nombre vendedor (30)
            + self._fmt_amount(data['total'])                    #  9: Importe total (15)
            + self._fmt_amount(data['no_gravado'])               # 10: No gravado (15)
            + self._fmt_amount(data['exento'])                   # 11: Exentas (15)
            + self._fmt_amount(data['perc_iva'])                 # 12: Perc IVA (15)
            + self._fmt_amount(data['perc_nacionales'])          # 13: Perc nacionales (15)
            + self._fmt_amount(data['perc_iibb'])                # 14: Perc IIBB (15)
            + self._fmt_amount(data['perc_mun'])                 # 15: Perc municipales (15)
            + self._fmt_amount(data['imp_internos'])             # 16: Imp internos (15)
            + self._fmt_text(cur_code, 3)                        # 17: Cod moneda (3)
            + self._fmt_rate(cur_rate)                           # 18: Tipo cambio (10)
            + str(n_alic)                                        # 19: Cant alícuotas (1)
            + self._fmt_text(op_code, 1)                         # 20: Cod operación (1)
            + self._fmt_amount(credito_fiscal)                   # 21: Crédito fiscal (15)
            + self._fmt_amount(data['otros_tributos'])           # 22: Otros tributos (15)
            + self._fmt_num('0' * 11, 11)                        # 23: CUIT emisor (11)
            + self._fmt_text('', 30)                             # 24: Nombre emisor (30)
            + self._fmt_amount(0)                                # 25: IVA comisión (15)
        )

        if len(line) != 325:
            raise UserError(
                f'Compras Cbte {move.name}: línea de {len(line)} chars, se esperan 325.'
            )
        return line

    # -------------------------------------------------------------------------
    # FORMATEO COMPRAS ALÍCUOTAS (84 chars, 8 campos)
    # -------------------------------------------------------------------------

    def _fmt_compras_alic(self, move, alic):
        """Genera una línea del archivo LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS."""
        partner = move.commercial_partner_id
        pv, num = self._get_doc_parts(move)
        doc_code, doc_num = self._get_partner_doc(partner)

        line = (
            self._fmt_num(move.l10n_latam_document_type_id.code, 3)  # 1: Tipo cbte (3)
            + self._fmt_num(pv, 5)                                   # 2: Pto venta (5)
            + self._fmt_num(num, 20)                                 # 3: Nro cbte (20)
            + self._fmt_num(doc_code, 2)                             # 4: Cod doc vendedor (2)
            + self._fmt_num(doc_num, 20)                             # 5: Nro doc vendedor (20)
            + self._fmt_amount(alic['base'])                         # 6: Neto gravado (15)
            + self._fmt_num(alic['code'], 4)                         # 7: Alícuota IVA (4)
            + self._fmt_amount(alic['amount'])                       # 8: Impuesto liq (15)
        )

        if len(line) != 84:
            raise UserError(
                f'Compras Alic {move.name}: línea de {len(line)} chars, se esperan 84.'
            )
        return line

    # -------------------------------------------------------------------------
    # HELPERS: EXTRACCIÓN DE DATOS DEL MOVE
    # -------------------------------------------------------------------------

    def _get_doc_parts(self, move):
        """Extrae punto de venta y número del comprobante.

        Por qué: l10n_latam_document_number tiene formato "00001-00000001".
        Se parsea para obtener PV (5 digits) y número (hasta 20 digits).
        """
        doc_number = move.l10n_latam_document_number or ''
        parts = doc_number.split('-')
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()
        # Fallback: intentar parsear desde move.name
        name_parts = (move.name or '').split(' ')
        if len(name_parts) >= 2:
            num_parts = name_parts[-1].split('-')
            if len(num_parts) >= 2:
                return num_parts[0].strip(), num_parts[1].strip()
        return '0', '0'

    def _get_partner_doc(self, partner):
        """Obtiene código y número de documento del partner.

        Returns:
            tuple: (codigo_doc, numero_doc)
            - codigo_doc: código AFIP del tipo de documento (80=CUIT, 96=DNI, etc.)
            - numero_doc: número del documento (CUIT/DNI/etc.)
        """
        doc_type = partner.l10n_latam_identification_type_id
        # Por qué: l10n_ar_afip_code tiene el código AFIP del tipo de documento
        doc_code = getattr(doc_type, 'l10n_ar_afip_code', '99') or '99'
        doc_num = (partner.vat or '0').replace('-', '').replace(' ', '')
        return str(doc_code), doc_num

    def _get_currency_info(self, move):
        """Obtiene código de moneda y tipo de cambio AFIP.

        Returns:
            tuple: (codigo_moneda, tipo_cambio)
        """
        currency = move.currency_id
        company_currency = move.company_currency_id

        # Código moneda AFIP
        code = getattr(currency, 'l10n_ar_afip_code', False)
        if not code:
            code = self.MONEDA_MAP.get(currency.name, 'PES')

        # Tipo de cambio
        if currency == company_currency:
            rate = 1.0
        else:
            # Por qué: l10n_ar_currency_rate es el TC usado en la factura
            rate = getattr(move, 'l10n_ar_currency_rate', False)
            if not rate:
                rate = currency._convert(
                    1.0, company_currency, move.company_id,
                    move.invoice_date or fields.Date.today()
                )
        return code, rate or 1.0

    def _get_operation_code(self, data):
        """Determina el código de operación AFIP.

        ' ' = gravado, 'E' = exento, 'N' = no gravado, 'X' = exportación.
        """
        tiene_gravado = bool(data['iva_alicuotas'])
        tiene_exento = abs(data['exento']) > 0.01
        tiene_no_gravado = abs(data['no_gravado']) > 0.01

        if tiene_gravado:
            return ' '
        elif tiene_exento:
            return 'E'
        elif tiene_no_gravado:
            return 'N'
        return ' '

    # -------------------------------------------------------------------------
    # HELPERS: FORMATEO DE CAMPOS
    # -------------------------------------------------------------------------

    def _fmt_date(self, dt):
        """Fecha → AAAAMMDD (8 chars). Formato AFIP sin separadores."""
        if not dt:
            return '00000000'
        return dt.strftime('%Y%m%d')

    def _fmt_amount(self, amount, length=15):
        """Importe → posición fija, sin punto decimal.

        Por qué: ARCA usa "13 enteros 2 decimales sin punto decimal" = 15 chars.
        Para NC (negativos): signo '-' ocupa 1 posición, quedan length-1 dígitos.
        Ejemplo: 1500.00 → '000000000150000', -1500.00 → '-00000000150000'
        """
        cents = int(round(abs(amount) * 100))
        if amount < -0.001:
            return '-' + str(cents).zfill(length - 1)
        return str(cents).zfill(length)

    def _fmt_rate(self, rate):
        """Tipo de cambio → 4 enteros + 6 decimales, sin punto (10 chars).

        Ejemplo: 1.0 → '0001000000', 1250.50 → '1250500000'
        """
        value = int(round(abs(rate) * 1000000))
        return str(value).zfill(10)

    def _fmt_num(self, value, length):
        """Campo numérico → ceros a la izquierda, longitud fija."""
        # Limpiar caracteres no numéricos
        cleaned = ''.join(c for c in str(value) if c.isdigit())
        return cleaned.zfill(length)[-length:]

    def _fmt_text(self, value, length):
        """Campo alfanumérico → espacios a la derecha, longitud fija.

        Por qué: Campos de texto se completan con espacios (no ceros).
        Se trunca si excede la longitud.
        """
        text = str(value or '')[:length]
        return text.ljust(length)

    # -------------------------------------------------------------------------
    # DDJJ IVA - REPORTE PORTAL ARCA (F.2002)
    # -------------------------------------------------------------------------

    def _compute_ddjj_iva_html(self, ventas, compras):
        """Genera HTML que simula cómo cargar la DDJJ IVA en el portal ARCA.

        Por qué: El usuario necesita verificar los totales que debe cargar
        en el F.2002 de ARCA antes de presentar. Este reporte agrupa los
        comprobantes por tipo y alícuota, igual que el portal.
        """
        v_data = self._agrupar_ddjj(ventas)
        c_data = self._agrupar_ddjj(compras)
        return self._render_ddjj_html(v_data, c_data)

    def _agrupar_ddjj(self, moves):
        """Agrupa comprobantes por tipo de documento y acumula importes.

        Por qué: El portal ARCA muestra totales agrupados por tipo de
        comprobante (FA-A, FA-B, NC-A, etc.) y por alícuota de IVA.
        Reutiliza _extract_move_data para clasificar importes.
        """
        por_tipo = {}
        alicuotas = {}
        totales = {
            'count': 0, 'gravado': 0.0, 'iva': 0.0,
            'no_gravado': 0.0, 'exento': 0.0, 'total': 0.0,
            'perc_iva': 0.0, 'perc_nacionales': 0.0,
            'perc_iibb': 0.0, 'perc_mun': 0.0,
            'imp_internos': 0.0, 'otros_tributos': 0.0,
            'perc_no_categ': 0.0,
        }

        for move in moves:
            data = self._extract_move_data(move)
            doc_type = move.l10n_latam_document_type_id
            key = doc_type.id

            gravado = sum(a['base'] for a in data['iva_alicuotas'])
            iva = sum(a['amount'] for a in data['iva_alicuotas'])

            if key not in por_tipo:
                por_tipo[key] = {
                    'name': doc_type.name, 'code': doc_type.code,
                    'count': 0, 'gravado': 0.0, 'iva': 0.0,
                    'no_gravado': 0.0, 'exento': 0.0, 'total': 0.0,
                }
            row = por_tipo[key]
            row['count'] += 1
            row['gravado'] += gravado
            row['iva'] += iva
            row['no_gravado'] += data['no_gravado']
            row['exento'] += data['exento']
            row['total'] += data['total']

            # Acumular totales generales
            for field in ('no_gravado', 'exento', 'perc_iva', 'perc_nacionales',
                          'perc_iibb', 'perc_mun', 'imp_internos',
                          'otros_tributos', 'perc_no_categ'):
                totales[field] += data.get(field, 0.0)
            totales['count'] += 1
            totales['gravado'] += gravado
            totales['iva'] += iva
            totales['total'] += data['total']

            # Acumular por código de alícuota IVA
            for alic in data['iva_alicuotas']:
                code = alic['code']
                if code not in alicuotas:
                    alicuotas[code] = {'base': 0.0, 'amount': 0.0}
                alicuotas[code]['base'] += alic['base']
                alicuotas[code]['amount'] += alic['amount']

        return {
            'por_tipo': sorted(por_tipo.values(), key=lambda x: x['code']),
            'alicuotas': alicuotas,
            'totales': totales,
        }

    def _fmt_money(self, amount):
        """Formatea importe estilo argentino: 1.500,00

        Por qué: El reporte DDJJ es para lectura humana, se usa el
        formato de moneda argentino con punto como separador de miles
        y coma para decimales.
        """
        s = f'{abs(amount):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        if amount < -0.005:
            return f'({s})'
        return s

    def _render_ddjj_html(self, v_data, c_data):
        """Renderiza HTML que simula la carga de DDJJ IVA en portal ARCA.

        Por qué: Estructura el reporte en las mismas secciones que el
        F.2002: comprobantes emitidos, recibidos, detalle de alícuotas
        y determinación del impuesto.
        """
        fmt = self._fmt_money
        periodo = self.date_from.strftime('%m/%Y')
        empresa = self.env.company.name
        cuit = self.env.company.vat or 'Sin configurar'

        # Por qué: CSS inline porque Odoo sanitiza <style> blocks en algunos contextos
        css = """
        <style>
            .ddjj-iva { font-family: Arial, sans-serif; font-size: 12px; color: #333; }
            .ddjj-iva h2 { color: #2c3e50; border-bottom: 2px solid #875A7B; padding-bottom: 8px; font-size: 16px; }
            .ddjj-iva h3 { color: #875A7B; margin-top: 20px; font-size: 13px; }
            .ddjj-iva table { width: 100%; border-collapse: collapse; margin-bottom: 15px; }
            .ddjj-iva th { background-color: #875A7B; color: white; padding: 6px 10px;
                           text-align: right; font-size: 11px; white-space: nowrap; }
            .ddjj-iva th:first-child { text-align: left; }
            .ddjj-iva td { padding: 5px 10px; border-bottom: 1px solid #e8e8e8; text-align: right; }
            .ddjj-iva td:first-child { text-align: left; }
            .ddjj-iva .total-row { font-weight: bold; background-color: #f3eef5; }
            .ddjj-iva .subtotal-row { font-weight: bold; border-top: 2px solid #875A7B; }
            .ddjj-iva .saldo-pagar { color: #e74c3c; font-weight: bold; }
            .ddjj-iva .saldo-favor { color: #27ae60; font-weight: bold; }
            .ddjj-iva .det-table td:first-child { width: 55%; }
            .ddjj-iva .section { margin-bottom: 25px; }
            .ddjj-iva .info { color: #888; font-size: 11px; font-style: italic; margin-top: 15px; }
        </style>
        """

        h = [css, '<div class="ddjj-iva">']

        # ---- ENCABEZADO ----
        h.append(f'<h2>DDJJ IVA - F.2002 | Período {periodo}</h2>')
        h.append(f'<p><strong>{empresa}</strong> | CUIT: {cuit}</p>')

        # ---- VENTAS: COMPROBANTES EMITIDOS ----
        vt = v_data['totales']
        h.append('<div class="section">')
        h.append('<h3>COMPROBANTES EMITIDOS (Ventas) - Débito Fiscal</h3>')
        h.append('<table><tr>'
                 '<th>Tipo Comprobante</th><th>Cant.</th><th>Neto Gravado</th>'
                 '<th>Débito Fiscal</th><th>No Gravado</th>'
                 '<th>Exento</th><th>Total</th></tr>')
        for row in v_data['por_tipo']:
            h.append(
                f'<tr><td>{row["name"]}</td>'
                f'<td style="text-align:center">{row["count"]}</td>'
                f'<td>{fmt(row["gravado"])}</td><td>{fmt(row["iva"])}</td>'
                f'<td>{fmt(row["no_gravado"])}</td><td>{fmt(row["exento"])}</td>'
                f'<td>{fmt(row["total"])}</td></tr>'
            )
        h.append(
            f'<tr class="total-row"><td>TOTAL</td>'
            f'<td style="text-align:center">{vt["count"]}</td>'
            f'<td>{fmt(vt["gravado"])}</td><td>{fmt(vt["iva"])}</td>'
            f'<td>{fmt(vt["no_gravado"])}</td><td>{fmt(vt["exento"])}</td>'
            f'<td>{fmt(vt["total"])}</td></tr>'
        )
        h.append('</table>')

        # ---- VENTAS: ALÍCUOTAS IVA ----
        h.append('<h3>Detalle Alícuotas IVA - Débito Fiscal</h3>')
        h.append('<table><tr><th>Alícuota</th>'
                 '<th>Base Imponible</th><th>Débito Fiscal</th></tr>')
        total_base_v = total_iva_v = 0.0
        for code in sorted(v_data['alicuotas'].keys()):
            a = v_data['alicuotas'][code]
            label = self.IVA_CODE_LABEL.get(code, f'Cód. {code}')
            h.append(f'<tr><td>IVA {label}</td>'
                     f'<td>{fmt(a["base"])}</td><td>{fmt(a["amount"])}</td></tr>')
            total_base_v += a['base']
            total_iva_v += a['amount']
        h.append(f'<tr class="total-row"><td>TOTAL DÉBITO FISCAL</td>'
                 f'<td>{fmt(total_base_v)}</td><td>{fmt(total_iva_v)}</td></tr>')
        h.append('</table></div>')

        # ---- COMPRAS: COMPROBANTES RECIBIDOS ----
        ct = c_data['totales']
        h.append('<div class="section">')
        h.append('<h3>COMPROBANTES RECIBIDOS (Compras) - Crédito Fiscal</h3>')
        h.append('<table><tr>'
                 '<th>Tipo Comprobante</th><th>Cant.</th><th>Neto Gravado</th>'
                 '<th>Crédito Fiscal</th><th>No Gravado</th>'
                 '<th>Exento</th><th>Total</th></tr>')
        for row in c_data['por_tipo']:
            h.append(
                f'<tr><td>{row["name"]}</td>'
                f'<td style="text-align:center">{row["count"]}</td>'
                f'<td>{fmt(row["gravado"])}</td><td>{fmt(row["iva"])}</td>'
                f'<td>{fmt(row["no_gravado"])}</td><td>{fmt(row["exento"])}</td>'
                f'<td>{fmt(row["total"])}</td></tr>'
            )
        h.append(
            f'<tr class="total-row"><td>TOTAL</td>'
            f'<td style="text-align:center">{ct["count"]}</td>'
            f'<td>{fmt(ct["gravado"])}</td><td>{fmt(ct["iva"])}</td>'
            f'<td>{fmt(ct["no_gravado"])}</td><td>{fmt(ct["exento"])}</td>'
            f'<td>{fmt(ct["total"])}</td></tr>'
        )
        h.append('</table>')

        # ---- COMPRAS: ALÍCUOTAS IVA ----
        h.append('<h3>Detalle Alícuotas IVA - Crédito Fiscal</h3>')
        h.append('<table><tr><th>Alícuota</th>'
                 '<th>Base Imponible</th><th>Crédito Fiscal</th></tr>')
        total_base_c = total_iva_c = 0.0
        for code in sorted(c_data['alicuotas'].keys()):
            a = c_data['alicuotas'][code]
            label = self.IVA_CODE_LABEL.get(code, f'Cód. {code}')
            h.append(f'<tr><td>IVA {label}</td>'
                     f'<td>{fmt(a["base"])}</td><td>{fmt(a["amount"])}</td></tr>')
            total_base_c += a['base']
            total_iva_c += a['amount']
        h.append(f'<tr class="total-row"><td>TOTAL CRÉDITO FISCAL</td>'
                 f'<td>{fmt(total_base_c)}</td><td>{fmt(total_iva_c)}</td></tr>')
        h.append('</table></div>')

        # ---- DETERMINACIÓN DEL IMPUESTO ----
        debito = vt['iva']
        credito = ct['iva']
        subtotal = debito - credito
        perc_iva = ct['perc_iva']
        saldo = subtotal - perc_iva

        h.append('<div class="section">')
        h.append('<h3>DETERMINACIÓN DEL IMPUESTO</h3>')
        h.append('<table class="det-table">')
        h.append(f'<tr><td>Débito Fiscal</td><td>{fmt(debito)}</td></tr>')
        h.append(f'<tr><td>(-) Crédito Fiscal</td><td>{fmt(credito)}</td></tr>')
        h.append(f'<tr class="subtotal-row"><td>Subtotal</td>'
                 f'<td>{fmt(subtotal)}</td></tr>')

        if abs(perc_iva) > 0.005:
            h.append(f'<tr><td>(-) Percepciones IVA sufridas</td>'
                     f'<td>{fmt(perc_iva)}</td></tr>')
            h.append(f'<tr class="subtotal-row"><td>Subtotal post-percepciones</td>'
                     f'<td>{fmt(saldo)}</td></tr>')

        # Resultado final
        if saldo > 0.005:
            h.append(f'<tr><td><strong>SALDO A PAGAR</strong></td>'
                     f'<td class="saldo-pagar">{fmt(saldo)}</td></tr>')
        elif saldo < -0.005:
            h.append(f'<tr><td><strong>SALDO A FAVOR</strong></td>'
                     f'<td class="saldo-favor">{fmt(abs(saldo))}</td></tr>')
        else:
            h.append('<tr><td><strong>SIN SALDO</strong></td>'
                     '<td>0,00</td></tr>')

        h.append('</table>')

        # ---- RESUMEN OTROS TRIBUTOS (informativo) ----
        otros_items = [
            ('Percepciones IIBB', vt.get('perc_iibb', 0), ct.get('perc_iibb', 0)),
            ('Percepciones Municipales', vt.get('perc_mun', 0), ct.get('perc_mun', 0)),
            ('Percepciones Nacionales', vt.get('perc_nacionales', 0), ct.get('perc_nacionales', 0)),
            ('Impuestos Internos', vt.get('imp_internos', 0), ct.get('imp_internos', 0)),
            ('Otros Tributos', vt.get('otros_tributos', 0), ct.get('otros_tributos', 0)),
        ]
        # Por qué: Solo mostrar tributos que tengan algún importe
        otros_con_valor = [(n, e, r) for n, e, r in otros_items
                           if abs(e) > 0.005 or abs(r) > 0.005]
        if otros_con_valor:
            h.append('<h3>OTROS TRIBUTOS (informativo - no integran DDJJ IVA)</h3>')
            h.append('<table><tr><th>Concepto</th>'
                     '<th>Emitidos</th><th>Recibidos</th></tr>')
            for nombre, emitido, recibido in otros_con_valor:
                h.append(f'<tr><td>{nombre}</td>'
                         f'<td>{fmt(emitido)}</td><td>{fmt(recibido)}</td></tr>')
            h.append('</table>')

        h.append('</div>')

        h.append('<p class="info">(*) Este reporte es una previsualización '
                 'orientativa. Verificar contra el portal ARCA antes de presentar. '
                 'No incluye retenciones IVA sufridas ni saldo a favor de '
                 'períodos anteriores.</p>')
        h.append('</div>')
        return '\n'.join(h)

    # -------------------------------------------------------------------------
    # UTILIDADES
    # -------------------------------------------------------------------------

    def _encode_lines(self, lines):
        """Codifica lista de líneas TXT a base64 para descarga.

        Por qué: ARCA espera archivos con salto de línea CRLF y encoding latin-1.
        Sin salto de línea al final del último registro.
        """
        if not lines:
            return False
        content = '\r\n'.join(lines)
        return base64.b64encode(content.encode('latin-1', errors='replace'))

    def _generar_resumen(self, ventas, compras, v_lines, c_lines,
                         v_alic=None, c_alic=None):
        """Genera texto de resumen para mostrar en el wizard."""
        return (
            f'Período: {self.date_from.strftime("%d/%m/%Y")} - '
            f'{self.date_to.strftime("%d/%m/%Y")}\n'
            f'Empresa: {self.env.company.name}\n'
            f'CUIT: {self.env.company.vat or "Sin configurar"}\n\n'
            f'VENTAS:\n'
            f'  Comprobantes: {len(ventas)}\n'
            f'  Líneas cabecera: {len(v_lines)}\n'
            f'  Líneas alícuotas: {len(v_alic or [])}\n\n'
            f'COMPRAS:\n'
            f'  Comprobantes: {len(compras)}\n'
            f'  Líneas cabecera: {len(c_lines)}\n'
            f'  Líneas alícuotas: {len(c_alic or [])}'
        )
