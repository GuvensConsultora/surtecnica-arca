# Fix layout PDF facturas - clearfix totales (guvens_fix_pdf_totales)

## 1. Introducción

### Qué limitación existe
En la plantilla nativa de Odoo `account.report_invoice_document`, la caja de totales (`#right-elements`) usa `float-end` (flotante) pero el bloque que sigue (nota/pie del comprobante, `t-field="o.narration"`) no tiene un `clearfix` después. Mientras la nota sea corta (1-2 líneas), el texto entra debajo de la caja flotante sin problema. Cuando la nota es larga (varias líneas), el texto sube y queda superpuesto con la caja de totales.

### Qué hace este módulo
Agrega un `<div class="clearfix"/>` justo después de la caja de totales, vía herencia de vista (sin tocar la nativa). Con eso, cualquier contenido posterior (nota, QR) siempre respeta la altura real de la caja de totales, tenga la nota el largo que tenga.

---

## 2. Funcionamiento para el usuario final

No hay nada que operar: el fix aplica automáticamente a todo PDF de factura/nota de crédito/débito que use la plantilla nativa. Ya no debería verse texto de la nota superpuesto con "Importe base / IVA / Total".

---

## 3. Parametrización

### Instalación
1. Ir a **Ajustes → Aplicaciones**
2. Buscar "Fix layout PDF facturas - clearfix totales"
3. Instalar

No requiere configuración adicional ni depende de módulos AR específicos, solo de `account`.

---

## 4. Referencia técnica

### Arquitectura
```
guvens_fix_pdf_totales/
├── __init__.py                       # vacío, no hay modelos
├── __manifest__.py
└── views/
    └── report_invoice_document.xml   # hereda account.report_invoice_document
```

### Vista: `report_invoice_document_clearfix`
Hereda `account.report_invoice_document` (core, sin `_inherit` de módulos de terceros — cumple la regla de heredar solo de nativos). Vía `xpath` sobre `//div[@id='right-elements']`, inserta un `<div class="clearfix"/>` en posición `after`.

### Decisión técnica
No se modificó la vista nativa ni se agregó CSS global: un solo `clearfix` puntual, mínimamente invasivo, reversible con solo desinstalar el módulo.

### Verificación
1. Instalar el módulo
2. Abrir/descargar el PDF de un comprobante con nota de varias líneas (ej. NC-A 00006-00000319)
3. Verificar que el texto de la nota ya no se superpone con la caja de totales
