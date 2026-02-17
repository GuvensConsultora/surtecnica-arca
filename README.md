# Surtecnica ARCA - Libro IVA Digital

Módulo Odoo 17.0 Enterprise para generar los archivos TXT del **Libro IVA Digital** según el Anexo I - Diseños de Registros oficial de ARCA (ex AFIP).

## Contexto

El **Libro IVA Digital** es la presentación obligatoria ante ARCA donde se informan todas las operaciones de compra y venta con su detalle de IVA. Se presenta mensualmente mediante archivos TXT de posición fija que se cargan en la página de ARCA para la DDJJ de IVA.

## Archivos Generados

| Archivo | Longitud | Campos | Descripción |
|---------|----------|--------|-------------|
| `LIBRO_IVA_DIGITAL_VENTAS_CBTE` | 266 chars | 22 | Cabecera de comprobantes de venta |
| `LIBRO_IVA_DIGITAL_VENTAS_ALICUOTAS` | 62 chars | 6 | Alícuotas IVA de ventas |
| `LIBRO_IVA_DIGITAL_COMPRAS_CBTE` | 325 chars | 25 | Cabecera de comprobantes de compra |
| `LIBRO_IVA_DIGITAL_COMPRAS_ALICUOTAS` | 84 chars | 8 | Alícuotas IVA de compras |

## Diseño de Registros

### Ventas Cabecera (266 chars)

| # | Pos | Cant | Tipo | Campo | Formato |
|---|-----|------|------|-------|---------|
| 1 | 1-8 | 8 | Num | Fecha comprobante | AAAAMMDD |
| 2 | 9-11 | 3 | Num | Tipo comprobante | Tabla Comprobantes |
| 3 | 12-16 | 5 | Num | Punto de venta | Ceros izq |
| 4 | 17-36 | 20 | Num | Número comprobante | Ceros izq |
| 5 | 37-56 | 20 | Num | Número comprobante hasta | Ceros izq |
| 6 | 57-58 | 2 | Num | Código doc comprador | Tabla Documentos |
| 7 | 59-78 | 20 | Alfanum | Nro identificación comprador | Ceros izq |
| 8 | 79-108 | 30 | Alfanum | Nombre comprador | Espacios der |
| 9 | 109-123 | 15 | Num | Importe total | 13ent+2dec sin punto |
| 10 | 124-138 | 15 | Num | No gravado | 13ent+2dec sin punto |
| 11 | 139-153 | 15 | Num | Percepción no categorizados | 13ent+2dec sin punto |
| 12 | 154-168 | 15 | Num | Operaciones exentas | 13ent+2dec sin punto |
| 13 | 169-183 | 15 | Num | Percepciones nacionales | 13ent+2dec sin punto |
| 14 | 184-198 | 15 | Num | Percepciones IIBB | 13ent+2dec sin punto |
| 15 | 199-213 | 15 | Num | Percepciones municipales | 13ent+2dec sin punto |
| 16 | 214-228 | 15 | Num | Impuestos internos | 13ent+2dec sin punto |
| 17 | 229-231 | 3 | Alfanum | Código moneda | Tabla Monedas |
| 18 | 232-241 | 10 | Num | Tipo de cambio | 4ent+6dec sin punto |
| 19 | 242 | 1 | Num | Cantidad alícuotas IVA | |
| 20 | 243 | 1 | Alfa | Código operación | Tabla Operaciones |
| 21 | 244-258 | 15 | Num | Otros tributos | 13ent+2dec sin punto |
| 22 | 259-266 | 8 | Num | Fecha vencimiento/pago | AAAAMMDD |

### Ventas Alícuotas (62 chars)

| # | Pos | Cant | Tipo | Campo |
|---|-----|------|------|-------|
| 1 | 1-3 | 3 | Num | Tipo comprobante |
| 2 | 4-8 | 5 | Num | Punto de venta |
| 3 | 9-28 | 20 | Num | Número comprobante |
| 4 | 29-43 | 15 | Num | Importe neto gravado |
| 5 | 44-47 | 4 | Num | Alícuota IVA (tabla) |
| 6 | 48-62 | 15 | Num | Impuesto liquidado |

### Compras Cabecera (325 chars)

| # | Pos | Cant | Tipo | Campo |
|---|-----|------|------|-------|
| 1 | 1-8 | 8 | Num | Fecha comprobante |
| 2 | 9-11 | 3 | Num | Tipo comprobante |
| 3 | 12-16 | 5 | Num | Punto de venta |
| 4 | 17-36 | 20 | Num | Número comprobante |
| 5 | 37-52 | 16 | Alfanum | Despacho importación |
| 6 | 53-54 | 2 | Num | Código doc vendedor |
| 7 | 55-74 | 20 | Alfanum | Nro identificación vendedor |
| 8 | 75-104 | 30 | Alfanum | Nombre vendedor |
| 9-16 | ... | 15 c/u | Num | Importes (total, no gravado, exentas, perc IVA, perc nac, perc IIBB, perc mun, imp internos) |
| 17 | 225-227 | 3 | Alfanum | Código moneda |
| 18 | 228-237 | 10 | Num | Tipo de cambio |
| 19 | 238 | 1 | Num | Cantidad alícuotas IVA |
| 20 | 239 | 1 | Alfa | Código operación |
| 21 | 240-254 | 15 | Num | Crédito fiscal computable |
| 22 | 255-269 | 15 | Num | Otros tributos |
| 23 | 270-280 | 11 | Num | CUIT emisor/corredor |
| 24 | 281-310 | 30 | Alfanum | Denominación emisor/corredor |
| 25 | 311-325 | 15 | Num | IVA comisión |

### Compras Alícuotas (84 chars)

| # | Pos | Cant | Tipo | Campo |
|---|-----|------|------|-------|
| 1 | 1-3 | 3 | Num | Tipo comprobante |
| 2 | 4-8 | 5 | Num | Punto de venta |
| 3 | 9-28 | 20 | Num | Número comprobante |
| 4 | 29-30 | 2 | Num | Código doc vendedor |
| 5 | 31-50 | 20 | Alfanum | Nro identificación vendedor |
| 6 | 51-65 | 15 | Num | Importe neto gravado |
| 7 | 66-69 | 4 | Num | Alícuota IVA (tabla) |
| 8 | 70-84 | 15 | Num | Impuesto liquidado |

## Tablas AFIP

### Alícuotas IVA

| Código | Alícuota |
|--------|----------|
| 0003 | 0% |
| 0004 | 10.5% |
| 0005 | 21% |
| 0006 | 27% |
| 0008 | 5% |
| 0009 | 2.5% |

### Códigos Documento

| Código | Tipo |
|--------|------|
| 80 | CUIT |
| 86 | CUIL |
| 87 | CDI |
| 96 | DNI |
| 99 | Sin identificar / Consumidor Final |

### Códigos Operación

| Código | Descripción |
|--------|-------------|
| (espacio) | Operación gravada |
| E | Operación exenta |
| N | No gravado |
| X | Exportación al exterior |
| Z | Exportación zona franca |

### Formato de Importes

- **Importes monetarios** (15 chars): 13 enteros + 2 decimales, **sin punto decimal**
  - `$1500.00` → `000000000150000`
  - `-$1500.00` (NC) → `-00000000150000`
- **Tipo de cambio** (10 chars): 4 enteros + 6 decimales, **sin punto decimal**
  - `1.0` → `0001000000`
- Campos numéricos: ceros a la izquierda
- Campos alfanuméricos: espacios a la derecha

## Explicación Técnica

### Flujo de Datos

```
Wizard: Seleccionar período (desde/hasta)
        ↓
_get_moves('out')  →  Facturas de venta posted con doc fiscal
_get_moves('in')   →  Facturas de compra posted con doc fiscal
        ↓
_extract_move_data(move)  →  Clasifica líneas e impuestos:
  - invoice_line_ids → gravado / exento / no_gravado
  - tax lines con l10n_ar_vat_afip_code → IVA alícuotas
  - tax lines sin código IVA → percepciones / otros
        ↓
_fmt_ventas_cbte()  →  Línea posición fija 266 chars
_fmt_ventas_alic()  →  Línea posición fija 62 chars (una por alícuota)
_fmt_compras_cbte() →  Línea posición fija 325 chars
_fmt_compras_alic() →  Línea posición fija 84 chars (una por alícuota)
        ↓
4 archivos TXT + ZIP para descarga
```

### Clasificación de Impuestos

El método `_extract_move_data()` clasifica cada tax line:

1. **IVA gravado** (afip_code 0003-0009): va al archivo de alícuotas
2. **No gravado** (afip_code 0001): va a cabecera campo "no gravado"
3. **Exento** (afip_code 0002): va a cabecera campo "exentas"
4. **Percepciones/otros**: clasificados por `l10n_ar_tribute_afip_code` o nombre del tax group

### Mapeo Odoo → ARCA

| Dato | Campo Odoo | Campo ARCA |
|------|-----------|------------|
| Tipo comprobante | `l10n_latam_document_type_id.code` | Campo 2 (3 dígitos) |
| Punto de venta | `l10n_latam_document_number` split('-')[0] | Campo 3 (5 dígitos) |
| Número cbte | `l10n_latam_document_number` split('-')[1] | Campo 4 (20 dígitos) |
| Código doc partner | `partner.l10n_latam_identification_type_id.l10n_ar_afip_code` | Campo 6 |
| Nro doc partner | `partner.vat` | Campo 7 |
| Moneda | `currency_id.l10n_ar_afip_code` | Campo 17 |
| Tipo cambio | `l10n_ar_currency_rate` | Campo 18 |
| Alícuota IVA | `tax.tax_group_id.l10n_ar_vat_afip_code` | Alícuotas campo 5/7 |

## Estructura del Módulo

```
surtecnica_arca/
├── __manifest__.py
├── __init__.py
├── wizard/
│   ├── __init__.py
│   ├── libro_iva_digital_wizard.py       # Lógica de generación TXT
│   └── libro_iva_digital_wizard_views.xml
├── views/
│   └── menu_views.xml                    # Contabilidad > Informes > ARCA
├── security/
│   └── ir.model.access.csv
└── static/description/
    └── icon.png
```

## Dependencias

| Módulo | Razón |
|--------|-------|
| `account` | account.move, account.tax, menús contables |
| `l10n_ar` | CUIT, tipos documento AFIP, códigos moneda, alícuotas IVA |

## Instalación

1. Copiar `surtecnica_arca` en la carpeta de addons
2. Actualizar lista de apps
3. Buscar "ARCA" o "Libro IVA" e instalar

## Uso

1. **Contabilidad > Informes > ARCA > Libro IVA Digital**
2. Seleccionar período (desde / hasta)
3. Click **Generar Archivos**
4. Descargar los 4 TXT individualmente o el ZIP completo
5. Cargar los archivos en la página de ARCA para la DDJJ de IVA

## Reporte DDJJ IVA (F.2002)

Al generar los archivos, la primera pestaña muestra una previsualización de cómo debe quedar cargada la DDJJ IVA en el portal ARCA:

- **Comprobantes Emitidos** — agrupados por tipo (FA-A, FA-B, NC-A, etc.) con cantidad, neto gravado, débito fiscal, no gravado, exento y total
- **Detalle Alícuotas IVA - Débito Fiscal** — por alícuota (0%, 5%, 10.5%, 21%, 27%) con base imponible y débito
- **Comprobantes Recibidos** — misma estructura con crédito fiscal
- **Detalle Alícuotas IVA - Crédito Fiscal** — por alícuota con base y crédito
- **Determinación del Impuesto** — débito - crédito = subtotal, percepciones IVA sufridas, saldo a pagar/favor
- **Otros Tributos** (informativo) — IIBB, municipales, nacionales, internos

## Bugs corregidos

### Percepciones clasificadas como IVA gravado (v1.1.0)

**Problema:** `_get_vat_afip_code()` tenía un fallback que mapeaba cualquier impuesto por su tasa porcentual (ej: 5% → código '8', 21% → código '5'). Cuando una percepción IIBB al 5% o una retención IVA al 21% no tenía `l10n_ar_vat_afip_code` en su tax group, el fallback la clasificaba como IVA gravado. Esto agregaba su `tax_base_amount` (el total de la factura) como neto gravado, inflando enormemente los importes.

**Síntoma en ARCA:** `El Importe Total (242) no coincide con la suma de los demás montos (360580)` — diferencias de órdenes de magnitud.

**Fix:** Antes del fallback por monto, verificar:
1. Si el tax group tiene `l10n_ar_tribute_afip_code` → no es IVA
2. Si el nombre del grupo contiene "percep", "reten", "iibb", "munic", etc. → no es IVA
3. Solo entonces usar el fallback por tasa

### Total no coincide con suma de partes (v1.1.0)

**Problema:** El campo Importe Total usaba `move.amount_total` de Odoo, pero los demás campos (no gravado, exento, percepciones, etc.) se computaban desde las tax lines individuales. Cualquier diferencia de clasificación o redondeo generaba inconsistencia.

**Síntoma en ARCA:** `El Importe Total (1231730.44) no coincide con la suma de los demás montos (1201930.51)`

**Fix:** Calcular el total como suma de todas las partes: `total = gravado + iva + no_gravado + exento + percepciones + otros_tributos`. ARCA valida que `Total = suma de campos`, así que calculándolo desde los mismos campos la validación siempre pasa.

### IVA liquidado no coincide con alícuota × base (v1.1.0)

**Problema:** Odoo calcula IVA por línea de producto y luego suma. Para un comprobante con varias líneas, `sum(round(línea × 21%))` puede diferir de `round(sum(líneas) × 21%)` por centavos.

**Síntoma en ARCA:** `Para la alícuota 21% el impuesto liquidado debe ser igual al 21% del importe neto gravado a dicha alícuota`

**Fix:** Después de agregar las bases por alícuota, recalcular: `amount = round(base × rate / 100, 2)`. ARCA exige consistencia matemática exacta.

## Licencia

LGPL-3
