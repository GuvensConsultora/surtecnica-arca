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

    # Archivo TXT descargable con la cuadratura
    cuadratura_file = fields.Binary('Cuadratura F.2051')
    cuadratura_name = fields.Char()

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
    IVA_AMOUNT_MAP = {
        0: '0003', 2.5: '0009', 5: '0008',
        10.5: '0004', 21: '0005', 27: '0006',
    }

    # Códigos AFIP que son alícuotas de IVA gravado (van al archivo de alícuotas)
    IVA_GRAVADO_CODES = ('0003', '0004', '0005', '0006', '0008', '0009')

    # Nombres legibles de alícuotas para el resumen de cuadratura
    IVA_NAMES = {
        '0003': '0%', '0004': '10.5%', '0005': '21%',
        '0006': '27%', '0008': '5%', '0009': '2.5%',
    }

    # Tasas IVA por código AFIP (para ajuste de redondeo)
    IVA_RATES = {
        '0003': 0.0, '0004': 0.105, '0005': 0.21,
        '0006': 0.27, '0008': 0.05, '0009': 0.025,
    }

    # Códigos moneda AFIP - fallback si no existe l10n_ar_afip_code en currency
    MONEDA_MAP = {
        'ARS': 'PES', 'USD': 'DOL', 'EUR': '060', 'BRL': '012',
        'GBP': '021', 'UYU': '011', 'CLP': '033', 'MXN': '010',
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
        """Genera los 4 archivos TXT del Libro IVA Digital + 4 CSV IVA Simple."""
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError('La fecha "Desde" no puede ser posterior a "Hasta".')

        ventas = self._get_moves('out')
        compras = self._get_moves('in')

        # Validar CUIT de partners antes de generar
        self._validar_cuit_partners(ventas + compras)

        # Extraer datos una sola vez por comprobante (evita doble procesamiento TXT+CSV)
        v_extracted = {m.id: self._extract_move_data(m) for m in ventas}
        c_extracted = {m.id: self._extract_move_data(m) for m in compras}

        # Ventas: procesar todas juntas
        v_cbte, v_alic, v_cuad = self._procesar_moves(
            ventas, 'ventas', v_extracted)

        # Compras: separar por letra para cuadratura
        # Por qué: Solo A/M generan crédito fiscal. B/C se informan pero no computan CF.
        compras_cf = compras.filtered(
            lambda m: m.l10n_latam_document_type_id.l10n_ar_letter in ('A', 'M')
        )
        compras_no_cf = compras - compras_cf
        c_cbte_cf, c_alic_cf, c_cuad_cf = self._procesar_moves(
            compras_cf, 'compras', c_extracted)
        c_cbte_no, c_alic_no, c_cuad_no = self._procesar_moves(
            compras_no_cf, 'compras', c_extracted)

        # TXT: combinar todas las compras (ARCA requiere todos los cbtes)
        c_cbte = c_cbte_cf + c_cbte_no
        c_alic = c_alic_cf + c_alic_no

        # CSV IVA Simple: mismos datos extraídos que TXT
        csv_data = self._generar_csvs_iva_simple(
            ventas, compras, v_extracted, c_extracted)

        # Resumen y cuadratura
        resumen = self._generar_resumen(
            ventas, compras, v_cbte, v_alic, c_cbte, c_alic,
            v_cuad, c_cuad_cf, c_cuad_no, compras_cf, compras_no_cf,
        )
        periodo = self.date_from.strftime('%Y%m')

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
            'resumen': resumen,
            'cuadratura_file': base64.b64encode(resumen.encode('utf-8')),
            'cuadratura_name': f'CUADRATURA_F2051_{periodo}.txt',
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
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 4 archivos TXT del Libro IVA Digital
            for fname, fdata in [
                (self.ventas_cbte_name, self.ventas_cbte_file),
                (self.ventas_alic_name, self.ventas_alic_file),
                (self.compras_cbte_name, self.compras_cbte_file),
                (self.compras_alic_name, self.compras_alic_file),
                (self.cuadratura_name, self.cuadratura_file),
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

        # Guardar ZIP en un attachment para descarga
        zip_data = base64.b64encode(buf.getvalue())
        periodo = self.date_from.strftime('%Y%m')
        company_name = self.env.company.name or 'EMPRESA'
        attachment = self.env['ir.attachment'].create({
            'name': f'LIBRO_IVA_DIGITAL_{company_name}_{periodo}.zip',
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

    def _procesar_moves(self, moves, tipo, extracted=None):
        """Procesa moves y genera líneas de cabecera + alícuotas.

        Args:
            tipo: 'ventas' o 'compras' (determina el formato de salida).
            extracted: dict {move_id: data} pre-extraído (evita doble cálculo).
        Returns:
            tuple: (lista_cabecera, lista_alicuotas, datos_cuadratura)
            Por qué: datos_cuadratura acumula totales para el resumen
            de cuadratura que permite verificar contra la DDJJ F.2051.
        """
        cbte_lines = []
        alic_lines = []
        errores = []
        # Acumuladores para cuadratura
        cuad = {
            'total': 0.0, 'no_gravado': 0.0, 'exento': 0.0,
            'perc_no_categ': 0.0, 'perc_iva': 0.0,
            'perc_nacionales': 0.0, 'perc_iibb': 0.0,
            'perc_mun': 0.0, 'imp_internos': 0.0,
            'otros_tributos': 0.0,
            'iva_by_code': {},  # {code: {base, amount}}
        }

        for move in moves:
            try:
                # Usar datos pre-extraídos si están disponibles
                data = extracted[move.id] if extracted else \
                    self._extract_move_data(move)

                # Acumular datos para cuadratura
                for key in ('total', 'no_gravado', 'exento', 'perc_no_categ',
                            'perc_iva', 'perc_nacionales', 'perc_iibb',
                            'perc_mun', 'imp_internos', 'otros_tributos'):
                    cuad[key] += data[key]
                for alic in data['iva_alicuotas']:
                    code = alic['code']
                    if code not in cuad['iva_by_code']:
                        cuad['iva_by_code'][code] = {'base': 0.0, 'amount': 0.0}
                    cuad['iva_by_code'][code]['base'] += alic['base']
                    cuad['iva_by_code'][code]['amount'] += alic['amount']

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
        return cbte_lines, alic_lines, cuad

    # -------------------------------------------------------------------------
    # EXTRACCIÓN DE DATOS DE UN MOVE
    # -------------------------------------------------------------------------

    def _extract_move_data(self, move):
        """Extrae y clasifica todos los importes de un comprobante.

        Por qué: Separa IVA gravado (→ archivo alícuotas), no gravado,
        exento, percepciones y otros tributos (→ archivo cabecera).
        IMPORTANTE: Todos los importes se expresan en moneda de la factura
        (amount_currency / price_subtotal), NO en moneda compañía (balance),
        para consistencia con amount_total. Si la factura es en USD,
        balance está en ARS y amount_currency en USD.
        """
        # Por qué: NC tienen importes positivos en Odoo, pero negativos en el TXT
        sign = -1 if move.move_type in ('out_refund', 'in_refund') else 1

        result = {
            'total': move.amount_total * sign,
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

        # Paso 1: Clasificar líneas de producto → no_gravado / exento / gravado
        # Por qué: price_subtotal está en moneda factura, consistente con amount_total
        # Para gravado, se acumula la base por código IVA desde las líneas de producto
        # en vez de usar tax_base_amount (que está en moneda compañía).
        # Patrón: Una sola pasada alimenta iva_bases (para TXT) e iva_by_concepto (para CSV).
        iva_bases = {}
        iva_by_concepto = {}
        # Por qué: en Odoo 19 display_type='product' para líneas de producto.
        # Filtro explícito: solo líneas de producto (excluye section, note, rounding).
        for line in move.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product'
        ):
            line_class = self._classify_line_iva(line)
            if line_class == 'no_gravado':
                result['no_gravado'] += line.price_subtotal * sign
            elif line_class == 'exento':
                result['exento'] += line.price_subtotal * sign
            elif line_class == 'gravado':
                # Acumular base por código IVA en moneda factura
                for tax in line.tax_ids:
                    code = self._get_vat_afip_code(tax)
                    if code and code in self.IVA_GRAVADO_CODES:
                        bal = line.price_subtotal * sign
                        iva_bases.setdefault(code, 0.0)
                        iva_bases[code] += bal
                        # Concepto ARCA: 1=Bienes, 2=Locaciones, 3=Servicios
                        # Por qué: Todo como 1 (bienes) para que el total CSV
                        # coincida con el total TXT sin ambigüedad.
                        ckey = ('1', code)
                        if ckey not in iva_by_concepto:
                            iva_by_concepto[ckey] = {'base': 0.0, 'amount': 0.0}
                        iva_by_concepto[ckey]['base'] += bal
                        break  # Solo un IVA gravado por línea

        # Paso 2: Importes de IVA y otros impuestos desde tax lines
        # Por qué: amount_currency está en moneda factura.
        # balance está en moneda compañía → NO usar para importes del TXT.
        iva_amounts = {}
        for line in move.line_ids.filtered(lambda l: l.tax_line_id):
            tax = line.tax_line_id
            vat_code = self._get_vat_afip_code(tax)

            if vat_code and vat_code in self.IVA_GRAVADO_CODES:
                # IVA gravado → monto en moneda factura
                iva_amounts.setdefault(vat_code, 0.0)
                iva_amounts[vat_code] += abs(line.amount_currency) * sign
            elif vat_code in ('0001', '0002'):
                # No gravado / exento ya computados en paso 1
                pass
            else:
                # Impuesto no-IVA → moneda factura
                cat = self._classify_non_iva_tax(tax.tax_group_id)
                result[cat] += abs(line.amount_currency) * sign

        # Paso 3: Armar alícuotas combinando bases (paso 1) y montos (paso 2)
        # Por qué: Odoo calcula IVA por línea (redondeo individual), pero AFIP
        # valida round(base_total * tasa, 2) == iva_total. Se ajusta base/monto
        # para satisfacer esa validación manteniendo base + monto constante.
        all_codes = sorted(set(list(iva_bases.keys()) + list(iva_amounts.keys())))
        for code in all_codes:
            base = iva_bases.get(code, 0.0)
            amount = iva_amounts.get(code, 0.0)
            base, amount = self._ajustar_redondeo_iva(base, amount, code, sign)
            result['iva_alicuotas'].append({
                'code': code, 'base': base, 'amount': amount,
            })

        # Recalcular IVA en iva_by_concepto para consistencia CSV ↔ TXT
        # Por qué: Misma fórmula que iva_alicuotas asegura importes idénticos
        for ckey, cdata in iva_by_concepto.items():
            rate = self.IVA_RATES.get(ckey[1], 0.0)
            if rate > 0:
                cdata['amount'] = round(cdata['base'] * rate, 2)
        result['iva_by_concepto'] = iva_by_concepto

        return result

    def _ajustar_redondeo_iva(self, base, amount, code, sign):
        """Ajusta base/monto IVA para satisfacer validación AFIP.

        Por qué: Odoo calcula IVA por línea → sum(round(linea*tasa)) puede
        diferir de round(sum(lineas)*tasa). AFIP valida lo segundo.
        Dos estrategias:
        1. Mantener base+monto constante (preserva total cabecera).
        2. Si no hay solución, ajustar base ±0.01 manteniendo el monto IVA
           real de Odoo. Prioriza que cuadre en alícuotas; el centavo
           de diferencia en total de cabecera es aceptado por AFIP.
        """
        rate = self.IVA_RATES.get(code, 0.0)
        if not rate:
            return base, amount

        abs_base = abs(base)
        abs_amount = abs(amount)
        s = round(abs_base + abs_amount, 2)

        # Verificar si ya cuadra
        if round(abs_base * rate, 2) == abs_amount:
            return base, amount

        # Estrategia 1: mantener sum constante (no cambia total cabecera)
        # Por qué: partir de s/(1+tasa) y buscar ±1.00 cubre redondeos
        # por acumulación de muchas líneas.
        target = s / (1 + rate)
        for cents in range(0, 101):
            deltas = (0.0,) if cents == 0 else (cents * 0.01, -cents * 0.01)
            for delta in deltas:
                test_base = round(target + delta, 2)
                test_amount = round(s - test_base, 2)
                if test_base >= 0 and test_amount >= 0 \
                        and round(test_base * rate, 2) == test_amount:
                    return test_base * sign, test_amount * sign

        # Estrategia 2: ajustar base para que round(base*tasa)==amount
        # Por qué: cuando la estrategia 1 no encuentra solución (ej: base
        # 1500.50 + IVA 315.11 al 21%), se busca la base más cercana que
        # satisfaga la validación AFIP manteniendo el monto IVA real.
        # El centavo de diferencia en base se absorbe en el total.
        for cents in range(1, 101):
            for delta in (cents * 0.01, -cents * 0.01):
                test_base = round(abs_base + delta, 2)
                if test_base >= 0 \
                        and round(test_base * rate, 2) == abs_amount:
                    return test_base * sign, amount

        # Último recurso: recalcular monto IVA desde base
        # Por qué: garantiza que AFIP acepte la alícuota.
        # La diferencia de centavo se absorbe en total de cabecera.
        return base, round(abs_base * rate, 2) * sign

    def _classify_line_iva(self, line):
        """Clasifica una línea de factura: gravado / exento / no_gravado.

        Por qué: Se determina por el tipo de IVA aplicado a la línea.
        Si no tiene IVA → no_gravado (concepto sin impuesto).
        """
        for tax in line.tax_ids:
            code = self._get_vat_afip_code(tax)
            if code == '0002':
                return 'exento'
            elif code == '0001':
                return 'no_gravado'
            elif code in self.IVA_GRAVADO_CODES:
                return 'gravado'
        return 'no_gravado'

    def _get_vat_afip_code(self, tax):
        """Obtiene el código AFIP de alícuota IVA de un impuesto.

        Por qué: l10n_ar guarda el código como 1 dígito ('5' para 21%),
        pero ARCA necesita 4 dígitos ('0005'). Se normaliza con zfill(4).
        Fallback: mapear por monto del impuesto si no tiene código.
        """
        tax_group = tax.tax_group_id
        code = getattr(tax_group, 'l10n_ar_vat_afip_code', False)
        if code:
            # Por qué: l10n_ar usa '5', ARCA necesita '0005'
            return str(code).zfill(4)

        # Por qué: si el grupo tiene código de tributo (06=perc IVA,
        # 07=IIBB, 08=municipal, etc.), NO es alícuota IVA.
        # Sin este filtro, una percepción al 5% matchea IVA 5% (0008).
        tribute_code = getattr(tax_group, 'l10n_ar_tribute_afip_code', None)
        if tribute_code:
            return False

        # Fallback: mapear por monto solo para impuestos sin código tributo
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
    # VALIDACIÓN DE DATOS
    # -------------------------------------------------------------------------

    def _validar_cuit_partners(self, moves):
        """Valida CUIT de todos los partners antes de generar archivos.

        Por qué: AFIP rechaza registros con CUIT inválida (dígito verificador
        incorrecto, longitud ≠ 11, o vacío). Se valida antes de generar
        para dar un listado claro de lo que hay que corregir.
        """
        errores = []
        partners_vistos = set()
        for move in moves:
            partner = move.commercial_partner_id
            if partner.id in partners_vistos:
                continue
            partners_vistos.add(partner.id)

            doc_type = partner.l10n_latam_identification_type_id
            doc_code = str(getattr(doc_type, 'l10n_ar_afip_code', '99') or '99')

            # Solo validar CUIT (código 80)
            if doc_code != '80':
                continue

            vat = (partner.vat or '').replace('-', '').replace(' ', '')
            error = self._validar_cuit(vat)
            if error:
                errores.append(
                    f'  - {partner.name} (ID {partner.id}): {error}'
                )

        if errores:
            raise UserError(
                f'Proveedores/Clientes con CUIT inválida ({len(errores)}):\n'
                + '\n'.join(errores)
                + '\n\nCorrija los datos en la ficha del contacto antes de generar.'
            )

    def _validar_cuit(self, cuit):
        """Valida formato y dígito verificador de CUIT argentina.

        Por qué: Algoritmo oficial AFIP módulo 11.
        CUIT = XX-XXXXXXXX-V donde V es dígito verificador.
        Multiplicadores: [5,4,3,2,7,6,5,4,3,2] sobre los 10 primeros dígitos.
        """
        if not cuit or cuit == '0':
            return 'CUIT vacía'

        # Solo dígitos
        digits = ''.join(c for c in cuit if c.isdigit())
        if len(digits) != 11:
            return f'CUIT "{cuit}" debe tener 11 dígitos (tiene {len(digits)})'

        # Dígito verificador módulo 11
        mult = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
        total = sum(int(digits[i]) * mult[i] for i in range(10))
        resto = total % 11
        verificador = 11 - resto if resto > 1 else (0 if resto == 0 else 9)

        if verificador != int(digits[10]):
            return f'CUIT "{cuit}" dígito verificador inválido'

        return None

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

    # -------------------------------------------------------------------------
    # CSV IVA SIMPLE — APERTURA OTROS CONCEPTOS (F.2051)
    # -------------------------------------------------------------------------

    def _get_tipo_sujeto(self, partner):
        """Tipo sujeto comprador desde responsabilidad AFIP.

        Por qué: ARCA IVA Simple agrupa ventas por tipo de comprador.
        Fallback: '3' (CF/Exentos/NA) si no tiene responsabilidad configurada.
        """
        resp = partner.l10n_ar_afip_responsibility_type_id
        # Por qué: En Odoo 19 el campo puede ser l10n_ar_afip_code o code
        code = str(
            getattr(resp, 'l10n_ar_afip_code', False)
            or getattr(resp, 'code', False)
            or ''
        )
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

        df_bin = self._csv_debito_fiscal(ventas_fac, v_extracted)
        rdf_bin = self._csv_rest_debito_fiscal(ventas_nc, v_extracted)
        cf_bin = self._csv_credito_fiscal(compras_fac, c_extracted)
        rcf_bin = self._csv_rest_credito_fiscal(compras_nc, c_extracted)

        return {
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

    def _afip_code_1d(self, code_4d):
        """Convierte código AFIP 4 dígitos → 1 dígito para CSV.

        Por qué: v19 usa '0005', CSV ARCA espera '5'.
        """
        return code_4d.lstrip('0') or '0'

    def _csv_debito_fiscal(self, moves, extracted):
        """CSV 1: Débito fiscal — facturas + ND de venta.

        Por qué: Agrupa por (actividad, tipo_sujeto, alícuota).
        8 columnas según modelo ARCA. Alícuota = código AFIP (1 dígito).
        tipo_op 1 = gravado, tipo_op 3 = exento/no gravado.
        """
        act = self.actividad_afip or '465320'
        acum = {}
        exento_ng = 0.0

        for move in moves:
            data = extracted[move.id]
            sujeto = self._get_tipo_sujeto(move.commercial_partner_id)

            # Gravado: una línea por alícuota × sujeto
            for alic in data['iva_alicuotas']:
                code = self._afip_code_1d(alic['code'])
                key = (act, '1', sujeto, code)
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
            lines.append(
                f'{key[0]};{tipo_op};{sujeto};{code};'
                f'{fmt(vals["neto"])};{fmt(vals["iva"])};0'
            )

        # Exento/no gravado: tipo_op 3, cols 3-7 vacías, monto en col 8
        if exento_ng > 0.005:
            lines.append(f'{act};3;;;;;;{fmt(exento_ng)}')

        return self._encode_lines(lines) if len(lines) > 1 else False

    def _csv_rest_debito_fiscal(self, moves, extracted):
        """CSV 2: Restitución débito fiscal — NC de venta.

        Por qué: Mismo esquema que CSV 1 pero sin campo O.D.P. (7 cols).
        tipo_op 2 para exento/NG en restitución. Importes en valor absoluto.
        """
        act = self.actividad_afip or '465320'
        acum = {}
        exento_ng = 0.0

        for move in moves:
            data = extracted[move.id]
            sujeto = self._get_tipo_sujeto(move.commercial_partner_id)

            for alic in data['iva_alicuotas']:
                code = self._afip_code_1d(alic['code'])
                key = (act, '1', sujeto, code)
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                acum[key]['neto'] += alic['base']
                acum[key]['iva'] += alic['amount']

            monto_exng = data['exento'] + data['no_gravado']
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

        # Por qué: En restitución, ARCA usa tipo_op 2 para exento/NG (no 3)
        if exento_ng > 0.005:
            lines.append(f'{act};2;;;;;{fmt(exento_ng)}')

        return self._encode_lines(lines) if len(lines) > 1 else False

    def _csv_credito_fiscal(self, moves, extracted):
        """CSV 3: Crédito fiscal — facturas + ND de compra.

        Por qué: Agrupa por (concepto, alícuota). concepto = tipo de bien:
        1=bienes, 3=servicios. Usa iva_by_concepto de _extract_move_data.
        Formato (5 cols): concepto;code_afip;neto;cf_facturado;cf_computable
        """
        acum = {}

        for move in moves:
            by_concepto = extracted[move.id]['iva_by_concepto']
            for key, vals in by_concepto.items():
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                acum[key]['neto'] += vals['base']
                acum[key]['iva'] += vals['amount']

        lines = [self._CSV_HEADER_CREDITO]
        fmt = self._fmt_csv_amount
        for key in sorted(acum.keys()):
            concepto, code_4d = key
            code = self._afip_code_1d(code_4d)
            vals = acum[key]
            # credito_computable = credito_facturado (sin prorrateo)
            lines.append(
                f'{concepto};{code};{fmt(vals["neto"])};'
                f'{fmt(vals["iva"])};{fmt(vals["iva"])}'
            )

        return self._encode_lines(lines) if len(lines) > 1 else False

    def _csv_rest_credito_fiscal(self, moves, extracted):
        """CSV 4: Restitución crédito fiscal — NC de compra.

        Por qué: Igual que CSV 3 pero sin campo credito_computable (4 cols).
        Formato: concepto;code_afip;neto;credito_facturado
        """
        acum = {}

        for move in moves:
            by_concepto = extracted[move.id]['iva_by_concepto']
            for key, vals in by_concepto.items():
                if key not in acum:
                    acum[key] = {'neto': 0.0, 'iva': 0.0}
                acum[key]['neto'] += vals['base']
                acum[key]['iva'] += vals['amount']

        lines = [self._CSV_HEADER_REST_CREDITO]
        fmt = self._fmt_csv_amount
        for key in sorted(acum.keys()):
            concepto, code_4d = key
            code = self._afip_code_1d(code_4d)
            vals = acum[key]
            lines.append(
                f'{concepto};{code};{fmt(vals["neto"])};{fmt(vals["iva"])}'
            )

        return self._encode_lines(lines) if len(lines) > 1 else False

    # -------------------------------------------------------------------------
    # RESUMEN Y CUADRATURA
    # -------------------------------------------------------------------------

    def _generar_resumen(self, ventas, compras, v_cbte, v_alic, c_cbte, c_alic,
                         v_cuad, c_cuad_cf, c_cuad_no, compras_cf, compras_no_cf):
        """Genera resumen de cuadratura para verificar contra DDJJ F.2051.

        Por qué: Los totales de débito/crédito fiscal por alícuota deben
        coincidir con los campos del F.2051. Solo compras A/M generan CF.
        Compras B/C se informan en el Libro IVA pero no computan crédito.
        """
        def fmt(amount):
            """Formatea importe con separador de miles y 2 decimales."""
            sign = '-' if amount < 0 else ''
            abs_val = abs(amount)
            return f'{sign}{abs_val:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

        def iva_table(iva_by_code, label_fiscal):
            """Genera tabla de alícuotas IVA con totales."""
            lines = []
            total_base = 0.0
            total_fiscal = 0.0
            # Ordenar por código AFIP
            for code in sorted(iva_by_code.keys()):
                vals = iva_by_code[code]
                name = self.IVA_NAMES.get(code, code)
                lines.append(
                    f'  IVA {name:>5}  '
                    f'Neto: {fmt(vals["base"]):>16}  '
                    f'{label_fiscal}: {fmt(vals["amount"]):>16}'
                )
                total_base += vals['base']
                total_fiscal += vals['amount']
            lines.append(f'  {"─" * 58}')
            lines.append(
                f'  TOTAL      '
                f'Neto: {fmt(total_base):>16}  '
                f'{label_fiscal}: {fmt(total_fiscal):>16}'
            )
            return '\n'.join(lines), total_base, total_fiscal

        # Cabecera
        r = (
            f'{"═" * 60}\n'
            f'  CUADRATURA LIBRO IVA DIGITAL - F.2051\n'
            f'{"═" * 60}\n'
            f'Período: {self.date_from.strftime("%d/%m/%Y")} - '
            f'{self.date_to.strftime("%d/%m/%Y")}\n'
            f'Empresa: {self.env.company.name}\n'
            f'CUIT: {self.env.company.vat or "Sin configurar"}\n'
        )

        # Archivos generados
        r += (
            f'\nArchivos: {len(v_cbte)} líneas ventas cbte, '
            f'{len(v_alic)} ventas alíc, '
            f'{len(c_cbte)} compras cbte, '
            f'{len(c_alic)} compras alíc\n'
        )

        # Ventas (Débito Fiscal)
        v_iva_table, _v_neto, v_df = iva_table(v_cuad['iva_by_code'], 'DF')
        r += (
            f'\n{"─" * 60}\n'
            f'  VENTAS (Débito Fiscal)\n'
            f'{"─" * 60}\n'
            f'Comprobantes: {len(ventas)}\n\n'
            f'{v_iva_table}\n\n'
            f'  Op. Exentas:          {fmt(v_cuad["exento"]):>16}\n'
            f'  Op. No Gravadas:      {fmt(v_cuad["no_gravado"]):>16}\n'
            f'  Perc. no categ:       {fmt(v_cuad["perc_no_categ"]):>16}\n'
            f'  Perc. nacionales:     {fmt(v_cuad["perc_nacionales"]):>16}\n'
            f'  Perc. IIBB:           {fmt(v_cuad["perc_iibb"]):>16}\n'
            f'  Perc. municipales:    {fmt(v_cuad["perc_mun"]):>16}\n'
            f'  Imp. internos:        {fmt(v_cuad["imp_internos"]):>16}\n'
            f'  Otros tributos:       {fmt(v_cuad["otros_tributos"]):>16}\n'
            f'  {"─" * 42}\n'
            f'  TOTAL VENTAS:         {fmt(v_cuad["total"]):>16}\n'
        )

        # Compras con Crédito Fiscal (A/M)
        # Por qué: Solo facturas A y M generan crédito fiscal computable
        cf_iva_table, _cf_neto, c_cf = iva_table(c_cuad_cf['iva_by_code'], 'CF')
        r += (
            f'\n{"─" * 60}\n'
            f'  COMPRAS con Crédito Fiscal (A/M)\n'
            f'{"─" * 60}\n'
            f'Comprobantes: {len(compras_cf)}\n\n'
            f'{cf_iva_table}\n\n'
            f'  Op. Exentas:          {fmt(c_cuad_cf["exento"]):>16}\n'
            f'  Op. No Gravadas:      {fmt(c_cuad_cf["no_gravado"]):>16}\n'
            f'  Perc. IVA sufridas:   {fmt(c_cuad_cf["perc_iva"]):>16}\n'
            f'  Perc. nacionales:     {fmt(c_cuad_cf["perc_nacionales"]):>16}\n'
            f'  Perc. IIBB:           {fmt(c_cuad_cf["perc_iibb"]):>16}\n'
            f'  Perc. municipales:    {fmt(c_cuad_cf["perc_mun"]):>16}\n'
            f'  Imp. internos:        {fmt(c_cuad_cf["imp_internos"]):>16}\n'
            f'  Otros tributos:       {fmt(c_cuad_cf["otros_tributos"]):>16}\n'
            f'  {"─" * 42}\n'
            f'  SUBTOTAL A/M:         {fmt(c_cuad_cf["total"]):>16}\n'
        )

        # Compras sin Crédito Fiscal (B/C)
        # Por qué: Facturas B/C no discriminan IVA, no generan CF
        r += (
            f'\n{"─" * 60}\n'
            f'  COMPRAS sin Crédito Fiscal (B/C)\n'
            f'{"─" * 60}\n'
            f'Comprobantes: {len(compras_no_cf)}\n'
            f'  Total (no genera CF): {fmt(c_cuad_no["total"]):>16}\n'
            f'  Op. No Gravadas:      {fmt(c_cuad_no["no_gravado"]):>16}\n'
            f'  Op. Exentas:          {fmt(c_cuad_no["exento"]):>16}\n'
        )

        # Total compras
        total_compras = c_cuad_cf['total'] + c_cuad_no['total']
        r += (
            f'\n  {"═" * 42}\n'
            f'  TOTAL COMPRAS:        {fmt(total_compras):>16}\n'
        )

        # Balance F.2051
        saldo = v_df - c_cf
        estado = 'A PAGAR' if saldo >= 0 else 'A FAVOR'
        r += (
            f'\n{"═" * 60}\n'
            f'  BALANCE F.2051\n'
            f'{"═" * 60}\n'
            f'  Débito Fiscal:        {fmt(v_df):>16}\n'
            f'  Crédito Fiscal (A/M): {fmt(c_cf):>16}\n'
            f'  {"─" * 42}\n'
            f'  SALDO:                {fmt(saldo):>16}  ({estado})\n'
            f'\n  * Solo compras A/M generan crédito fiscal.\n'
            f'  * Compras B/C se informan pero no computan CF.\n'
        )

        return r
