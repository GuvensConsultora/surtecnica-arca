# -*- coding: utf-8 -*-
import base64
import csv
import io
import re
from datetime import datetime

from odoo import models, fields, _
from odoo.exceptions import UserError


# Por qué: mapeo entre el texto del CSV de AFIP y el internal_type de l10n_latam.document.type
# AFIP usa texto libre ("Factura", "Nota de Crédito", etc.), Odoo usa internal_type
AFIP_DOC_TYPE_MAP = {
    'factura': 'invoice',
    'nota de débito': 'debit_note',
    'nota de crédito': 'credit_note',
    'recibo': 'invoice',
}

# Por qué: AFIP usa texto, necesitamos el move_type de Odoo para filtrar
AFIP_MOVE_TYPE_MAP = {
    'factura': 'in_invoice',
    'nota de débito': 'in_invoice',  # ND es invoice con doc_type debit_note
    'nota de crédito': 'in_refund',
    'recibo': 'in_invoice',
}


class ImportMisComprobantes(models.TransientModel):
    _name = 'guvens.import.mis.comprobantes'
    _description = 'Importar CSV de Mis Comprobantes AFIP'

    csv_file = fields.Binary(string='Archivo CSV', required=True)
    csv_filename = fields.Char(string='Nombre archivo')
    period = fields.Char(
        string='Período (MM/YYYY)',
        help='Se auto-detecta de la primera línea del CSV',
    )

    def _parse_afip_amount(self, value):
        """Convierte importe formato AFIP (punto=miles, coma=decimal) a float.
        Ejemplo: '10.000,00' → 10000.00
        """
        if not value or not value.strip():
            return 0.0
        # Por qué: AFIP usa punto como separador de miles y coma como decimal
        clean = value.strip().replace('.', '').replace(',', '.')
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

    def _find_matching_move(self, doc_number, partner_vat, afip_tipo, date):
        """Busca factura en Odoo que matchee con la línea del CSV.

        Criterios de match:
        1. document_number exacto (PV-Nro)
        2. CUIT del proveedor
        3. Tipo de comprobante (internal_type del document_type)
        4. Solo facturas de proveedor posteadas
        """
        Move = self.env['account.move']
        normalized = self._normalize_doc_type(afip_tipo)
        move_type = AFIP_MOVE_TYPE_MAP.get(normalized, 'in_invoice')
        internal_type = AFIP_DOC_TYPE_MAP.get(normalized, 'invoice')

        # Por qué: buscamos por document_number que es PV-Nro normalizado
        domain = [
            ('move_type', '=', move_type),
            ('state', '=', 'posted'),
            ('l10n_latam_document_number', '=', doc_number),
            ('l10n_latam_document_type_id.internal_type', '=', internal_type),
        ]

        # Por qué: filtramos por CUIT si está disponible, pero con ilike
        # porque en Odoo puede estar con o sin guiones
        if partner_vat:
            domain.append(('partner_id.vat', '=', partner_vat))

        moves = Move.search(domain, limit=1)
        return moves

    def action_import(self):
        """Importa CSV de Mis Comprobantes y cruza contra facturas de proveedor."""
        self.ensure_one()
        if not self.csv_file:
            raise UserError(_('Debe seleccionar un archivo CSV.'))

        # -- Decodificar CSV --
        # Por qué: AFIP exporta en latin-1 (ISO-8859-1), no UTF-8
        try:
            csv_data = base64.b64decode(self.csv_file)
            # Tip: intentar utf-8-sig primero (tiene BOM), fallback a latin-1
            # Por qué: archivos de AFIP a veces contienen bytes NUL (0x00)
            # que rompen csv.reader — los eliminamos antes de decodificar
            csv_data = csv_data.replace(b'\x00', b'')
            try:
                csv_text = csv_data.decode('utf-8-sig')
            except UnicodeDecodeError:
                csv_text = csv_data.decode('latin-1')
        except Exception as e:
            raise UserError(_('Error al leer el archivo: %s') % str(e))

        # Por qué: CSV de AFIP puede tener \r sueltos (Mac) o \r\n (Windows)
        # splitlines() normaliza cualquier line ending → reconstruimos con \n
        csv_text = '\n'.join(csv_text.splitlines())
        reader = csv.reader(io.StringIO(csv_text), delimiter=';')

        # Por qué: primera fila es header, la saltamos
        try:
            header = next(reader)
        except StopIteration:
            raise UserError(_('El archivo CSV está vacío.'))

        Line = self.env['guvens.mis.comprobantes.line']
        lines_data = []
        period_detected = False

        for row in reader:
            if not row or len(row) < 15:
                continue

            # -- Parsear campos del CSV --
            date = self._parse_afip_date(row[0])
            doc_type = row[1].strip() if row[1] else ''
            pos_number = row[2].strip() if row[2] else ''
            doc_number_from = row[3].strip() if row[3] else ''
            cae = row[5].strip() if len(row) > 5 and row[5] else ''
            # Columna 7: tipo doc emisor (no usado)
            partner_vat_raw = row[7].strip() if len(row) > 7 and row[7] else ''
            partner_name = row[8].strip() if len(row) > 8 and row[8] else ''
            amount_net = self._parse_afip_amount(row[11] if len(row) > 11 else '')
            amount_untaxed = self._parse_afip_amount(row[12] if len(row) > 12 else '')
            amount_exempt = self._parse_afip_amount(row[13] if len(row) > 13 else '')
            amount_iva = self._parse_afip_amount(row[14] if len(row) > 14 else '')
            amount_total = self._parse_afip_amount(row[15] if len(row) > 15 else '')

            partner_vat = self._clean_cuit(partner_vat_raw)
            document_number = self._build_document_number(pos_number, doc_number_from)

            # Por qué: auto-detectar período de la primera línea con fecha válida
            if date and not period_detected:
                self.period = date.strftime('%m/%Y')
                period_detected = True

            # -- Buscar match en Odoo --
            move = self._find_matching_move(
                document_number, partner_vat, doc_type, date
            )

            if move:
                # Comparar importes con tolerancia de 1 centavo
                diff = abs(move.amount_total) - amount_total
                if abs(diff) <= 0.01:
                    state = 'match'
                else:
                    state = 'mismatch'
            else:
                state = 'missing_in_odoo'

            lines_data.append({
                'import_date': fields.Date.context_today(self),
                'period': self.period or '',
                'company_id': self.env.company.id,
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
                'move_id': move.id if move else False,
            })

        if not lines_data:
            raise UserError(_('No se encontraron líneas válidas en el CSV.'))

        # -- Crear líneas importadas --
        created_lines = Line.create(lines_data)

        # -- Detectar facturas en Odoo que no están en el CSV (missing_in_afip) --
        if self.period:
            self._detect_missing_in_afip(created_lines)

        # -- Recargar líneas del período para incluir missing_in_afip --
        all_lines = Line.search([
            ('period', '=', self.period),
            ('import_date', '=', fields.Date.context_today(self)),
        ])

        # Por qué: devolver action para abrir la vista tree con los resultados
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cruce Mis Comprobantes — %s') % (self.period or ''),
            'res_model': 'guvens.mis.comprobantes.line',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', all_lines.ids)],
            'target': 'current',
            'context': {'search_default_group_state': 1},
        }

    def _detect_missing_in_afip(self, imported_lines):
        """Busca facturas de proveedor en Odoo del período que no están en el CSV.

        Por qué: si una factura está cargada en Odoo pero no aparece en AFIP,
        puede ser un comprobante apócrifo o un error de carga.
        """
        if not self.period:
            return

        # Parsear período MM/YYYY → rango de fechas
        try:
            month, year = self.period.split('/')
            date_from = datetime.strptime('01/%s/%s' % (month, year), '%d/%m/%Y').date()
            # Último día del mes
            if int(month) == 12:
                date_to = datetime.strptime('01/01/%s' % (int(year) + 1), '%d/%m/%Y').date()
            else:
                date_to = datetime.strptime('01/%s/%s' % (str(int(month) + 1).zfill(2), year), '%d/%m/%Y').date()
        except (ValueError, AttributeError):
            return

        # Buscar todas las facturas de proveedor del período
        moves = self.env['account.move'].search([
            ('move_type', 'in', ['in_invoice', 'in_refund']),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<', date_to),
            ('company_id', '=', self.env.company.id),
        ])

        # Por qué: set de move_ids ya matcheados para no duplicar
        matched_move_ids = set(imported_lines.filtered('move_id').mapped('move_id.id'))

        Line = self.env['guvens.mis.comprobantes.line']
        missing_data = []
        for move in moves:
            if move.id in matched_move_ids:
                continue

            # Extraer PV y número del document_number de Odoo
            doc_num = move.l10n_latam_document_number or ''
            parts = doc_num.split('-')
            pos = parts[0] if parts else ''
            number = parts[1] if len(parts) > 1 else ''

            missing_data.append({
                'import_date': fields.Date.context_today(self),
                'period': self.period,
                'company_id': self.env.company.id,
                'date': move.invoice_date,
                'doc_type': move.l10n_latam_document_type_id.name or '',
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
