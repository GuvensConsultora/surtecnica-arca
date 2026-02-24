# Clase WS pura Python para el servicio de padrón A13
# Por qué: ARCA reemplazó A4/A5 por A13 como servicio recomendado.
# A13 usa la misma interfaz SOAP getPersona() que A5, solo cambia el WSDL.
# Patrón: herencia simple — reutilizamos toda la lógica de A5 sin duplicar código.
from pyafipws.ws_sr_padron import WSSrPadronA5


class WSSrPadronA13(WSSrPadronA5):
    """WS Padrón A13 — misma interfaz que A5, distinto endpoint WSDL."""

    # Por qué: la URL se inyecta desde afipws_connection.connect() via Conectar(),
    # pero dejamos el WSDL por defecto apuntando a A13 producción.
    WSDL = "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA13?WSDL"

    # Por qué: False evita que pyafipws fuerce la URL de homologación
    # (mismo parche que aplica l10n_ar_afipws en connect() para A4/A5).
    HOMO = False
