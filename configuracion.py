# ==========================================================
#  configuracion.py  -  Aqui van los "ajustes" de tu TMS
#  Solo cambia lo que esta entre comillas o los numeros.
#
#  Las CONTRASENAS ya no van aqui. Se leen, en este orden, de:
#    1) variables de entorno (OMNITRACS_USUARIO, OMNITRACS_CONTRASENA, OMNITRACS_CLIENTE)
#    2) el archivo configuracion_local.py (NO lo compartas ni lo subas a ningun lado)
# ==========================================================
import os

try:
    import configuracion_local as _local
except ImportError:
    _local = None


def _secreto(nombre):
    """Lee un dato sensible de variable de entorno o de configuracion_local.py."""
    valor = os.environ.get(nombre) or getattr(_local, nombre, None)
    return valor or "ESCRIBE_AQUI"


# ---- Datos de Omnitracs ----
OMNITRACS_URL = "https://command.omnitracs.com/customer-services/VehicleActivityService"
OMNITRACS_USUARIO = _secreto("OMNITRACS_USUARIO")
OMNITRACS_CONTRASENA = _secreto("OMNITRACS_CONTRASENA")
OMNITRACS_CLIENTE = _secreto("OMNITRACS_CLIENTE")

# ---- Como consultar posiciones ----
MINUTOS_HISTORIAL = 120          # cuantos minutos hacia atras pedimos a Omnitracs
AJUSTE_HORAS_RESPUESTA = -6      # si las horas salen mal, prueba con 0
SEGUNDOS_ENTRE_CONSULTAS = 60    # cada cuantos segundos se actualiza
CONSULTAR_AUTOMATICAMENTE = True # True = la pagina consulta sola Omnitracs

# ---- Vista de viajes abiertos (ZAM / ConsultaVista) ----
# De aqui se dan de alta ECOs y nominas nuevos, se guardan las placas del tracto
# y se marcan como ACTIVOS (no asignables) las unidades y operadores con viaje 'open'.
ZAM_URL = "https://er-zam-web01.estrellaroja.com.mx/ConsultaVista/Consulta.aspx"
MINUTOS_ENTRE_SYNC_ZAM = 10      # cada cuantos minutos se actualiza el catalogo
SYNC_ZAM_AUTOMATICO = True       # True = se actualiza solo mientras el TMS este abierto

# ---- Plan de carga ----
DIAS_ATRAS_ABIERTOS = 15         # viajes sin terminar se arrastran hasta 15 dias

# ---- Archivos ----
ARCHIVO_BD = "tms.db"            # aqui se guarda TODA tu informacion (los viajes nunca se borran solos)
RESPALDOS_DIAS = 150             # copias diarias de tms.db que se guardan en la carpeta respaldos (150 = 5 meses)

# ---- Coincidencias con ZAM (el TMS NO sustituye a ZAM: solo toma datos de el) ----
# Si un viaje del plan no tiene PEDIDO pero coincide con un viaje de ZAM (misma unidad y
# horarios parecidos) el TMS pregunta si quieres traer el numero de viaje ZAM como PEDIDO.
ZAM_TOLERANCIA_HORAS = 4         # que tan parecidas deben ser las horas de cita vs. horas planeadas en ZAM

# Cuando el viaje ya tiene PEDIDO igual al numero de viaje ZAM y ZAM lo reporta con alguno de
# estos estatus, el TMS lo marca solo como ✅ finalizado / ❌ cancelado en el Plan del dia.
# (En minusculas. Revisa en Catalogos > Viajes ZAM los estatus reales que trae tu vista y ajusta.)
ZAM_AUTO_CERRAR = True
ZAM_ESTATUS_FINALIZADO = ["closed", "close", "complete", "completed", "finished", "delivered", "done",
                          "finalized", "finalizado", "cerrado"]
ZAM_ESTATUS_CANCELADO = ["cancelled", "canceled", "cancel", "cancelado", "void", "voided", "rejected"]

# ---- Ficha del pedido (pantalla para tomar captura) ----
# Si la liga de la ficha abre en otra direccion que no es la correcta, escribe aqui la liga base
# de tu TMS, por ejemplo "http://MIPC.estrellaroja.com.mx:8501". Vacio = se detecta sola.
URL_BASE_TMS = ""

# ---- Estadias: tiempo libre por cliente (horas por parada: carga y descarga) ----
# Solo se usa para llenar el catalogo la PRIMERA vez. Despues se edita en Catalogos > Estadias.
# Clientes que no esten en la lista = N/A (no se cuenta tiempo extra).
ESTADIAS_INICIALES = {
    "SABRITAS": 24, "BIMBO": 24, "WATCOM": 24, "SAMSUNG": 24, "CEREALES Y PASTAS": 24,
    "QUALAMEX": 24, "KUEHNE NAGEL": 12, "ACCEL": 8, "PABISAN": 8, "PEPSICO": 24,
    "GRUPO VIDA": 24, "MANANTIALES": 24,
}


# ---- Monitoreo: cuando se enciende una alerta ----
MON_CITA_PROXIMA_HORAS = 2          # 🟠 falta menos de esto para la cita de descarga (o ya se pasó = 🔴)
MON_SIN_REPORTAR_MIN = 60           # 🟠 la unidad lleva mas de esto sin mandar posicion
MON_DETENIDA_TRANSITO_MIN = 60      # 🟠 unidad CARGADA en transito, detenida mas de este tiempo
MON_METROS_PARA_CONSIDERAR_MOVIDA = 300   # cuanto debe moverse para dejar de contarla como "detenida desde"
MON_ESTATUS_MANUAL_HORAS = 12      # cuanto dura un ESTATUS 1/2 puesto a mano por TC (despues vuelve al automatico)

# ---- Estatus para clientes (pestaña de TC) ----
UBICACION_EN_TEXTO = True          # pone nombre (colonia, municipio, estado) a la ubicacion del command (usa OpenStreetMap)
ESTATUS_PEPSICO_HORAS_EXTRA = 2     # PEPSICO DEDICADO: horas que se le suman a la cita de carga y de descarga del plan
