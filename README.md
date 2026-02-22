# Libro IVA Digital - ARCA

## 1. Introducción

### Odoo nativo

Odoo con localización argentina (`l10n_ar`) gestiona facturación electrónica (AFIP), tipos de comprobante, CUIT y alícuotas IVA, pero **no genera los archivos que exige ARCA** para la presentación del Libro IVA Digital ni la apertura de conceptos del formulario F.2051 (IVA Simple).

### Limitación

El contador debe armar manualmente los archivos TXT de posición fija, los CSV de apertura y las planillas Excel del Libro IVA, procesando comprobante por comprobante. Esto es lento, propenso a errores y dificulta la cuadratura contra la DDJJ.

### Qué resuelve este módulo

Genera automáticamente **11 archivos** a partir de las facturas posted del período:

| Formato | Archivos | Destino |
|---------|----------|---------|
| TXT posición fija | 4 (Ventas Cbte/Alic + Compras Cbte/Alic) | Carga en ARCA - Libro IVA Digital |
| CSV | 4 (Débito Fiscal, Rest. DF, Crédito Fiscal, Rest. CF) | Importación F.2051 IVA Simple |
| Excel XLSX | 2 (Libro IVA Ventas + Libro IVA Compras) | Revisión contable / archivo |
| TXT | 1 (Cuadratura F.2051) | Verificación interna |

Todo se descarga en un único ZIP.

---

## 2. Funcionamiento para el usuario final

### Flujo paso a paso

1. Ir a **Contabilidad > Informes > ARCA > Libro IVA Digital**
2. Seleccionar el período (Desde / Hasta)
3. Ingresar el código de actividad AFIP (por defecto `465320`)
4. Click en **Generar Archivos**
5. El sistema valida CUIT de todos los partners, procesa comprobantes y genera los archivos
6. Se muestra la pantalla de resultados con:
   - Links de descarga individual para cada archivo
   - Resumen de cuadratura (débito/crédito fiscal por alícuota, balance F.2051)
7. Click en **Descargar ZIP** para obtener todos los archivos en un solo archivo comprimido

### Qué ve el usuario en pantalla

**Estado borrador:**
- Campos de fecha (Desde / Hasta)
- Código de actividad AFIP

**Estado generado:**
- Sección **Archivos TXT Generados**: 4 TXT + cuadratura
- Sección **Excel Libro IVA**: 2 planillas XLSX (Ventas y Compras)
- Sección **CSV IVA Simple**: 4 CSV de apertura F.2051
- Sección **Cuadratura DDJJ F.2051**: resumen con totales por alícuota y balance

### Reglas de negocio

| Regla | Por qué |
|-------|---------|
| Solo facturas `posted` con documento fiscal (`l10n_latam_document_type_id`) | ARCA solo acepta comprobantes electrónicos validados |
| NC con importes negativos en TXT | Formato AFIP requiere signo negativo para notas de crédito |
| Compras A/M generan crédito fiscal; B/C solo se informan | RG 4597: solo facturas que discriminan IVA generan CF computable |
| Ajuste de redondeo IVA (3 estrategias) | AFIP valida `round(base * tasa) == iva_total`, Odoo redondea por línea |
| Validación CUIT (módulo 11) antes de generar | ARCA rechaza registros con dígito verificador inválido |

### Ejemplo de planilla Excel (Libro IVA Ventas)

| Fecha | Tipo | Pto Vta | Número | CUIT | Razón Social | Neto 21% | DF 21% | No Gravado | Exento | Perc. IIBB | Total |
|-------|------|---------|--------|------|--------------|----------|--------|------------|--------|------------|-------|
| 01/02/2026 | FA-A | 00001 | 00000123 | 30-12345678-9 | Cliente SA | 10.000,00 | 2.100,00 | 0,00 | 0,00 | 300,00 | 12.400,00 |
| 05/02/2026 | NC-A | 00001 | 00000045 | 30-12345678-9 | Cliente SA | -2.000,00 | -420,00 | 0,00 | 0,00 | -60,00 | -2.480,00 |

Columnas por alícuota IVA (2,5%, 5%, 10,5%, 21%, 27%) con neto e impuesto, más percepciones, impuestos internos y total. Fila de totales al final.

---

## 3. Parametrización

### Requisitos previos

1. Módulo `l10n_ar` instalado y configurado (localización argentina)
2. Compañía con CUIT cargada en **Ajustes > Empresas**
3. Partners con CUIT válida y tipo de responsabilidad AFIP asignado
4. Impuestos con grupos configurados con `l10n_ar_vat_afip_code` (estándar de `l10n_ar`)

### Instalación

1. Copiar el módulo en la carpeta de addons
2. Actualizar lista de aplicaciones: **Ajustes > Aplicaciones > Actualizar lista**
3. Buscar "Libro IVA Digital" e instalar

### Configuración

| Paso | Menú | Campo | Valor |
|------|------|-------|-------|
| 1 | Ajustes > Empresas | CUIT | CUIT de la empresa (11 dígitos) |
| 2 | Contabilidad > Informes > ARCA > Libro IVA Digital | Actividad AFIP | Código de 6 dígitos (ej: `465320`) |

No requiere configuración adicional. El módulo lee la estructura fiscal de `l10n_ar` automáticamente.

---

## 4. Referencia técnica

### Arquitectura

```
surtecnica_arca/
├── __init__.py
├── __manifest__.py
├── models/
│   └── __init__.py
├── wizard/
│   ├── __init__.py
│   ├── libro_iva_digital_wizard.py      # Lógica principal (~1540 líneas)
│   └── libro_iva_digital_wizard_views.xml
├── views/
│   └── menu_views.xml
├── security/
│   └── ir.model.access.csv
└── static/
    └── description/
        └── icon.png
```

### Modelo: `libro.iva.digital.wizard` (TransientModel)

#### Campos principales

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `date_from` / `date_to` | Date | Período de generación |
| `actividad_afip` | Char(6) | Código actividad AFIP para CSV |
| `state` | Selection | `draft` → `done` |
| `ventas_cbte_file` / `ventas_alic_file` | Binary | TXT ventas (266 + 62 chars) |
| `compras_cbte_file` / `compras_alic_file` | Binary | TXT compras (325 + 84 chars) |
| `iva_simple_debito_csv` / `iva_simple_rest_debito_csv` | Binary | CSV Débito Fiscal / Restitución |
| `iva_simple_credito_csv` / `iva_simple_rest_credito_csv` | Binary | CSV Crédito Fiscal / Restitución |
| `libro_iva_ventas_xlsx` / `libro_iva_compras_xlsx` | Binary | Excel Libro IVA Ventas / Compras |
| `cuadratura_file` | Binary | TXT cuadratura F.2051 |
| `resumen` | Text | Resumen legible en pantalla |

#### Métodos principales

| Método | Descripción |
|--------|-------------|
| `action_generar()` | Genera los 11 archivos (TXT + CSV + XLSX + cuadratura) |
| `action_descargar_zip()` | Empaqueta todo en ZIP y retorna URL de descarga |
| `_extract_move_data(move)` | Extrae y clasifica importes de un comprobante |
| `_procesar_moves(moves, tipo, extracted)` | Genera líneas TXT de cabecera + alícuotas |
| `_generar_csvs_iva_simple(...)` | Genera 4 CSV de apertura F.2051 |
| `_generar_libro_iva_xlsx(moves, extracted, tipo)` | Genera planilla Excel con openpyxl |
| `_ajustar_redondeo_iva(base, amount, code, sign)` | Ajuste de redondeo AFIP (3 estrategias) |
| `_validar_cuit_partners(moves)` | Valida CUIT con algoritmo módulo 11 |
| `_generar_resumen(...)` | Genera cuadratura con balance F.2051 |

### Formatos de archivo

#### TXT Libro IVA Digital (posición fija, encoding latin-1, CRLF)

| Archivo | Chars | Campos |
|---------|-------|--------|
| Ventas Cabecera | 266 | 22 (fecha, tipo, PV, nro, doc comprador, importes, moneda, TC, alícuotas, op, tributos, vto) |
| Ventas Alícuotas | 62 | 6 (tipo, PV, nro, neto, código IVA, impuesto) |
| Compras Cabecera | 325 | 25 (+ despacho importación, CUIT emisor, IVA comisión) |
| Compras Alícuotas | 84 | 8 (+ doc vendedor) |

#### CSV IVA Simple F.2051 (separador `;`, decimal coma, encoding latin-1)

| Archivo | Columnas | Agrupación |
|---------|----------|------------|
| Débito Fiscal | 8 | Actividad × Tipo Operación × Tipo Sujeto × Alícuota |
| Rest. Débito Fiscal | 7 | Idem sin O.D.P. |
| Crédito Fiscal | 5 | Concepto × Alícuota |
| Rest. Crédito Fiscal | 4 | Idem sin CF computable |

#### Excel Libro IVA (XLSX, openpyxl)

| Sección | Columnas |
|---------|----------|
| Identificación | Fecha, Tipo, Pto Vta, Número, CUIT, Razón Social |
| IVA por alícuota | Neto + DF/CF para cada alícuota (2,5%, 5%, 10,5%, 21%, 27%) |
| Otros conceptos | No Gravado, Exento |
| Percepciones | Perc. no Categ./IVA, Nacionales, IIBB, Municipales |
| Tributos | Imp. Internos, Otros Tributos |
| Total | Importe total del comprobante |

Incluye: encabezado con empresa/CUIT/período, headers con estilo, formato numérico `#,##0.00`, fila de totales.

### Códigos AFIP utilizados

#### Alícuotas IVA

| Código AFIP | Alícuota | Tasa |
|-------------|----------|------|
| 0003 | 0% | 0.00 |
| 0009 | 2,5% | 0.025 |
| 0008 | 5% | 0.05 |
| 0004 | 10,5% | 0.105 |
| 0005 | 21% | 0.21 |
| 0006 | 27% | 0.27 |

#### Clasificación de impuestos no-IVA

| Código tributo AFIP | Categoría | Campo TXT |
|---------------------|-----------|-----------|
| 06 | Percepción IVA | `perc_iva` (compras) |
| 07 | Percepción IIBB | `perc_iibb` |
| 08 | Percepción Municipal | `perc_mun` |
| 04 | Impuestos Internos | `imp_internos` |
| 01 | Percepción Nacional | `perc_nacionales` |
| 09 | Otros tributos | `otros_tributos` |

### Decisiones técnicas

| Decisión | Justificación |
|----------|---------------|
| `price_subtotal` en vez de `balance` | `balance` está en moneda compañía; `price_subtotal` en moneda factura, consistente con `amount_total` |
| Extracción única (`_extract_move_data`) | Evita procesar cada comprobante 3 veces (TXT, CSV, XLSX) |
| Ajuste de redondeo con 3 estrategias | Odoo redondea IVA por línea; AFIP valida `round(base_total * tasa)`. Se intenta preservar total, luego ajustar base, último recurso recalcular IVA |
| Estilos openpyxl como atributos de clase | Reutilización eficiente, se instancian una sola vez |
| `TransientModel` | Datos temporales, no persisten en DB tras cerrar wizard |

### Seguridad

| Grupo | Permisos |
|-------|----------|
| `account.group_account_user` | Lectura, escritura, creación, eliminación |

### Dependencias

| Módulo | Razón |
|--------|-------|
| `account` | Modelo `account.move`, menú de informes |
| `l10n_ar` | CUIT, tipos de documento AFIP, códigos IVA, responsabilidad fiscal |

**Dependencia Python:** `openpyxl` (incluido en Odoo)

### Verificación

1. Instalar módulo en base con `l10n_ar` configurado
2. Crear facturas de venta/compra con distintas alícuotas IVA
3. Ir a **Contabilidad > Informes > ARCA > Libro IVA Digital**
4. Generar archivos para el período
5. Verificar:
   - TXT: longitud de línea correcta (266/62/325/84 chars)
   - CSV: importación exitosa en ARCA F.2051
   - Excel: totales cuadran con cuadratura
   - Cuadratura: balance DF - CF coincide con DDJJ
   - ZIP: contiene los 11 archivos
