# Padrón AFIP A13 (guvens_padron_a13)

## 1. Introducción

### Qué hace Odoo nativamente
El módulo `l10n_ar_padron` (de la comunidad argentina) permite consultar el padrón de AFIP desde la ficha del contacto, usando el Web Service A5 (`ws_sr_constancia_inscripcion`). Al hacer click en "Actualizar desde padrón AFIP", obtiene razón social, domicilio, condición IVA, actividades e impuestos del contribuyente.

### Qué limitación existe
ARCA (ex AFIP) reemplazó el servicio A5 por **A13** (`ws_sr_padron_a13`) como servicio recomendado. A13 usa la misma interfaz `getPersona()` pero con un namespace SOAP distinto que la librería `pysimplesoap` (usada por pyafipws) no maneja bien: envía elementos calificados con namespace y A13 los espera sin calificar. Resultado: la consulta falla.

### Qué hace este módulo
- Reemplaza la consulta A5 por **A13** usando SOAP directo con `requests` (sin pysimplesoap)
- Autentica con **WSAA directamente** usando el certificado Enterprise de la empresa, sin depender de `l10n_ar_afipws` (`afipws.connection`)
- Cachea los tokens WSAA en base de datos para no re-autenticar en cada consulta (TTL ~12hs)
- Monkey-patchea un bug de `l10n_ar_padron` que usa `_logger` sin definirlo

---

## 2. Funcionamiento para el usuario final

### Consultar padrón

1. Ir a **Contactos** → abrir un contacto con CUIT cargado
2. Click en **"Actualizar desde padrón AFIP"** (o shortcut **Alt+A**)
3. El módulo consulta AFIP A13 y actualiza automáticamente:
   - Razón social / Nombre y apellido
   - Domicilio fiscal (dirección, localidad, provincia, CP)
   - Condición frente al IVA (Responsable Inscripto, Monotributo, Exento, etc.)
   - Actividades económicas
   - Impuestos inscriptos

### Qué ve el usuario
El comportamiento es idéntico al botón original de `l10n_ar_padron`. La diferencia es interna: usa A13 en vez de A5.

### Errores posibles

| Error | Causa | Solución |
|-------|-------|----------|
| "No se encontró certificado AFIP" | Falta cert/key en la empresa | Configurar en Contabilidad → Config → Certificado AFIP |
| "AFIP A13 HTTP 500" | Token expirado o CUIT inválido | Reintentar (el cache se renueva solo) |
| "La afip no devolvió nombre" | CUIT dado de baja o inexistente | Verificar CUIT en constancia web de AFIP |

---

## 3. Parametrización

### Requisitos previos

1. **`l10n_ar_padron`** instalado (provee el botón "Actualizar desde padrón AFIP" y `parce_census_vals()`)
2. **Certificado AFIP** configurado en la empresa:
   - Ir a **Contabilidad → Configuración**
   - Cargar **Clave privada** (`l10n_ar_afip_ws_key`) y **Certificado** (`l10n_ar_afip_ws_crt`)
   - El certificado debe tener habilitado el servicio `ws_sr_padron_a13` en AFIP

### Instalación

1. Ir a **Ajustes → Aplicaciones**
2. Buscar "Padron AFIP A13"
3. Instalar

### Entorno producción / homologación

El módulo detecta automáticamente el entorno:
1. Parámetro de sistema `afip.ws.env.type` → `production` o `homologation`
2. Fallback: `server_mode` en config file → `test`/`develop` = homologación

### Permisos

| Grupo | Acceso a cache de tokens |
|-------|--------------------------|
| Facturación (account.group_account_invoice) | Lectura/escritura |

---

## 4. Referencia técnica

### Arquitectura

```
guvens_padron_a13/
├── __init__.py                  # Monkey-patch _logger en l10n_ar_padron
├── __manifest__.py
├── security/ir.model.access.csv
├── models/
│   ├── __init__.py
│   ├── ws_sr_padron_a13.py      # Clase WS pura Python — SOAP directo
│   ├── a13_token_cache.py       # Cache de tokens WSAA en DB
│   ├── res_company.py           # Auth WSAA + factory de WSSrPadronA13
│   └── res_partner.py           # Override check_padron() → usa A13
└── views/
    └── res_partner_views.xml    # Shortcut Alt+A al botón
```

### Modelo: `guvens.a13.token.cache`

Cache de tokens WSAA para evitar re-autenticar en cada consulta.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `company_id` | Many2one → res.company | Empresa dueña del token |
| `token` | Text | Token WSAA |
| `sign` | Text | Firma WSAA |
| `generation_time` | Datetime | Cuándo se generó |
| `expiration_time` | Datetime | Cuándo expira (~12hs) |

Método `_get_valid_token(company)`: busca token no expirado para la empresa.

### Clase: `WSSrPadronA13` (ws_sr_padron_a13.py)

Hereda de `pyafipws.ws_sr_padron.WSSrPadronA5` y sobreescribe dos métodos:

**`Conectar()`** — Solo guarda la URL del endpoint. No crea cliente pysimplesoap porque A13 requiere SOAP manual.

**`Consultar(id_persona)`** — Flujo:
1. Construye XML SOAP manualmente con namespace `per:` en `getPersona` pero hijos sin namespace (así lo espera A13)
2. Envía POST con `requests` al endpoint
3. Parsea la respuesta XML ignorando namespaces (helpers `_find`, `_findall`, `_text`)
4. Extrae: datos generales (razón social, domicilio, estado), impuestos, actividades, categoría monotributo
5. Llama a `self.analizar_datos()` heredado de A5 para determinar condición IVA

**Helpers XML:**
- `_find(element, name, recursive)` — Busca hijo por nombre local ignorando namespace
- `_findall(element, name)` — Busca todos los hijos con ese nombre
- `_text(element, child_name, default)` — Texto de un hijo
- `_elem_to_dict(element)` — Convierte XML a dict para serialización/debug

### Herencia: `res.company` (res_company.py)

**`_get_environment_type()`** — Detecta producción/homologación desde parámetro de sistema o config file.

**`_get_a13_ws()`** — Factory que retorna instancia `WSSrPadronA13` lista para consultar:
1. Busca token cacheado en `guvens.a13.token.cache`
2. Si no hay token válido → llama a `_authenticate_wsaa()`
3. Crea instancia con Token/Sign/Cuit y retorna

**`_authenticate_wsaa(env_type)`** — Autentica con WSAA:
1. Lee cert+key de campos Enterprise (`l10n_ar_afip_ws_key`, `l10n_ar_afip_ws_crt`)
2. Crea TRA con `pyafipws.wsaa` para servicio `ws_sr_padron_a13`
3. Firma con CMS, hace LoginCMS
4. Parsea ticket → extrae token, sign, tiempos
5. Cachea en `guvens.a13.token.cache`

### Herencia: `res.partner` (res_partner.py)

**`check_padron()`** — Override del método de `l10n_ar_padron`:
1. Obtiene CUIT del contacto
2. Llama a `company._get_a13_ws()` para obtener WS autenticado
3. Ejecuta `padron.Consultar(cuit)`
4. Llama a `self.parce_census_vals(padron)` (heredado de `l10n_ar_padron`) para convertir respuesta a vals de Odoo
5. Quita campos que no deben escribirse (`imp_iva_padron`, `last_update_census`, `imp_ganancias_padron`)
6. Escribe vals en el partner

### Monkey-patch en `__init__.py`

`l10n_ar_padron/models.py` usa `_logger` pero no lo define. Como no podemos modificar ese repo externo, inyectamos el logger al importar el módulo.

### Decisiones técnicas

| Decisión | Justificación |
|----------|---------------|
| SOAP manual con `requests` | pysimplesoap califica los hijos de `getPersona` con namespace A13, pero el server los espera sin calificar. No hay forma de configurar pysimplesoap para evitarlo |
| Auth WSAA directa (sin `l10n_ar_afipws`) | Elimina dependencia de un módulo complejo. El cert Enterprise (`l10n_ar_afip_ws_key/crt`) está disponible sin necesidad de `afipws.connection` |
| Cache de tokens en DB | Los tokens WSAA duran ~12hs. Sin cache, cada consulta al padrón requeriría autenticar con WSAA (lento + rate limiting) |
| Herencia de `WSSrPadronA5` | Reutiliza `inicializar()`, `analizar_datos()`, constantes (`TIPO_CLAVE`, `PROVINCIAS`) y atributos estándar. Solo sobreescribimos `Conectar()` y `Consultar()` |
| `pop()` en vez de `del` | `parce_census_vals()` no siempre agrega todas las keys; `pop()` no falla si la key no existe |

### Dependencias

- `l10n_ar_padron` — botón "Actualizar desde padrón AFIP", `parce_census_vals()`, vista del partner
- `pyafipws` — librería WSAA + clase base `WSSrPadronA5`
- `requests` — HTTP POST para SOAP directo

### Verificación

1. Instalar módulo
2. Verificar que el certificado AFIP esté configurado con servicio `ws_sr_padron_a13` habilitado
3. Abrir un contacto con CUIT → Click "Actualizar desde padrón AFIP" (o Alt+A)
4. Verificar que se actualizan: razón social, domicilio, condición IVA
5. Verificar en log que dice `A13 using cached token` en la segunda consulta
