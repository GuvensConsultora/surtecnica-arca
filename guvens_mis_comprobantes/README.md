# Cruce Mis Comprobantes AFIP (guvens_mis_comprobantes)

## 1. Introducción

### Qué hace Odoo nativamente
Odoo permite cargar facturas de proveedores manualmente o importarlas, pero **no tiene forma de verificar** si esas facturas coinciden con lo que AFIP tiene registrado. El usuario no sabe si le falta cargar algún comprobante, si hay diferencias de importe, o si cargó algo que no existe en AFIP.

### Qué limitación resuelve
El portal "Mis Comprobantes" de AFIP permite descargar un CSV con todos los comprobantes recibidos por período. Este módulo toma ese CSV, lo importa en Odoo y **cruza automáticamente** cada línea contra las facturas de proveedor cargadas.

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

1. Ir a [Mis Comprobantes AFIP](https://miscomprobantes.afip.gob.ar/)
2. Seleccionar **Recibidos**
3. Filtrar por el mes deseado
4. Click en **Descargar** (formato CSV)

El archivo descargado tiene formato CSV con separador `;` y codificación latin-1.

### Paso 2 — Importar en Odoo

1. Ir a **Contabilidad → Proveedores → Importar CSV AFIP**
2. Se abre un wizard: subir el archivo CSV
3. Click en **"Importar y Cruzar"**

El período se auto-detecta de la primera fecha del CSV.

### Paso 3 — Revisar resultados

Se abre una tabla con todos los comprobantes, agrupados por estado:

| Fecha | Proveedor | CUIT | Tipo | PV | Número | Total AFIP | Factura Odoo | Diferencia | Estado |
|-------|-----------|------|------|----|--------|------------|--------------|------------|--------|
| 01/05/2024 | PROVEEDOR SA | 30712345678 | Factura | 00001 | 00000123 | 12.100,00 | FA-A 00001-00000123 | 0,00 | Coincide |
| 05/05/2024 | OTRO SRL | 30798765432 | Factura | 00003 | 00000456 | 5.000,00 | — | — | Falta en Odoo |
| 10/05/2024 | SERVICIOS SA | 20301234567 | NC | 00001 | 00000010 | 1.500,00 | NC-A 00001-00000010 | 100,00 | Diferencia |

### Qué hacer con cada estado

- **Coincide** → No requiere acción
- **Diferencia importe** → Click en "Ver factura" para revisar. Verificar si hay impuestos mal calculados o algún importe cargado distinto
- **Falta en Odoo** → Falta cargar esa factura. El proveedor la emitió pero no está en el sistema
- **Falta en AFIP** → Revisar si es un comprobante apócrifo, si se cargó con datos incorrectos (CUIT, PV, número), o si es de otro período

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

| Grupo | Puede ver | Puede importar |
|-------|-----------|----------------|
| Facturación (account.group_account_invoice) | Si | Si |
| Contabilidad (account.group_account_manager) | Si | Si (+ editar/eliminar) |

### Menú

**Contabilidad → Proveedores → Cruce Mis Comprobantes** — Vista de resultados
**Contabilidad → Proveedores → Importar CSV AFIP** — Wizard de importación

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
│   └── import_mis_comprobantes.py  # Wizard: importar CSV + cruzar
├── views/
│   ├── mis_comprobantes_views.xml  # Tree/form/search de resultados
│   ├── wizard_views.xml            # Form del wizard
│   └── account_move_views.xml      # Badge en facturas proveedor
└── data/
    └── menuitem.xml
```

### Modelo: `guvens.mis.comprobantes.line`

Almacena cada comprobante importado del CSV de AFIP.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `import_date` | Date | Fecha en que se ejecutó la importación |
| `period` | Char | Período auto-detectado (MM/YYYY) |
| `date` | Date | Fecha emisión del comprobante |
| `doc_type` | Char | Tipo: Factura, Nota de Crédito, Nota de Débito |
| `pos_number` | Char | Punto de venta (ej: 00001) |
| `doc_number` | Char | Número comprobante (ej: 00000123) |
| `cae` | Char | Código de Autorización Electrónica |
| `partner_vat` | Char | CUIT del emisor (solo dígitos) |
| `partner_name` | Char | Denominación del emisor |
| `amount_total` | Float | Importe total según AFIP |
| `amount_net` | Float | Neto gravado |
| `amount_untaxed` | Float | No gravado |
| `amount_exempt` | Float | Exento |
| `amount_iva` | Float | IVA |
| `state` | Selection | match / mismatch / missing_in_odoo / missing_in_afip |
| `move_id` | Many2one → account.move | Factura Odoo matcheada |
| `diff_amount` | Float (computed) | abs(total_odoo) - total_afip |

### Herencia: `account.move`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `mis_comprobantes_line_ids` | One2many | Líneas de cruce vinculadas |
| `mis_comprobantes_state` | Selection (related) | Estado del cruce |

### Wizard: `guvens.import.mis.comprobantes`

#### Formato del CSV esperado

Separador: `;` | Encoding: UTF-8 con BOM o Latin-1 | Decimales: coma

Columnas del CSV de AFIP "Mis Comprobantes — Recibidos":

| Índice | Columna | Ejemplo |
|--------|---------|---------|
| 0 | Fecha | 01/05/2024 |
| 1 | Tipo | Factura |
| 2 | Punto de Venta | 00001 |
| 3 | Número Desde | 00000123 |
| 4 | Número Hasta | 00000123 |
| 5 | Cód. Autorización | 74123456789012 |
| 6 | Tipo Doc. Emisor | 80 - CUIT |
| 7 | Nro. Doc. Emisor | 30712345678 |
| 8 | Denominación Emisor | PROVEEDOR SA |
| 9 | Tipo Cambio | 1,00 |
| 10 | Moneda | PES |
| 11 | Imp. Neto Gravado | 10.000,00 |
| 12 | Imp. Neto No Gravado | 0,00 |
| 13 | Imp. Op. Exentas | 0,00 |
| 14 | IVA | 2.100,00 |
| 15 | Imp. Total | 12.100,00 |

#### Algoritmo de cruce (`action_import`)

1. Decodifica el CSV (intenta UTF-8 con BOM, fallback Latin-1)
2. Por cada fila del CSV:
   - Parsea fecha (`dd/mm/yyyy`), importes (punto=miles, coma=decimal), CUIT (solo dígitos)
   - Construye `document_number` en formato Odoo: `00001-00000123`
   - Busca match en `account.move` por:
     - `move_type` (in_invoice / in_refund según tipo)
     - `state = posted`
     - `l10n_latam_document_number` exacto
     - `l10n_latam_document_type_id.internal_type` (invoice / credit_note / debit_note)
     - `partner_id.vat` = CUIT
   - Si match: compara importes con tolerancia de $0.01
3. Auto-detecta período de la primera fecha válida
4. Detecta facturas de Odoo del período no presentes en el CSV (`missing_in_afip`)

#### Mapeo tipo comprobante AFIP → Odoo

| Texto CSV AFIP | move_type Odoo | internal_type |
|----------------|----------------|---------------|
| Factura | in_invoice | invoice |
| Nota de Débito | in_invoice | debit_note |
| Nota de Crédito | in_refund | credit_note |
| Recibo | in_invoice | invoice |

### Dependencias

- `account` — modelo account.move
- `l10n_ar` — localización argentina (document_type, document_number, VAT)

### Verificación

1. Instalar módulo
2. Cargar al menos una factura de proveedor con PV-Número y CUIT correctos
3. Descargar CSV de "Mis Comprobantes" → Recibidos para el mismo período
4. Ir a Contabilidad → Proveedores → Importar CSV AFIP
5. Subir CSV → "Importar y Cruzar"
6. Verificar que las facturas cargadas aparezcan en verde (Coincide)
7. Verificar que comprobantes no cargados aparezcan en rojo (Falta en Odoo)
