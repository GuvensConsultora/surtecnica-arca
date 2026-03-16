# Cruce Mis Comprobantes AFIP (guvens_mis_comprobantes)

## 1. Introducción

### Qué hace Odoo nativamente
Odoo gestiona facturas de proveedores con localización argentina (`l10n_ar`): tipo de comprobante AFIP, punto de venta, número, CUIT, CAE. Pero **no ofrece ninguna herramienta para cruzar** esas facturas contra lo que AFIP tiene registrado.

### Qué limitación resuelve
Sin cruce automático, el contador debe comparar manualmente cada factura de Odoo contra el portal de AFIP, comprobante por comprobante. En empresas con volumen esto es inviable y propenso a errores: facturas apócrifas, diferencias de importes o comprobantes faltantes pasan desapercibidos.

### Qué resuelve este módulo
Importa CSV descargados de **dos portales de AFIP** y los cruza automáticamente contra las facturas de proveedores cargadas en Odoo:

| Portal AFIP | Qué contiene | Campos extra |
|-------------|-------------|--------------|
| **Mis Comprobantes** | Comprobantes recibidos con totales | CAE, neto, exento, IVA |
| **Portal IVA — Compras** | DDJJ de IVA con desglose completo | IVA por alícuota (0%, 2.5%, 5%, 10.5%, 21%, 27%), percepciones (IIBB, IVA, municipales, internos), multi-moneda, crédito fiscal |

El formato del CSV se **auto-detecta** — el usuario solo sube el archivo.

### Qué detecta el módulo

| Estado | Color | Significado |
|--------|-------|-------------|
| **Coincide** | Verde | La factura existe en Odoo y el importe es igual al de AFIP |
| **Diferencia importe** | Amarillo | La factura existe en Odoo pero el importe no coincide |
| **Falta en Odoo** | Rojo | El comprobante está en AFIP pero no fue cargado en Odoo |
| **Falta en AFIP** | Azul | La factura está en Odoo pero no aparece en el CSV de AFIP |

---

## 2. Funcionamiento para el usuario final

### Paso 1 — Descargar CSV de AFIP

**Opción A — Mis Comprobantes:**
1. Ir a [Mis Comprobantes AFIP](https://miscomprobantes.afip.gob.ar/)
2. Seleccionar **Recibidos**
3. Filtrar por el mes deseado
4. Click en **Descargar** (formato CSV)

**Opción B — Portal IVA (Compras):**
1. Ir a AFIP → Portal IVA
2. Seleccionar **DDJJ Compras** del período
3. Descargar CSV (montos expresados en pesos)

### Paso 2 — Importar en Odoo

1. Ir a **Contabilidad → Proveedores → Importar CSV AFIP**
2. Se abre un wizard: subir el archivo CSV
3. El formato se auto-detecta y se muestra ("Portal IVA — Compras" o "Mis Comprobantes")
4. Click en **"Importar y Cruzar"**

El período se auto-detecta del nombre del archivo (ej: `comprobantes_periodo_202602_...`) o de la primera fecha del CSV.

### Paso 3 — Revisar resultados

Se abre una tabla con todos los comprobantes, agrupados por estado:

| Fecha | Origen | Proveedor | CUIT | Tipo | PV | Número | Total AFIP | Factura Odoo | Diferencia | Estado |
|-------|--------|-----------|------|------|----|--------|------------|--------------|------------|--------|
| 01/02/2026 | Portal IVA | DIMENSION SA | 30615850124 | Factura A (1) | 00400 | 00029594 | 52.900,00 | FA-A 00400-00029594 | 0,00 | Coincide |
| 01/02/2026 | Portal IVA | MARTINEZ E. | 20444064863 | Factura C (11) | 00005 | 00000049 | 195.000,00 | — | — | Falta en Odoo |

**Columnas opcionales** (activar desde el ícono de columnas):
- Código AFIP, Moneda, Crédito fiscal, Percepciones IIBB

### Qué hacer con cada estado

- **Coincide** → No requiere acción
- **Diferencia importe** → Click en "Ver factura" para revisar. Verificar impuestos, percepciones, redondeos
- **Falta en Odoo** → Falta cargar esa factura. El proveedor la emitió pero no está en el sistema
- **Falta en AFIP** → Revisar si es un comprobante apócrifo, si se cargó con datos incorrectos (CUIT, PV, número), o si es de otro período

### Vista formulario — 5 tabs

Al abrir un registro, la información se organiza en tabs:

| Tab | Contenido | Visible |
|-----|-----------|---------|
| **Comprobante** | Fecha, tipo, PV, número, CAE, CUIT, razón social, moneda, TC | Siempre |
| **Importes** | Total, neto, no gravado, exento, IVA, crédito fiscal | Siempre |
| **Desglose IVA** | Neto + IVA por alícuota: 0%, 2.5%, 5%, 10.5%, 21%, 27% | Solo Portal IVA |
| **Percepciones** | IIBB, IVA, municipales, internos, otros nacionales, otros tributos | Solo Portal IVA |
| **Cruce Odoo** | Factura vinculada, diferencia de importes | Siempre |

### Badge en facturas de proveedor

Una vez importado el cruce, cada factura de proveedor muestra un badge junto a la fecha:
- **AFIP: Coincide** (verde)
- **AFIP: Diferencia** (amarillo)
- **AFIP: No encontrado** (azul)

---

## 3. Parametrización

### Instalación

1. Ir a **Ajustes → Aplicaciones**
2. Buscar "Cruce Mis Comprobantes"
3. Instalar

### Requisitos previos
- Módulo `l10n_ar` instalado (localización argentina)
- Facturas de proveedor cargadas con:
  - Tipo de documento AFIP correcto (Factura A, NC B, etc.)
  - Número de documento completo (PV-Número, ej: 00001-00000123)
  - CUIT del proveedor cargado en el contacto

### Permisos

| Grupo | Ver resultados | Importar CSV | Editar/Eliminar |
|-------|---------------|--------------|-----------------|
| Facturación (usuario) | ✓ | ✓ | — |
| Contabilidad (administrador) | ✓ | ✓ | ✓ |

### Menú

| Menú | Ubicación | Función |
|------|-----------|---------|
| Cruce Mis Comprobantes | Contabilidad → Proveedores | Vista de resultados |
| Importar CSV AFIP | Contabilidad → Proveedores | Wizard de importación |

### Formatos CSV aceptados

**Mis Comprobantes** (16+ columnas, separador `;`):
- Fecha: `dd/mm/yyyy`
- Importes: punto = miles, coma = decimal (`10.000,00`)
- Tipo comprobante: texto libre ("Factura", "Nota de Crédito", etc.)
- Columnas: Fecha, Tipo, PV, Nro, Hasta, CAE, TipoDoc, CUIT, Denominación, TC, Moneda, Neto, NoGravado, Exento, IVA, Total

**Portal IVA — Compras** (32 columnas, separador `;`):
- Fecha: `yyyy-mm-dd`
- Importes: coma = decimal, sin separador de miles (`52900,00`)
- Tipo comprobante: código numérico AFIP (1, 3, 6, 11, etc.)
- Incluye: moneda original, tipo de cambio, crédito fiscal, 6 percepciones, IVA desglosado por 6 alícuotas

---

## 4. Referencia técnica

### Arquitectura

```
guvens_mis_comprobantes/
├── __init__.py
├── __manifest__.py
├── security/ir.model.access.csv
├── models/
│   ├── __init__.py
│   ├── mis_comprobantes_line.py    # Modelo principal: líneas importadas
│   └── account_move.py             # Herencia: link inverso + badge
├── wizard/
│   ├── __init__.py
│   └── import_mis_comprobantes.py  # Wizard: auto-detección + 2 parsers + matching
├── views/
│   ├── mis_comprobantes_views.xml  # Tree/form (notebook 5 tabs)/search
│   ├── wizard_views.xml            # Form wizard con ayuda de formatos
│   └── account_move_views.xml      # Badge en facturas proveedor
└── data/
    └── menuitem.xml
```

### Modelo: `guvens.mis.comprobantes.line`

| Campo | Tipo | Descripción | Origen |
|-------|------|-------------|--------|
| `source` | Selection | `mis_comprobantes` / `portal_iva` | Ambos |
| `date` | Date | Fecha emisión | Ambos |
| `doc_type` | Char | Nombre tipo comprobante | Ambos |
| `afip_code` | Char | Código numérico AFIP (1, 3, 6, 11...) | Portal IVA |
| `pos_number` | Char | Punto de venta | Ambos |
| `doc_number` | Char | Número comprobante | Ambos |
| `cae` | Char | Código de Autorización Electrónica | Mis Comprobantes |
| `partner_vat` | Char | CUIT emisor (solo dígitos) | Ambos |
| `partner_name` | Char | Denominación emisor | Ambos |
| `amount_total` | Float | Importe total | Ambos |
| `amount_net` | Float | Neto gravado | Ambos |
| `amount_untaxed` | Float | No gravado | Ambos |
| `amount_exempt` | Float | Exento | Ambos |
| `amount_iva` | Float | IVA total | Ambos |
| `currency_code` | Char | Moneda (PES, DOL) | Portal IVA |
| `exchange_rate` | Float | Tipo de cambio | Portal IVA |
| `credito_fiscal` | Float | Crédito fiscal computable | Portal IVA |
| `amount_perc_iibb` | Float | Percepciones IIBB | Portal IVA |
| `amount_perc_iva` | Float | Percepciones IVA | Portal IVA |
| `amount_perc_municipal` | Float | Impuestos municipales | Portal IVA |
| `amount_perc_internos` | Float | Impuestos internos | Portal IVA |
| `amount_perc_otros_nac` | Float | Otros impuestos nacionales | Portal IVA |
| `amount_otros_tributos` | Float | Otros tributos | Portal IVA |
| `neto_iva_0` | Float | Neto gravado al 0% | Portal IVA |
| `neto_iva_25` / `iva_25` | Float | Neto + IVA al 2.5% | Portal IVA |
| `neto_iva_5` / `iva_5` | Float | Neto + IVA al 5% | Portal IVA |
| `neto_iva_105` / `iva_105` | Float | Neto + IVA al 10.5% | Portal IVA |
| `neto_iva_21` / `iva_21` | Float | Neto + IVA al 21% | Portal IVA |
| `neto_iva_27` / `iva_27` | Float | Neto + IVA al 27% | Portal IVA |
| `state` | Selection | match / mismatch / missing_in_odoo / missing_in_afip | Ambos |
| `move_id` | Many2one → account.move | Factura Odoo matcheada | Ambos |
| `diff_amount` | Float (computed) | abs(total_odoo) - total_afip | Ambos |

### Herencia: `account.move`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `mis_comprobantes_line_ids` | One2many | Líneas de cruce vinculadas |
| `mis_comprobantes_state` | Selection (related) | Estado del cruce |

### Wizard: `guvens.import.mis.comprobantes`

#### Auto-detección de formato
```python
def _detect_csv_format(self, header_row):
    # "Fecha de Emisión" → portal_iva (32 columnas)
    # "Fecha" → mis_comprobantes (16+ columnas)
```

#### Parsers de importes
- `_parse_afip_amount()`: Mis Comprobantes — `10.000,00` → `10000.00` (punto=miles, coma=decimal)
- `_parse_portal_iva_amount()`: Portal IVA — `52900,00` → `52900.00` (solo coma=decimal, soporta negativos)

#### Parsers de fechas
- `_parse_afip_date()`: Mis Comprobantes — `dd/mm/yyyy`
- `_parse_portal_iva_date()`: Portal IVA — `yyyy-mm-dd`

#### Matching contra Odoo

**Mis Comprobantes** — busca por:
- `l10n_latam_document_number` exacto (PV-Nro)
- `l10n_latam_document_type_id.internal_type` (del texto: "Factura" → `invoice`)
- `partner_id.vat` = CUIT
- Tolerancia: ±$0.01

**Portal IVA** — busca por:
- `l10n_latam_document_number` exacto (PV-Nro)
- `l10n_latam_document_type_id.code` = código numérico AFIP (más preciso que texto)
- `partner_id.vat` = CUIT
- Tolerancia: ±$0.01

#### Códigos AFIP → move_type
NC (códigos 3, 8, 13, 203, 208, 213) → `in_refund`. El resto → `in_invoice`.

#### Mapeo tipo comprobante Mis Comprobantes → Odoo

| Texto CSV AFIP | move_type Odoo | internal_type |
|----------------|----------------|---------------|
| Factura | in_invoice | invoice |
| Nota de Débito | in_invoice | debit_note |
| Nota de Crédito | in_refund | credit_note |
| Recibo | in_invoice | invoice |

#### Columnas CSV Portal IVA (32 columnas)

| Índice | Columna | Ejemplo |
|--------|---------|---------|
| 0 | Fecha de Emisión | 2026-02-01 |
| 1 | Tipo de Comprobante | 1 |
| 2 | Punto de Venta | 400 |
| 3 | Número de Comprobante | 29594 |
| 4 | Tipo Doc. Vendedor | 80 |
| 5 | Nro. Doc. Vendedor | 30615850124 |
| 6 | Denominación Vendedor | DIMENSION S A |
| 7 | Importe Total | 52900,00 |
| 8 | Moneda Original | PES |
| 9 | Tipo de Cambio | 1,00 |
| 10 | Importe No Gravado | 0,00 |
| 11 | Importe Exento | 0,00 |
| 12 | Crédito Fiscal Computable | 5026,70 |
| 13 | Perc/Pagos Otros Imp. Nac. | 0,00 |
| 14 | Perc. Ingresos Brutos | 0,00 |
| 15 | Imp. Municipales | 0,00 |
| 16 | Perc/Pagos IVA | 0,00 |
| 17 | Imp. Internos | 0,00 |
| 18 | Otros Tributos | 0,00 |
| 19-20 | Neto 0% / Neto 2.5% | 0,00 |
| 21-23 | IVA 2.5% / Neto 5% / IVA 5% | 0,00 |
| 24-25 | Neto 10.5% / IVA 10.5% | 47873,30 / 5026,70 |
| 26-27 | Neto 21% / IVA 21% | 0,00 |
| 28-29 | Neto 27% / IVA 27% | 0,00 |
| 30 | Total Neto Gravado | 47873,30 |
| 31 | Total IVA | 5026,70 |

### Decisiones técnicas

| Decisión | Justificación |
|----------|---------------|
| `diff_amount` computed no stored | Siempre actualizado si cambia la factura de Odoo |
| Dos parsers de importes separados | Mis Comprobantes usa `.` como miles; Portal IVA no — mezclarlos genera errores silenciosos |
| Matching por `code` en Portal IVA | El código numérico AFIP es único vs. texto libre que puede variar |
| Período auto-detectado del filename | Más confiable que la primera fecha (puede haber comprobantes desfasados) |
| Campos Portal IVA con `invisible` en vistas | No saturar la UI cuando se usa Mis Comprobantes |
| Campo `source` en cada línea | Permite filtrar y agrupar por origen sin ambigüedad |

### Dependencias

- `account` — modelo account.move
- `l10n_ar` — localización argentina (document_type, document_number, VAT)

### Verificación

1. Instalar módulo (o upgrade si ya estaba instalado)
2. Subir CSV de **Mis Comprobantes** → verificar formato detectado, parseo de importes con punto/coma, matching
3. Subir CSV de **Portal IVA** → verificar 32 columnas, percepciones, desglose IVA, multi-moneda (DOL con TC)
4. Verificar matching contra facturas existentes en Odoo
5. Verificar badge en factura de proveedor
6. Verificar filtros por origen (Portal IVA / Mis Comprobantes) en vista search
7. Verificar tabs Desglose IVA y Percepciones visibles solo en registros Portal IVA
