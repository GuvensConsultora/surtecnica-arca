# -*- coding: utf-8 -*-

import base64
import io
import json
import logging
import zipfile
from datetime import date
from html import escape as html_escape

import xlsxwriter

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


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

    # Por qué: Mapeo JSON {línea_TXT: datos_comprobante} para identificar
    # rápidamente qué factura corresponde a cada error de validación ARCA
    line_map_json = fields.Text()

    # Por qué: almacena datos DDJJ pre-calculados en JSON para evitar
    # reprocesar moves al generar el Excel
    ddjj_data_json = fields.Text()

    # Procesador de errores ARCA
    errores_arca_tipo = fields.Selection([
        ('compras', 'Compras'),
        ('ventas', 'Ventas'),
    ], string='Archivo con error', default='compras')
    # Por qué: Binary para subir el CSV directo desde disco, sin copiar/pegar
    errores_arca_csv = fields.Binary(
        string='Archivo CSV errores ARCA',
    )
    errores_arca_csv_name = fields.Char(string='Nombre archivo CSV')
    errores_arca_html = fields.Html(
        string='Resultado', readonly=True, sanitize=False,
    )

    # Configuración IVA Simple (F.2051)
    # Por qué: Código de actividad AFIP principal para agrupar ventas en CSV
    actividad_afip = fields.Char(
        'Actividad AFIP', size=6, default='465320',
        help='Código actividad principal AFIP (6 dígitos)',
    )

    # CSV IVA Simple — Apertura de otros conceptos (estado done)
    # Por qué: Desde nov 2025, ARCA F.2051 importa apertura vía CSV
    iva_simple_debito_csv = fields.Binary('CSV Débito Fiscal')
    iva_simple_debito_csv_name = fields.Char(
        default='IVA_SIMPLE_DEBITO_FISCAL.csv')
    iva_simple_rest_debito_csv = fields.Binary('CSV Rest. Débito Fiscal')
    iva_simple_rest_debito_csv_name = fields.Char(
        default='IVA_SIMPLE_REST_DEBITO_FISCAL.csv')
    iva_simple_credito_csv = fields.Binary('CSV Crédito Fiscal')
    iva_simple_credito_csv_name = fields.Char(
        default='IVA_SIMPLE_CREDITO_FISCAL.csv')
    iva_simple_rest_credito_csv = fields.Binary('CSV Rest. Crédito Fiscal')
    iva_simple_rest_credito_csv_name = fields.Char(
        default='IVA_SIMPLE_REST_CREDITO_FISCAL.csv')

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

    # Mapeo responsabilidad AFIP → tipo sujeto CSV IVA Simple
    # Por qué: ARCA IVA Simple agrupa ventas por tipo de comprador
    RESP_TIPO_SUJETO = {
        '1': '1',   # RI → Operaciones con RI
        '6': '1',   # Resp. Acuerdo → igual que RI (recibe Factura A)
        '3': '2',   # Monotributo → Operaciones con Monotributistas
        '4': '3',   # Autónomo → CF/Exentos/NA
        '5': '3',   # Consumidor Final → CF/Exentos/NA
        '9': '3',   # Sujeto Exento → CF/Exentos/NA
        '10': '3',  # Act. Exentas → CF/Exentos/NA
        '13': '3',  # Sin categoría → CF/Exentos/NA
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

        # Extraer datos una sola vez por comprobante (evita triple procesamiento)
        v_extracted = {m.id: self._extract_move_data(m) for m in ventas}
        c_extracted = {m.id: self._extract_move_data(m) for m in compras}

        # Generar líneas TXT + mapeo usando datos pre-extraídos
        # Por qué: _procesar_moves llama internamente a _to_invoice_currency
        # para cada move, convirtiendo ARS → moneda factura en el TXT.
        v_cbte, v_alic, v_cbte_map, v_alic_map = self._procesar_moves(
            ventas, 'ventas', v_extracted)
        c_cbte, c_alic, c_cbte_map, c_alic_map = self._procesar_moves(
            compras, 'compras', c_extracted)

        # Convertir extracted a moneda factura para DDJJ y CSV
        # Por qué: ARCA valida que CSV apertura == suma TXT comprobantes.
        # El TXT usa moneda factura → CSV y DDJJ deben usar lo mismo.
        # Para facturas ARS (rate=1): _to_invoice_currency es no-op.
        v_conv = {m.id: self._to_invoice_currency(v_extracted[m.id], m)
                  for m in ventas}
        c_conv = {m.id: self._to_invoice_currency(c_extracted[m.id], m)
                  for m in compras}

        # DDJJ IVA: en moneda factura para coincidir con portal ARCA
        ddjj_html, v_ddjj, c_ddjj = self._compute_ddjj_iva_html(
            ventas, compras, v_conv, c_conv)

        # CSV IVA Simple: en moneda factura para coincidir con TXT
        csv_data, csv_totals = self._generar_csvs_iva_simple(
            ventas, compras, v_conv, c_conv)

        # Cruce: validar que CSV apertura coincida con comprobantes TXT
        ddjj_html += self._render_cruce_csv_html(csv_totals, v_ddjj, c_ddjj)

        # Codificar archivos
        vals = {
            'state': 'done',
            # Mapeo línea TXT → comprobante para cruzar errores ARCA
            'line_map_json': json.dumps({
                'ventas_cbte': v_cbte_map, 'ventas_alic': v_alic_map,
                'compras_cbte': c_cbte_map, 'compras_alic': c_alic_map,
            }),
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
            'ddjj_iva_html': ddjj_html,
            # Datos DDJJ serializados para Excel (evita re-query y reproceso)
            'ddjj_data_json': json.dumps({
                'v_data': v_ddjj, 'c_data': c_ddjj,
            }),
        }
        # Agregar CSV IVA Simple al write
        vals.update(csv_data)
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
        periodo = self.date_from.strftime('%Y%m')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 4 archivos TXT del Libro IVA Digital
            for fname, fdata in [
                (self.ventas_cbte_name, self.ventas_cbte_file),
                (self.ventas_alic_name, self.ventas_alic_file),
                (self.compras_cbte_name, self.compras_cbte_file),
                (self.compras_alic_name, self.compras_alic_file),
            ]:
                if fdata:
                    zf.writestr(fname, base64.b64decode(fdata))

            # 4 CSV IVA Simple (Apertura otros conceptos F.2051)
            for fname, fdata in [
                (self.iva_simple_debito_csv_name,
                 self.iva_simple_debito_csv),
                (self.iva_simple_rest_debito_csv_name,
                 self.iva_simple_rest_debito_csv),
                (self.iva_simple_credito_csv_name,
                 self.iva_simple_credito_csv),
                (self.iva_simple_rest_credito_csv_name,
                 self.iva_simple_rest_credito_csv),
            ]:
                if fdata:
                    zf.writestr(fname, base64.b64decode(fdata))

            # Reporte DDJJ IVA en PDF
            pdf_data = self._generate_ddjj_pdf()
            if pdf_data:
                zf.writestr(f'DDJJ_IVA_{periodo}.pdf', pdf_data)

            # Reporte DDJJ IVA en Excel
            excel_data = self._generate_ddjj_excel()
            if excel_data:
                zf.writestr(f'DDJJ_IVA_{periodo}.xlsx', excel_data)

        # Guardar ZIP en un attachment para descarga
        zip_data = base64.b64encode(buf.getvalue())
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
    # PROCESAMIENTO DE ERRORES ARCA
    # -------------------------------------------------------------------------

    def action_procesar_errores(self):
        """Cruza el CSV de errores ARCA con el mapeo de líneas del TXT.

        Por qué: ARCA devuelve errores referenciando nro de línea del archivo
        TXT. Sin un índice, el usuario tiene que contar líneas manualmente.
        Este método parsea el CSV, busca el comprobante de cada línea y
        muestra nombre, proveedor, CUIT e importe con link al asiento.
        """
        self.ensure_one()
        if not self.errores_arca_csv:
            raise UserError('Suba el archivo CSV de errores de ARCA.')
        # Por qué: ARCA exporta CSV en latin-1; fallback a utf-8
        raw = base64.b64decode(self.errores_arca_csv)
        try:
            csv_text = raw.decode('latin-1')
        except Exception:
            csv_text = raw.decode('utf-8', errors='replace')

        tipo = self.errores_arca_tipo or 'compras'
        line_map = json.loads(self.line_map_json or '{}')
        cbte_map = line_map.get(f'{tipo}_cbte', {})
        alic_map = line_map.get(f'{tipo}_alic', {})

        # Parsear CSV semicolon-separated de ARCA
        rows = []
        for line in csv_text.strip().split('\n'):
            # Saltar encabezado
            if 'Num.' in line or 'num.' in line or not line.strip():
                continue
            parts = line.split(';')
            if len(parts) < 3:
                continue
            cbte_line = parts[0].strip().strip('"')
            alic_line = parts[1].strip().strip('"')
            error = ';'.join(parts[2:]).strip().strip('"')

            # Buscar datos completos en los mapas de líneas
            rows.append({
                'cbte_line': cbte_line,
                'alic_line': alic_line,
                'error': error,
                'cbte': cbte_map.get(cbte_line, {}),
                'alic': alic_map.get(alic_line, {}),
            })

        self.write({'errores_arca_html': self._render_errores_html(rows)})

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _render_errores_html(self, rows):
        """Renderiza errores ARCA con detalle completo para diagnóstico.

        Por qué: Muestra todos los importes exportados en cada línea del TXT
        junto al error de ARCA, para que el usuario identifique el campo con
        problema sin necesidad de abrir cada factura en Odoo.
        """
        fmt = self._fmt_money
        css = """
        <style>
            .arca-err { font-family: Arial, sans-serif; font-size: 12px; }
            .arca-err h3 { color: #875A7B; }
            .arca-err a { color: #875A7B; text-decoration: none;
                          font-weight: bold; }
            .arca-err a:hover { text-decoration: underline; }
            .err-card { border: 1px solid #e0e0e0;
                        border-left: 4px solid #c0392b;
                        margin: 10px 0; padding: 10px 15px;
                        background: #fafafa; }
            .err-card .err-head { font-size: 12px; margin-bottom: 4px; }
            .err-card .err-head .lines { color: #666; font-size: 11px; }
            .err-card .err-msg { color: #c0392b; font-weight: bold;
                                 padding: 6px 0; border-bottom: 1px solid #eee;
                                 font-size: 12px; }
            .err-card .err-detail { margin-top: 8px; }
            .err-card .err-detail table { width: 100%;
                                          border-collapse: collapse; }
            .err-card .err-detail th { background: #f3eef5; color: #333;
                                       padding: 3px 6px; font-size: 10px;
                                       text-align: right; border: 1px solid #ddd; }
            .err-card .err-detail th:first-child { text-align: left; }
            .err-card .err-detail td { padding: 3px 6px; font-size: 11px;
                                       text-align: right; border: 1px solid #eee; }
            .err-card .err-detail td:first-child { text-align: left; }
            .err-card .err-alic { margin-top: 6px; padding-top: 6px;
                                  border-top: 1px dashed #ccc;
                                  font-size: 11px; }
            .err-card .err-compare { color: #888; font-size: 10px;
                                     margin-top: 4px; }
        </style>
        """
        h = [css, '<div class="arca-err">']
        h.append(
            f'<h3>Errores de validacion ARCA — {len(rows)} encontrados</h3>')

        for r in rows:
            cbte = r['cbte']
            alic = r['alic']
            # Datos del comprobante (preferir cbte, fallback alic)
            name = cbte.get('name') or alic.get('name', '—')
            move_id = cbte.get('id') or alic.get('id', '')
            partner = cbte.get('partner') or alic.get('partner', '—')
            cuit = cbte.get('cuit', '')

            h.append('<div class="err-card">')

            # ---- Encabezado: líneas + comprobante + partner ----
            if move_id:
                name_html = (
                    f'<a href="/web#id={move_id}'
                    f'&model=account.move&view_type=form" '
                    f'target="_blank">{html_escape(name)}</a>')
            else:
                name_html = html_escape(name)

            h.append(
                f'<div class="err-head">'
                f'<span class="lines">Lin. Cbte: {r["cbte_line"]} | '
                f'Lin. Alic: {r["alic_line"]}</span> — '
                f'{name_html} — {html_escape(partner)}'
                f'{" — CUIT: " + html_escape(cuit) if cuit else ""}'
                f'</div>')

            # ---- Mensaje de error ----
            h.append(
                f'<div class="err-msg">{html_escape(r["error"])}</div>')

            # ---- Detalle importes cabecera ----
            if cbte:
                h.append('<div class="err-detail">')
                h.append(
                    '<table><tr>'
                    '<th>Total</th><th>Gravado</th><th>IVA</th>'
                    '<th>No Grav.</th><th>Exento</th>'
                    '<th>Perc.IVA</th><th>Perc.IIBB</th>'
                    '<th>Perc.Nac.</th><th>Imp.Int.</th>'
                    '<th>Otros</th>'
                    '</tr><tr>')
                for key in ('total', 'gravado', 'iva', 'no_gravado',
                            'exento', 'perc_iva', 'perc_iibb',
                            'perc_nacionales', 'imp_internos',
                            'otros_tributos'):
                    val = cbte.get(key)
                    if isinstance(val, (int, float)):
                        h.append(f'<td>{fmt(val)}</td>')
                    else:
                        h.append('<td>—</td>')
                h.append('</tr></table>')

                # Comparar total exportado vs total Odoo
                odoo_total = cbte.get('odoo_total')
                exp_total = cbte.get('total')
                if (odoo_total is not None and exp_total is not None
                        and abs(odoo_total - exp_total) > 0.005):
                    h.append(
                        f'<div class="err-compare">'
                        f'Total Odoo: {fmt(odoo_total)} vs '
                        f'Total exportado: {fmt(exp_total)} '
                        f'(dif: {fmt(odoo_total - exp_total)})</div>')
                h.append('</div>')

            # ---- Detalle alícuota ----
            if alic and alic.get('base') is not None:
                tasa = alic.get('tasa', 0)
                base = alic.get('base', 0)
                impuesto = alic.get('impuesto', 0)
                # Recalcular para comparar
                esperado = round(base * tasa / 100, 2) if tasa else 0
                h.append(
                    f'<div class="err-alic">'
                    f'Alicuota: <strong>{alic.get("alicuota", "—")}'
                    f'</strong> | '
                    f'Base: <strong>{fmt(base)}</strong> | '
                    f'Impuesto: <strong>{fmt(impuesto)}</strong>')
                if tasa and abs(esperado - impuesto) > 0.005:
                    h.append(
                        f' | Esperado (base x {tasa}%): '
                        f'<strong>{fmt(esperado)}</strong>')
                h.append('</div>')

            h.append('</div>')  # close err-card

        h.append('</div>')
        return '\n'.join(h)

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

    def _procesar_moves(self, moves, tipo, extracted_data=None):
        """Procesa moves y genera líneas de cabecera + alícuotas + mapeo.

        Args:
            tipo: 'ventas' o 'compras' (determina el formato de salida).
            extracted_data: dict {move_id: data} pre-calculado. Si es None,
                extrae datos de cada move.
        Returns:
            tuple: (lista_cabecera, lista_alicuotas, mapa_cbte, mapa_alic)
            Los mapas vinculan nro de línea TXT → datos del comprobante,
            para cruzar con errores de validación de ARCA.
        """
        cbte_lines = []
        alic_lines = []
        cbte_map = {}
        alic_map = {}
        errores = []

        for move in moves:
            try:
                data = extracted_data[move.id] if extracted_data else self._extract_move_data(move)
                # TXT: importes en moneda de factura (ARCA multiplica × TC)
                # data queda en ARS para DDJJ/CSV; txt_data en moneda factura
                txt_data = self._to_invoice_currency(data, move)
                partner = move.commercial_partner_id
                # Línea cabecera: nro 1-based
                cbte_num = len(cbte_lines) + 1

                if tipo == 'ventas':
                    cbte_lines.append(self._fmt_ventas_cbte(move, txt_data))
                    fmt_alic = self._fmt_ventas_alic
                else:
                    cbte_lines.append(self._fmt_compras_cbte(move, txt_data))
                    fmt_alic = self._fmt_compras_alic

                # Alícuotas: línea TXT + mapa con detalle para diagnóstico
                for alic in txt_data['iva_alicuotas']:
                    alic_num = len(alic_lines) + 1
                    alic_lines.append(fmt_alic(move, alic))
                    alic_map[str(alic_num)] = {
                        'name': move.name, 'id': move.id,
                        'partner': partner.name or '',
                        'alicuota': self.IVA_CODE_LABEL.get(
                            alic['code'], alic['code']),
                        # Detalle para diagnóstico de errores ARCA
                        'codigo_iva': alic['code'],
                        'base': round(alic['base'], 2),
                        'impuesto': round(alic['amount'], 2),
                        'tasa': self.IVA_CODE_RATE.get(alic['code'], 0),
                    }

                # Cabecera: mapa con todos los importes para diagnóstico
                gravado = sum(a['base'] for a in data['iva_alicuotas'])
                iva_total = sum(a['amount'] for a in data['iva_alicuotas'])
                cbte_map[str(cbte_num)] = {
                    'id': move.id,
                    'name': move.name,
                    'partner': partner.name or '',
                    'cuit': partner.vat or '',
                    'fecha': str(move.invoice_date),
                    'tipo_cbte': (
                        move.l10n_latam_document_type_id.name or ''),
                    'total': round(data['total'], 2),
                    'gravado': round(gravado, 2),
                    'iva': round(iva_total, 2),
                    'no_gravado': round(data['no_gravado'], 2),
                    'exento': round(data['exento'], 2),
                    'perc_iva': round(data['perc_iva'], 2),
                    'perc_iibb': round(data['perc_iibb'], 2),
                    'perc_nacionales': round(data['perc_nacionales'], 2),
                    'perc_mun': round(data['perc_mun'], 2),
                    'perc_no_categ': round(data['perc_no_categ'], 2),
                    'imp_internos': round(data['imp_internos'], 2),
                    'otros_tributos': round(data['otros_tributos'], 2),
                    'n_alic': len(data['iva_alicuotas']),
                    # Total Odoo original para comparar
                    'odoo_total': round(abs(move.amount_total), 2),
                }
            except Exception as e:
                errores.append(f'{move.name}: {str(e)}')

        if errores:
            raise UserError(
                'Errores al procesar comprobantes:\n' + '\n'.join(errores)
            )
        return cbte_lines, alic_lines, cbte_map, alic_map

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

        # Por qué: documentos con letra 'E' son exportación (código operación 'X')
        doc_type = move.l10n_latam_document_type_id
        is_export = getattr(doc_type, 'l10n_ar_letter', '') == 'E'

        result = {
            'total': 0.0,  # Se recalcula en Paso 4 como suma de partes
            'is_export': is_export,
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
            'iva_by_concepto': {},     # {(concepto, code): {base, amount}}
        }

        # Paso 1: Clasificar líneas de producto → no_gravado / exento / base IVA
        # Por qué: Usar abs(line.balance) (moneda empresa ARS) en vez de
        # price_subtotal (moneda factura). Para facturas en moneda extranjera
        # price_subtotal está en USD/EUR, causando importes mixtos.
        # balance siempre está en ARS = moneda de la empresa.
        # Tip: Calcular base IVA aquí (no desde tax_base_amount) asegura
        # consistencia con el CSV que también usa invoice_line_ids.balance.
        # Patrón: Una sola pasada sobre invoice_line_ids alimenta iva_by_code
        # (para TXT/DDJJ) e iva_by_concepto (para CSV crédito/restitución).
        iva_by_code = {}
        iva_by_concepto = {}
        for line in move.invoice_line_ids.filtered(
            lambda l: l.display_type not in ('line_section', 'line_note')
        ):
            line_class = self._classify_line_iva(line)
            if line_class == 'no_gravado':
                result['no_gravado'] += abs(line.balance) * sign
            elif line_class == 'exento':
                result['exento'] += abs(line.balance) * sign
            else:
                # Gravado: acumular base por código de alícuota IVA
                for tax in line.tax_ids:
                    code = self._get_vat_afip_code(tax)
                    if code and code in self.IVA_GRAVADO_CODES:
                        bal = abs(line.balance) * sign
                        if code not in iva_by_code:
                            iva_by_code[code] = {
                                'code': code, 'base': 0.0, 'amount': 0.0}
                        iva_by_code[code]['base'] += bal

                        # Concepto: bienes si producto físico, servicios si no
                        # Por qué: CSV crédito fiscal agrupa por concepto+alícuota
                        product = line.product_id
                        concepto = '1' if (
                            product and product.type in ('consu', 'product')
                        ) else '3'
                        ckey = (concepto, code)
                        if ckey not in iva_by_concepto:
                            iva_by_concepto[ckey] = {
                                'base': 0.0, 'amount': 0.0}
                        iva_by_concepto[ckey]['base'] += bal
                        break  # Una línea tiene una sola alícuota IVA

        # Paso 2: IVA amount + impuestos no-IVA desde tax lines
        # Por qué: El monto de IVA (amount) se toma de la tax line (balance),
        # y los impuestos no-IVA (percepciones, IIBB, etc.) se clasifican aquí.
        for line in move.line_ids.filtered(lambda l: l.tax_line_id):
            tax = line.tax_line_id
            vat_code = self._get_vat_afip_code(tax)

            if vat_code and vat_code in self.IVA_GRAVADO_CODES:
                # IVA gravado → sumar amount (el monto del impuesto en ARS)
                if vat_code not in iva_by_code:
                    iva_by_code[vat_code] = {
                        'code': vat_code, 'base': 0.0, 'amount': 0.0}
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
        # Patrón: Misma fórmula para iva_by_code e iva_by_concepto asegura
        # que CSV crédito y TXT/DDJJ tengan idénticos importes de IVA.
        for code, alic in iva_by_code.items():
            rate = self.IVA_CODE_RATE.get(code)
            if rate is not None and rate > 0:
                alic['amount'] = round(alic['base'] * rate / 100, 2)

        for ckey, cdata in iva_by_concepto.items():
            rate = self.IVA_CODE_RATE.get(ckey[1])
            if rate is not None and rate > 0:
                cdata['amount'] = round(cdata['base'] * rate / 100, 2)

        result['iva_alicuotas'] = list(iva_by_code.values())
        result['iva_by_concepto'] = iva_by_concepto

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
                # Por qué: código AFIP 13 = Percepciones IVA a No Categorizado
                '13': 'perc_no_categ',
            }
            return mapping.get(tribute_code, 'otros_tributos')

        # Fallback: clasificar por nombre
        name = (tax_group.name or '').lower()
        if 'percep' in name:
            # Por qué: detectar percepciones a no categorizados por nombre
            if 'no categ' in name or 'no inscri' in name:
                return 'perc_no_categ'
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

    def _to_invoice_currency(self, data, move):
        """Convierte importes extraídos (ARS) a moneda de factura para TXT.

        Por qué: El Libro IVA Digital espera importes en moneda de factura.
        ARCA multiplica importe × tipo_cambio para obtener ARS.
        Si escribimos ARS y el TC es 1650, ARCA computa ARS × 1650 → error.
        Para facturas en ARS (rate=1): no-op, retorna data sin modificar.
        """
        if move.currency_id == move.company_currency_id:
            return data
        _, rate = self._get_currency_info(move)
        if rate <= 1.001:
            return data

        # Copiar para no modificar el original (usado en DDJJ y CSV en ARS)
        conv = dict(data)
        for field in ('total', 'no_gravado', 'exento', 'perc_no_categ',
                      'perc_iva', 'perc_nacionales', 'perc_iibb',
                      'perc_mun', 'imp_internos', 'otros_tributos'):
            conv[field] = round(data[field] / rate, 2)

        # Alícuotas: convertir base y recalcular IVA para consistencia ARCA
        conv['iva_alicuotas'] = []
        for a in data['iva_alicuotas']:
            base_conv = round(a['base'] / rate, 2)
            iva_rate = self.IVA_CODE_RATE.get(a['code'], 0)
            # Recalcular IVA = base × tasa para que ARCA valide OK
            amount_conv = round(base_conv * iva_rate / 100, 2) if iva_rate > 0 \
                else round(a['amount'] / rate, 2)
            conv['iva_alicuotas'].append({
                'code': a['code'],
                'base': base_conv,
                'amount': amount_conv,
            })

        # iva_by_concepto: convertir base y recalcular IVA (igual que alícuotas)
        # Por qué: CSV crédito/restitución agrupa por concepto+alícuota.
        # Debe coincidir con lo que ARCA suma del TXT (moneda factura).
        conv['iva_by_concepto'] = {}
        for ckey, cdata in data.get('iva_by_concepto', {}).items():
            base_conv = round(cdata['base'] / rate, 2)
            iva_rate = self.IVA_CODE_RATE.get(ckey[1], 0)
            amount_conv = round(base_conv * iva_rate / 100, 2) if iva_rate > 0 \
                else round(cdata['amount'] / rate, 2)
            conv['iva_by_concepto'][ckey] = {
                'base': base_conv, 'amount': amount_conv,
            }

        # Recalcular total desde partes (evita drift de redondeo)
        gravado = sum(a['base'] for a in conv['iva_alicuotas'])
        iva = sum(a['amount'] for a in conv['iva_alicuotas'])
        conv['total'] = round(
            gravado + iva
            + conv['no_gravado'] + conv['exento']
            + conv['perc_no_categ'] + conv['perc_iva']
            + conv['perc_nacionales'] + conv['perc_iibb']
            + conv['perc_mun'] + conv['imp_internos']
            + conv['otros_tributos'],
        2)
        return conv

    def _get_operation_code(self, data):
        """Determina el código de operación AFIP.

        ' ' = gravado, 'E' = exento, 'N' = no gravado, 'X' = exportación.
        """
        # Por qué: exportaciones tienen código propio independiente del IVA
        if data.get('is_export'):
            return 'X'
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

    def _compute_ddjj_iva_html(self, ventas, compras,
                                v_extracted=None, c_extracted=None):
        """Genera HTML y datos DDJJ IVA para el portal ARCA.

        Returns:
            tuple: (html, v_data, c_data) — HTML renderizado y datos
            agregados para reutilizar en Excel sin reprocesar.
        """
        v_data = self._agrupar_ddjj(ventas, v_extracted)
        c_data = self._agrupar_ddjj(compras, c_extracted)
        return self._render_ddjj_html(v_data, c_data), v_data, c_data

    def _agrupar_ddjj(self, moves, extracted_data=None):
        """Agrupa comprobantes por tipo de documento y acumula importes.

        Args:
            extracted_data: dict {move_id: data} pre-calculado. Si es None,
                extrae datos de cada move.
        Por qué: El portal ARCA separa la "Apertura de otros conceptos" en
        secciones distintas para facturas, ND y NC (restitución).
        Se calculan totales separados para cruce contra CSV apertura.
        """
        _EMPTY_TOTALES = {
            'count': 0, 'gravado': 0.0, 'iva': 0.0,
            'no_gravado': 0.0, 'exento': 0.0, 'total': 0.0,
            'perc_iva': 0.0, 'perc_nacionales': 0.0,
            'perc_iibb': 0.0, 'perc_mun': 0.0,
            'imp_internos': 0.0, 'otros_tributos': 0.0,
            'perc_no_categ': 0.0,
        }
        por_tipo = {}
        alicuotas = {}
        # Por qué: alícuotas separadas fac/nd/nc para cruce CSV detallado
        alicuotas_fac = {}
        alicuotas_nd = {}
        alicuotas_nc = {}
        totales = dict(_EMPTY_TOTALES)
        # Por qué: ARCA exige aperturas separadas para facturas y NC
        # Facturas → Débito/Crédito Fiscal
        # ND → Débito/Crédito Fiscal (separado de facturas para cruce)
        # NC → Restitución del Débito/Crédito Fiscal
        totales_fac = dict(_EMPTY_TOTALES)
        totales_nd = dict(_EMPTY_TOTALES)
        totales_nc = dict(_EMPTY_TOTALES)

        for move in moves:
            data = extracted_data[move.id] if extracted_data else self._extract_move_data(move)
            doc_type = move.l10n_latam_document_type_id
            key = doc_type.id
            # Por qué: internal_type discrimina Factura/ND/NC sin hardcodear
            # códigos AFIP. out_refund/in_refund son NC por move_type.
            es_nc = move.move_type in ('out_refund', 'in_refund')
            es_nd = (not es_nc
                     and getattr(doc_type, 'internal_type', '') == 'debit_note')

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

            # Acumular en totales generales + separados por tipo (fac/nd/nc)
            if es_nc:
                target = totales_nc
            elif es_nd:
                target = totales_nd
            else:
                target = totales_fac
            for bucket in (totales, target):
                for field in ('no_gravado', 'exento', 'perc_iva',
                              'perc_nacionales', 'perc_iibb', 'perc_mun',
                              'imp_internos', 'otros_tributos',
                              'perc_no_categ'):
                    bucket[field] += data.get(field, 0.0)
                bucket['count'] += 1
                bucket['gravado'] += gravado
                bucket['iva'] += iva
                bucket['total'] += data['total']

            # Acumular por código de alícuota IVA (total + separado fac/nd/nc)
            if es_nc:
                target_alic = alicuotas_nc
            elif es_nd:
                target_alic = alicuotas_nd
            else:
                target_alic = alicuotas_fac
            for alic in data['iva_alicuotas']:
                code = alic['code']
                for bucket in (alicuotas, target_alic):
                    if code not in bucket:
                        bucket[code] = {'base': 0.0, 'amount': 0.0}
                    bucket[code]['base'] += alic['base']
                    bucket[code]['amount'] += alic['amount']

        return {
            'por_tipo': sorted(por_tipo.values(), key=lambda x: x['code']),
            'alicuotas': alicuotas,
            'alicuotas_fac': alicuotas_fac,
            'alicuotas_nd': alicuotas_nd,
            'alicuotas_nc': alicuotas_nc,
            'totales': totales,
            'totales_fac': totales_fac,   # solo facturas
            'totales_nd': totales_nd,      # solo notas de débito
            'totales_nc': totales_nc,      # notas de crédito
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
            .ddjj-iva .step { background: #f8f6fa; border-left: 4px solid #875A7B;
                              padding: 10px 15px; margin: 10px 0; }
            .ddjj-iva .step-num { color: #875A7B; font-weight: bold; font-size: 13px;
                                  margin-bottom: 5px; }
            .ddjj-iva .step p { margin: 4px 0; }
            .ddjj-iva .step ul { margin: 5px 0; padding-left: 20px; }
            .ddjj-iva .step li { margin: 2px 0; }
            .ddjj-iva .valor-llenar { color: #c0392b; font-weight: bold; }
            .ddjj-iva .valor-cero { color: #aaa; }
            .ddjj-iva .importante { color: #c0392b; font-weight: bold;
                                    background: #fdf2f2; padding: 8px; margin: 8px 0;
                                    border: 1px solid #f5c6cb; font-size: 11px; }
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
            ('Perc. a No Categ.', vt.get('perc_no_categ', 0), ct.get('perc_no_categ', 0)),
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

        # ---- GUÍA PASO A PASO — CARGA EN PORTAL ARCA ----
        # Por qué: el usuario necesita saber exactamente qué valores cargar
        # en cada campo del portal F.2002 para que la DDJJ quede completa.
        # Sin la "Apertura de otros conceptos", el Crédito Fiscal queda en 0.
        h.append('<div class="section">')
        h.append('<h2>GUIA PASO A PASO — CARGA EN PORTAL ARCA (F.2051 IVA Simple)</h2>')

        # Paso 1: Subir archivos
        h.append('<div class="step">')
        h.append('<div class="step-num">PASO 1 — Subir archivos TXT</div>')
        h.append('<p>Menu: <strong>ARCA &rarr; Mis Comprobantes &rarr; '
                 'Libro IVA Digital &rarr; Importar</strong></p>')
        h.append('<p>Subir los 4 archivos TXT generados:</p>')
        h.append('<ul>'
                 '<li>LIBRO_IVA_DIGITAL_VENTAS_CBTE.txt</li>'
                 '<li>LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS.txt</li>'
                 '<li>LIBRO_IVA_DIGITAL_COMPRAS_CBTE.txt</li>'
                 '<li>LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS.txt</li>'
                 '</ul>')
        h.append('</div>')

        # Paso 2: Validar
        h.append('<div class="step">')
        h.append('<div class="step-num">PASO 2 — Validar archivos</div>')
        h.append('<p>Clic en <strong>"Validar"</strong>. Si hay errores, '
                 'usar la pestaña "Errores ARCA" del wizard en Odoo para '
                 'identificar los comprobantes con problema.</p>')
        h.append('</div>')

        # Por qué: ARCA F.2051 (IVA Simple) importa apertura vía CSV.
        # Los 4 CSV se generan automáticamente y se importan en el portal.

        # Paso 3: Importar CSV Apertura otros conceptos
        h.append('<div class="step">')
        h.append('<div class="step-num">PASO 3 — Importar CSV Apertura '
                 'de otros conceptos</div>')
        h.append('<p>En el <strong>Portal IVA (F.2051)</strong>, importar '
                 'los 4 archivos CSV generados en cada seccion '
                 'correspondiente:</p>')
        h.append('<table><tr><th>Seccion en portal ARCA</th>'
                 '<th>Archivo CSV a importar</th></tr>')
        csv_map = [
            ('Operaciones que generan Debito Fiscal',
             'IVA_SIMPLE_DEBITO_FISCAL.csv'),
            ('Restitucion del Debito Fiscal',
             'IVA_SIMPLE_REST_DEBITO_FISCAL.csv'),
            ('Operaciones que generan Credito Fiscal',
             'IVA_SIMPLE_CREDITO_FISCAL.csv'),
            ('Credito Fiscal Computable a Restituir',
             'IVA_SIMPLE_REST_CREDITO_FISCAL.csv'),
        ]
        for seccion, archivo in csv_map:
            h.append(f'<tr><td>{seccion}</td>'
                     f'<td><strong>{archivo}</strong></td></tr>')
        h.append('</table>')
        h.append('<p>En cada seccion: clic en <strong>"Importar"</strong> '
                 '&rarr; seleccionar el CSV &rarr; confirmar.</p>')
        h.append('<p class="importante">IMPORTANTE: Si no se importan los '
                 'CSV de Credito Fiscal, el credito aparecera en 0,00 en '
                 'la determinacion del impuesto.</p>')
        h.append('</div>')

        # Paso 4: Verificar determinación
        h.append('<div class="step">')
        h.append('<div class="step-num">PASO 4 — Verificar Determinacion '
                 'del Impuesto</div>')
        h.append('<p>El portal debe mostrar automaticamente:</p>')
        h.append('<table class="det-table">')
        h.append(f'<tr><td>Debito Fiscal</td><td>{fmt(debito)}</td></tr>')
        h.append(f'<tr><td>(-) Credito Fiscal</td>'
                 f'<td>{fmt(credito)}</td></tr>')
        h.append(f'<tr class="subtotal-row"><td>Subtotal</td>'
                 f'<td>{fmt(subtotal)}</td></tr>')
        if abs(perc_iva) > 0.005:
            h.append(f'<tr><td>(-) Percepciones IVA sufridas</td>'
                     f'<td>{fmt(perc_iva)}</td></tr>')
        if saldo > 0.005:
            h.append(f'<tr class="total-row"><td>Saldo a pagar</td>'
                     f'<td class="saldo-pagar">{fmt(saldo)}</td></tr>')
        elif saldo < -0.005:
            h.append(f'<tr class="total-row"><td>Saldo a favor</td>'
                     f'<td class="saldo-favor">{fmt(abs(saldo))}</td></tr>')
        else:
            h.append('<tr class="total-row"><td>Sin saldo</td>'
                     '<td>0,00</td></tr>')
        h.append('</table>')
        h.append('<p><strong>Agregar manualmente si corresponde:</strong></p>')
        h.append('<ul>'
                 '<li>Retenciones IVA sufridas</li>'
                 '<li>Saldo a favor de periodos anteriores</li>'
                 '</ul>')
        h.append('</div>')

        # Paso 5: Presentar
        h.append('<div class="step">')
        h.append('<div class="step-num">PASO 5 — Presentar DDJJ</div>')
        h.append('<p>Verificar que los totales coincidan con este reporte '
                 'y hacer clic en <strong>"Presentar"</strong>.</p>')
        h.append('</div>')

        h.append('</div>')  # close guia section

        h.append('<p class="info">(*) Este reporte es una previsualizacion '
                 'orientativa. Verificar contra el portal ARCA antes de presentar. '
                 'No incluye retenciones IVA sufridas ni saldo a favor de '
                 'periodos anteriores.</p>')
        h.append('</div>')
        return '\n'.join(h)

    # Mapeo concepto compra → label para display
    CONCEPTO_LABEL = {'1': 'Bienes', '3': 'Servicios'}

    def _render_cruce_csv_html(self, csv_totals, v_ddjj, c_ddjj):
        """Cruce de totales CSV apertura vs comprobantes informados.

        Por qué: Valida que los importes de los CSV de apertura coincidan
        con los comprobantes informados. Muestra facturas y ND por separado
        para facilitar la identificación de diferencias. CSV débito/crédito
        incluye Fac+ND; CSV restitución incluye solo NC.
        Tip: Todos los importes están en ARS (moneda empresa). El TXT puede
        tener moneda factura, pero DDJJ y CSV siempre usan ARS.
        """
        fmt = self._fmt_money
        h = ['<div class="ddjj-iva"><div class="section">']
        h.append('<h3>CRUCE: CSV Apertura vs Comprobantes Informados</h3>')
        hay_dif = False

        def _diff_html(val_a, val_b):
            """Compara dos importes y retorna HTML con OK/diferencia."""
            nonlocal hay_dif
            diff = round(val_a - val_b, 2)
            if abs(diff) > 0.01:
                hay_dif = True
                return (f'<span style="color:#c0392b;font-weight:bold">'
                        f'{fmt(diff)}</span>')
            return '<span style="color:#27ae60">OK</span>'

        # ---- VENTAS: Fac + ND separadas, luego NC ----
        h.append('<h3 style="font-size:12px">Debito Fiscal (Ventas)</h3>')
        h.append('<table><tr>'
                 '<th>Concepto</th><th>Comprobantes</th>'
                 '<th>CSV Apertura</th><th>Dif.</th></tr>')
        vf = v_ddjj['totales_fac']
        vnd = v_ddjj['totales_nd']
        vnc = v_ddjj['totales_nc']
        df = csv_totals['df']
        rdf = csv_totals['rdf']
        # CSV débito = Fac + ND → sumar ambos para comparar
        fac_nd_grav = vf['gravado'] + vnd['gravado']
        fac_nd_iva = vf['iva'] + vnd['iva']
        fac_nd_exng = (vf['exento'] + vf['no_gravado']
                       + vnd['exento'] + vnd['no_gravado'])
        ventas_rows = [
            ('Fac — Neto Gravado', vf['gravado'], ''),
            ('ND — Neto Gravado', vnd['gravado'], ''),
            ('Fac+ND — Neto Gravado', fac_nd_grav, df['neto']),
            ('Fac+ND — Debito Fiscal', fac_nd_iva, df['iva']),
            ('Fac+ND — Exento + No Grav.', fac_nd_exng, df['exento_ng']),
            ('NC — Neto Gravado', abs(vnc['gravado']), rdf['neto']),
            ('NC — Rest. Debito', abs(vnc['iva']), rdf['iva']),
            ('NC — Exento + No Grav.',
             abs(vnc['exento']) + abs(vnc['no_gravado']), rdf['exento_ng']),
        ]
        for label, ddjj_val, csv_val in ventas_rows:
            if csv_val == '':
                # Fila informativa sin comparación (detalle Fac / ND)
                h.append(f'<tr style="color:#7f8c8d"><td>{label}</td>'
                         f'<td>{fmt(ddjj_val)}</td>'
                         f'<td></td><td></td></tr>')
            else:
                dh = _diff_html(ddjj_val, csv_val)
                h.append(f'<tr><td>{label}</td><td>{fmt(ddjj_val)}</td>'
                         f'<td>{fmt(csv_val)}</td><td>{dh}</td></tr>')
        h.append('</table>')

        # ---- COMPRAS FAC+ND: detalle por concepto (bienes/servicios) ----
        cf_csv = csv_totals['cf']
        # Por qué: CSV crédito incluye Fac+ND → sumar alicuotas_fac + alicuotas_nd
        cf_alic_fac = c_ddjj.get('alicuotas_fac', {})
        cf_alic_nd = c_ddjj.get('alicuotas_nd', {})
        cf_alic = {}
        for code in set(list(cf_alic_fac.keys()) + list(cf_alic_nd.keys())):
            cf_alic[code] = {
                'base': (cf_alic_fac.get(code, {}).get('base', 0)
                         + cf_alic_nd.get(code, {}).get('base', 0)),
                'amount': (cf_alic_fac.get(code, {}).get('amount', 0)
                           + cf_alic_nd.get(code, {}).get('amount', 0)),
            }
        h.append('<h3 style="font-size:12px">'
                 'Credito Fiscal — Facturas + ND de Compra</h3>')
        h.append('<table><tr><th>Concepto</th><th>Alicuota</th>'
                 '<th>Neto Gravado</th><th>Credito Fiscal</th></tr>')

        # Filas de detalle por concepto+alícuota del CSV
        csv_by_code = {}
        for d in cf_csv.get('detalle', []):
            clabel = self.CONCEPTO_LABEL.get(d['concepto'], d['concepto'])
            alabel = self.IVA_CODE_LABEL.get(d['code'], d['code'])
            h.append(f'<tr><td>{clabel}</td><td>{alabel}</td>'
                     f'<td>{fmt(d["neto"])}</td>'
                     f'<td>{fmt(d["iva"])}</td></tr>')
            if d['code'] not in csv_by_code:
                csv_by_code[d['code']] = {'neto': 0.0, 'iva': 0.0}
            csv_by_code[d['code']]['neto'] += d['neto']
            csv_by_code[d['code']]['iva'] += d['iva']

        # Total CSV
        h.append(f'<tr class="total-row"><td colspan="2">TOTAL CSV</td>'
                 f'<td>{fmt(cf_csv["neto"])}</td>'
                 f'<td>{fmt(cf_csv["iva"])}</td></tr>')

        # Comparación por alícuota: DDJJ (fac+nd) vs CSV agrupado por code
        # Muestra detalle Fac / ND por separado para diagnóstico
        for code in sorted(set(list(cf_alic.keys()) +
                               list(csv_by_code.keys()))):
            alabel = self.IVA_CODE_LABEL.get(code, code)
            # Detalle informativo: Fac y ND por separado
            fac_b = round(cf_alic_fac.get(code, {}).get('base', 0), 2)
            fac_i = round(cf_alic_fac.get(code, {}).get('amount', 0), 2)
            nd_b = round(cf_alic_nd.get(code, {}).get('base', 0), 2)
            nd_i = round(cf_alic_nd.get(code, {}).get('amount', 0), 2)
            if fac_b or fac_i:
                h.append(
                    f'<tr style="color:#7f8c8d;background:#f8f6fa">'
                    f'<td>Fac</td><td>{alabel}</td>'
                    f'<td>{fmt(fac_b)}</td>'
                    f'<td>{fmt(fac_i)}</td></tr>')
            if nd_b or nd_i:
                h.append(
                    f'<tr style="color:#7f8c8d;background:#f8f6fa">'
                    f'<td>ND</td><td>{alabel}</td>'
                    f'<td>{fmt(nd_b)}</td>'
                    f'<td>{fmt(nd_i)}</td></tr>')
            # Comparación Fac+ND vs CSV
            ddjj_b = round(cf_alic.get(code, {}).get('base', 0), 2)
            ddjj_i = round(cf_alic.get(code, {}).get('amount', 0), 2)
            csv_b = round(csv_by_code.get(code, {}).get('neto', 0), 2)
            csv_i = round(csv_by_code.get(code, {}).get('iva', 0), 2)
            db = _diff_html(ddjj_b, csv_b)
            di = _diff_html(ddjj_i, csv_i)
            h.append(
                f'<tr style="background:#eef;font-weight:bold">'
                f'<td>Fac+ND</td><td>vs CSV {alabel}</td>'
                f'<td>{db} ({fmt(ddjj_b)})</td>'
                f'<td>{di} ({fmt(ddjj_i)})</td></tr>')
        h.append('</table>')

        # ---- COMPRAS NC: detalle por concepto (restitución) ----
        rcf_csv = csv_totals['rcf']
        rcf_alic = c_ddjj.get('alicuotas_nc', {})
        h.append('<h3 style="font-size:12px">'
                 'Rest. Credito Fiscal — NC de Compra</h3>')
        h.append('<table><tr><th>Concepto</th><th>Alicuota</th>'
                 '<th>Neto Gravado</th><th>Credito Fiscal</th></tr>')

        rcf_by_code = {}
        for d in rcf_csv.get('detalle', []):
            clabel = self.CONCEPTO_LABEL.get(d['concepto'], d['concepto'])
            alabel = self.IVA_CODE_LABEL.get(d['code'], d['code'])
            h.append(f'<tr><td>{clabel}</td><td>{alabel}</td>'
                     f'<td>{fmt(d["neto"])}</td>'
                     f'<td>{fmt(d["iva"])}</td></tr>')
            if d['code'] not in rcf_by_code:
                rcf_by_code[d['code']] = {'neto': 0.0, 'iva': 0.0}
            rcf_by_code[d['code']]['neto'] += d['neto']
            rcf_by_code[d['code']]['iva'] += d['iva']

        h.append(f'<tr class="total-row"><td colspan="2">TOTAL CSV</td>'
                 f'<td>{fmt(rcf_csv["neto"])}</td>'
                 f'<td>{fmt(rcf_csv["iva"])}</td></tr>')

        # NC: DDJJ tiene negativos → abs para comparar
        for code in sorted(set(list(rcf_alic.keys()) +
                               list(rcf_by_code.keys()))):
            alabel = self.IVA_CODE_LABEL.get(code, code)
            ddjj_b = round(abs(rcf_alic.get(code, {}).get('base', 0)), 2)
            ddjj_i = round(abs(rcf_alic.get(code, {}).get('amount', 0)), 2)
            csv_b = round(rcf_by_code.get(code, {}).get('neto', 0), 2)
            csv_i = round(rcf_by_code.get(code, {}).get('iva', 0), 2)
            db = _diff_html(ddjj_b, csv_b)
            di = _diff_html(ddjj_i, csv_i)
            h.append(
                f'<tr style="background:#f8f6fa">'
                f'<td colspan="2">vs DDJJ IVA {alabel}</td>'
                f'<td>{db} ({fmt(ddjj_b)})</td>'
                f'<td>{di} ({fmt(ddjj_i)})</td></tr>')
        h.append('</table>')

        # ---- Resultado general ----
        if hay_dif:
            h.append(
                '<p class="importante">Se detectaron diferencias entre '
                'los totales de comprobantes y la apertura CSV. '
                'Revisar antes de presentar.</p>')
        else:
            h.append(
                '<p style="color:#27ae60;font-weight:bold">'
                'Todos los totales coinciden.</p>')

        h.append('</div></div>')
        return '\n'.join(h)

    # -------------------------------------------------------------------------
    # DDJJ IVA - EXPORTACIÓN PDF / EXCEL
    # -------------------------------------------------------------------------

    def _generate_ddjj_pdf(self):
        """Genera PDF del reporte DDJJ IVA desde el HTML del wizard.

        Por qué: wkhtmltopdf (incluido en Odoo) convierte el HTML a PDF
        manteniendo estilos y tablas. Landscape para que quepan las columnas.
        """
        html = self.ddjj_iva_html
        if not html:
            return False
        full_html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"/>'
            '<style>body{font-family:Arial,sans-serif;font-size:12px;'
            'margin:20px;}</style></head><body>'
            + html + '</body></html>'
        )
        try:
            return self.env['ir.actions.report']._run_wkhtmltopdf(
                [full_html], landscape=True,
            )
        except Exception:
            _logger.warning('No se pudo generar PDF de DDJJ IVA', exc_info=True)
            return False

    def _generate_ddjj_excel(self):
        """Genera Excel con el reporte DDJJ IVA en 3 hojas.

        Por qué: Lee datos DDJJ pre-calculados de ddjj_data_json
        (generados en action_generar) para no reprocesar moves.
        Hojas: Débito Fiscal, Crédito Fiscal, Determinación.
        """
        ddjj_raw = json.loads(self.ddjj_data_json or '{}')
        v_data = ddjj_raw.get('v_data')
        c_data = ddjj_raw.get('c_data')
        if not v_data or not c_data:
            return False

        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})

        # Formatos reutilizables
        fmts = self._excel_formats(wb)
        empresa = self.env.company.name
        cuit = self.env.company.vat or 'Sin configurar'
        periodo = self.date_from.strftime('%m/%Y')

        # Hoja 1: Débito Fiscal (Ventas)
        self._excel_sheet_iva(
            wb, fmts, 'Débito Fiscal', v_data,
            'COMPROBANTES EMITIDOS (Ventas)', 'Débito Fiscal',
            empresa, cuit, periodo,
        )
        # Hoja 2: Crédito Fiscal (Compras)
        self._excel_sheet_iva(
            wb, fmts, 'Crédito Fiscal', c_data,
            'COMPROBANTES RECIBIDOS (Compras)', 'Crédito Fiscal',
            empresa, cuit, periodo,
        )
        # Hoja 3: Determinación del Impuesto
        self._excel_sheet_determinacion(
            wb, fmts, v_data, c_data, empresa, cuit, periodo,
        )
        # Hoja 4: Guía paso a paso para carga en portal ARCA
        self._excel_sheet_guia_portal(
            wb, fmts, v_data, c_data, empresa, cuit, periodo,
        )

        wb.close()
        return buf.getvalue()

    def _excel_formats(self, wb):
        """Crea y retorna dict con formatos reutilizables para el Excel."""
        return {
            'title': wb.add_format({
                'bold': True, 'font_size': 14, 'font_color': '#2c3e50',
                'bottom': 2, 'bottom_color': '#875A7B',
            }),
            'section': wb.add_format({
                'bold': True, 'font_size': 11, 'font_color': '#875A7B',
            }),
            'header': wb.add_format({
                'bold': True, 'bg_color': '#875A7B', 'font_color': 'white',
                'border': 1, 'text_wrap': True, 'align': 'center',
            }),
            'text': wb.add_format({'border': 1}),
            'center': wb.add_format({'border': 1, 'align': 'center'}),
            'money': wb.add_format({
                'num_format': '#,##0.00', 'border': 1, 'align': 'right',
            }),
            'total_text': wb.add_format({
                'bold': True, 'bg_color': '#f3eef5', 'border': 1,
            }),
            'total_center': wb.add_format({
                'bold': True, 'bg_color': '#f3eef5', 'border': 1,
                'align': 'center',
            }),
            'total_money': wb.add_format({
                'bold': True, 'num_format': '#,##0.00', 'border': 1,
                'bg_color': '#f3eef5', 'align': 'right',
            }),
        }

    def _excel_sheet_iva(self, wb, fmts, sheet_name, data, titulo_cbte,
                         label_iva, empresa, cuit, periodo):
        """Escribe una hoja de comprobantes + alícuotas (ventas o compras).

        Por qué: Ventas y compras tienen la misma estructura de tabla,
        solo cambia el título (Débito/Crédito Fiscal).
        """
        ws = wb.add_worksheet(sheet_name)
        ws.set_column('A:A', 35)
        ws.set_column('B:B', 8)
        ws.set_column('C:G', 18)

        row = 0
        ws.write(row, 0, f'DDJJ IVA - F.2002 | Período {periodo}', fmts['title'])
        row += 1
        ws.write(row, 0, f'{empresa} | CUIT: {cuit}')
        row += 2

        # Tabla de comprobantes por tipo
        ws.write(row, 0, titulo_cbte, fmts['section'])
        row += 1
        headers = ['Tipo Comprobante', 'Cant.', 'Neto Gravado',
                   label_iva, 'No Gravado', 'Exento', 'Total']
        for col, h in enumerate(headers):
            ws.write(row, col, h, fmts['header'])
        row += 1

        for r in data['por_tipo']:
            ws.write(row, 0, r['name'], fmts['text'])
            ws.write(row, 1, r['count'], fmts['center'])
            for col, key in enumerate(
                ('gravado', 'iva', 'no_gravado', 'exento', 'total'), 2
            ):
                ws.write(row, col, r[key], fmts['money'])
            row += 1

        # Fila total
        t = data['totales']
        ws.write(row, 0, 'TOTAL', fmts['total_text'])
        ws.write(row, 1, t['count'], fmts['total_center'])
        for col, key in enumerate(
            ('gravado', 'iva', 'no_gravado', 'exento', 'total'), 2
        ):
            ws.write(row, col, t[key], fmts['total_money'])
        row += 2

        # Detalle alícuotas IVA
        ws.write(row, 0, f'Detalle Alícuotas IVA - {label_iva}', fmts['section'])
        row += 1
        for col, h in enumerate(['Alícuota', 'Base Imponible', label_iva]):
            ws.write(row, col, h, fmts['header'])
        row += 1

        total_base = total_iva = 0.0
        for code in sorted(data['alicuotas'].keys()):
            a = data['alicuotas'][code]
            label = self.IVA_CODE_LABEL.get(code, f'Cód. {code}')
            ws.write(row, 0, f'IVA {label}', fmts['text'])
            ws.write(row, 1, a['base'], fmts['money'])
            ws.write(row, 2, a['amount'], fmts['money'])
            total_base += a['base']
            total_iva += a['amount']
            row += 1

        ws.write(row, 0, f'TOTAL {label_iva.upper()}', fmts['total_text'])
        ws.write(row, 1, total_base, fmts['total_money'])
        ws.write(row, 2, total_iva, fmts['total_money'])

    def _excel_sheet_determinacion(self, wb, fmts, v_data, c_data,
                                   empresa, cuit, periodo):
        """Escribe la hoja de Determinación del Impuesto.

        Por qué: Resume débito - crédito = saldo, que es lo que el
        usuario carga en el F.2002 de ARCA.
        """
        ws = wb.add_worksheet('Determinación')
        ws.set_column('A:A', 40)
        ws.set_column('B:C', 20)

        vt = v_data['totales']
        ct = c_data['totales']
        debito = vt['iva']
        credito = ct['iva']
        subtotal = debito - credito
        perc_iva = ct['perc_iva']
        saldo = subtotal - perc_iva

        row = 0
        ws.write(row, 0, f'DETERMINACIÓN DEL IMPUESTO | Período {periodo}',
                 fmts['title'])
        row += 2

        ws.write(row, 0, 'Débito Fiscal', fmts['text'])
        ws.write(row, 1, debito, fmts['money'])
        row += 1
        ws.write(row, 0, '(-) Crédito Fiscal', fmts['text'])
        ws.write(row, 1, credito, fmts['money'])
        row += 1
        ws.write(row, 0, 'Subtotal', fmts['total_text'])
        ws.write(row, 1, subtotal, fmts['total_money'])
        row += 1

        if abs(perc_iva) > 0.005:
            ws.write(row, 0, '(-) Percepciones IVA sufridas', fmts['text'])
            ws.write(row, 1, perc_iva, fmts['money'])
            row += 1

        # Resultado final con color según saldo
        if saldo > 0.005:
            label, color = 'SALDO A PAGAR', '#e74c3c'
        elif saldo < -0.005:
            label, color = 'SALDO A FAVOR', '#27ae60'
        else:
            label, color = 'SIN SALDO', '#333333'

        result_fmt = wb.add_format({
            'bold': True, 'num_format': '#,##0.00', 'border': 1,
            'bg_color': '#f3eef5', 'align': 'right', 'font_color': color,
        })
        ws.write(row, 0, label, fmts['total_text'])
        ws.write(row, 1, abs(saldo), result_fmt)
        row += 2

        # Otros tributos (informativo)
        otros_items = [
            ('Perc. a No Categ.', vt.get('perc_no_categ', 0),
             ct.get('perc_no_categ', 0)),
            ('Percepciones IIBB', vt.get('perc_iibb', 0), ct.get('perc_iibb', 0)),
            ('Percepciones Municipales', vt.get('perc_mun', 0), ct.get('perc_mun', 0)),
            ('Percepciones Nacionales', vt.get('perc_nacionales', 0),
             ct.get('perc_nacionales', 0)),
            ('Impuestos Internos', vt.get('imp_internos', 0),
             ct.get('imp_internos', 0)),
            ('Otros Tributos', vt.get('otros_tributos', 0),
             ct.get('otros_tributos', 0)),
        ]
        otros_con_valor = [(n, e, r) for n, e, r in otros_items
                           if abs(e) > 0.005 or abs(r) > 0.005]
        if otros_con_valor:
            ws.write(row, 0, 'OTROS TRIBUTOS (informativo)', fmts['section'])
            row += 1
            for col, h in enumerate(['Concepto', 'Emitidos', 'Recibidos']):
                ws.write(row, col, h, fmts['header'])
            row += 1
            for nombre, emitido, recibido in otros_con_valor:
                ws.write(row, 0, nombre, fmts['text'])
                ws.write(row, 1, emitido, fmts['money'])
                ws.write(row, 2, recibido, fmts['money'])
                row += 1

    def _excel_sheet_guia_portal(self, wb, fmts, v_data, c_data,
                                 empresa, cuit, periodo):
        """Escribe la hoja Guía Carga Portal con el paso a paso.

        Por qué: El usuario necesita saber qué archivos importar en el
        portal ARCA (F.2051 IVA Simple) y verificar la determinación.
        """
        ws = wb.add_worksheet('Guia Carga Portal')
        ws.set_column('A:A', 50)
        ws.set_column('B:B', 20)

        vt = v_data['totales']
        ct = c_data['totales']
        debito = vt['iva']
        credito = ct['iva']
        subtotal = debito - credito
        perc_iva = ct.get('perc_iva', 0)
        saldo = subtotal - perc_iva

        # Formatos específicos de esta hoja
        step_fmt = wb.add_format({
            'bold': True, 'font_size': 12, 'font_color': '#875A7B',
            'bottom': 1, 'bottom_color': '#875A7B',
        })
        highlight_fmt = wb.add_format({
            'num_format': '#,##0.00', 'border': 1, 'align': 'right',
            'font_color': '#c0392b', 'bold': True,
        })
        warn_fmt = wb.add_format({
            'bold': True, 'font_color': '#c0392b', 'text_wrap': True,
        })
        wrap_fmt = wb.add_format({'text_wrap': True})

        row = 0
        ws.write(row, 0,
                 f'GUIA CARGA PORTAL ARCA (F.2051) | Periodo {periodo}',
                 fmts['title'])
        row += 1
        ws.write(row, 0, f'{empresa} | CUIT: {cuit}')
        row += 2

        # ---- PASO 1 ----
        ws.write(row, 0, 'PASO 1 — Subir archivos TXT', step_fmt)
        row += 1
        ws.write(row, 0,
                 'Menu: ARCA > Mis Comprobantes > Libro IVA Digital > Importar',
                 wrap_fmt)
        row += 1
        for fname in [
            'LIBRO_IVA_DIGITAL_VENTAS_CBTE.txt',
            'LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS.txt',
            'LIBRO_IVA_DIGITAL_COMPRAS_CBTE.txt',
            'LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS.txt',
        ]:
            ws.write(row, 0, f'  {fname}')
            row += 1
        row += 1

        # ---- PASO 2 ----
        ws.write(row, 0, 'PASO 2 — Validar archivos', step_fmt)
        row += 1
        ws.write(row, 0,
                 'Clic en "Validar". Si hay errores, usar pestaña '
                 '"Errores ARCA" del wizard en Odoo.', wrap_fmt)
        row += 2

        # ---- PASO 3: Importar CSV Apertura otros conceptos ----
        # Por qué: ARCA F.2051 importa apertura vía CSV (no carga manual)
        ws.write(row, 0,
                 'PASO 3 — Importar CSV Apertura de otros conceptos',
                 step_fmt)
        row += 1
        ws.write(row, 0,
                 'En el Portal IVA (F.2051), importar los 4 CSV en cada '
                 'seccion correspondiente:', wrap_fmt)
        row += 1
        ws.write(row, 0, 'Seccion en portal ARCA', fmts['header'])
        ws.write(row, 1, 'Archivo CSV a importar', fmts['header'])
        row += 1
        csv_map = [
            ('Operaciones que generan Debito Fiscal',
             'IVA_SIMPLE_DEBITO_FISCAL.csv'),
            ('Restitucion del Debito Fiscal',
             'IVA_SIMPLE_REST_DEBITO_FISCAL.csv'),
            ('Operaciones que generan Credito Fiscal',
             'IVA_SIMPLE_CREDITO_FISCAL.csv'),
            ('Credito Fiscal Computable a Restituir',
             'IVA_SIMPLE_REST_CREDITO_FISCAL.csv'),
        ]
        for seccion, archivo in csv_map:
            ws.write(row, 0, seccion, fmts['text'])
            ws.write(row, 1, archivo, highlight_fmt)
            row += 1
        ws.write(row, 0,
                 'IMPORTANTE: Si no se importan los CSV de Credito Fiscal, '
                 'el credito aparecera en 0,00.', warn_fmt)
        row += 2

        # ---- PASO 4: Verificar Determinación ----
        ws.write(row, 0,
                 'PASO 4 — Verificar Determinacion del Impuesto', step_fmt)
        row += 1
        ws.write(row, 0, 'El portal debe mostrar automaticamente:')
        row += 1

        det_items = [
            ('Debito Fiscal', debito),
            ('(-) Credito Fiscal', credito),
            ('Subtotal', subtotal),
        ]
        if abs(perc_iva) > 0.005:
            det_items.append(('(-) Percepciones IVA sufridas', perc_iva))

        if saldo > 0.005:
            det_items.append(('SALDO A PAGAR', saldo))
        elif saldo < -0.005:
            det_items.append(('SALDO A FAVOR', abs(saldo)))
        else:
            det_items.append(('SIN SALDO', 0.0))

        for label, valor in det_items:
            is_total = label.startswith('SALDO') or label == 'SIN SALDO'
            ws.write(row, 0, label,
                     fmts['total_text'] if is_total else fmts['text'])
            ws.write(row, 1, valor,
                     fmts['total_money'] if is_total else fmts['money'])
            row += 1
        row += 1

        ws.write(row, 0, 'Agregar manualmente si corresponde:', wrap_fmt)
        row += 1
        ws.write(row, 0, '  - Retenciones IVA sufridas')
        row += 1
        ws.write(row, 0, '  - Saldo a favor de periodos anteriores')
        row += 2

        # ---- PASO 5: Presentar ----
        ws.write(row, 0, 'PASO 5 — Presentar DDJJ', step_fmt)
        row += 1
        ws.write(row, 0,
                 'Verificar que los totales coincidan con este reporte '
                 'y hacer clic en "Presentar".', wrap_fmt)

    # -------------------------------------------------------------------------
    # CSV IVA SIMPLE — APERTURA OTROS CONCEPTOS (F.2051)
    # -------------------------------------------------------------------------

    def _get_tipo_sujeto(self, partner):
        """Tipo sujeto comprador desde responsabilidad AFIP.

        Por qué: ARCA IVA Simple agrupa ventas por tipo de comprador.
        Fallback: '3' (CF/Exentos/NA) si no tiene responsabilidad configurada.
        """
        resp = partner.l10n_ar_afip_responsibility_type_id
        code = str(resp.code) if resp else ''
        return self.RESP_TIPO_SUJETO.get(code, '3')

    def _fmt_csv_amount(self, amount):
        """Importe para CSV ARCA: coma decimal, sin padding.

        Por qué: ARCA IVA Simple espera formato numérico simple con coma
        como separador decimal. Sin ceros trailing ni zero-padding.
        Ejemplos: 100.00 → '100', 10.50 → '10,5', 1234.56 → '1234,56'
        """
        if abs(amount) < 0.005:
            return '0'
        rounded = round(amount, 2)
        if rounded == int(rounded):
            return str(int(rounded))
        s = f'{rounded:.2f}'.rstrip('0')
        return s.replace('.', ',')

    def _generar_csvs_iva_simple(self, ventas, compras,
                                  v_extracted, c_extracted):
        """Genera los 4 CSV de Apertura otros conceptos (IVA Simple F.2051).

        Por qué: Desde nov 2025, ARCA reemplazó F.2002 por F.2051 (IVA Simple).
        La apertura se importa vía CSV (separador ;, decimal coma, latin-1).
        Separa facturas (débito/crédito) de NC (restitución).
        """
        # Separar facturas/ND de NC
        ventas_fac = ventas.filtered(lambda m: m.move_type == 'out_invoice')
        ventas_nc = ventas.filtered(lambda m: m.move_type == 'out_refund')
        compras_fac = compras.filtered(lambda m: m.move_type == 'in_invoice')
        compras_nc = compras.filtered(lambda m: m.move_type == 'in_refund')

        # Por qué: cada CSV retorna (binary, totals) para cruce posterior
        df_bin, df_totals = self._csv_debito_fiscal(ventas_fac, v_extracted)
        rdf_bin, rdf_totals = self._csv_rest_debito_fiscal(
            ventas_nc, v_extracted)
        cf_bin, cf_totals = self._csv_credito_fiscal(
            compras_fac, c_extracted)
        rcf_bin, rcf_totals = self._csv_rest_credito_fiscal(
            compras_nc, c_extracted)

        csv_fields = {
            'iva_simple_debito_csv': df_bin,
            'iva_simple_debito_csv_name': 'IVA_SIMPLE_DEBITO_FISCAL.csv',
            'iva_simple_rest_debito_csv': rdf_bin,
            'iva_simple_rest_debito_csv_name':
                'IVA_SIMPLE_REST_DEBITO_FISCAL.csv',
            'iva_simple_credito_csv': cf_bin,
            'iva_simple_credito_csv_name': 'IVA_SIMPLE_CREDITO_FISCAL.csv',
            'iva_simple_rest_credito_csv': rcf_bin,
            'iva_simple_rest_credito_csv_name':
                'IVA_SIMPLE_REST_CREDITO_FISCAL.csv',
        }
        csv_totals = {
            'df': df_totals, 'rdf': rdf_totals,
            'cf': cf_totals, 'rcf': rcf_totals,
        }
        return csv_fields, csv_totals

    # Headers CSV IVA Simple — formato ARCA F.2051
    # Por qué: ARCA espera headers SIN comillas. Con comillas ARCA no los
    # reconoce como cabecera y los parsea como datos → "Alícuota inválida".
    _CSV_HEADER_DEBITO = (
        'Actividad;Tipo de Operacion;Tipo de sujeto comprador;'
        'Codigo de Alicuota;Monto Neto Gravado;'
        'Debito Fiscal Facturado;Debito Fiscal O.D.P.;'
        'Monto Neto Exento o No Gravado'
    )
    _CSV_HEADER_REST_DEBITO = (
        'Actividad;Tipo de Operacion;Tipo de sujeto comprador;'
        'Codigo de Alicuota;Monto Neto Gravado;'
        'Debito Fiscal a Restituir;'
        'Monto Neto Exento o No Gravado'
    )
    _CSV_HEADER_CREDITO = (
        'Concepto;Codigo de Alicuota;Monto Neto Gravado;'
        'Credito Fiscal Facturado;Credito Fiscal Computable'
    )
    _CSV_HEADER_REST_CREDITO = (
        'Concepto;Codigo de Alicuota;Monto Neto Gravado;'
        'Credito Fiscal Facturado'
    )

    def _csv_debito_fiscal(self, moves, extracted):
        """CSV 1: Débito fiscal — facturas + ND de venta.

        Por qué: Agrupa por (actividad, tipo_sujeto, alícuota).
        8 columnas según modelo ARCA. Alícuota = código AFIP (no tasa).
        tipo_op 1 = gravado, tipo_op 3 = exento/no gravado.
        Gravado (7 valores, col 8 vacía):
            act;1;sujeto;code_afip;neto;debito;0
        Exento (8 valores, cols 3-7 vacías):
            act;3;;;;;;;monto
        """
        act = self.actividad_afip or '465320'
        acum = {}
        exento_ng = 0.0

        for move in moves:
            data = extracted[move.id]
            sujeto = self._get_tipo_sujeto(move.commercial_partner_id)

            # Gravado: una línea por alícuota × sujeto
            for alic in data['iva_alicuotas']:
                key = (act, '1', sujeto, alic['code'])
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                acum[key]['neto'] += alic['base']
                acum[key]['iva'] += alic['amount']

            # Exento + No gravado → tipo_op 3 (sin sujeto ni alícuota)
            monto_exng = data['exento'] + data['no_gravado']
            if abs(monto_exng) > 0.005:
                exento_ng += monto_exng

        # Generar líneas CSV con header
        lines = [self._CSV_HEADER_DEBITO]
        fmt = self._fmt_csv_amount
        for key in sorted(acum.keys()):
            vals = acum[key]
            _, tipo_op, sujeto, code = key
            # Por qué: ARCA espera código AFIP de alícuota (5=21%), no la tasa
            lines.append(
                f'{key[0]};{tipo_op};{sujeto};{code};'
                f'{fmt(vals["neto"])};{fmt(vals["iva"])};0'
            )

        # Exento/no gravado: tipo_op 3, cols 3-7 vacías, monto en col 8
        if abs(exento_ng) > 0.005:
            lines.append(f'{act};3;;;;;;{fmt(exento_ng)}')

        # Totales para cruce con comprobantes informados (TXT)
        totals = {
            'neto': round(sum(v['neto'] for v in acum.values()), 2),
            'iva': round(sum(v['iva'] for v in acum.values()), 2),
            'exento_ng': round(exento_ng, 2),
        }
        binary = self._encode_lines(lines) if len(lines) > 1 else False
        return binary, totals

    def _csv_rest_debito_fiscal(self, moves, extracted):
        """CSV 2: Restitución débito fiscal — NC de venta.

        Por qué: Mismo esquema que CSV 1 pero sin campo O.D.P. (7 cols).
        tipo_op 3 para exento/NG. Importes en valor absoluto.
        Gravado: act;1;sujeto;code_afip;neto;debito
        Exento:  act;3;;;;;monto
        """
        act = self.actividad_afip or '465320'
        acum = {}
        exento_ng = 0.0

        for move in moves:
            data = extracted[move.id]
            sujeto = self._get_tipo_sujeto(move.commercial_partner_id)

            for alic in data['iva_alicuotas']:
                key = (act, '1', sujeto, alic['code'])
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                # NC tienen valores negativos en extracted → abs
                acum[key]['neto'] += abs(alic['base'])
                acum[key]['iva'] += abs(alic['amount'])

            monto_exng = abs(data['exento']) + abs(data['no_gravado'])
            if monto_exng > 0.005:
                exento_ng += monto_exng

        lines = [self._CSV_HEADER_REST_DEBITO]
        fmt = self._fmt_csv_amount
        for key in sorted(acum.keys()):
            vals = acum[key]
            _, tipo_op, sujeto, code = key
            lines.append(
                f'{key[0]};{tipo_op};{sujeto};{code};'
                f'{fmt(vals["neto"])};{fmt(vals["iva"])}'
            )

        # Exento/no gravado: tipo_op 2, cols 3-6 vacías, monto en col 7
        # Por qué: En restitución, ARCA usa tipo_op 2 para exento/NG (no 3)
        if abs(exento_ng) > 0.005:
            lines.append(f'{act};2;;;;;{fmt(exento_ng)}')

        # Totales para cruce con comprobantes informados (TXT)
        totals = {
            'neto': round(sum(v['neto'] for v in acum.values()), 2),
            'iva': round(sum(v['iva'] for v in acum.values()), 2),
            'exento_ng': round(exento_ng, 2),
        }
        binary = self._encode_lines(lines) if len(lines) > 1 else False
        return binary, totals

    def _csv_credito_fiscal(self, moves, extracted):
        """CSV 3: Crédito fiscal — facturas + ND de compra.

        Por qué: Agrupa por (concepto, alícuota). concepto = tipo de bien:
        1=bienes, 3=servicios. Alícuota = código AFIP.
        Usa extracted[move.id]['iva_by_concepto'] — misma fuente que TXT/DDJJ.
        Formato (5 cols):
            concepto;code_afip;neto;credito_facturado;credito_computable
        """
        acum = {}

        for move in moves:
            # Por qué: Usar iva_by_concepto de _extract_move_data (única fuente)
            # elimina discrepancias entre CSV y TXT/DDJJ.
            by_concepto = extracted[move.id]['iva_by_concepto']
            for key, vals in by_concepto.items():
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                acum[key]['neto'] += vals['base']
                acum[key]['iva'] += vals['amount']

        lines = [self._CSV_HEADER_CREDITO]
        fmt = self._fmt_csv_amount
        for key in sorted(acum.keys()):
            concepto, code = key
            vals = acum[key]
            # credito_computable = credito_facturado (sin prorrateo)
            lines.append(
                f'{concepto};{code};{fmt(vals["neto"])};'
                f'{fmt(vals["iva"])};{fmt(vals["iva"])}'
            )

        # Totales + detalle por concepto para cruce en wizard
        totals = {
            'neto': round(sum(v['neto'] for v in acum.values()), 2),
            'iva': round(sum(v['iva'] for v in acum.values()), 2),
            # Por qué: detalle por (concepto, alícuota) para desglose
            # bienes/servicios en el reporte de cruce del wizard
            'detalle': [
                {'concepto': k[0], 'code': k[1],
                 'neto': round(v['neto'], 2), 'iva': round(v['iva'], 2)}
                for k, v in sorted(acum.items())
            ],
        }
        binary = self._encode_lines(lines) if len(lines) > 1 else False
        return binary, totals

    def _csv_rest_credito_fiscal(self, moves, extracted):
        """CSV 4: Restitución crédito fiscal — NC de compra.

        Por qué: Igual que CSV 3 pero sin campo credito_computable (4 cols).
        Importes en valor absoluto (NC tienen signo negativo en extracted).
        Usa extracted[move.id]['iva_by_concepto'] — misma fuente que TXT/DDJJ.
        Formato: concepto;code_afip;neto;credito_facturado
        """
        acum = {}

        for move in moves:
            # Por qué: Usar iva_by_concepto de _extract_move_data (única fuente)
            by_concepto = extracted[move.id]['iva_by_concepto']
            for key, vals in by_concepto.items():
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                # NC → valores negativos en extracted → abs
                acum[key]['neto'] += abs(vals['base'])
                acum[key]['iva'] += abs(vals['amount'])

        lines = [self._CSV_HEADER_REST_CREDITO]
        fmt = self._fmt_csv_amount
        for key in sorted(acum.keys()):
            concepto, code = key
            vals = acum[key]
            lines.append(
                f'{concepto};{code};{fmt(vals["neto"])};{fmt(vals["iva"])}'
            )

        # Totales + detalle por concepto para cruce en wizard
        totals = {
            'neto': round(sum(v['neto'] for v in acum.values()), 2),
            'iva': round(sum(v['iva'] for v in acum.values()), 2),
            'detalle': [
                {'concepto': k[0], 'code': k[1],
                 'neto': round(v['neto'], 2), 'iva': round(v['iva'], 2)}
                for k, v in sorted(acum.items())
            ],
        }
        binary = self._encode_lines(lines) if len(lines) > 1 else False
        return binary, totals

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
