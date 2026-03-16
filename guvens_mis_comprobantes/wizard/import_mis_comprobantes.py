# -*- coding: utf-8 -*-
import base64
import csv
import io
import re
from datetime import datetime

from odoo import models, fields, _
from odoo.exceptions import UserError


# Por qué: mapeo entre el texto del CSV de "Mis Comprobantes" y el internal_type
# de l10n_latam.document.type. AFIP usa texto libre, Odoo usa internal_type
AFIP_DOC_TYPE_MAP = {
    'factura': 'invoice',
    'nota de débito': 'debit_note',
    'nota de crédito': 'credit_note',
    'recibo': 'invoice',
}

# Por qué: AFIP texto → move_type de Odoo para filtrar en search
AFIP_MOVE_TYPE_MAP = {
    'factura': 'in_invoice',
    'nota de débito': 'in_invoice',  # ND es invoice con doc_type debit_note
    'nota de crédito': 'in_refund',
    'recibo': 'in_invoice',
}

# Por qué: código numérico AFIP → move_type de Odoo
# NC (códigos 3, 8, 13, 203, 208, 213) son refunds, el resto invoices
AFIP_CODE_REFUND = {'3', '8', '13', '203', '208', '213'}


class ImportMisComprobantes(models.TransientModel):
    _name = 'guvens.import.mis.comprobantes'
    _description = 'Importar CSV de Mis Comprobantes / Portal IVA AFIP'

    csv_file = fields.Binary(string='Archivo CSV', required=True)
    csv_filename = fields.Char(string='Nombre archivo')
    period = fields.Char(
        string='Período (MM/YYYY)',
        help='Se auto-detecta de la primera línea del CSV',
    )
    detected_format = fields.Char(
        string='Formato detectado',
        readonly=True,
    )

    # -------------------------------------------------------------------------
    # Utilidades de parseo comunes
    # -------------------------------------------------------------------------

    def _parse_afip_amount(self, value):
        """Convierte importe formato AFIP (punto=miles, coma=decimal) a float.
        Ejemplo: '10.000,00' → 10000.00 | '52900,00' → 52900.00
        """
        if not value or not value.strip():
            return 0.0
        # Por qué: AFIP usa punto como separador de miles y coma como decimal
        clean = value.strip().replace('.', '').replace(',', '.')
        try:
            return float(clean)
        except (ValueError, TypeError):
            return 0.0

    def _parse_portal_iva_amount(self, value):
        """Convierte importe Portal IVA (solo coma=decimal, sin sep miles).
        Ejemplo: '52900,00' → 52900.00 | '-210439,68' → -210439.68
        """
        if not value or not value.strip():
            return 0.0
        # Por qué: Portal IVA no usa punto como separador de miles,
        # solo coma como decimal → reemplazar coma por punto directo
        clean = value.strip().replace(',', '.')
        try:
            return float(clean)
        except (ValueError, TypeError):
            return 0.0

    def _parse_afip_date(self, value):
        """Convierte fecha AFIP dd/mm/yyyy a date object."""
        if not value or not value.strip():
            return False
        try:
            return datetime.strptime(value.strip(), '%d/%m/%Y').date()
        except ValueError:
            return False

    def _parse_portal_iva_date(self, value):
        """Convierte fecha Portal IVA yyyy-mm-dd a date object."""
        if not value or not value.strip():
            return False
        try:
            return datetime.strptime(value.strip(), '%Y-%m-%d').date()
        except ValueError:
            return False

    def _normalize_doc_type(self, afip_tipo):
        """Normaliza el tipo de comprobante del CSV a lowercase para mapeo."""
        if not afip_tipo:
            return ''
        return afip_tipo.strip().lower()

    def _build_document_number(self, pos, number):
        """Construye el document_number en formato Odoo: 00001-00000123"""
        pos_clean = re.sub(r'[^0-9]', '', pos or '')
        num_clean = re.sub(r'[^0-9]', '', number or '')
        if not pos_clean or not num_clean:
            return False
        return '%s-%s' % (pos_clean.zfill(5), num_clean.zfill(8))

    def _clean_cuit(self, cuit):
        """Extrae solo dígitos del CUIT."""
        return re.sub(r'[^0-9]', '', cuit or '')

    # -------------------------------------------------------------------------
    # Auto-detección de formato
    # -------------------------------------------------------------------------

    def _detect_csv_format(self, header_row):
        """Detecta formato del CSV por la primera columna del header.
        Por qué: Portal IVA empieza con 'Fecha de Emisión',
        Mis Comprobantes empieza con 'Fecha'.
        """
        first_col = header_row[0].strip().strip('"').lower()
        if 'fecha de emisi' in first_col:
            return 'portal_iva'
        elif 'fecha' == first_col:
            return 'mis_comprobantes'
        return 'mis_comprobantes'

    def _detect_period_from_filename(self, filename):
        """Intenta extraer período del nombre del archivo.
        Ejemplo: 'comprobantes_periodo_202602_compras_...' → '02/2026'
        """
        if not filename:
            return False
        match = re.search(r'(\d{4})(\d{2})', filename)
        if match:
            return '%s/%s' % (match.group(2), match.group(1))
        return False

    # -------------------------------------------------------------------------
    # Decodificación CSV
    # -------------------------------------------------------------------------

    def _decode_csv(self):
        """Decodifica el archivo CSV subido. Retorna el texto limpio."""
        try:
            csv_data = base64.b64decode(self.csv_file)
            csv_data = csv_data.replace(b'\x00', b'')
            try:
                csv_text = csv_data.decode('utf-8-sig')
            except UnicodeDecodeError:
                csv_text = csv_data.decode('latin-1')
        except Exception as e:
            raise UserError(_('Error al leer el archivo: %s') % str(e))

        # Por qué: CSV de AFIP puede tener \r sueltos o \r\n
        csv_text = '\n'.join(csv_text.splitlines())
        return csv_text

    # -------------------------------------------------------------------------
    # Matching por scoring contra Odoo
    # -------------------------------------------------------------------------

    def _score_move(self, move, partner_vat, doc_number, date, amount_total):
        """Calcula score 0-100 de correspondencia entre línea AFIP y factura Odoo.

        Criterios (100 pts total):
        - CUIT proveedor coincide:        30 pts
        - Nro comprobante (PV-Nro):       30 pts
        - Fecha exacta: 20 pts / ±5 días: 10 pts
        - Importe ±$0.01: 20 pts / ±1%: 15 pts / ±5%: 10 pts
        """
        score = 0
        details = []

        # -- CUIT (30 pts) --
        move_vat = self._clean_cuit(move.partner_id.vat or '')
        if partner_vat and move_vat and partner_vat == move_vat:
            score += 30
            details.append('CUIT OK')
        elif partner_vat and move_vat:
            details.append('CUIT dif')

        # -- Nro comprobante (30 pts) --
        odoo_doc_num = move.l10n_latam_document_number or ''
        if doc_number and odoo_doc_num and doc_number == odoo_doc_num:
            score += 30
            details.append('Nro OK')
        elif doc_number and odoo_doc_num:
            details.append('Nro dif')

        # -- Fecha (20 pts exacta, 10 pts ±5 días) --
        if date and move.invoice_date:
            delta = abs((move.invoice_date - date).days)
            if delta == 0:
                score += 20
                details.append('Fecha OK')
            elif delta <= 5:
                score += 10
                details.append('Fecha ±%dd' % delta)
            else:
                details.append('Fecha dif %dd' % delta)

        # -- Importe (20 pts exacto, 15 pts ±1%, 10 pts ±5%) --
        odoo_total = abs(move.amount_total)
        afip_total = abs(amount_total)
        if afip_total > 0:
            diff_abs = abs(odoo_total - afip_total)
            diff_pct = (diff_abs / afip_total) * 100
            if diff_abs <= 0.01:
                score += 20
                details.append('Importe OK')
            elif diff_pct <= 1.0:
                score += 15
                details.append('Importe ±%.1f%%' % diff_pct)
            elif diff_pct <= 5.0:
                score += 10
                details.append('Importe ±%.1f%%' % diff_pct)
            else:
                details.append('Importe dif %.1f%%' % diff_pct)
        elif odoo_total == 0 and afip_total == 0:
            score += 20
            details.append('Importe OK ($0)')

        return score, ' | '.join(details)

    def _find_best_match(self, partner_vat, doc_number, date, amount_total,
                         move_type='in_invoice', afip_code=False,
                         afip_tipo=False):
        """Busca la mejor factura candidata en Odoo por scoring.

        Por qué: el CUIT del emisor es el dato más confiable del CSV — es único
        por proveedor y no tiene ambigüedad. Las fases priorizan CUIT como eje
        principal y sólo buscan sin CUIT como último recurso.

        Estrategia en 3 fases (CUIT-first):
        1. CUIT + nro comprobante exacto → si score >= 80, listo
        2. CUIT + fecha ±30 días → scorear candidatos del mismo proveedor
        3. Sin CUIT: importe similar + fecha cercana → último recurso
        """
        Move = self.env['account.move']
        best_move = Move
        best_score = 0
        best_detail = ''
        from datetime import timedelta

        # -- Fase 1: CUIT + nro comprobante exacto --
        # Por qué: arrancamos por CUIT para acotar el universo al proveedor
        if partner_vat and doc_number:
            domain_exact = [
                ('move_type', '=', move_type),
                ('state', '=', 'posted'),
                ('partner_id.vat', '=', partner_vat),
                ('l10n_latam_document_number', '=', doc_number),
            ]
            if afip_code:
                domain_exact.append(
                    ('l10n_latam_document_type_id.code', '=', afip_code))
            elif afip_tipo:
                normalized = self._normalize_doc_type(afip_tipo)
                internal_type = AFIP_DOC_TYPE_MAP.get(normalized, 'invoice')
                domain_exact.append(
                    ('l10n_latam_document_type_id.internal_type', '=',
                     internal_type))

            for move in Move.search(domain_exact, limit=5):
                sc, det = self._score_move(
                    move, partner_vat, doc_number, date, amount_total)
                if sc > best_score:
                    best_score, best_detail, best_move = sc, det, move

        if best_score >= 80:
            return best_move, best_score, best_detail

        # -- Fase 2: CUIT + fecha cercana (sin exigir nro exacto) --
        # Por qué: mismo proveedor, puede haber diferencia en PV/nro
        # por carga manual o prefijo distinto
        if partner_vat:
            domain_cuit = [
                ('move_type', '=', move_type),
                ('state', '=', 'posted'),
                ('partner_id.vat', '=', partner_vat),
            ]
            if date:
                domain_cuit += [
                    ('invoice_date', '>=', date - timedelta(days=30)),
                    ('invoice_date', '<=', date + timedelta(days=30)),
                ]

            for move in Move.search(domain_cuit, limit=15):
                sc, det = self._score_move(
                    move, partner_vat, doc_number, date, amount_total)
                if sc > best_score:
                    best_score, best_detail, best_move = sc, det, move

        if best_score >= 60:
            return best_move, best_score, best_detail

        # -- Fase 3: sin CUIT — importe + fecha (último recurso) --
        # Por qué: si el CUIT no está o no matchea nada, buscar por
        # importe cercano + fecha como indicador débil
        if date and amount_total:
            afip_abs = abs(amount_total)
            tolerance = max(afip_abs * 0.05, 1.0)
            domain_amount = [
                ('move_type', '=', move_type),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', date - timedelta(days=15)),
                ('invoice_date', '<=', date + timedelta(days=15)),
            ]
            for move in Move.search(domain_amount, limit=20):
                if abs(abs(move.amount_total) - afip_abs) > tolerance:
                    continue
                sc, det = self._score_move(
                    move, partner_vat, doc_number, date, amount_total)
                if sc > best_score:
                    best_score, best_detail, best_move = sc, det, move

        return best_move, best_score, best_detail

    def _score_to_state(self, score, move):
        """Convierte score a estado del cruce."""
        if not move:
            return 'missing_in_odoo'
        if score >= 80:
            return 'match'
        # Por qué: score entre 40-79 = encontró algo pero con diferencias
        return 'mismatch'

    # -------------------------------------------------------------------------
    # Parser Mis Comprobantes (formato original)
    # -------------------------------------------------------------------------

    def _parse_mis_comprobantes(self, reader):
        """Parsea CSV de Mis Comprobantes (formato original, 16+ columnas)."""
        lines_data = []
        period_detected = False

        for row in reader:
            if not row or len(row) < 15:
                continue

            date = self._parse_afip_date(row[0])
            doc_type = row[1].strip() if row[1] else ''
            pos_number = row[2].strip() if row[2] else ''
            doc_number_from = row[3].strip() if row[3] else ''
            cae = row[5].strip() if len(row) > 5 and row[5] else ''
            partner_vat_raw = row[7].strip() if len(row) > 7 and row[7] else ''
            partner_name = row[8].strip() if len(row) > 8 and row[8] else ''
            amount_net = self._parse_afip_amount(row[11] if len(row) > 11 else '')
            amount_untaxed = self._parse_afip_amount(row[12] if len(row) > 12 else '')
            amount_exempt = self._parse_afip_amount(row[13] if len(row) > 13 else '')
            amount_iva = self._parse_afip_amount(row[14] if len(row) > 14 else '')
            amount_total = self._parse_afip_amount(row[15] if len(row) > 15 else '')

            partner_vat = self._clean_cuit(partner_vat_raw)
            document_number = self._build_document_number(pos_number, doc_number_from)

            if date and not period_detected:
                self.period = date.strftime('%m/%Y')
                period_detected = True

            # Matching por scoring
            normalized = self._normalize_doc_type(doc_type)
            move_type = AFIP_MOVE_TYPE_MAP.get(normalized, 'in_invoice')
            move, score, detail = self._find_best_match(
                partner_vat, document_number, date, amount_total,
                move_type=move_type, afip_tipo=doc_type,
            )
            state = self._score_to_state(score, move)

            lines_data.append({
                'import_date': fields.Date.context_today(self),
                'period': self.period or '',
                'company_id': self.env.company.id,
                'source': 'mis_comprobantes',
                'date': date,
                'doc_type': doc_type,
                'pos_number': pos_number,
                'doc_number': doc_number_from,
                'cae': cae,
                'partner_vat': partner_vat,
                'partner_name': partner_name,
                'amount_total': amount_total,
                'amount_net': amount_net,
                'amount_exempt': amount_exempt,
                'amount_untaxed': amount_untaxed,
                'amount_iva': amount_iva,
                'state': state,
                'match_score': score,
                'match_detail': detail,
                'move_id': move.id if move else False,
            })

        return lines_data

    # -------------------------------------------------------------------------
    # Parser Portal IVA (formato nuevo, 32 columnas)
    # -------------------------------------------------------------------------

    def _parse_portal_iva(self, reader):
        """Parsea CSV de Portal IVA — Compras DDJJ (32 columnas).
        Columnas:
         0: Fecha Emisión (yyyy-mm-dd)
         1: Tipo Comprobante (código numérico AFIP)
         2: Punto de Venta
         3: Número Comprobante
         4: Tipo Doc. Vendedor (80=CUIT)
         5: Nro. Doc. Vendedor
         6: Denominación Vendedor
         7: Importe Total
         8: Moneda Original (PES, DOL)
         9: Tipo de Cambio
        10: Importe No Gravado
        11: Importe Exento
        12: Crédito Fiscal Computable
        13: Perc/Pagos Otros Imp. Nac.
        14: Perc. IIBB
        15: Imp. Municipales
        16: Perc/Pagos IVA
        17: Imp. Internos
        18: Otros Tributos
        19: Neto 0%
        20: Neto 2.5%    21: IVA 2.5%
        22: Neto 5%      23: IVA 5%
        24: Neto 10.5%   25: IVA 10.5%
        26: Neto 21%     27: IVA 21%
        28: Neto 27%     29: IVA 27%
        30: Total Neto Gravado
        31: Total IVA
        """
        lines_data = []
        period_detected = False
        # Por qué: buscamos l10n_latam.document.type por code para obtener el nombre
        DocType = self.env['l10n_latam.document.type']

        for row in reader:
            if not row or len(row) < 20:
                continue

            date = self._parse_portal_iva_date(row[0])
            afip_code = row[1].strip() if row[1] else ''
            pos_number = row[2].strip() if row[2] else ''
            doc_number_from = row[3].strip() if row[3] else ''
            partner_vat_raw = row[5].strip() if len(row) > 5 and row[5] else ''
            partner_name = (row[6].strip().strip('"')
                           if len(row) > 6 and row[6] else '')
            amount_total = self._parse_portal_iva_amount(
                row[7] if len(row) > 7 else '')
            currency_code = (row[8].strip().strip('"')
                             if len(row) > 8 and row[8] else '')
            exchange_rate = self._parse_portal_iva_amount(
                row[9] if len(row) > 9 else '')
            amount_untaxed = self._parse_portal_iva_amount(
                row[10] if len(row) > 10 else '')
            amount_exempt = self._parse_portal_iva_amount(
                row[11] if len(row) > 11 else '')
            credito_fiscal = self._parse_portal_iva_amount(
                row[12] if len(row) > 12 else '')

            # Percepciones
            perc_otros_nac = self._parse_portal_iva_amount(
                row[13] if len(row) > 13 else '')
            perc_iibb = self._parse_portal_iva_amount(
                row[14] if len(row) > 14 else '')
            perc_municipal = self._parse_portal_iva_amount(
                row[15] if len(row) > 15 else '')
            perc_iva = self._parse_portal_iva_amount(
                row[16] if len(row) > 16 else '')
            perc_internos = self._parse_portal_iva_amount(
                row[17] if len(row) > 17 else '')
            otros_tributos = self._parse_portal_iva_amount(
                row[18] if len(row) > 18 else '')

            # Desglose IVA por alícuota
            neto_iva_0 = self._parse_portal_iva_amount(
                row[19] if len(row) > 19 else '')
            neto_iva_25 = self._parse_portal_iva_amount(
                row[20] if len(row) > 20 else '')
            iva_25 = self._parse_portal_iva_amount(
                row[21] if len(row) > 21 else '')
            neto_iva_5 = self._parse_portal_iva_amount(
                row[22] if len(row) > 22 else '')
            iva_5 = self._parse_portal_iva_amount(
                row[23] if len(row) > 23 else '')
            neto_iva_105 = self._parse_portal_iva_amount(
                row[24] if len(row) > 24 else '')
            iva_105 = self._parse_portal_iva_amount(
                row[25] if len(row) > 25 else '')
            neto_iva_21 = self._parse_portal_iva_amount(
                row[26] if len(row) > 26 else '')
            iva_21 = self._parse_portal_iva_amount(
                row[27] if len(row) > 27 else '')
            neto_iva_27 = self._parse_portal_iva_amount(
                row[28] if len(row) > 28 else '')
            iva_27 = self._parse_portal_iva_amount(
                row[29] if len(row) > 29 else '')
            amount_net = self._parse_portal_iva_amount(
                row[30] if len(row) > 30 else '')
            amount_iva = self._parse_portal_iva_amount(
                row[31] if len(row) > 31 else '')

            partner_vat = self._clean_cuit(partner_vat_raw)
            document_number = self._build_document_number(
                pos_number, doc_number_from)

            # Por qué: buscar nombre del tipo de comprobante por código AFIP
            doc_type_rec = DocType.search(
                [('code', '=', afip_code)], limit=1)
            doc_type_name = doc_type_rec.name if doc_type_rec else afip_code

            if date and not period_detected:
                self.period = date.strftime('%m/%Y')
                period_detected = True

            # Matching por scoring con código AFIP
            move_type = ('in_refund' if afip_code in AFIP_CODE_REFUND
                         else 'in_invoice')
            move, score, detail = self._find_best_match(
                partner_vat, document_number, date, amount_total,
                move_type=move_type, afip_code=afip_code,
            )
            state = self._score_to_state(score, move)

            lines_data.append({
                'import_date': fields.Date.context_today(self),
                'period': self.period or '',
                'company_id': self.env.company.id,
                'source': 'portal_iva',
                'date': date,
                'doc_type': doc_type_name,
                'afip_code': afip_code,
                'pos_number': pos_number,
                'doc_number': doc_number_from,
                'partner_vat': partner_vat,
                'partner_name': partner_name,
                'amount_total': amount_total,
                'amount_net': amount_net,
                'amount_exempt': amount_exempt,
                'amount_untaxed': amount_untaxed,
                'amount_iva': amount_iva,
                'currency_code': currency_code,
                'exchange_rate': exchange_rate,
                'credito_fiscal': credito_fiscal,
                # Percepciones
                'amount_perc_otros_nac': perc_otros_nac,
                'amount_perc_iibb': perc_iibb,
                'amount_perc_municipal': perc_municipal,
                'amount_perc_iva': perc_iva,
                'amount_perc_internos': perc_internos,
                'amount_otros_tributos': otros_tributos,
                # Desglose IVA
                'neto_iva_0': neto_iva_0,
                'neto_iva_25': neto_iva_25,
                'iva_25': iva_25,
                'neto_iva_5': neto_iva_5,
                'iva_5': iva_5,
                'neto_iva_105': neto_iva_105,
                'iva_105': iva_105,
                'neto_iva_21': neto_iva_21,
                'iva_21': iva_21,
                'neto_iva_27': neto_iva_27,
                'iva_27': iva_27,
                'amount_no_gravado': amount_untaxed,
                'state': state,
                'match_score': score,
                'match_detail': detail,
                'move_id': move.id if move else False,
            })

        return lines_data

    # -------------------------------------------------------------------------
    # Acción principal
    # -------------------------------------------------------------------------

    def action_import(self):
        """Importa CSV de Mis Comprobantes o Portal IVA y cruza contra facturas."""
        self.ensure_one()
        if not self.csv_file:
            raise UserError(_('Debe seleccionar un archivo CSV.'))

        csv_text = self._decode_csv()
        reader = csv.reader(io.StringIO(csv_text), delimiter=';')

        try:
            header = next(reader)
        except StopIteration:
            raise UserError(_('El archivo CSV está vacío.'))

        # Auto-detectar formato por header
        csv_format = self._detect_csv_format(header)
        self.detected_format = (
            'Portal IVA — Compras' if csv_format == 'portal_iva'
            else 'Mis Comprobantes'
        )

        # Por qué: intentar extraer período del nombre del archivo
        # antes de parsear (fallback: primera fecha del CSV)
        period_from_filename = self._detect_period_from_filename(
            self.csv_filename)
        if period_from_filename:
            self.period = period_from_filename

        # Parsear según formato detectado
        if csv_format == 'portal_iva':
            lines_data = self._parse_portal_iva(reader)
        else:
            lines_data = self._parse_mis_comprobantes(reader)

        if not lines_data:
            raise UserError(_('No se encontraron líneas válidas en el CSV.'))

        Line = self.env['guvens.mis.comprobantes.line']

        # -- Crear líneas importadas --
        created_lines = Line.create(lines_data)

        # -- Detectar facturas en Odoo que no están en el CSV --
        if self.period:
            self._detect_missing_in_afip(created_lines, csv_format)

        # -- Recargar líneas del período --
        all_lines = Line.search([
            ('period', '=', self.period),
            ('import_date', '=', fields.Date.context_today(self)),
        ])

        # Resumen para notification
        counts = {s: 0 for s in ['match', 'mismatch', 'missing_in_odoo', 'missing_in_afip']}
        for line in all_lines:
            if line.state in counts:
                counts[line.state] += 1

        return {
            'type': 'ir.actions.act_window',
            'name': _('Cruce %s — %s') % (self.detected_format, self.period or ''),
            'res_model': 'guvens.mis.comprobantes.line',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', all_lines.ids)],
            'target': 'current',
            'context': {
                'search_default_group_state': 1,
                # Por qué: notification con resumen del cruce
                'default_notification': _(
                    'Importadas: %d | Coinciden: %d | Difieren: %d | '
                    'Faltan en Odoo: %d | Faltan en AFIP: %d'
                ) % (
                    len(created_lines),
                    counts['match'],
                    counts['mismatch'],
                    counts['missing_in_odoo'],
                    counts['missing_in_afip'],
                ),
            },
        }

    def _detect_missing_in_afip(self, imported_lines, csv_format='mis_comprobantes'):
        """Busca facturas de proveedor en Odoo del período que no están en el CSV."""
        if not self.period:
            return

        try:
            month, year = self.period.split('/')
            date_from = datetime.strptime(
                '01/%s/%s' % (month, year), '%d/%m/%Y').date()
            if int(month) == 12:
                date_to = datetime.strptime(
                    '01/01/%s' % (int(year) + 1), '%d/%m/%Y').date()
            else:
                date_to = datetime.strptime(
                    '01/%s/%s' % (str(int(month) + 1).zfill(2), year),
                    '%d/%m/%Y').date()
        except (ValueError, AttributeError):
            return

        moves = self.env['account.move'].search([
            ('move_type', 'in', ['in_invoice', 'in_refund']),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<', date_to),
            ('company_id', '=', self.env.company.id),
        ])

        matched_move_ids = set(
            imported_lines.filtered('move_id').mapped('move_id.id'))

        Line = self.env['guvens.mis.comprobantes.line']
        missing_data = []
        for move in moves:
            if move.id in matched_move_ids:
                continue

            doc_num = move.l10n_latam_document_number or ''
            parts = doc_num.split('-')
            pos = parts[0] if parts else ''
            number = parts[1] if len(parts) > 1 else ''

            missing_data.append({
                'import_date': fields.Date.context_today(self),
                'period': self.period,
                'company_id': self.env.company.id,
                'source': csv_format,
                'date': move.invoice_date,
                'doc_type': move.l10n_latam_document_type_id.name or '',
                'afip_code': (move.l10n_latam_document_type_id.code or ''
                              if csv_format == 'portal_iva' else ''),
                'pos_number': pos,
                'doc_number': number,
                'cae': '',
                'partner_vat': move.partner_id.vat or '',
                'partner_name': move.partner_id.name or '',
                'amount_total': abs(move.amount_total),
                'amount_net': 0.0,
                'amount_iva': 0.0,
                'state': 'missing_in_afip',
                'move_id': move.id,
            })

        if missing_data:
            Line.create(missing_data)
