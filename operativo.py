# =====================================================================
#  operativo.py - "Levantar operativo": Reporte de Robo listo para WhatsApp
#
#  Se elige el ECO del tracto y el reporte se llena solo:
#    - catalogo de Vehiculos : placa, VIN, marca, modelo, año
#    - plan de carga         : viaje activo/asignado -> origen, destino, operador, caja
#    - ZAM                   : si el plan no trae el viaje, operador y folio de ZAM
#    - catalogo Operadores   : telefono del operador
#    - Omnitracs             : coordenadas del tracto y de la caja
#  Todo se puede corregir a mano antes de copiarlo.
# =====================================================================
from datetime import date, datetime, timedelta

import pandas as pd

import base_datos as bd
import calculos
import configuracion as cfg
import omnitracs
from columnas import ESTATUS_CERRADOS

TIPO_UNIDAD = "TRACTOCAMION"
COLOR_FIJO = "BLANCO CON LOGO DE ESTRELLA ROJA"
RAZON_SOCIAL = "VIAJES ESTRELLA ROJA"
# Toda la flota de tractos es Freightliner Cascadia (si el catalogo trae otro dato, gana el catalogo)
MARCA_FLOTA = "FREIGHTLINER"
MODELO_FLOTA = "CASCADIA"


def anio_por_eco(eco):
    """El año sale de los 2 primeros digitos del ECO: 2510 -> 2025, 2144 -> 2021."""
    e = "".join(ch for ch in str(eco or "") if ch.isdigit())
    return f"20{e[:2]}" if len(e) >= 3 else ""

# (clave, etiqueta en el reporte). El orden es el del reporte.
CAMPOS = [
    ("tipo_unidad", "TIPO DE UNIDAD"),
    ("eco", "ECONOMICO"),
    ("placa", "PLACA"),
    ("vin", "VIN"),
    ("marca", "MARCA"),
    ("anio", "AÑO"),
    ("modelo", "MODELO"),
    ("color", "COLOR"),
    ("razon_social", "RAZÓN SOCIAL"),
    ("origen", "ORIGEN"),
    ("destino", "DESTINO"),
    ("mercancia", "MERCANCIA"),
    ("operador", "NOMBRE Y NUMERO DEL OPERADOR"),
    ("telefono", "TEL"),
    ("caja", "DATOS DE CAJA / CONTENEDOR"),
    ("ubicacion", "UBICACIÓN (LIGA Y/O COORDENADAS)"),
]
ETIQUETA = dict(CAMPOS)


def _t(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if v is pd.NaT:
        return ""
    s = " ".join(str(v).split())
    return "" if s.upper() in ("NAN", "NONE", "NAT") else s.removesuffix(".0")


def _k(v):
    return _t(v).upper()


def liga(lat, lon):
    """Liga de Google Maps para compartir (abre el punto exacto)."""
    return f"https://www.google.com/maps/place/{float(lat):.6f},{float(lon):.6f}"


def coordenadas(lat, lon):
    return f"{float(lat):.6f}, {float(lon):.6f}"


# ---------------------------------------------------------- posiciones de Omnitracs
def posiciones(ecos_extra=()):
    """Posiciones de Omnitracs con 'clave' = ECO del tracto (igual que en Monitoreo)."""
    pos = bd.leer_posiciones()
    if pos.empty:
        return pos.assign(clave=pd.Series(dtype=object), exacto=pd.Series(dtype=bool))
    tractos = {omnitracs.normalizar_alias(e) for e in bd.opciones_catalogo("unidades")}
    tractos |= {omnitracs.normalizar_alias(e) for e in ecos_extra if e}
    remolques = {omnitracs.normalizar_alias(r) for r in bd.opciones_catalogo("remolques")}
    clas = pos["alias"].map(lambda a: omnitracs.clasificar_alias(a, tractos, remolques))
    pos["clave"] = clas.map(lambda x: x[1] if x[0] == "TRACTO" else "")
    pos["exacto"] = pos["alias"].map(omnitracs.normalizar_alias) == pos["clave"]
    return pos


def posicion_tracto(eco, pos=None):
    """Ultima posicion del tracto (dict) o None. Si tiene varios aliases, gana el exacto y el mas reciente."""
    pos = posiciones([eco]) if pos is None else pos
    if pos.empty or "clave" not in pos:
        return None
    fila = pos[pos["clave"] == omnitracs.normalizar_alias(eco)]
    if len(fila):
        fila = fila.sort_values(["exacto", "fecha"]).tail(1)
    return _posicion(fila)


def posicion_caja(remolque, pos=None):
    """Ultima posicion de la caja/remolque (dict) o None."""
    if not _t(remolque):
        return None
    pos = bd.leer_posiciones() if pos is None else pos
    if pos.empty:
        return None
    clave = omnitracs.normalizar_alias(remolque)
    fila = pos[pos["alias"].map(omnitracs.normalizar_alias) == clave]
    return _posicion(fila)


def _posicion(fila):
    fila = fila.dropna(subset=["latitud", "longitud"]) if len(fila) else fila
    if fila is None or fila.empty:
        return None
    r = fila.sort_values("fecha").iloc[-1]
    fecha = pd.to_datetime(r.get("fecha"), errors="coerce")
    vel = r.get("velocidad")
    return {
        "alias": _t(r.get("alias")),
        "lat": float(r["latitud"]), "lon": float(r["longitud"]),
        "fecha": fecha,
        "velocidad": float(vel) if vel is not None and pd.notna(vel) else None,
        "ignicion": _t(r.get("ignicion")),
        "lugar": _t(r.get("ubicacion")),
        "liga": liga(r["latitud"], r["longitud"]),
    }


def hace(fecha, ahora=None):
    if fecha is None or pd.isna(fecha):
        return "sin fecha"
    seg = int(((ahora or datetime.now()) - fecha.to_pydatetime()).total_seconds())
    if seg < 90:
        return "hace 1 min"
    if seg < 3600:
        return f"hace {seg // 60} min"
    if seg < 86400:
        return f"hace {seg // 3600} h {seg % 3600 // 60} min"
    return f"hace {seg // 86400} d"


def texto_ubicacion(p):
    """Lo que va en UBICACIÓN: coordenadas + liga (+ colonia/municipio si Omnitracs lo manda)."""
    if not p:
        return "SIN SEÑAL DE OMNITRACS"
    partes = [coordenadas(p["lat"], p["lon"]), p["liga"]]
    if p.get("lugar"):
        partes.insert(1, p["lugar"])
    return "\n".join(partes)


# ---------------------------------------------------------- viaje de la unidad
def viaje_activo(eco, hoy=None):
    """El viaje abierto (activo o asignado) del tracto en el plan de carga: el mas antiguo que sigue abierto.
    Si no tiene, None."""
    hoy = hoy or date.today()
    desde = hoy - timedelta(days=getattr(cfg, "DIAS_ATRAS_ABIERTOS", 15))
    df = bd.leer_viajes(desde, hoy + timedelta(days=30))
    if df.empty:
        return None
    df = df[df["eco"].map(_k) == _k(eco)]
    if df.empty:
        return None
    df = calculos.agregar_calculados(df)
    df = df[~df["estatus"].isin(ESTATUS_CERRADOS)]
    if df.empty:
        return None
    return df.sort_values(["fecha_plan", "cita_carga", "id"], na_position="last").iloc[0].to_dict()


# ---------------------------------------------------------- datos de la unidad
def datos_unidad(eco):
    """Todo lo que el sistema sabe del tracto para el reporte. Devuelve (datos, info) donde
    info explica de donde salio cada cosa (viaje del plan, ZAM, etc.)."""
    eco = _t(eco)
    ke = _k(eco)
    info = {"viaje": "", "fuente": "", "remolque": ""}

    uni = bd.leer_catalogo("unidades")
    u = uni[uni["eco"].map(_k) == ke]
    u = u.iloc[0].to_dict() if len(u) else {}

    v = viaje_activo(eco)

    zam = bd.leer_zam()
    z = None
    if not zam.empty:
        zz = zam[zam["eco"].map(_k) == ke]
        if len(zz):
            z = zz.sort_values("salida_plan").iloc[-1].to_dict()

    ops = bd.leer_catalogo("operadores")
    por_nomina = {_k(r["nomina"]): r for r in ops.to_dict("records") if _k(r["nomina"])}
    por_nombre = {_k(r["nombre"]): r for r in ops.to_dict("records") if _k(r["nombre"])}

    origen = destino = nombre = nomina = remolque = ""
    if v is not None:
        origen, destino = _t(v.get("origen")), _t(v.get("destino"))
        nombre, nomina = _t(v.get("operador")), _t(v.get("nomina"))
        remolque = _t(v.get("remolque_1"))
        folio = _t(v.get("num_viaje")) or _t(v.get("pedido"))
        info["viaje"] = f"Viaje del plan #{_t(v.get('id'))}" + (f" · folio {folio}" if folio else "") \
            + (f" · {_t(v.get('cliente'))}" if _t(v.get("cliente")) else "")
        info["fuente"] = "PLAN"
    if z is not None:
        nomina = nomina or _t(z.get("nomina"))
        if v is None:                                  # sin viaje en el plan: lo que dice ZAM
            origen = f"ZAM origen {_t(z.get('origen_id'))}" if _t(z.get("origen_id")) else ""
            destino = f"ZAM destino {_t(z.get('destino_id'))}" if _t(z.get("destino_id")) else ""
        if not info["viaje"]:
            info["viaje"] = f"Viaje abierto en ZAM · folio {_t(z.get('shipment_id'))}"
            info["fuente"] = "ZAM"
    reg = por_nomina.get(_k(nomina)) or por_nombre.get(_k(nombre)) or {}
    nombre = nombre or _t(reg.get("nombre")) or _t(u.get("operador"))
    nomina = nomina or _t(reg.get("nomina"))
    if not reg and nombre:
        reg = por_nombre.get(_k(nombre)) or {}
    telefono = _t(reg.get("telefono"))

    caja = ""
    if remolque:
        rem = bd.leer_catalogo("remolques")
        r = rem[rem["remolque"].map(_k) == _k(remolque)]
        r = r.iloc[0].to_dict() if len(r) else {}
        partes = [f"CAJA {remolque}"]
        if _t(r.get("placas")):
            partes.append(f"PLACAS {_t(r.get('placas'))}")
        if _t(r.get("vin")):
            partes.append(f"VIN {_t(r.get('vin'))}")
        if _t(r.get("tipo")):
            partes.append(_t(r.get("tipo")))
        caja = " · ".join(partes)
    info["remolque"] = remolque

    operador = nombre
    if nombre and nomina:
        operador = f"{nombre} (NÓMINA {nomina})"
    elif nomina:
        operador = f"NÓMINA {nomina}"

    datos = {
        "tipo_unidad": TIPO_UNIDAD,
        "eco": eco,
        "placa": _t(u.get("placas")),
        "vin": _t(u.get("vin")),
        "marca": _t(u.get("marca")) or MARCA_FLOTA,
        "anio": _t(u.get("anio")) or anio_por_eco(eco),
        "modelo": _t(u.get("modelo")) or MODELO_FLOTA,
        "color": COLOR_FIJO,
        "razon_social": RAZON_SOCIAL,
        "origen": origen,
        "destino": destino,
        "mercancia": "",
        "operador": operador,
        "telefono": telefono,
        "caja": caja,
        "ubicacion": texto_ubicacion(posicion_tracto(eco)),
    }
    return datos, info


# ---------------------------------------------------------- texto para WhatsApp
def texto_whatsapp(datos, titulo="REPORTE DE ROBO"):
    """Texto plano para pegar en WhatsApp (el titulo va en negritas con *...*)."""
    lineas = [f"*{titulo}*", ""]
    for clave, etiqueta in CAMPOS:
        valor = (datos.get(clave) or "").strip()
        if "\n" in valor:
            lineas.append(f"*{etiqueta}:*")
            lineas.extend(l.strip() for l in valor.splitlines() if l.strip())
        else:
            lineas.append(f"*{etiqueta}:* {valor}")
    return "\n".join(lineas)


# ---------------------------------------------------------- registro (historial de operativos)
def registrar(quien, datos, texto):
    usuario = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    with bd.conexion() as con:
        con.execute("INSERT INTO operativos (fecha, usuario, eco, remolque, tipo, texto) VALUES (?, ?, ?, ?, ?, ?)",
                    [bd.AHORA_TXT(), usuario, _t(datos.get("eco")), _t(datos.get("caja")), "ROBO", texto])
    bd.bitacora(quien, "OPERATIVO_ROBO", f"unidad {_t(datos.get('eco'))}", texto[:3900])


def historial(limite=30):
    with bd.conexion() as con:
        return pd.read_sql_query("SELECT fecha, usuario, eco, remolque AS caja, texto FROM operativos "
                                 "ORDER BY id DESC LIMIT ?", con, params=[limite])
