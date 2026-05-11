# -*- coding: utf-8 -*-
"""
Wizard de importación de comprobantes emitidos por portal ARCA.

Resuelve el caso en que parte de las facturas (típicamente FCE MiPyME) se emiten
desde el portal de ARCA y no quedan registradas en Odoo, generando diferencia
en el libro IVA Ventas. Toma el CSV de "Mis Comprobantes → Emitidos" y crea
los account.move faltantes con CAE ya cargado, sin volver a llamar a WSFE.

Flujo en tres pasos: subir → previsualizar → confirmar.
"""
import base64
import csv
import io
import re
from collections import Counter
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError


# ---------------------------------------------------------------------------
# Mapeo de códigos ARCA → tipo Odoo
# ---------------------------------------------------------------------------
# Por qué: ARCA identifica el tipo de comprobante por código numérico en el CSV
# de emitidos (a diferencia del de recibidos, que usa texto). Cada código define
# tres cosas en simultáneo: si es factura/ND/NC (move_type), la letra del
# comprobante (A/B/C/M/E) que infiere la condición frente a IVA del receptor,
# y si se trata de una FCE MiPyME (informativo, no cambia la lógica contable).

# Códigos de notas de crédito (refund). El resto se trata como out_invoice.
ARCA_REFUND_CODES = {
    '3', '8', '13', '21', '53',
    '203', '208', '213',  # FCE NC
}

# Códigos de notas de débito (out_invoice con document_type debit_note)
ARCA_DEBIT_NOTE_CODES = {
    '2', '7', '12', '20', '52',
    '202', '207', '212',  # FCE ND
}

# Letra del comprobante por código (A/B/C/M/E)
ARCA_CODE_LETTER = {
    '1': 'A', '2': 'A', '3': 'A', '4': 'A', '5': 'A',
    '6': 'B', '7': 'B', '8': 'B',
    '11': 'C', '12': 'C', '13': 'C',
    '19': 'E', '20': 'E', '21': 'E',
    '51': 'M', '52': 'M', '53': 'M',
    '81': 'A', '82': 'B', '83': 'C',
    '201': 'A', '202': 'A', '203': 'A',
    '206': 'B', '207': 'B', '208': 'B',
    '211': 'C', '212': 'C', '213': 'C',
}

# Etiqueta legible para mostrar en la grilla
ARCA_CODE_LABEL = {
    '1': 'Factura A', '2': 'ND A', '3': 'NC A',
    '4': 'Recibo A', '5': 'NV A',
    '6': 'Factura B', '7': 'ND B', '8': 'NC B',
    '11': 'Factura C', '12': 'ND C', '13': 'NC C',
    '19': 'Factura E', '20': 'ND E', '21': 'NC E',
    '51': 'Factura M', '52': 'ND M', '53': 'NC M',
    '201': 'FCE A', '202': 'FCE ND A', '203': 'FCE NC A',
    '206': 'FCE B', '207': 'FCE ND B', '208': 'FCE NC B',
    '211': 'FCE C', '212': 'FCE ND C', '213': 'FCE NC C',
}


class ImportComprobantesEmitidos(models.TransientModel):
    _name = 'guvens.import.comprobantes.emitidos'
    _description = 'Importar comprobantes emitidos (Ventas) de ARCA'

    # -------------------------------------------------------------------------
    # Estado del wizard
    # -------------------------------------------------------------------------
    state = fields.Selection([
        ('upload', 'Subir archivo'),
        ('preview', 'Previsualizar'),
        ('done', 'Importación completada'),
    ], default='upload', required=True)

    # -------------------------------------------------------------------------
    # Paso 1 — Subida del CSV
    # -------------------------------------------------------------------------
    csv_file = fields.Binary(string='Archivo CSV')
    csv_filename = fields.Char(string='Nombre archivo')
    period = fields.Char(string='Período (MM/AAAA)', readonly=True)

    # -------------------------------------------------------------------------
    # Configuración para creación de comprobantes
    # -------------------------------------------------------------------------
    journal_id = fields.Many2one(
        'account.journal',
        string='Diario de ventas',
        domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]",
        help='Diario al que se imputan los comprobantes creados. Por defecto, '
             'el primer diario de ventas de la compañía.',
    )
    sale_account_id = fields.Many2one(
        'account.account',
        string='Cuenta de ventas por defecto',
        domain="[('account_type', '=', 'income'), ('company_id', '=', company_id)]",
        help='Cuenta a la que se imputan las líneas. Editable por fila desde la grilla.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        default=lambda self: self.env.company,
        required=True,
    )

    # -------------------------------------------------------------------------
    # Paso 2 — Previsualización
    # -------------------------------------------------------------------------
    line_ids = fields.One2many(
        'guvens.import.emitidos.line',
        'wizard_id',
        string='Comprobantes',
    )

    # Contadores para el banner de la previsualización
    count_total = fields.Integer(string='Total filas', compute='_compute_counts')
    count_exists = fields.Integer(string='Ya en Odoo', compute='_compute_counts')
    count_to_create = fields.Integer(string='A crear', compute='_compute_counts')
    count_review = fields.Integer(string='A revisar', compute='_compute_counts')
    count_invalid = fields.Integer(string='No procesables', compute='_compute_counts')

    @api.depends('line_ids', 'line_ids.state')
    def _compute_counts(self):
        for wiz in self:
            wiz.count_total = len(wiz.line_ids)
            wiz.count_exists = len(wiz.line_ids.filtered(lambda l: l.state == 'exists'))
            wiz.count_to_create = len(wiz.line_ids.filtered(lambda l: l.state == 'to_create'))
            wiz.count_review = len(wiz.line_ids.filtered(lambda l: l.state == 'needs_review'))
            wiz.count_invalid = len(wiz.line_ids.filtered(lambda l: l.state == 'invalid'))

    # -------------------------------------------------------------------------
    # Defaults
    # -------------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env.company
        # Diario de ventas por defecto: primero de la compañía
        sale_journal = self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', company.id),
        ], limit=1)
        if sale_journal:
            res['journal_id'] = sale_journal.id
            if sale_journal.default_account_id:
                res['sale_account_id'] = sale_journal.default_account_id.id
        return res

    # -------------------------------------------------------------------------
    # Utilidades de parseo (formato Mis Comprobantes Emitidos)
    # -------------------------------------------------------------------------
    def _parse_amount(self, value):
        """Convierte importe del CSV emitidos a float.
        Por qué: el CSV de emitidos usa solo coma como decimal, sin separador
        de miles. Vacío o no numérico → 0.0.
        """
        if not value or not str(value).strip():
            return 0.0
        clean = str(value).strip().replace(',', '.')
        try:
            return float(clean)
        except (ValueError, TypeError):
            return 0.0

    def _parse_date(self, value):
        """Convierte fecha del CSV emitidos (yyyy-mm-dd) a date."""
        if not value or not str(value).strip():
            return False
        try:
            return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
        except ValueError:
            return False

    def _clean_cuit(self, cuit):
        """Extrae solo dígitos. CUIT argentino: 11 dígitos.
        Por qué: Odoo puede almacenar 'AR20123456789' con prefijo país y romper
        el match. Sin limpiar quedaría con dígitos extra.
        """
        digits = re.sub(r'[^0-9]', '', cuit or '')
        if len(digits) > 11:
            digits = digits[-11:]
        return digits

    def _build_document_number(self, pos, number):
        """Construye doc_number en formato Odoo: 00001-00000123."""
        pos_clean = re.sub(r'[^0-9]', '', str(pos or ''))
        num_clean = re.sub(r'[^0-9]', '', str(number or ''))
        if not pos_clean or not num_clean:
            return False
        return '%s-%s' % (pos_clean.zfill(5), num_clean.zfill(8))

    # -------------------------------------------------------------------------
    # Paso 1 — action: cargar CSV y armar preview
    # -------------------------------------------------------------------------
    def action_load_csv(self):
        """Lee el CSV subido, arma las líneas de previsualización y pasa a paso 2."""
        self.ensure_one()
        if not self.csv_file:
            raise UserError(_('Subí primero el archivo CSV de "Mis Comprobantes → Emitidos" de ARCA.'))

        rows = self._parse_csv()
        if not rows:
            raise UserError(_('El archivo no contiene filas procesables. Verificá que sea el CSV correcto.'))

        # Detectar período más representativo (mes/año más frecuente)
        self.period = self._detect_period(rows)

        # Limpiar líneas previas si las hubiese (re-upload)
        self.line_ids.unlink()

        # Indexar facturas existentes del período para matching
        date_from, date_to = self._period_bounds(self.period)
        moves_index = self._build_moves_index(date_from, date_to)
        partners_by_vat = self._build_partners_index()

        line_vals_list = []
        for row in rows:
            line_vals = self._build_line_vals(row, moves_index, partners_by_vat)
            if line_vals:
                line_vals_list.append((0, 0, line_vals))

        self.line_ids = line_vals_list
        self.state = 'preview'
        return self._reload_action()

    def _parse_csv(self):
        """Decodifica el archivo y retorna lista de dicts (uno por fila)."""
        try:
            raw = base64.b64decode(self.csv_file)
            raw = raw.replace(b'\x00', b'')
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('latin-1')
        except Exception as e:
            raise UserError(_('No se pudo leer el archivo: %s') % str(e))

        text = '\n'.join(text.splitlines())
        reader = csv.reader(io.StringIO(text), delimiter=';', quotechar='"')

        try:
            header = next(reader)
        except StopIteration:
            return []

        # Por qué: validar formato esperado mirando el primer encabezado
        first = (header[0] or '').strip().lower()
        if 'fecha de emisi' not in first:
            raise UserError(_(
                'El archivo no parece ser el CSV de "Mis Comprobantes → Emitidos" de ARCA. '
                'Esperaba que la primera columna fuera "Fecha de Emisión" pero encontré "%s".'
            ) % header[0])

        rows = []
        for raw_row in reader:
            if not raw_row or not any(c.strip() for c in raw_row):
                continue
            # Por qué: ARCA puede agregar columnas en el futuro. Tomamos por
            # índice fijo solo las que conocemos y descartamos extras.
            row = self._row_to_dict(raw_row)
            if row:
                rows.append(row)
        return rows

    def _row_to_dict(self, raw):
        """Mapea fila por posición a dict con campos lógicos.
        Por qué: el header del CSV está fijado (28 columnas en formato 2026).
        Mapeamos por índice para ser robustos a renombres menores.
        """
        def get(idx):
            return raw[idx].strip() if idx < len(raw) else ''

        return {
            'date_str': get(0),
            'afip_code': get(1),
            'pos': get(2),
            'num_from': get(3),
            'num_to': get(4),
            'cae': get(5),
            'receiver_doc_type': get(6),
            'receiver_vat': get(7),
            'receiver_name': get(8),
            'exchange_rate': get(9),
            'currency': get(10),
            'neto_0': get(11),
            'iva_25': get(12),
            'neto_25': get(13),
            'iva_5': get(14),
            'neto_5': get(15),
            'iva_105': get(16),
            'neto_105': get(17),
            'iva_21': get(18),
            'neto_21': get(19),
            'iva_27': get(20),
            'neto_27': get(21),
            'neto_total': get(22),
            'no_gravado': get(23),
            'exento': get(24),
            'otros_tributos': get(25),
            'iva_total': get(26),
            'total': get(27),
        }

    def _detect_period(self, rows):
        """Devuelve 'MM/AAAA' del mes/año más frecuente en las fechas del CSV."""
        counter = Counter()
        for row in rows:
            d = self._parse_date(row.get('date_str'))
            if d:
                counter[(d.month, d.year)] += 1
        if not counter:
            return False
        (m, y), _ = counter.most_common(1)[0]
        return '%s/%s' % (str(m).zfill(2), y)

    def _period_bounds(self, period):
        """Convierte 'MM/AAAA' a (date_from, date_to) [inclusive, exclusive]."""
        if not period:
            return None, None
        try:
            month, year = period.split('/')
            date_from = datetime.strptime('01/%s/%s' % (month, year), '%d/%m/%Y').date()
            if int(month) == 12:
                date_to = datetime.strptime('01/01/%s' % (int(year) + 1), '%d/%m/%Y').date()
            else:
                date_to = datetime.strptime(
                    '01/%s/%s' % (str(int(month) + 1).zfill(2), year),
                    '%d/%m/%Y').date()
            return date_from, date_to
        except (ValueError, AttributeError):
            return None, None

    def _build_moves_index(self, date_from, date_to):
        """Indexa account.move de venta del período por (afip_code, CUIT, doc_number).
        Por qué: con una sola búsqueda y un dict en memoria, el match por fila
        es O(1) en lugar de hacer un search por cada línea del CSV.
        El código ARCA (l10n_latam_document_type_id.code) es necesario en la clave
        porque cada letra de comprobante (A/B/C) y cada subtipo (FA/ND/NC/FCE)
        tiene numeración independiente y arranca en 00000001 — sin la letra,
        Fact A 0001 cruzaría con Fact B 0001 generando falsos "ya existe".
        """
        domain = [
            ('move_type', 'in', ['out_invoice', 'out_refund']),
            ('state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
        ]
        if date_from and date_to:
            domain += [('invoice_date', '>=', date_from), ('invoice_date', '<', date_to)]

        moves = self.env['account.move'].search(domain)
        index = {}
        for move in moves:
            doc_code = move.l10n_latam_document_type_id.code or ''
            if not doc_code:
                continue
            vat = self._clean_cuit(move.commercial_partner_id.vat or '')
            doc_num = self._normalize_doc_number(move.l10n_latam_document_number or '')
            if doc_num:
                index[(doc_code, vat, doc_num)] = move
                # También indexar sin CUIT, por si la factura quedó cargada sin partner válido
                index.setdefault((doc_code, '', doc_num), move)
        return index

    def _normalize_doc_number(self, doc_num):
        """Normaliza a formato 00001-00000123 (5+8 dígitos)."""
        if not doc_num:
            return False
        parts = str(doc_num).strip().split('-')
        if len(parts) != 2:
            return doc_num
        pos = re.sub(r'[^0-9]', '', parts[0])
        num = re.sub(r'[^0-9]', '', parts[1])
        if not pos or not num:
            return doc_num
        return '%s-%s' % (pos.zfill(5), num.zfill(8))

    def _build_partners_index(self):
        """Indexa res.partner por CUIT limpio. Solo los activos de la compañía."""
        partners = self.env['res.partner'].search([
            ('vat', '!=', False),
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
        ])
        index = {}
        for p in partners:
            vat = self._clean_cuit(p.vat or '')
            if vat and vat not in index:
                index[vat] = p
        return index

    # -------------------------------------------------------------------------
    # Construcción de cada línea de previsualización
    # -------------------------------------------------------------------------
    def _build_line_vals(self, row, moves_index, partners_by_vat):
        """A partir de una fila del CSV, arma los vals de la línea de preview
        ya clasificada por estado (exists/to_create/needs_review/invalid).
        """
        date = self._parse_date(row.get('date_str'))
        afip_code = (row.get('afip_code') or '').strip()
        pos = row.get('pos') or ''
        # ARCA puede agrupar comprobantes en lote (Número Desde / Número Hasta).
        # Si son distintos, marcamos como inválido — no estamos en el caso típico
        # de FCE MiPyME y conviene que el usuario lo cargue manualmente.
        num_from = row.get('num_from') or ''
        num_to = row.get('num_to') or ''
        cae = (row.get('cae') or '').strip()

        doc_number = self._build_document_number(pos, num_from)
        receiver_vat = self._clean_cuit(row.get('receiver_vat') or '')

        # Determinar move_type por código
        if afip_code in ARCA_REFUND_CODES:
            move_type = 'out_refund'
        else:
            move_type = 'out_invoice'
        is_debit_note = afip_code in ARCA_DEBIT_NOTE_CODES

        vals = {
            'date': date,
            'afip_code': afip_code,
            'doc_type_label': ARCA_CODE_LABEL.get(afip_code, 'Cód. %s' % afip_code),
            'pos_number': pos,
            'doc_number': num_from,
            'cae': cae,
            'receiver_doc_type': row.get('receiver_doc_type'),
            'receiver_vat': row.get('receiver_vat'),
            'receiver_name': row.get('receiver_name'),
            'currency_code': row.get('currency'),
            'exchange_rate': self._parse_amount(row.get('exchange_rate')),
            'amount_neto_0': self._parse_amount(row.get('neto_0')),
            'amount_iva_25': self._parse_amount(row.get('iva_25')),
            'amount_neto_25': self._parse_amount(row.get('neto_25')),
            'amount_iva_5': self._parse_amount(row.get('iva_5')),
            'amount_neto_5': self._parse_amount(row.get('neto_5')),
            'amount_iva_105': self._parse_amount(row.get('iva_105')),
            'amount_neto_105': self._parse_amount(row.get('neto_105')),
            'amount_iva_21': self._parse_amount(row.get('iva_21')),
            'amount_neto_21': self._parse_amount(row.get('neto_21')),
            'amount_iva_27': self._parse_amount(row.get('iva_27')),
            'amount_neto_27': self._parse_amount(row.get('neto_27')),
            'amount_neto_total': self._parse_amount(row.get('neto_total')),
            'amount_no_gravado': self._parse_amount(row.get('no_gravado')),
            'amount_exento': self._parse_amount(row.get('exento')),
            'amount_otros_tributos': self._parse_amount(row.get('otros_tributos')),
            'amount_iva_total': self._parse_amount(row.get('iva_total')),
            'amount_total': self._parse_amount(row.get('total')),
            'move_type': move_type,
            'is_debit_note': is_debit_note,
            'sale_account_id': self.sale_account_id.id if self.sale_account_id else False,
        }

        # Clasificación de estado y notas de revisión
        if not date or not doc_number or not cae:
            vals['state'] = 'invalid'
            vals['review_note'] = _('Faltan fecha, número o CAE en el CSV.')
            return vals

        if num_from != num_to and num_to:
            vals['state'] = 'invalid'
            vals['review_note'] = _('Comprobante en lote (%s-%s). Cargar manualmente.') % (num_from, num_to)
            return vals

        # ¿Ya existe en Odoo? (clave con afip_code para no cruzar Fact A 0001
        # con Fact B 0001 — cada letra tiene numeración independiente)
        existing = moves_index.get((afip_code, receiver_vat, doc_number)) \
            or moves_index.get((afip_code, '', doc_number))
        if existing:
            vals['state'] = 'exists'
            vals['existing_move_id'] = existing.id
            vals['partner_id'] = existing.partner_id.id
            return vals

        # ¿Tenemos al cliente?
        partner = partners_by_vat.get(receiver_vat) if receiver_vat else None
        if partner:
            vals['partner_id'] = partner.id
            vals['state'] = 'to_create'
        else:
            # Cliente nuevo: lo creamos al confirmar. Si la letra no permite inferir
            # condición frente a IVA con certeza, marcamos para revisión.
            letter = ARCA_CODE_LETTER.get(afip_code)
            vals['partner_will_be_created'] = True
            if letter == 'A':
                # Letra A solo se emite a Responsable Inscripto → inferencia segura
                vals['state'] = 'to_create'
                vals['review_note'] = _('Cliente nuevo: se creará como Responsable Inscripto.')
            elif letter == 'M':
                vals['state'] = 'to_create'
                vals['review_note'] = _('Cliente nuevo: se creará como Responsable Inscripto (letra M).')
            elif letter == 'E':
                vals['state'] = 'to_create'
                vals['review_note'] = _('Cliente nuevo: se creará como Cliente del Exterior.')
            else:
                vals['state'] = 'needs_review'
                vals['review_note'] = _(
                    'Cliente nuevo y la condición frente a IVA no es unívoca '
                    '(letra %s). Confirmar antes de crear.'
                ) % (letter or '?')

        return vals

    # -------------------------------------------------------------------------
    # Paso 2 → 1 (volver a subir otro CSV)
    # -------------------------------------------------------------------------
    def action_back_to_upload(self):
        self.ensure_one()
        self.line_ids.unlink()
        self.state = 'upload'
        self.csv_file = False
        self.csv_filename = False
        self.period = False
        return self._reload_action()

    # -------------------------------------------------------------------------
    # Paso 3 — confirmar y crear los comprobantes
    # -------------------------------------------------------------------------
    def action_confirm_import(self):
        """Crea los comprobantes en estado posted, con CAE y vencimiento del CAE.
        No llama a WSFE: los CAE vienen del CSV de ARCA, ya están autorizados.
        """
        self.ensure_one()
        to_create = self.line_ids.filtered(lambda l: l.state in ('to_create',))
        if not to_create:
            raise UserError(_('No hay comprobantes a crear. Si querés revisar los marcados '
                              'en amarillo, ajustalos a "Se va a crear" desde la grilla.'))

        if not self.journal_id:
            raise UserError(_('Configurá un diario de ventas antes de confirmar.'))
        if not self.sale_account_id:
            raise UserError(_('Configurá una cuenta de ventas por defecto antes de confirmar.'))

        for line in to_create:
            if line.partner_will_be_created and not line.partner_id:
                line.partner_id = self._create_partner(line)
            move = self._create_move(line)
            line.created_move_id = move.id
            line.state = 'exists'
            line.existing_move_id = move.id

        self.state = 'done'
        return self._reload_action()

    def _create_partner(self, line):
        """Crea res.partner inferido del CSV. La condición frente a IVA se
        deduce de la letra del comprobante.
        """
        letter = ARCA_CODE_LETTER.get(line.afip_code)
        responsibility = self._get_responsibility_for_letter(letter)
        identification_type = self._get_identification_type(line.receiver_doc_type)

        vals = {
            'name': line.receiver_name or _('Cliente %s') % (line.receiver_vat or 'sin CUIT'),
            'company_type': 'company',
            'vat': line.receiver_vat or False,
        }
        if responsibility:
            vals['l10n_ar_afip_responsibility_type_id'] = responsibility.id
        if identification_type:
            vals['l10n_latam_identification_type_id'] = identification_type.id

        return self.env['res.partner'].create(vals)

    def _get_responsibility_for_letter(self, letter):
        """Devuelve l10n_ar.afip.responsibility.type según la letra A/B/C/M/E."""
        Resp = self.env['l10n_ar.afip.responsibility.type']
        # Mapeo por código; los códigos los define el módulo l10n_ar
        code_map = {
            'A': '1',   # IVA Responsable Inscripto
            'M': '1',   # también RI (M es para aspirantes a RI)
            'E': '9',   # Cliente del Exterior
        }
        code = code_map.get(letter)
        if code:
            return Resp.search([('code', '=', code)], limit=1)
        return Resp

    def _get_identification_type(self, receiver_doc_type):
        """ARCA usa códigos: 80=CUIT, 86=CUIL, 96=DNI. Mapeamos a l10n_latam_identification_type."""
        IdType = self.env['l10n_latam.identification.type']
        code = (receiver_doc_type or '').strip()
        # Por qué: en l10n_ar los xmlid son l10n_ar.it_cuit / it_cuil / it_dni
        if code == '80':
            return self.env.ref('l10n_ar.it_cuit', raise_if_not_found=False)
        if code == '86':
            return self.env.ref('l10n_ar.it_cuil', raise_if_not_found=False)
        if code == '96':
            return self.env.ref('l10n_ar.it_dni', raise_if_not_found=False)
        return IdType

    def _create_move(self, line):
        """Crea el account.move publicado, con CAE y líneas por alícuota."""
        Move = self.env['account.move']

        # Document type por (move_type, letra, FCE/normal/ND)
        document_type = self._resolve_document_type(line)
        if not document_type:
            raise UserError(_(
                'No encuentro un tipo de documento Odoo para el código ARCA %s. '
                'Verificá que la localización argentina (l10n_ar) esté instalada.'
            ) % line.afip_code)

        # Construcción de líneas por alícuota
        invoice_lines = self._build_invoice_lines(line)
        if not invoice_lines:
            raise UserError(_(
                'La factura %s no tiene importes parseables en el CSV. '
                'Cargala manualmente.'
            ) % line.display_comprobante)

        vals = {
            'move_type': line.move_type,
            'partner_id': line.partner_id.id,
            'invoice_date': line.date,
            'date': line.date,
            'journal_id': self.journal_id.id,
            'company_id': self.company_id.id,
            'l10n_latam_document_type_id': document_type.id,
            'l10n_latam_document_number': self._normalize_doc_number(
                '%s-%s' % (line.pos_number, line.doc_number)),
            'invoice_line_ids': invoice_lines,
        }

        move = Move.with_context(check_move_validity=False).create(vals)

        # CAE: el campo correcto en l10n_ar 17 es l10n_ar_afip_auth_code y due
        cae_due = self._guess_cae_due(line.date)
        move_writes = {}
        if hasattr(move, 'l10n_ar_afip_auth_code'):
            move_writes['l10n_ar_afip_auth_code'] = line.cae
        if hasattr(move, 'l10n_ar_afip_auth_code_due') and cae_due:
            move_writes['l10n_ar_afip_auth_code_due'] = cae_due
        if move_writes:
            move.write(move_writes)

        # Publicar
        move.action_post()
        return move

    def _resolve_document_type(self, line):
        """Busca l10n_latam.document.type por código ARCA."""
        DocType = self.env['l10n_latam.document.type']
        return DocType.search([
            ('code', '=', line.afip_code),
            ('country_id.code', '=', 'AR'),
        ], limit=1)

    def _guess_cae_due(self, invoice_date):
        """Estimación del vencimiento del CAE.
        Por qué: el CSV de Mis Comprobantes Emitidos no incluye la fecha de
        vencimiento del CAE. ARCA lo otorga típicamente a 10 días desde la
        emisión. Esta es solo una estimación informativa para el campo;
        si el cliente necesita el dato exacto, lo edita manualmente.
        """
        if not invoice_date:
            return False
        from datetime import timedelta
        return invoice_date + timedelta(days=10)

    # Códigos AFIP de alícuota IVA (account.tax.group.l10n_ar_vat_afip_code):
    # 0=No Corresponde, 1=No Gravado, 2=Exento, 3=IVA 0%, 4=IVA 10,5%,
    # 5=IVA 21%, 6=IVA 27%, 8=IVA 5%, 9=IVA 2,5%.
    AFIP_VAT_CODE_NO_CORRESPONDE = '0'
    AFIP_VAT_CODE_NO_GRAVADO = '1'
    AFIP_VAT_CODE_EXENTO = '2'

    def _find_vat_tax(self, afip_vat_code):
        """Devuelve el account.tax de ventas cuyo tax_group tiene ese código AFIP.

        Por qué: en l10n_ar el campo `l10n_ar_vat_afip_code` vive en
        `account.tax.group`, no en el tax directo. Es la única forma de
        distinguir IVA 0% / No Gravado / Exento / No Corresponde, que tienen
        todos `amount=0` — buscar por `amount` levanta cualquiera de ellos.
        """
        return self.env['account.tax'].search([
            ('type_tax_use', '=', 'sale'),
            ('tax_group_id.l10n_ar_vat_afip_code', '=', str(afip_vat_code)),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

    def _make_line_vals(self, name, neto, account, tax, afip_vat_code):
        """Arma vals de una línea de factura validando que la tax exista.
        Argentina exige exactamente un impuesto del grupo VAT por línea; sin él
        `action_post` rompe con 'There should be a single tax from the VAT
        tax group'. Falla temprano y claro si la tax no está configurada.
        """
        if not tax:
            raise UserError(_(
                'No encuentro el impuesto IVA con código AFIP "%s" para ventas '
                'en la compañía %s. Verificá que la localización argentina '
                '(l10n_ar) esté correctamente instalada y que el tax_group '
                'del impuesto tenga seteado `l10n_ar_vat_afip_code`.'
            ) % (afip_vat_code, self.company_id.display_name))
        return (0, 0, {
            'name': name,
            'quantity': 1.0,
            'price_unit': neto,
            'account_id': account.id,
            'tax_ids': [(6, 0, [tax.id])],
        })

    def _build_invoice_lines(self, line):
        """Arma una línea por alícuota presente en el CSV.

        Lógica por responsabilidad del receptor (inferida de la letra del
        comprobante):

        - Letra E (cliente del exterior, responsabilidad código 9): todas las
          líneas llevan "IVA No Corresponde" (código AFIP 0), independientemente
          de la columna del CSV donde venga el importe. Las facturas E no
          discriminan IVA porque son operaciones de exportación.
        - Letras A/B/C/M: una línea por cada alícuota presente en el CSV, más
          una línea adicional por "No Gravado" y "Exento" cuando esas columnas
          traen importe. Cada línea con su tax del grupo VAT correspondiente.
        """
        account = line.sale_account_id or self.sale_account_id
        if not account:
            return []

        letter = ARCA_CODE_LETTER.get(line.afip_code)
        lines = []

        if letter == 'E':
            tax = self._find_vat_tax(self.AFIP_VAT_CODE_NO_CORRESPONDE)
            neto = (line.amount_neto_total
                    or line.amount_no_gravado
                    or line.amount_exento
                    or line.amount_total)
            if neto:
                lines.append(self._make_line_vals(
                    _('Operación de exportación'), neto, account, tax,
                    self.AFIP_VAT_CODE_NO_CORRESPONDE))
            return lines

        # Tramos por alícuota: (neto en CSV, código AFIP, label visible)
        tramos = [
            (line.amount_neto_0, '3', 'IVA 0%'),
            (line.amount_neto_25, '9', 'IVA 2,5%'),
            (line.amount_neto_5, '8', 'IVA 5%'),
            (line.amount_neto_105, '4', 'IVA 10,5%'),
            (line.amount_neto_21, '5', 'IVA 21%'),
            (line.amount_neto_27, '6', 'IVA 27%'),
        ]
        for neto, afip_vat_code, label in tramos:
            if not neto:
                continue
            tax = self._find_vat_tax(afip_vat_code)
            lines.append(self._make_line_vals(
                label, neto, account, tax, afip_vat_code))

        if line.amount_no_gravado:
            tax = self._find_vat_tax(self.AFIP_VAT_CODE_NO_GRAVADO)
            lines.append(self._make_line_vals(
                _('No gravado'), line.amount_no_gravado, account, tax,
                self.AFIP_VAT_CODE_NO_GRAVADO))

        if line.amount_exento:
            tax = self._find_vat_tax(self.AFIP_VAT_CODE_EXENTO)
            lines.append(self._make_line_vals(
                _('Operaciones exentas'), line.amount_exento, account, tax,
                self.AFIP_VAT_CODE_EXENTO))

        return lines

    # -------------------------------------------------------------------------
    # Patrón global del estudio: importación desde ir.attachment
    # -------------------------------------------------------------------------
    def action_import_from_attachment(self, attachment_id):
        """Permite invocar el flujo desde un ir.attachment subido vía multipart.
        Por qué: la regla del estudio establece que toda importación de archivos
        a Odoo debe poder ejecutarse desde un attachment ya cargado, evitando
        XML-RPC con base64 inflado. Este método ejecuta upload + load + confirm
        en un solo paso para integraciones server-to-server.
        """
        attachment = self.env['ir.attachment'].browse(attachment_id)
        if not attachment.exists():
            raise UserError(_('Attachment %s no existe.') % attachment_id)

        wiz = self.create({
            'csv_file': base64.b64encode(attachment.raw),
            'csv_filename': attachment.name,
        })
        wiz.action_load_csv()
        wiz.action_confirm_import()
        return wiz.id

    # -------------------------------------------------------------------------
    # Helpers internos
    # -------------------------------------------------------------------------
    def _reload_action(self):
        """Recarga la vista del wizard manteniendo el mismo registro."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
