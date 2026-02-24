# Libro IVA Digital — ARCA (surtecnica_arca)

## 1. Introducción

### Qué hace Odoo nativamente
Odoo con localización argentina (`l10n_ar`) permite emitir facturas con tipos de comprobante AFIP, alícuotas de IVA y estructura fiscal argentina. Sin embargo, **no genera los archivos TXT de posición fija** que exige ARCA para la presentación del Libro IVA Digital, ni los CSV de apertura del IVA Simple (F.2051).

### Qué limitación existe
Sin este módulo, el contador debe:
- Exportar datos de Odoo manualmente y formatearlos a posición fija según el Anexo I de ARCA
- Generar 4 archivos TXT (ventas cabecera, ventas alícuotas, compras cabecera, compras alícuotas)
- Calcular la DDJJ IVA a mano
- Identificar errores de ARCA contando líneas del TXT
- Generar CSV de apertura del F.2051 manualmente

### Qué hace este módulo
Genera **todo lo necesario para presentar el Libro IVA Digital ante ARCA** desde un wizard:

- **4 archivos TXT** de posición fija (Anexo I — diseño de registros ARCA)
- **Preview DDJJ IVA** — simulación del F.2002 con débito/crédito fiscal desglosado
- **4 CSV de IVA Simple** (F.2051) — apertura de otros conceptos por tipo de sujeto
- **Libro IVA en Excel** (RG 4597) — libro de ventas y compras con detalle por comprobante
- **Procesador de errores ARCA** — sube el CSV de errores y muestra qué comprobante falló con link directo
- **Descarga ZIP** con todos los archivos (TXT + CSV + Excel + PDF de DDJJ)

---

## 2. Funcionamiento para el usuario final

### Generar el Libro IVA Digital

1. Ir a **Contabilidad → Informes → ARCA → Libro IVA Digital**
2. Se abre el wizard con:
   - **Período**: Desde / Hasta (default: mes actual)
   - **Moneda TXT**: "Todo en Pesos" o "En moneda del comprobante" (para facturas en USD/EUR)
   - **Actividad AFIP**: código de actividad principal (para CSV IVA Simple)
3. Click en **"Generar Archivos"**

### Qué se genera

El wizard pasa al estado "Generado" con 4 pestañas:

#### Pestaña "DDJJ IVA"
Preview HTML que simula el F.2002 de ARCA:
- Ventas: débito fiscal desglosado por alícuota IVA (0%, 10.5%, 21%, 27%)
- Compras: crédito fiscal desglosado por alícuota IVA
- Percepciones y retenciones (IVA, IIBB, nacionales, municipales)
- Totales de débito y crédito fiscal
- Cruce automático: valida que los CSV de apertura coincidan con los TXT

#### Pestaña "Archivos"
- Resumen con cantidad de comprobantes y líneas generadas
- **Libro IVA Excel** (RG 4597) — descarga directa
- **4 archivos TXT** — descarga individual o todo junto vía ZIP

| Archivo | Chars/línea | Contenido |
|---------|-------------|-----------|
| VENTAS_CBTE | 266 | Cabecera de comprobantes de venta |
| VENTAS_ALICUOTAS | 62 | Alícuotas IVA por comprobante de venta |
| COMPRAS_CBTE | 325 | Cabecera de comprobantes de compra |
| COMPRAS_ALICUOTAS | 84 | Alícuotas IVA por comprobante de compra |

#### Pestaña "IVA Simple"
4 CSV para importar en el F.2051 (apertura de otros conceptos):
- **Débito Fiscal** — ventas por tipo de sujeto (RI, Monotributo, CF/Exento)
- **Restitución Débito Fiscal** — notas de crédito de ventas
- **Crédito Fiscal** — compras con detalle de percepciones
- **Restitución Crédito Fiscal** — notas de crédito de compras

#### Pestaña "Errores ARCA"
Cuando ARCA rechaza el TXT, devuelve un CSV con errores referenciando número de línea:
1. Subir el CSV de errores
2. Seleccionar si es de Compras o Ventas
3. Click en **"Identificar Comprobantes"**
4. Se muestra cada error con:
   - Link al comprobante en Odoo
   - Proveedor y CUIT
   - Todos los importes exportados (total, gravado, IVA, percepciones, etc.)
   - Detalle de alícuota con recálculo (base × tasa vs importado)

### Descargar ZIP

Click en **"Descargar ZIP"** — descarga un ZIP con:
- 4 TXT del Libro IVA Digital
- 4 CSV del IVA Simple
- DDJJ IVA en PDF
- DDJJ IVA en Excel
- Libro IVA Excel (RG 4597)

### Validación de duplicados

Antes de generar, el módulo detecta comprobantes duplicados (mismo tipo + número + proveedor). ARCA deduplica líneas idénticas silenciosamente, causando diferencias entre TXT y CSV. Si hay duplicados, se muestra una alerta con links a los comprobantes afectados.

---

## 3. Parametrización

### Instalación

1. Ir a **Ajustes → Aplicaciones**
2. Buscar "Libro IVA Digital"
3. Instalar

### Requisitos previos

- Módulos `account` y `l10n_ar` instalados
- Facturas con:
  - Tipo de documento AFIP asignado (`l10n_latam_document_type_id`)
  - Alícuotas de IVA con código AFIP (`l10n_ar_vat_afip_code` en tax group)
  - CUIT cargado en proveedor/cliente

### Configuración del wizard

| Campo | Default | Descripción |
|-------|---------|-------------|
| Desde | 1er día del mes actual | Inicio del período |
| Hasta | Hoy | Fin del período |
| Moneda TXT | Todo en Pesos (ARS) | ARS: importes convertidos a pesos, moneda=PES, TC=1. Moneda comprobante: importes en USD/EUR con TC real |
| Actividad AFIP | 465320 | Código de actividad principal para CSV IVA Simple (6 dígitos) |

### Permisos

| Grupo | Acceso |
|-------|--------|
| Contabilidad / Contable (account.group_account_user) | Total (leer, crear, editar, eliminar) |

### Menú

**Contabilidad → Informes → ARCA → Libro IVA Digital**

---

## 4. Referencia técnica

### Arquitectura

```
surtecnica_arca/
├── __init__.py
├── __manifest__.py
├── security/ir.model.access.csv
├── models/
│   └── __init__.py              # (vacío, sin modelos persistentes)
├── wizard/
│   ├── __init__.py
│   ├── libro_iva_digital_wizard.py       # Toda la lógica (~3300 líneas)
│   └── libro_iva_digital_wizard_views.xml # Wizard form con notebook
├── views/
│   └── menu_views.xml           # Menú ARCA en Informes
└── static/
    └── description/
        └── icon.png
```

### Modelo: `libro.iva.digital.wizard` (TransientModel)

Wizard que genera todos los archivos en memoria sin persistir datos.

#### Campos principales

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `date_from` / `date_to` | Date | Período a procesar |
| `moneda_reporte` | Selection | ARS o moneda del comprobante |
| `actividad_afip` | Char | Código actividad AFIP para CSV |
| `ventas_cbte_file` | Binary | TXT ventas cabecera |
| `ventas_alic_file` | Binary | TXT ventas alícuotas |
| `compras_cbte_file` | Binary | TXT compras cabecera |
| `compras_alic_file` | Binary | TXT compras alícuotas |
| `iva_simple_*_csv` | Binary | 4 CSV de IVA Simple |
| `libro_iva_excel` | Binary | Excel Libro IVA (RG 4597) |
| `ddjj_iva_html` | Html | Preview DDJJ IVA |
| `ddjj_data_json` | Text | Datos DDJJ serializados (para Excel sin re-query) |
| `line_map_json` | Text | Mapeo línea TXT → comprobante (para errores ARCA) |
| `errores_arca_csv` | Binary | CSV de errores ARCA subido |
| `errores_arca_html` | Html | Resultado del procesamiento de errores |
| `duplicados_html` | Html | Alerta de comprobantes duplicados |

#### Métodos principales

| Método | Descripción |
|--------|-------------|
| `action_generar()` | Orquestador principal: busca moves, extrae datos, genera TXT/CSV/Excel/DDJJ |
| `action_descargar_zip()` | Empaqueta todos los archivos en ZIP para descarga |
| `action_procesar_errores()` | Cruza CSV de errores ARCA con mapeo de líneas |
| `_get_moves(tipo)` | Busca facturas posted del período por tipo (out/in) |
| `_validar_duplicados(moves)` | Detecta comprobantes duplicados antes de procesar |
| `_extract_move_data(move)` | Extrae y clasifica importes: IVA gravado (por alícuota), no gravado, exento, percepciones IVA/IIBB/nacionales/municipales, impuestos internos, otros tributos |
| `_procesar_moves(moves, tipo)` | Genera líneas TXT de posición fija + mapeo para errores |
| `_compute_ddjj_iva_html()` | Calcula DDJJ IVA con desglose por alícuota |
| `_generar_csvs_iva_simple()` | Genera 4 CSV de apertura F.2051 por tipo de sujeto |
| `_generate_libro_iva_excel()` | Genera Libro IVA en Excel (RG 4597) |
| `_generate_ddjj_pdf()` / `_generate_ddjj_excel()` | Exporta DDJJ en PDF/Excel |
| `_render_errores_html(rows)` | Renderiza errores ARCA con detalle completo e importes |

#### Constantes AFIP

| Constante | Uso |
|-----------|-----|
| `IVA_AMOUNT_MAP` | Mapeo % alícuota → código AFIP (3=0%, 5=21%, etc.) |
| `IVA_GRAVADO_CODES` | Códigos que van al archivo de alícuotas |
| `IVA_CODE_RATE` | Código AFIP → tasa porcentual (para recálculo) |
| `IVA_CODE_LABEL` | Código AFIP → label para display |
| `MONEDA_MAP` | ISO currency → código AFIP (ARS=PES, USD=DOL, etc.) |
| `RESP_TIPO_SUJETO` | Responsabilidad AFIP → tipo sujeto CSV IVA Simple |

### Decisiones técnicas

| Decisión | Justificación |
|----------|---------------|
| TransientModel (sin modelo persistente) | Los archivos se generan bajo demanda, no necesitan historial en DB |
| Doble extracción (ARS + moneda factura) | TXT puede ir en moneda origen (ARCA lo acepta), pero DDJJ/CSV/Excel siempre en ARS |
| Recálculo IVA (base × tasa) | ARCA valida IVA = base × alícuota exacto. Odoo puede tener diferencias de centavos por redondeo en líneas de producto |
| Mapeo línea TXT → move | ARCA reporta errores por nro de línea; sin mapeo, el usuario tiene que contar líneas manualmente |
| HTML con sanitize=False | UserError no renderiza HTML en Odoo 17; los links a facturas necesitan HTML real |
| `ddjj_data_json` serializado | Evita re-query y reproceso de moves al generar Excel (los datos ya están calculados) |

### Dependencias

- `account` — modelo account.move, tax groups
- `l10n_ar` — tipos de documento AFIP, códigos IVA, códigos moneda, CUIT
- `xlsxwriter` — generación de Excel (Libro IVA, DDJJ)
- `markupsafe` — HTML seguro en campos Html

### Verificación

1. Instalar módulo
2. Ir a Contabilidad → Informes → ARCA → Libro IVA Digital
3. Seleccionar período con facturas de venta y compra
4. Click "Generar Archivos"
5. Verificar pestaña "DDJJ IVA" — totales deben coincidir con lo esperado
6. Descargar ZIP y subir TXT a ARCA portal
7. Si ARCA reporta errores: subir CSV en pestaña "Errores ARCA" → "Identificar Comprobantes"
