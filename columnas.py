# ==========================================================
#  columnas.py - El "mapa" de tu plan de carga y de tus catalogos
#  Si algun dia quieres UNA COLUMNA NUEVA, agregala aqui
#  y el sistema la crea solo (sin perder datos).
#
#  rol  : quien la llena (OPS, ATC, TC), TODOS (cualquiera) o AUTO (se calcula sola)
#  tipo : texto, entero, dinero, fecha, lista, catalogo, calculado
# ==========================================================
from collections import namedtuple

Columna = namedtuple("Columna", "clave titulo rol tipo")

COLUMNAS = [
    Columna("finalizado", "✅", "TODOS", "check"),   # palomita: viaje terminado -> la unidad queda libre
    Columna("cancelado", "❌", "TODOS", "check"),    # tache: viaje cancelado -> la unidad queda libre
    Columna("eco", "ECO", "OPS", "catalogo"),
    Columna("operador", "OPERADOR", "OPS", "catalogo"),
    Columna("nomina", "NÓMINA", "OPS", "texto"),
    Columna("tipo_operacion", "TIPO OPERACION", "ATC", "catalogo"),
    Columna("cliente", "CLIENTE", "ATC", "catalogo"),
    Columna("origen", "ORIGEN", "ATC", "catalogo"),
    Columna("destino", "DESTINO", "ATC", "catalogo"),
    Columna("localidad_destino", "LOCALIDAD DESTINO", "ATC", "texto"),
    Columna("modalidad", "MODALIDAD", "ATC", "lista"),
    Columna("pedido", "PEDIDO", "ATC", "texto"),
    Columna("folio_cliente", "FOLIO CLIENTE", "ATC", "texto"),
    Columna("flete", "FLETE", "ATC", "dinero"),
    Columna("remolque_1", "REMOLQUE 1", "OPS", "catalogo"),
    Columna("folio_confirmacion", "FOLIO DE CONFIRMACIÓN", "ATC", "texto"),
    Columna("sello_1", "SELLO 1", "OPS", "texto"),
    Columna("sello_2", "SELLO 2", "OPS", "texto"),
    Columna("fumigacion", "FUMIGACION", "ATC", "lista"),
    Columna("barras_rep", "BARRAS REP", "OPS", "entero"),
    Columna("barras_real", "BARRAS REAL", "OPS", "entero"),
    Columna("dif", "DIF.", "AUTO", "calculado"),
    Columna("barras_entregadas", "BARRAS ENTREGADAS", "ATC", "entero"),
    Columna("devolucion", "DEVOLUCIÓN", "ATC", "lista"),
    Columna("estatus", "ESTATUS", "TODOS", "lista"),
    Columna("cita_carga", "CITA DE CARGA", "ATC", "fecha"),
    Columna("llegada_carga", "LLEGADA CARGA", "TC", "fecha"),
    Columna("salida_carga", "SALIDA CARGA", "TC", "fecha"),
    Columna("tiempo_carga", "TIEMPO CARGA", "AUTO", "calculado"),
    Columna("motivo_carga", "MOTIVO DE INCUMPLIMIENTO CARGA", "ATC", "texto"),
    Columna("cita_descarga", "CITA DE DESCARGA", "ATC", "fecha"),
    Columna("llegada_descarga", "LLEGADA DESCARGA", "TC", "fecha"),
    Columna("salida_descarga", "SALIDA DESCARGA", "OPS", "fecha"),
    Columna("tiempo_descarga", "TIEMPO DESCARGA", "AUTO", "calculado"),
    Columna("motivo_descarga", "MOTIVO DE INCUMPLIMIENTO DESCARGA", "ATC", "texto"),
    Columna("inicio_ruta", "INICIO DE RUTA", "OPS", "fecha"),
    Columna("llegada_destino", "LLEGADA A DESTINO", "OPS", "fecha"),
    Columna("tiempo_transito", "T. TRÁNSITO", "AUTO", "calculado"),
    Columna("motivo_transito", "MOTIVO DE INCUMPLIMIENTO TRANSITO", "ATC", "texto"),
    Columna("instrucciones", "INSTRUCCIONES ESPECIALES", "ATC", "texto"),
    Columna("factura", "FACTURA", "OPS", "texto"),
    Columna("posicionam", "POSICIONAM", "OPS", "texto"),
    Columna("cp_cargado", "CP CARGADO", "OPS", "texto"),
    Columna("cp_vacio", "CP VACIO", "OPS", "texto"),
    Columna("num_viaje", "# VIAJE", "OPS", "texto"),
    Columna("viaje_vacio", "VIAJE VACIO", "OPS", "texto"),
    Columna("direccion_origen", "DIRECCION ORIGEN", "ATC", "texto"),
    Columna("link_origen", "LINK ORIGEN", "ATC", "texto"),
    Columna("direccion_destino", "DIRECCION DESTINO", "ATC", "texto"),
    Columna("link_destino", "LINK DESTINO", "ATC", "texto"),
    Columna("t_servicio", "T SERVICIO", "AUTO", "calculado"),
    Columna("estadia", "ESTADÍA (EXCEDENTE)", "AUTO", "calculado"),   # tiempo extra segun el cliente (ver catalogo Estadias)
]

TIPO_POR_CLAVE = {c.clave: c.tipo for c in COLUMNAS}
TITULO_POR_CLAVE = {c.clave: c.titulo for c in COLUMNAS}

ROLES = ["TODOS", "OPS", "ATC", "TC"]

# Estados posibles de una unidad
ESTADOS_UNIDAD = [
    "DISPONIBLE", "CARGANDO", "EN RESGUARDO", "REGRESO VACIO",
    "TRANSITO CARGADO", "DESCARGANDO", "REGRESO CON DEVOLUCION",
    "REMOLQUE EN CARGA",
]

# Un viaje en estos estatus ya esta cerrado: su unidad queda LIBRE en la flota
ESTATUS_CERRADOS = ["DISPONIBLE", "CANCELADO"]

# Listas desplegables simples
LISTAS = {
    "modalidad": ["SPOT", "DEDICADO"],
    "fumigacion": ["SI", "NO"],
    "devolucion": ["SI", "NO"],
    # ESTATUS: se calcula solo, pero se puede cambiar a mano (borrarlo = volver al automatico)
    "estatus": ["PROGRAMADO"] + ESTADOS_UNIDAD + ["CANCELADO"],
}

# Situacion de cada vehiculo en el catalogo (vacio = OPERANDO)
SITUACIONES = ["OPERANDO", "CORRALON", "SINIESTRO", "TALLER", "NO DISPONIBLE"]

# Catalogos: tabla -> columnas (la primera es la llave unica, no se repite)
CATALOGOS = {
    "unidades": ["eco", "operador", "situacion", "placas"],   # placas = assets_id de la vista ZAM
    "remolques": ["remolque", "tipo", "placas", "situacion"],   # situacion: solo OPERANDO se puede asignar
    "operadores": ["nomina", "nombre", "telefono"],
    "clientes": ["nombre", "rfc"],
    "origenes": ["nombre", "direccion", "link"],
    "destinos": ["nombre", "localidad", "direccion", "link"],
    "tipos_operacion": ["nombre", "cliente"],
    "estadias": ["cliente", "aplica", "horas_libres"],   # aplica = SI/NO ; horas_libres = tiempo libre por parada
}

# Columna del plan -> (tabla, columna) del catalogo que le da sus opciones
CATALOGO_DE = {
    "eco": ("unidades", "eco"),
    "operador": ("operadores", "nombre"),
    "remolque_1": ("remolques", "remolque"),
    "tipo_operacion": ("tipos_operacion", "nombre"),
    "cliente": ("clientes", "nombre"),
    "origen": ("origenes", "nombre"),
    "destino": ("destinos", "nombre"),
}

# Columnas que se ven siempre, aunque sean de otro rol (para dar contexto)
CONTEXTO = ["finalizado", "cancelado", "pedido", "eco", "operador", "tipo_operacion", "cliente",
            "origen", "destino", "estatus", "cita_carga", "cita_descarga", "link_origen", "link_destino", "estadia"]


# ----------------------------------------------------------------------------------------------
#  PERMISOS POR PERFIL  ("zapatero a tu zapato")
#   pestanas : que pestanas ve cada perfil
#   agregar_borrar : puede agregar / borrar filas directo en la tabla del Plan del dia
#   editar   : False = el Plan del dia es solo lectura (se ve y se exporta, nada mas)
#   zam      : puede usar el boton "Actualizar ahora" del catalogo ZAM y aceptar coincidencias con ZAM
# ----------------------------------------------------------------------------------------------
PESTANAS = {
    "plan": "📋 Plan del día",
    "nuevo": "➕ Nuevo pedido",
    "mon": "📍 Monitoreo",
    "est": "📣 Estatus clientes",         # el estatus que TC le manda a cada cliente (captura de pantalla)
    "cat": "📚 Catálogos",
    "exp": "📤 Exportar CSV",
    "seg": "🔐 Usuarios y bitácora",      # solo usuarios MASTER
}

PERMISOS = {
    "ATC":   {"pestanas": ["plan", "nuevo", "mon", "cat", "exp"], "agregar_borrar": True,  "editar": True,  "zam": True, "monitoreo_editar": False},
    "OPS":   {"pestanas": ["plan", "mon", "cat", "exp"],          "agregar_borrar": False, "editar": True,  "zam": True, "monitoreo_editar": False},
    "TC":    {"pestanas": ["plan", "mon", "est", "exp"],                 "agregar_borrar": False, "editar": True,  "zam": False, "monitoreo_editar": True},
    "TODOS": {"pestanas": ["plan", "exp"],                        "agregar_borrar": False, "editar": False, "zam": False, "monitoreo_editar": False},
}

# Que significa "pendiente" para cada perfil (filtro rapido del Plan del dia)
PENDIENTES = {
    "ATC": "Sin pedido",
    "OPS": "Sin unidad u operador",
    "TC": "Sin llegada o salida registrada",
}

# Datos que debe tener un viaje para habilitar su FICHA (la pantalla para captura)
FICHA_REQUERIDOS = ["pedido", "eco", "operador", "modalidad", "remolque_1",
                    "origen", "destino", "cliente", "cita_carga", "cita_descarga"]


# Cuando un viaje tiene TODOS estos tiempos capturados (ATC: citas · TC: llegadas/salida de carga · OPS: salida de descarga)
# el TMS lo marca solo como ✅ finalizado y la unidad y el operador quedan DISPONIBLES para otro viaje.
TIEMPOS_PARA_FINALIZAR = ["cita_carga", "llegada_carga", "salida_carga",
                          "cita_descarga", "llegada_descarga", "salida_descarga"]
