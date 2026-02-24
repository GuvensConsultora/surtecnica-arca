# Clase WS pura Python para el servicio de padrón A13
# Por qué: ARCA reemplazó A4/A5 por A13 como servicio recomendado.
# A13 usa la misma interfaz SOAP getPersona() que A5, pero con un namespace
# distinto que pysimplesoap no maneja bien (envía elementos calificados
# y A13 los espera sin calificar). Solución: SOAP request directo con requests.
import json
import logging
import requests as http_requests
from xml.etree import ElementTree as ET

from pyafipws.ws_sr_padron import WSSrPadronA5
from pyafipws.padron import TIPO_CLAVE, PROVINCIAS
from pyafipws.utils import json_serializer

_logger = logging.getLogger(__name__)


class WSSrPadronA13(WSSrPadronA5):
    """WS Padrón A13 — SOAP directo, sin pysimplesoap."""

    WSDL = "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA13?WSDL"
    HOMO = False

    def Conectar(self, cache="", wsdl="", proxy="", wrapper="",
                 cacert=None, timeout=30, soap_server=None):
        """Override: solo guarda URL, no crea cliente pysimplesoap."""
        self._wsdl_url = wsdl or self.WSDL
        self._endpoint_url = self._wsdl_url.split('?')[0]
        self._timeout = timeout
        return True

    def Consultar(self, id_persona):
        """Consulta A13 con SOAP directo.
        Por qué: pysimplesoap califica elementos con namespace A13
        (http://a13.soap.ws.server.puc.sr/) pero el server los espera
        sin calificar (<{}token>). Enviamos XML manual."""
        # Reinicializar atributos
        self.inicializar()

        # SOAP envelope — getPersona con prefijo para que los hijos
        # NO hereden el namespace (A13 espera <{}token>, no <{ns}token>)
        soap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv='
            '"http://schemas.xmlsoap.org/soap/envelope/"'
            ' xmlns:per="http://a13.soap.ws.server.puc.sr/">'
            '<soapenv:Body>'
            '<per:getPersona>'
            '<token>{token}</token>'
            '<sign>{sign}</sign>'
            '<cuitRepresentada>{cuit}</cuitRepresentada>'
            '<idPersona>{id_persona}</idPersona>'
            '</per:getPersona>'
            '</soapenv:Body>'
            '</soapenv:Envelope>'
        ).format(
            token=self.Token,
            sign=self.Sign,
            cuit=self.Cuit,
            id_persona=id_persona,
        )

        resp = http_requests.post(
            self._endpoint_url,
            data=soap_xml.encode('utf-8'),
            headers={'Content-Type': 'text/xml; charset=utf-8'},
            timeout=self._timeout,
        )
        resp.raise_for_status()

        # Parsear respuesta XML
        root = ET.fromstring(resp.content)

        # Buscar personaReturn (ignorando namespaces)
        persona_return = self._find(root, 'personaReturn', recursive=True)
        if persona_return is None:
            # Buscar SOAP Fault
            fault = self._find(root, 'faultstring', recursive=True)
            msg = fault.text if fault is not None else 'Respuesta inesperada de AFIP A13'
            raise RuntimeError(msg)

        # Extraer secciones (misma estructura que A5)
        datos_generales = self._find(persona_return, 'datosGenerales')
        datos_mt = self._find(persona_return, 'datosMonotributo')
        datos_rg = self._find(persona_return, 'datosRegimenGeneral')

        # Guardar data serializada (para debug/compatibilidad)
        ret_dict = self._elem_to_dict(persona_return)
        self.data = ret_dict.get('datosGenerales', {})
        self.Persona = json.dumps(ret_dict, default=json_serializer)

        # Errores (mismo patrón que A5)
        for er_key in ('errorConstancia', 'errorMonotributo', 'errorRegimenGeneral'):
            er_elem = self._find(persona_return, er_key)
            if er_elem is not None:
                error_text = self._text(er_elem, 'error', '')
                if error_text:
                    self.errores.append({'error': error_text})
        if self.errores:
            self.Excepcion = "\n\r".join([er["error"] for er in self.errores])

        # --- Datos generales (misma lógica que A5.Consultar) ---
        if datos_generales is not None:
            self.tipo_persona = self._text(datos_generales, 'tipoPersona')
            self.tipo_doc = TIPO_CLAVE.get(self._text(datos_generales, 'tipoClave'))
            self.nro_doc = self._text(datos_generales, 'idPersona')
            self.cuit = self.nro_doc
            self.estado = self._text(datos_generales, 'estadoClave')
            self.es_sucesion = self._text(datos_generales, 'esSucesion')

            razon_social = self._text(datos_generales, 'razonSocial')
            if razon_social:
                self.denominacion = razon_social
            else:
                apellido = self._text(datos_generales, 'apellido')
                nombre = self._text(datos_generales, 'nombre')
                self.denominacion = "%s, %s" % (apellido, nombre)

            # Domicilio fiscal
            domicilio = self._find(datos_generales, 'domicilioFiscal')
            if domicilio is not None:
                self.direccion = self._text(domicilio, 'direccion')
                self.localidad = self._text(domicilio, 'localidad')
                self.provincia = PROVINCIAS.get(
                    self._text(domicilio, 'idProvincia'), '')
                self.cod_postal = self._text(domicilio, 'codPostal')
            self.domicilios = [self._elem_to_dict(domicilio)] if domicilio is not None else []
            self.domicilio = "%s - %s (%s) - %s" % (
                self.direccion, self.localidad, self.cod_postal, self.provincia)

        # --- Impuestos (de monotributo + régimen general) ---
        impuestos_elems = []
        for section in (datos_mt, datos_rg):
            if section is not None:
                impuestos_elems.extend(self._findall(section, 'impuesto'))
        self.impuestos = [
            int(self._text(imp, 'idImpuesto', '0'))
            for imp in impuestos_elems
        ]

        # --- Actividades ---
        actividades_elems = []
        if datos_rg is not None:
            actividades_elems.extend(self._findall(datos_rg, 'actividad'))
        if datos_mt is not None:
            actividades_elems.extend(
                self._findall(datos_mt, 'actividadMonotributista'))
        self.actividades = [
            int(self._text(act, 'idActividad', '0'))
            for act in actividades_elems
        ]

        # --- Monotributo categoría ---
        cat_mt = {}
        if datos_mt is not None:
            cat_elem = self._find(datos_mt, 'categoriaMonotributo')
            if cat_elem is not None:
                cat_mt = self._elem_to_dict(cat_elem)

        # Análisis final: IVA, monotributo, empleador (herencia de A4)
        self.analizar_datos(cat_mt)
        return not self.errores

    # --- Helpers para parseo XML ignorando namespaces ---

    @staticmethod
    def _find(element, local_name, recursive=False):
        """Busca hijo por nombre local, ignorando namespace."""
        if recursive:
            for elem in element.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag == local_name:
                    return elem
        else:
            for child in element:
                tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if tag == local_name:
                    return child
        return None

    @staticmethod
    def _findall(element, local_name):
        """Busca todos los hijos con nombre local."""
        results = []
        for child in element:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if tag == local_name:
                results.append(child)
        return results

    @staticmethod
    def _text(element, child_name, default=''):
        """Texto de un hijo por nombre local."""
        if element is None:
            return default
        for child in element:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if tag == child_name:
                return child.text or default
        return default

    @classmethod
    def _elem_to_dict(cls, element):
        """Convierte XML element a dict (para serialización/debug)."""
        if element is None:
            return {}
        result = {}
        for child in element:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if len(child):
                value = cls._elem_to_dict(child)
            else:
                value = child.text or ''
            if tag in result:
                if not isinstance(result[tag], list):
                    result[tag] = [result[tag]]
                result[tag].append(value)
            else:
                result[tag] = value
        return result
