# ==========================================================
#  monitoreo.py - El "Excel de monitoreo" hecho automatico
#
#  Una fila por UNIDAD (ECO). Casi todo se llena solo:
#    - VIAJE, CLIENTE, ORIGEN, DESTINO, REMOLQUE, CITAS ........ del viaje activo (plan de carga) o de ZAM
#    - OPERADOR, NOMINA, TELEFONO ............................... de los catalogos
#    - ESTATUS 1 ................................................ segun la etapa del viaje:
#          sin viaje = 1 DISPONIBLE · PROGRAMADO = 2 ASIGNADO · CARGANDO = 3 EN CARGA
#          TRANSITO CARGADO = 4 CARGADO · DESCARGANDO = 5 DESCARGA
#    - ESTATUS 2 ................................................ segun Omnitracs: en movimiento = 1 EN TRANSITO,
#                                                                 detenida = 2 DETENIDO (3 RESGUARDO es manual)
#    - UBICACION / HORA / VEL / IGNICION / DURACION ............. de Omnitracs
#    - TIEMPO RESTANTE .......................................... cita - ahora
#
#  A MANO solo se capturan: ESTATUS 1 / ESTATUS 2 (si quieres cambiar el automatico), TELEFONO y OBSERVACIONES.
#    * TELEFONO  -> se guarda en el catalogo de OPERADORES.
#    * ESTATUS 1 -> se guarda en el catalogo de UNIDADES (y si es 7 TALLER / 8 CORRALON / ZPRESTAMO
#                   tambien cambia la SITUACION de la unidad, asi ya no se puede asignar a viajes).
#    * Un estatus puesto a mano se respeta hasta que el sistema detecte una etapa nueva
#      (por ejemplo, le asignan un viaje nuevo): entonces vuelve solo al automatico.
# ==========================================================
import re
from datetime import datetime

import pandas as pd

import base_datos as bd
import calculos
import configuracion as cfg
import omnitracs
from columnas import (ESTATUS1, ESTATUS1_DE_SITUACION, ESTATUS1_DE_VIAJE, ESTATUS2, SITUACION_DE_ESTATUS1,
                      SITUACIONES_MOVIBLES)

MIN_SENAL_OK = getattr(cfg, "MINUTOS_SENAL_OK", 15)             # 🟢 reporto hace menos de esto
HORAS_SENAL_ALERTA = getattr(cfg, "HORAS_SENAL_ALERTA", 24)     # 🟡 hasta esto; despues 🔴
VELOCIDAD_MAXIMA = getattr(cfg, "VELOCIDAD_MAXIMA", 100)        # 🚨 exceso de velocidad
HORAS_ALERTA_DESCARGA = getattr(cfg, "HORAS_ALERTA_DESCARGA", 4)  # ⏳ mucho tiempo descargando


# ---------------------------------------------------------- utilidades
def _t(v):
    """Texto limpio; vacio/NaN -> ''."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _k(v):
    return _t(v).upper()


def _fmt_td(td):
    """Duracion corta: '2h 15m', '-1h 05m', '3d 4h'. Vacio si no hay dato."""
    if td is None or pd.isna(td):
        return ""
    seg = int(td.total_seconds())
    signo = "-" if seg < 0 else ""
    seg = abs(seg)
    d, resto = divmod(seg, 86400)
    h, resto = divmod(resto, 3600)
    m = resto // 60
    if d:
        return f"{signo}{d}d {h}h"
    if h:
        return f"{signo}{h}h {m:02d}m"
    return f"{signo}{m}m"


def _hace(td):
    if td is None or pd.isna(td):
        return ""
    seg = max(int(td.total_seconds()), 0)
    if seg < 3600:
        return f"hace {seg // 60} min"
    if seg < 86400:
        return f"hace {seg // 3600} h"
    return f"hace {seg // 86400} d"


# ---------------------------------------------------------- posiciones Omnitracs (antes vivian en app.py)
def posiciones_por_eco(ecos_extra=()):
    """Ultima posicion de Omnitracs por alias, clasificada en TRACTO / REMOLQUE / FUERA de catalogo.
    'clave' solo se llena en tractos (es el ECO con el que se empata contra el plan)."""
    pos = bd.leer_posiciones()
    if pos.empty:
        return pos.assign(clave=pd.Series(dtype=object), tipo=pd.Series(dtype=object),
                          en_mov=pd.Series(dtype=bool), exacto=pd.Series(dtype=bool))
    tractos = {omnitracs.normalizar_alias(e) for e in bd.opciones_catalogo("unidades")}
    tractos |= {omnitracs.normalizar_alias(e) for e in ecos_extra if e}
    remolques = {omnitracs.normalizar_alias(r) for r in bd.opciones_catalogo("remolques")}
    clas = pos["alias"].map(lambda a: omnitracs.clasificar_alias(a, tractos, remolques))
    pos["tipo"] = clas.map(lambda x: x[0])
    pos["clave"] = clas.map(lambda x: x[1] if x[0] == "TRACTO" else "")
    pos["en_mov"] = pos["velocidad"].map(omnitracs.en_movimiento)
    pos["exacto"] = pos["alias"].map(omnitracs.normalizar_alias) == pos["clave"]
    return pos


def una_por_eco(pos):
    """Si un ECO tiene varios aliases, se queda con el exacto y el mas reciente."""
    con_clave = pos[pos["clave"] != ""].sort_values(["exacto", "fecha"])
    return con_clave.drop_duplicates("clave", keep="last")


# ---------------------------------------------------------- viaje que corresponde a cada unidad
def viaje_por_eco():
    """El viaje que 'trae' cada unidad: el ACTIVO (el mas reciente) o, si no tiene,
    el ultimo que quedo DESCARGANDO (TC lleno llegadas y OPS aun no llena la salida)."""
    t = calculos.agregar_calculados(bd.leer_viajes("0000-01-01", "9999-12-31"))
    if t.empty:
        return t
    t["_activo"] = calculos.es_activo(t)
    t = t[t["_activo"] | (t["estatus"] == "DESCARGANDO")]
    t = t[t["eco"].map(_t) != ""].copy()
    t["_eco"] = t["eco"].map(_k)
    t["_ref"] = t["cita_carga"].fillna(pd.to_datetime(t["fecha_plan"], errors="coerce"))
    t = t.sort_values(["_activo", "_ref", "id"])
    return t.drop_duplicates("_eco", keep="last").set_index("_eco", drop=False)


# ---------------------------------------------------------- sincronizar + construir la tabla
def _leer_monitoreo():
    with bd.conexion() as con:
        df = pd.read_sql_query("SELECT * FROM monitoreo", con)
    return {r["eco"]: r for r in df.to_dict("records")}


def _upsert(con, eco, **campos):
    con.execute("INSERT OR IGNORE INTO monitoreo (eco) VALUES (?)", [eco])
    if campos:
        sets = ", ".join(f"{k} = ?" for k in campos)
        con.execute(f"UPDATE monitoreo SET {sets} WHERE eco = ?", list(campos.values()) + [eco])


def _aprender_zam(zam, tipos_cliente):
    """Aprende de los viajes del plan ya enlazados (# VIAJE = folio ZAM) y de lo que captura el monitorista:
    folio -> tipo de operacion / cliente, y ruta ZAM (origen_id, destino_id) -> tipo de operacion.
    Devuelve {folio: (tipo, cliente, fuente)} para TODOS los folios abiertos en ZAM."""
    if zam.empty:
        return {}
    with bd.conexion() as con:
        sabidos = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
            "SELECT shipment_id, tipo_operacion, cliente, fuente FROM zam_clasif")}
        todos = bd.leer_viajes("0000-01-01", "9999-12-31")
        enlazados = {}
        for nv, tipo, cli in zip(todos["num_viaje"], todos["tipo_operacion"], todos["cliente"]):
            if _t(nv) and _t(tipo):
                enlazados[_t(nv)] = (_t(tipo), _t(cli) or tipos_cliente.get(_k(tipo), ""))
        for r in zam.to_dict("records"):
            f = _t(r["shipment_id"])
            if f in enlazados and (f not in sabidos or sabidos[f][2] != "MANUAL") and f not in sabidos:
                tipo, cli = enlazados[f]
                con.execute("INSERT OR REPLACE INTO zam_clasif VALUES (?, ?, ?, 'PLAN', ?)",
                            [f, tipo, cli, bd.AHORA_TXT()])
                con.execute("INSERT INTO zam_rutas VALUES (?, ?, ?, 1) ON CONFLICT(origen_id, destino_id, tipo_operacion) "
                            "DO UPDATE SET n = n + 1", [_t(r["origen_id"]), _t(r["destino_id"]), tipo])
                sabidos[f] = (tipo, cli, "PLAN")
        rutas = pd.read_sql_query("SELECT origen_id, destino_id, tipo_operacion, n FROM zam_rutas", con)
    res = {}
    for r in zam.to_dict("records"):
        f = _t(r["shipment_id"])
        if f in sabidos:
            tipo, cli, fuente = sabidos[f]
            res[f] = (_t(tipo), _t(cli) or tipos_cliente.get(_k(tipo), ""), fuente)
            continue
        cand = rutas[(rutas["origen_id"] == _t(r["origen_id"])) & (rutas["destino_id"] == _t(r["destino_id"]))]
        if cand.empty:                      # misma ruta no vista: probar solo por destino
            c2 = rutas[rutas["destino_id"] == _t(r["destino_id"])].groupby("tipo_operacion")["n"].sum()
            if len(c2) and c2.max() / c2.sum() >= 0.6:
                tipo = c2.idxmax()
                res[f] = (tipo, tipos_cliente.get(_k(tipo), ""), "RUTA")
            continue
        tipo = cand.sort_values("n").iloc[-1]["tipo_operacion"]
        res[f] = (_t(tipo), tipos_cliente.get(_k(tipo), ""), "RUTA")
    return res


def clasificar_folio(folio, tipo, tipos_cliente, origen_id="", destino_id=""):
    """El monitorista dice de que tipo de operacion es un folio ZAM: se recuerda y ayuda a clasificar los siguientes."""
    folio, tipo = _t(folio), _t(tipo)
    cli = tipos_cliente.get(_k(tipo), "")
    with bd.conexion() as con:
        if not tipo:
            con.execute("DELETE FROM zam_clasif WHERE shipment_id = ?", [folio])
            return
        con.execute("INSERT OR REPLACE INTO zam_clasif VALUES (?, ?, ?, 'MANUAL', ?)", [folio, tipo, cli, bd.AHORA_TXT()])
        con.execute("INSERT INTO zam_rutas VALUES (?, ?, ?, 1) ON CONFLICT(origen_id, destino_id, tipo_operacion) "
                    "DO UPDATE SET n = n + 1", [_t(origen_id), _t(destino_id), tipo])


def sincronizar(ahora=None):
    """Calcula el estado de cada unidad, GUARDA estatus 1/2 (y situacion si aplica) en el catalogo
    de unidades y devuelve la tabla de monitoreo (un renglon por unidad)."""
    ahora = ahora or datetime.now()
    uni = bd.leer_catalogo("unidades")
    ops = bd.leer_catalogo("operadores")
    viajes = viaje_por_eco()
    zam = bd.leer_zam()
    pos = una_por_eco(posiciones_por_eco(uni["eco"].tolist())) if len(uni) else pd.DataFrame()
    pos_d = {r["clave"]: r for r in pos.to_dict("records")} if len(pos) else {}
    mon = _leer_monitoreo()
    tipos_cat = bd.leer_catalogo("tipos_operacion")
    tipos_cliente = {_k(n): _t(c) for n, c in zip(tipos_cat["nombre"], tipos_cat["cliente"]) if _k(n)}
    clasif = _aprender_zam(zam, tipos_cliente)

    por_nomina = {_k(r["nomina"]): r for r in ops.to_dict("records") if _k(r["nomina"])}
    por_nombre = {_k(r["nombre"]): r for r in ops.to_dict("records") if _k(r["nombre"])}
    zam_d = {}
    if not zam.empty:
        for r in zam.to_dict("records"):
            zam_d.setdefault(_k(r["eco"]), r)

    filas, cambios_cat, cambios_mon = [], [], []
    for u in uni.to_dict("records"):
        eco = _t(u["eco"])
        if not eco:
            continue
        ke = _k(eco)
        v = viajes.loc[ke] if (len(viajes) and ke in viajes.index) else None
        z = zam_d.get(ke)
        m = mon.get(eco, {})
        situacion = _k(u.get("situacion")) or "OPERANDO"
        sin_plan = False

        # ---------------- ESTATUS 1 automatico
        def auto1(sit):
            if sit in ESTATUS1_DE_SITUACION:
                return ESTATUS1_DE_SITUACION[sit]
            if sit == "SINIESTRO":
                return ""
            if v is not None:
                return ESTATUS1_DE_VIAJE.get(v["estatus"], "2 ASIGNADO")
            if z is not None:
                return "2 ASIGNADO"
            return "1 DISPONIBLE"

        est1_auto = auto1(situacion)
        if v is None and z is not None and situacion == "OPERANDO":
            sin_plan = True
        man1, base1 = _t(m.get("est1_manual")), _t(m.get("est1_base"))
        mon_cambios = {}
        if man1:
            # el estatus puesto a mano se respeta hasta que aparezca una etapa NUEVA de viaje
            if est1_auto != base1 and est1_auto != "1 DISPONIBLE":
                man1 = ""
                mon_cambios.update(est1_manual=None, est1_base=None, est1_fecha=None)
        if man1:
            # un estatus manual mueve la SITUACION de la unidad en el catalogo
            destino_sit = SITUACION_DE_ESTATUS1.get(man1)
            nueva_sit = situacion
            if situacion in SITUACIONES_MOVIBLES:
                nueva_sit = destino_sit or "OPERANDO"
            if nueva_sit != situacion:
                cambios_cat.append(("situacion", nueva_sit, eco))
                situacion = nueva_sit
                est1_auto = auto1(situacion)
            if est1_auto == man1:        # ya coincide con el automatico: no hace falta el manual
                man1 = ""
                mon_cambios.update(est1_manual=None, est1_base=None, est1_fecha=None)
        est1 = man1 or est1_auto

        # ---------------- ESTATUS 2 automatico (Omnitracs)
        p = pos_d.get(ke)
        est2_auto = ""
        fecha_pos = pd.NaT
        if p is not None:
            fecha_pos = pd.to_datetime(p["fecha"], errors="coerce")
            est2_auto = "1 EN TRANSITO" if p["en_mov"] else "2 DETENIDO"
        man2, base2 = _t(m.get("est2_manual")), _t(m.get("est2_base"))
        if man2 and est2_auto and est2_auto != base2:
            man2 = ""                       # la unidad cambio de movimiento: vuelve al automatico
            mon_cambios.update(est2_manual=None, est2_base=None, est2_fecha=None)
        if man2 and est2_auto == man2:
            man2 = ""
            mon_cambios.update(est2_manual=None, est2_base=None, est2_fecha=None)
        est2 = man2 or est2_auto
        # desde cuando esta en ese estado (para DURACION)
        desde = pd.to_datetime(m.get("est2_desde"), errors="coerce")
        if est2_auto and _t(m.get("est2_auto")) != est2_auto:
            desde = fecha_pos if pd.notna(fecha_pos) else pd.Timestamp(ahora)
            mon_cambios.update(est2_auto=est2_auto, est2_desde=desde.strftime("%Y-%m-%d %H:%M:%S"))
        if mon_cambios:
            cambios_mon.append((eco, mon_cambios))

        # ---------------- guardar en el catalogo de unidades lo que cambio
        if _t(u.get("estatus1")) != est1:
            cambios_cat.append(("estatus1", est1 or None, eco))
        if _t(u.get("estatus2")) != est2:
            cambios_cat.append(("estatus2", est2 or None, eco))

        # ---------------- operador, telefono, viaje
        nomina = nombre = ""
        if v is not None:
            nombre, nomina = _t(v.get("operador")), _t(v.get("nomina"))
        elif z is not None:
            nomina = _t(z.get("nomina"))
        nombre = nombre or _t(u.get("operador"))
        reg = por_nomina.get(_k(nomina)) or por_nombre.get(_k(nombre)) or {}
        nomina = nomina or _t(reg.get("nomina"))
        nombre = nombre or _t(reg.get("nombre"))
        telefono = _t(reg.get("telefono"))

        tiene_viaje = v is not None and (bool(v["_activo"]) or (est1 in ("5 DESCARGA", "DEVOLUCION")))
        g = (lambda c: v.get(c) if tiene_viaje else None)
        num_viaje = _t(g("num_viaje")) or (_t(z.get("shipment_id")) if z is not None and not tiene_viaje else "")
        cita_c, cita_d = g("cita_carga"), g("cita_descarga")
        lleg_d, sal_c, lleg_c = g("llegada_descarga"), g("salida_carga"), g("llegada_carga")
        ahora_ts = pd.Timestamp(ahora)
        rest_c = (cita_c - ahora_ts) if (pd.notna(cita_c) and pd.isna(sal_c) and pd.isna(lleg_c)) else None
        rest_d = (cita_d - ahora_ts) if (pd.notna(cita_d) and pd.isna(lleg_d)) else None
        en_descarga = (est1 == "5 DESCARGA") and pd.notna(lleg_d)
        t_desc = (ahora_ts - lleg_d) if en_descarga else None
        if pd.notna(lleg_d) and pd.notna(cita_d):
            dif = lleg_d - cita_d
            punt = "✅ a tiempo" if dif <= pd.Timedelta(0) else f"⚠️ tarde {_fmt_td(dif)}"
        else:
            punt = ""

        # ---------------- senal Omnitracs
        if pd.notna(fecha_pos):
            edad = ahora_ts - fecha_pos
            if edad <= pd.Timedelta(minutes=MIN_SENAL_OK):
                nivel, icono = 0, "🟢"
            elif edad <= pd.Timedelta(hours=HORAS_SENAL_ALERTA):
                nivel, icono = 1, "🟡"
            else:
                nivel, icono = 2, "🔴"
            senal = f"{icono} {_hace(edad)}"
        else:
            nivel, senal = 2, "🔴 sin señal"
        vel = float(p["velocidad"]) if p is not None and pd.notna(p["velocidad"]) else None
        lat, lon = (p["latitud"], p["longitud"]) if p is not None else (None, None)
        mapa = (f"https://www.google.com/maps?q={lat},{lon}"
                if lat is not None and lon is not None and pd.notna(lat) and pd.notna(lon) else None)
        ign = ""
        if p is not None:
            ign = "🟢 Encendido" if _k(p["ignicion"]) == "ENCENDIDO" else "🔴 Apagado"

        # tipo / cliente: del viaje del plan o, si no hay, lo que se aprendio del folio ZAM
        tipo_op, cliente_v, fuente_cl = _t(g("tipo_operacion")), _t(g("cliente")), "PLAN" if tiene_viaje else ""
        if not tipo_op and z is not None:
            folio_z = _t(z.get("shipment_id"))
            tipo_op, cli_z, fuente_cl = clasif.get(folio_z, ("", "", ""))
            cliente_v = cliente_v or cli_z
        alertas = []
        if z is not None and not tipo_op:
            alertas.append("❓ cliente sin identificar")
        if nombre and not telefono:
            alertas.append("📵 sin teléfono")
        if vel is not None and vel > VELOCIDAD_MAXIMA:
            alertas.append(f"🚨 {vel:.0f} km/h")
        if nivel == 2 and est1 in ("2 ASIGNADO", "3 EN CARGA", "4 CARGADO", "5 DESCARGA", "DEVOLUCION"):
            alertas.append("📡 sin señal")
        if rest_c is not None and rest_c < pd.Timedelta(0) and est1 == "2 ASIGNADO":
            alertas.append("⏰ cita de carga vencida")
        if rest_d is not None and rest_d < pd.Timedelta(0) and est1 in ("2 ASIGNADO", "3 EN CARGA", "4 CARGADO"):
            alertas.append("⏰ cita de descarga vencida")
        if t_desc is not None and t_desc > pd.Timedelta(hours=HORAS_ALERTA_DESCARGA):
            alertas.append(f"⏳ {_fmt_td(t_desc)} descargando")
        if sin_plan:
            alertas.append("📋 viaje en ZAM sin plan TMS")

        filas.append({
            "eco": eco, "viaje": num_viaje,
            "folio_zam": _t(z.get("shipment_id")) if z is not None else "", "nomina": nomina, "operador": nombre, "telefono": telefono,
            "tipo_operacion": tipo_op, "_fuente_cl": fuente_cl,
            "_sin_plan": not tiene_viaje, "_origen_id": _t(z.get("origen_id")) if z is not None else "",
            "_destino_id": _t(z.get("destino_id")) if z is not None else "",
            "cliente": cliente_v, "origen": _t(g("origen")), "destino": _t(g("destino")),
            "ciudad": _t(g("localidad_destino")), "remolque": _t(g("remolque_1")),
            "estatus1": est1, "estatus2": est2, "senal": senal, "_nivel_senal": nivel,
            "ubicacion": mapa, "hora": fecha_pos, "vel": vel, "ignicion": ign,
            "duracion": _fmt_td(ahora_ts - desde) if pd.notna(desde) else "",
            "obs": _t(m.get("obs")),
            "cita_carga": cita_c if tiene_viaje else pd.NaT, "rest_carga": _fmt_td(rest_c),
            "cita_descarga": cita_d if tiene_viaje else pd.NaT, "rest_descarga": _fmt_td(rest_d),
            "llegada_descarga": lleg_d if tiene_viaje else pd.NaT,
            "puntualidad": punt, "t_descarga": _fmt_td(t_desc), "_t_desc_seg": t_desc.total_seconds() if t_desc is not None else None,
            "alertas": " · ".join(alertas),
            "situacion": situacion, "_est1_auto": est1_auto, "_est2_auto": est2_auto,
        })

    # ---------------- aplicar cambios a la BD (una sola transaccion)
    if cambios_cat or cambios_mon:
        with bd.conexion() as con:
            for col, valor, eco in cambios_cat:
                con.execute(f"UPDATE unidades SET {col} = ? WHERE eco = ?", [valor, eco])
            for eco, campos in cambios_mon:
                _upsert(con, eco, **campos)

    tabla = pd.DataFrame(filas)
    if tabla.empty:
        return tabla
    # operador repetido en 2 unidades = alerta (como el rojo de duplicados de tu Excel)
    cuenta = tabla.loc[tabla["operador"] != "", "operador"].map(_k).value_counts()
    rep = tabla["operador"].map(lambda o: _k(o) in cuenta.index and cuenta[_k(o)] > 1 and _k(o) != "")
    tabla.loc[rep, "alertas"] = tabla.loc[rep, "alertas"].map(lambda a: ("👥 operador en 2 unidades · " + a).strip(" ·"))
    tabla["hora"] = pd.to_datetime(tabla["hora"], errors="coerce")
    for c in ("cita_carga", "cita_descarga", "llegada_descarga"):
        tabla[c] = pd.to_datetime(tabla[c], errors="coerce")
    return tabla.sort_values("eco", key=lambda s: s.map(lambda x: (len(str(x)), str(x)))).reset_index(drop=True)


# ---------------------------------------------------------- guardar lo que el monitorista captura
def _igual(a, b):
    return _t(a) == _t(b)


def tipos_cliente_cat():
    t = bd.leer_catalogo("tipos_operacion")
    return {_k(n): _t(c) for n, c in zip(t["nombre"], t["cliente"]) if _k(n)}


def guardar_cambios(original, editado):
    """Compara lo que habia contra lo que quedo en pantalla y guarda SOLO lo editado:
    estatus1, estatus2, telefono y observaciones. Devuelve un resumen con avisos."""
    res = {"estatus1": 0, "estatus2": 0, "telefono": 0, "obs": 0, "avisos": []}
    orig = original.set_index("eco")
    ahora = bd.AHORA_TXT()
    with_cambios = []
    for r in editado.to_dict("records"):
        eco = r["eco"]
        if eco not in orig.index:
            continue
        o = orig.loc[eco]
        campos = {}
        if not _igual(r["estatus1"], o["estatus1"]):
            nuevo = _t(r["estatus1"])
            if not nuevo or nuevo == o["_est1_auto"]:
                campos.update(est1_manual=None, est1_base=None, est1_fecha=None)      # volver al automatico
            else:
                campos.update(est1_manual=nuevo, est1_base=o["_est1_auto"], est1_fecha=ahora)
            res["estatus1"] += 1
        if not _igual(r["estatus2"], o["estatus2"]):
            nuevo = _t(r["estatus2"])
            if not nuevo or nuevo == o["_est2_auto"]:
                campos.update(est2_manual=None, est2_base=None, est2_fecha=None)
            else:
                campos.update(est2_manual=nuevo, est2_base=o["_est2_auto"], est2_fecha=ahora)
            res["estatus2"] += 1
        if "tipo_operacion" in r and not _igual(r["tipo_operacion"], o["tipo_operacion"]):
            if _t(o["folio_zam"]) and o["_sin_plan"]:
                clasificar_folio(o["folio_zam"], r["tipo_operacion"], tipos_cliente_cat(), o["_origen_id"], o["_destino_id"])
                res["tipo"] = res.get("tipo", 0) + 1
            else:
                res["avisos"].append(f"{eco}: el tipo de operación de un viaje del plan se cambia en el Plan del día.")
        if not _igual(r["obs"], o["obs"]):
            campos.update(obs=_t(r["obs"]) or None, obs_fecha=ahora)
            res["obs"] += 1
        if campos:
            with_cambios.append((eco, campos))
        if not _igual(r["telefono"], o["telefono"]):
            if bd.actualizar_telefono_operador(o["nomina"], o["operador"], r["telefono"]):
                res["telefono"] += 1
            else:
                res["avisos"].append(f"{eco}: no se guardó el teléfono porque el operador "
                                     "no está en el catálogo (falta su nómina o nombre).")
    if with_cambios:
        with bd.conexion() as con:
            for eco, campos in with_cambios:
                _upsert(con, eco, **campos)
    sincronizar()          # aplica los estatus manuales al catalogo de unidades
    return res
