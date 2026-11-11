# ==========================================================
#  automatismos.py - Lo que el TMS hace SOLO
#
#  1) FINALIZAR AUTOMATICO: si TC / TDC llena LLEGADA CARGA y LLEGADA DESCARGA,
#     el viaje queda ✅ finalizado y su estatus es DESCARGANDO.
#
#  2) DEPURAR DUPLICADOS: nunca debe haber 2 viajes ACTIVOS con la misma unidad,
#     remolque u operador. Si existen (datos viejos, importaciones, dos personas
#     guardando a la vez), se queda el viaje MAS RECIENTE y a los demas se les pone
#       ✅ finalizado  (si ya tenian llegada a carga: avanzaron de verdad)
#       ❌ cancelado   (si nunca avanzaron)
#     Todo queda en la BITACORA y se puede REABRIR (si no choca con otro viaje).
#
#  3) ENLAZAR CON ZAM: el viaje del plan que corresponde (misma unidad y cita +-24 h) a un viaje
#     'open' de ZAM se une a el: # VIAJE = numero de ZAM, y se llenan NOMINA/OPERADOR si faltan.
#     Si la unidad esta en ZAM pero NO se parece, queda en 'Por revisar' (no se toca solo).
#  3b) HISTORIAL: lo vacio se llena con lo que casi siempre se ha hecho (ultimo operador de esa
#     unidad; cliente/origen/destino/modalidad mas frecuentes de ese tipo de operacion).
#
#  4) MONITOREO: recalcula estatus 1 / 2 de las unidades y los guarda en el catalogo.
#
#  ejecutar() corre todo; el TMS lo llama al abrir, despues de guardar y despues de actualizar ZAM.
# ==========================================================
from datetime import datetime, timedelta

import pandas as pd

import base_datos as bd
import calculos
import configuracion as cfg
import monitoreo
import reglas

TOLERANCIA_HORAS_ZAM = getattr(cfg, "TOLERANCIA_HORAS_ZAM", 24)
SEGUNDOS_ENTRE_AUTOMATISMOS = getattr(cfg, "SEGUNDOS_ENTRE_AUTOMATISMOS", 20)


def _k(v):
    return reglas._k(v)


# ---------------------------------------------------------- 1) finalizar automatico
def finalizar_automatico():
    """Persiste ✅ finalizado en los viajes con LLEGADA CARGA + LLEGADA DESCARGA. Devuelve cuantos."""
    with bd.conexion() as con:
        cur = con.execute(
            "UPDATE viajes SET finalizado = 'SI', estatus = NULL, modificado = ? "
            "WHERE COALESCE(TRIM(llegada_carga), '') <> '' AND COALESCE(TRIM(llegada_descarga), '') <> '' "
            "AND COALESCE(finalizado, '') <> 'SI' AND COALESCE(cancelado, '') <> 'SI' "
            "AND COALESCE(estatus, '') <> 'CANCELADO'",
            [bd.AHORA_TXT()])
        return cur.rowcount


# ---------------------------------------------------------- 2) y 3) depuracion
def _todos_activos():
    todos = calculos.agregar_calculados(bd.leer_viajes("0000-01-01", "9999-12-31"))
    act = todos[calculos.es_activo(todos)].copy()
    act["_ref"] = act["cita_carga"].fillna(pd.to_datetime(act["fecha_plan"], errors="coerce"))
    return act.sort_values(["_ref", "id"])          # el ULTIMO de cada grupo es el mas reciente


def _accion_para(v):
    return "FINALIZAR" if pd.notna(v["llegada_carga"]) or pd.notna(v["salida_carga"]) else "CANCELAR"


def _cerca(a, b, horas):
    return pd.notna(a) and pd.notna(b) and abs(a - b) <= timedelta(hours=horas)


def plan_depuracion(incluir_zam=False):
    """Calcula (sin guardar nada) que viajes hay que cerrar.
    Devuelve (acciones, por_revisar):
      acciones   = lista de dicts {id, eco, operador, remolque, accion, motivo}
      por_revisar= lista de dicts con unidades del TMS que tambien estan abiertas en ZAM pero NO se parecen."""
    act = _todos_activos()
    if act.empty:
        return [], []
    ops = bd.leer_catalogo("operadores")
    nomina_de = {_k(n): _k(m) for n, m in zip(ops["nombre"], ops["nomina"]) if _k(n)}

    def llave_operador(fila):
        return _k(fila.get("nomina")) or nomina_de.get(_k(fila.get("operador")), "") or _k(fila.get("operador"))

    acciones, cerrados = [], set()

    def registrar(v, motivo):
        acciones.append({"id": int(v["id"]), "eco": _t(v["eco"]), "operador": _t(v["operador"]),
                         "remolque": _t(v["remolque_1"]), "accion": _accion_para(v), "motivo": motivo})
        cerrados.add(int(v["id"]))

    # --- duplicados dentro del TMS: unidad, remolque, operador (en ese orden)
    for campo, etiqueta, f in (("eco", "la unidad", lambda r: _k(r["eco"])),
                               ("remolque_1", "el remolque", lambda r: _k(r["remolque_1"])),
                               ("operador", "el operador", llave_operador)):
        vivos = act[~act["id"].isin(cerrados)].copy()
        if vivos.empty:
            continue
        vivos["_llave"] = vivos.apply(f, axis=1)
        for llave, g in vivos[vivos["_llave"] != ""].groupby("_llave"):
            if len(g) < 2:
                continue
            ganador = g.iloc[-1]
            for _, v in g.iloc[:-1].iterrows():
                valor = _t(v[campo]) or llave
                registrar(v, f"Duplicado: {etiqueta} {valor} también estaba activa en el viaje #{int(ganador['id'])}, "
                             "que es el más reciente.")

    # --- unidades del TMS que estan abiertas en ZAM pero NO se parecen al viaje (no se tocan solas)
    por_revisar = []
    if incluir_zam:
        z = bd.leer_zam()
        if not z.empty:
            z = z.copy()
            z["_salida"] = pd.to_datetime(z["salida_plan"], errors="coerce")
            for _, v in act[~act["id"].isin(cerrados)].iterrows():
                if _k(v.get("num_viaje")) in {_k(x) for x in z["shipment_id"]}:
                    continue                                   # ya esta enlazado con ZAM
                cand = z[(z["eco"].map(_k) == _k(v["eco"])) & (_k(v["eco"]) != "")]
                if cand.empty or _cerca_alguno(v, cand):
                    continue
                zz = cand.iloc[0]
                por_revisar.append({"id": int(v["id"]), "eco": _t(v["eco"]), "operador": _t(v["operador"]),
                                    "viaje_zam": zz["shipment_id"], "salida_zam": zz["salida_plan"],
                                    "cita_carga_tms": v["cita_carga"]})
    return acciones, por_revisar


def _t(v):
    return monitoreo._t(v)


def aplicar(acciones):
    """Cierra los viajes del plan y deja todo en la bitacora. Devuelve cuantos se cerraron."""
    n = 0
    ahora = bd.AHORA_TXT()
    for a in acciones:
        with bd.conexion() as con:
            if a["accion"] == "FINALIZAR":
                con.execute("UPDATE viajes SET finalizado = 'SI', cancelado = NULL, modificado = ? WHERE id = ?",
                            [ahora, a["id"]])
            else:
                con.execute("UPDATE viajes SET cancelado = 'SI', finalizado = NULL, modificado = ? WHERE id = ?",
                            [ahora, a["id"]])
        bd.bitacora_agregar(a["id"], a["eco"], a["accion"], a["motivo"])
        n += 1
    return n


def reabrir(id_bitacora):
    """Deshace un cierre automatico si el viaje ya no choca con otro activo. Devuelve (ok, mensaje)."""
    with bd.conexion() as con:
        fila = con.execute("SELECT viaje_id, revertido FROM bitacora WHERE id = ?", [id_bitacora]).fetchone()
    if not fila:
        return False, "No encontré ese registro."
    viaje_id, revertido = fila
    if revertido:
        return False, "Ese cierre ya se había reabierto."
    v = bd.leer_viajes("0000-01-01", "9999-12-31")
    v = v[v["id"] == viaje_id]
    if v.empty:
        return False, "El viaje ya no existe."
    r = v.iloc[0]
    errores = reglas.validar([{"id": int(viaje_id), "etiqueta": f"Viaje #{viaje_id}", "eco": r["eco"],
                               "operador": r["operador"], "nomina": r["nomina"], "remolque_1": r["remolque_1"]}])
    if errores:
        return False, "No se puede reabrir: " + " ".join(errores)
    with bd.conexion() as con:
        con.execute("UPDATE viajes SET finalizado = NULL, cancelado = NULL, modificado = ? WHERE id = ?",
                    [bd.AHORA_TXT(), viaje_id])
        con.execute("UPDATE bitacora SET revertido = ? WHERE id = ?", [bd.AHORA_TXT(), id_bitacora])
    return True, f"Viaje #{viaje_id} reabierto."


# ---------------------------------------------------------- enlazar con ZAM y rellenar con historial
def _cerca_alguno(v, cand):
    fecha_plan = pd.to_datetime(v["fecha_plan"], errors="coerce")
    for _, zz in cand.iterrows():
        if (_cerca(v["cita_carga"], zz["_salida"], TOLERANCIA_HORAS_ZAM)
                or (pd.isna(v["cita_carga"]) and _cerca(fecha_plan, zz["_salida"], TOLERANCIA_HORAS_ZAM))):
            return True
    return False


def enlazar_zam():
    """Une cada viaje ACTIVO del plan con su viaje 'open' de ZAM (misma unidad y horario parecido):
    llena # VIAJE con el numero de ZAM y, si faltan, NOMINA y OPERADOR. Nunca pisa lo ya capturado."""
    z = bd.leer_zam()
    if z.empty:
        return 0
    z = z.copy()
    z["_salida"] = pd.to_datetime(z["salida_plan"], errors="coerce")
    ops = bd.leer_catalogo("operadores")
    nombre_de = {_k(m): n for m, n in zip(ops["nomina"], ops["nombre"]) if _k(m) and _k(n)}
    usados = {_k(x) for x in bd.leer_viajes("0000-01-01", "9999-12-31")["num_viaje"].dropna()}
    act = _todos_activos()
    n = 0
    for _, v in act.iterrows():
        cand = z[(z["eco"].map(_k) == _k(v["eco"])) & (_k(v["eco"]) != "")]
        cand = cand[~cand["shipment_id"].map(_k).isin(usados - {_k(v.get("num_viaje"))})]
        if cand.empty:
            continue
        ref = v["_ref"]
        cand = cand.assign(_d=(cand["_salida"] - ref).abs() if pd.notna(ref) else pd.Timedelta(0))
        mejor = cand.sort_values("_d").iloc[0]
        if pd.notna(ref) and pd.notna(mejor["_salida"]) and mejor["_d"] > timedelta(hours=TOLERANCIA_HORAS_ZAM):
            continue
        cambios = {}
        if not _t(v.get("num_viaje")):
            cambios["num_viaje"] = str(mejor["shipment_id"])
            usados.add(_k(mejor["shipment_id"]))
        if not _t(v.get("nomina")) and _t(mejor.get("nomina")):
            cambios["nomina"] = _t(mejor["nomina"])
        if not _t(v.get("operador")) and nombre_de.get(_k(mejor.get("nomina"))):
            cambios["operador"] = nombre_de[_k(mejor["nomina"])]
        if cambios:
            cambios["modificado"] = bd.AHORA_TXT()
            sets = ", ".join(f"{c} = ?" for c in cambios)
            with bd.conexion() as con:
                con.execute(f"UPDATE viajes SET {sets} WHERE id = ?", list(cambios.values()) + [int(v["id"])])
            n += 1
    return n


def _mas_frecuente(serie, minimo=3, proporcion=0.6):
    serie = serie.dropna().map(lambda x: str(x).strip()).loc[lambda s: s != ""]
    if len(serie) < minimo:
        return None
    frec = serie.value_counts(normalize=True)
    return frec.index[0] if frec.iloc[0] >= proporcion else None


def rellenar_con_historial():
    """Llena SOLO lo que esta vacio en viajes activos, usando lo que casi siempre se ha hecho antes:
      - OPERADOR: el ultimo que uso esa unidad.
      - CLIENTE / ORIGEN / DESTINO / MODALIDAD: lo mas frecuente (>=60%, minimo 3 viajes) de ese TIPO DE OPERACION
        (y para DESTINO / MODALIDAD, de ese tipo + origen)."""
    todos = bd.leer_viajes("0000-01-01", "9999-12-31")
    if todos.empty:
        return 0
    hist = todos.copy()
    act = _todos_activos()
    n = 0
    for _, v in act.iterrows():
        cambios = {}
        eco, tipo, origen = _k(v["eco"]), _t(v["tipo_operacion"]), _t(v["origen"])
        if not _t(v["operador"]) and eco:
            previos = hist[(hist["eco"].map(_k) == eco) & (hist["id"] != v["id"]) & hist["operador"].notna()]
            if len(previos):
                ult = previos.sort_values("id").iloc[-1]
                cambios["operador"] = ult["operador"]
                if not _t(v["nomina"]) and _t(ult["nomina"]):
                    cambios["nomina"] = ult["nomina"]
        if tipo:
            base = hist[(hist["tipo_operacion"] == tipo) & (hist["id"] != v["id"])]
            for campo, filtro in (("cliente", base), ("origen", base)):
                if not _t(v[campo]):
                    x = _mas_frecuente(filtro[campo])
                    if x:
                        cambios[campo] = x
                        if campo == "origen":
                            origen = x
            base2 = base[base["origen"] == origen] if origen else base
            for campo in ("destino", "modalidad"):
                if not _t(v[campo]):
                    x = _mas_frecuente(base2[campo])
                    if x:
                        cambios[campo] = x
        if cambios:
            cambios["modificado"] = bd.AHORA_TXT()
            sets = ", ".join(f"{c} = ?" for c in cambios)
            with bd.conexion() as con:
                con.execute(f"UPDATE viajes SET {sets} WHERE id = ?", list(cambios.values()) + [int(v["id"])])
            n += 1
    if n:
        bd.autocompletar_desde_catalogos()      # direcciones, links, localidad, nomina...
    return n


# ---------------------------------------------------------- orquestador
def _segundos_desde(clave):
    ultima = bd.leer_meta(clave)
    if not ultima:
        return 10 ** 9
    return (datetime.now() - datetime.strptime(ultima, "%Y-%m-%d %H:%M:%S")).total_seconds()


def ejecutar(forzar=False):
    """Corre todos los automatismos. Sin 'forzar' se salta si corrio hace menos de unos segundos.
    Devuelve un dict con lo que hizo (vacio si se salto)."""
    if not forzar and _segundos_desde("ultima_automatizacion") < SEGUNDOS_ENTRE_AUTOMATISMOS:
        return {}
    bd.guardar_meta("ultima_automatizacion", bd.AHORA_TXT())
    res = {"finalizados": finalizar_automatico(), "depurados": 0, "enlazados": 0, "rellenados": 0}
    if getattr(cfg, "DEPURAR_DUPLICADOS", True):
        acciones, _ = plan_depuracion(incluir_zam=getattr(cfg, "DEPURAR_ZAM_AUTOMATICO", False))
        res["depurados"] = aplicar(acciones)
        # tras cerrar viajes puede haber nuevos 'finalizados'; el estatus de unidades se recalcula abajo
    res["enlazados"] = enlazar_zam()
    res["rellenados"] = rellenar_con_historial()
    monitoreo.sincronizar()
    return res
