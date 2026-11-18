# =====================================================================
#  coherencia.py - Que Plan de carga, ZAM y Monitoreo digan LO MISMO
#
#  1) VIAJES SUPERADOS: una unidad no puede seguir en un viaje viejo si ya hizo otro despues.
#     - SEGURO (se cierra ✅ solo): la unidad ya EMPEZO o termino un viaje posterior, o el viaje
#       tiene mas de 3 dias y ya hay viajes posteriores.
#     - DUDOSO (solo paso la cita del siguiente viaje): va a 🔔 POR CONFIRMAR y TC decide.
#  2) ZAM: si ZAM ya finalizo el viaje (por PEDIDO o # VIAJE) y el viaje tiene sus horas, se cierra;
#     si le faltan horas (pudo haber DEVOLUCION) va a 🔔 POR CONFIRMAR. Cancelado en ZAM = ❌ aqui.
#  3) MONITOREO -> PLAN: si TC pone ESTATUS 1 = DISPONIBLE en Monitoreo, el viaje abierto de
#     esa unidad queda ✅ finalizado en el Plan (y la unidad libre en todos lados).
#  Todo queda en la BITACORA. Corre solo (cada minuto, al guardar y al importar planes).
# =====================================================================
import time
from datetime import timedelta

import pandas as pd

import base_datos as bd
import calculos
from columnas import ESTATUS_CERRADOS

SEGUNDOS_ENTRE_REVISIONES = 60
DIAS_CIERRE_SEGURO = 3          # un viaje superado con mas de estos dias se cierra solo (nadie lo va a confirmar)
_ultima = {"t": 0.0}


def _k(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split()).upper()


def _preparar(df, ahora):
    df = calculos.agregar_calculados(df, ahora)
    df = df[df["eco"].map(_k) != ""].copy()
    df["_eco"] = df["eco"].map(_k)
    df["_ref"] = pd.to_datetime(df["cita_carga"], errors="coerce").fillna(pd.to_datetime(df["fecha_plan"], errors="coerce"))
    df["_abierto"] = ~df["estatus"].isin(ESTATUS_CERRADOS)
    df["_empezo"] = df["llegada_carga"].notna() | df["salida_carga"].notna()
    df["_avanzo"] = df["_empezo"] | df["llegada_descarga"].notna() | df["salida_descarga"].notna()
    df["_fin"] = df["estatus"] == "DISPONIBLE"
    return df


def viajes_superados(ahora=None):
    """[(id, eco, motivo)] de viajes abiertos que la unidad ya dejo atras. No guarda nada."""
    ahora = pd.Timestamp(ahora or pd.Timestamp.now())
    todos = bd.leer_viajes("0000-01-01", "9999-12-31")
    if todos.empty:
        return []
    abiertos_eco = set(todos.loc[~todos["finalizado"].fillna(False).astype(bool)
                                 & ~todos["cancelado"].fillna(False).astype(bool), "eco"].map(_k)) - {""}
    df = _preparar(todos[todos["eco"].map(_k).isin(abiertos_eco)], ahora)
    salida = []
    for eco, g in df.groupby("_eco"):
        abiertos = g[g["_abierto"] & g["_ref"].notna()]
        if abiertos.empty:
            continue
        for _, v in abiertos.iterrows():
            despues = g[(g["_ref"] > v["_ref"]) & (g["id"] != v["id"])]
            if despues.empty:
                continue
            if v["_avanzo"]:          # ya iba en el viaje: solo lo cierra otro viaje que de verdad empezo
                prueba = despues[despues["_empezo"] | despues["_fin"]]
            else:                     # nunca se capturo nada: basta un viaje posterior cuya cita ya paso
                prueba = despues[despues["_empezo"] | despues["_fin"] | (despues["_ref"] <= ahora)]
            if prueba.empty:
                continue
            p = prueba.sort_values("_ref").iloc[0]
            seguro = bool(prueba["_empezo"].any() or prueba["_fin"].any()
                          or v["_ref"] < ahora - timedelta(days=DIAS_CIERRE_SEGURO))
            salida.append((int(v["id"]), v["eco"],
                           f"la unidad ya hizo el viaje #{int(p['id'])} (cita {p['_ref']:%d/%m %H:%M})", seguro))
    return salida


def cerrar(ids_motivos, quien="SISTEMA", accion="AUTO_CIERRE_VIAJE"):
    ids_motivos = [x[:3] for x in ids_motivos]
    if not ids_motivos:
        return 0
    ahora = bd.AHORA_TXT()
    with bd.conexion() as con:
        for i, _, _ in ids_motivos:
            con.execute("UPDATE viajes SET finalizado = 'SI', modificado = ? WHERE id = ? "
                        "AND COALESCE(finalizado, '') <> 'SI' AND COALESCE(cancelado, '') <> 'SI'", [ahora, i])
    detalle = "; ".join(f"#{i} ECO {e}: {m}" for i, e, m in ids_motivos)
    bd.bitacora(quien, accion, f"{len(ids_motivos)} viaje(s)", detalle[:3900])
    return len(ids_motivos)


def finalizar_por_monitoreo(eco, quien):
    """TC puso DISPONIBLE en Monitoreo: se finalizan los viajes abiertos de esa unidad cuya cita ya llego."""
    ahora = pd.Timestamp.now()
    todos = bd.leer_viajes("0000-01-01", "9999-12-31")
    g = _preparar(todos[todos["eco"].map(_k) == _k(eco)], ahora)
    g = g[g["_abierto"] & (g["_ref"].isna() | (g["_ref"] <= ahora + timedelta(hours=2)))]
    return cerrar([(int(v["id"]), v["eco"], "TC la marcó DISPONIBLE en Monitoreo") for _, v in g.iterrows()],
                  quien, "MONITOREO_DISPONIBLE_FINALIZA")


def ejecutar(forzar=False, quien="SISTEMA"):
    """Pone todo de acuerdo. Devuelve {'superados': n, 'zam': n}. Como mucho una vez por minuto."""
    if not forzar and time.time() - _ultima["t"] < SEGUNDOS_ENTRE_REVISIONES:
        return None
    _ultima["t"] = time.time()
    res = {"superados": 0, "zam": 0}
    try:
        import conciliacion
        res["zam"] = registrar_cierres_zam(conciliacion.aplicar_cierres())
    except Exception:
        pass
    try:
        sup = viajes_superados()
        res["superados"] = cerrar([x for x in sup if x[3]], quien)
        res["por_confirmar"] = poner_por_confirmar([x for x in sup if not x[3]], "PLAN")
    except Exception:
        pass
    return res


def registrar_cierres_zam(cerrados):
    """Resultado de conciliacion.aplicar_cierres(): bitacora de lo cerrado y a POR CONFIRMAR lo que falta."""
    dudosos = []
    for id_v, ped, nuevo in cerrados:
        if nuevo == "POR CONFIRMAR":
            dudosos.append((id_v, None, f"ZAM ya cerró el viaje {ped} pero faltan horas de descarga (¿devolución?)"))
        else:
            bd.bitacora("SISTEMA", "ZAM_CIERRE_AUTOMATICO", f"viaje #{id_v} · viaje ZAM {ped}", f"ZAM lo reporta {nuevo}")
    poner_por_confirmar(dudosos, "ZAM")
    return len(cerrados) - len(dudosos)


# ---------------------------------------------------------- 🔔 por confirmar (TC decide)
OPCIONES = {"✅ Terminó": "TERMINO", "↩️ Tiene devolución": "DEVOLUCION", "⏳ Sigue en curso": "SIGUE",
            "❌ Se canceló": "CANCELADO"}


def poner_por_confirmar(lista, fuente):
    """lista = [(viaje_id, eco|None, motivo, ...)]. No repite ni revive lo ya resuelto/pospuesto."""
    if not lista:
        return 0
    n = 0
    with bd.conexion() as con:
        for x in lista:
            i, eco, motivo = x[0], x[1], x[2]
            if eco is None:
                f = con.execute("SELECT eco FROM viajes WHERE id = ?", [i]).fetchone()
                eco = f[0] if f else None
            n += con.execute("INSERT OR IGNORE INTO por_confirmar (viaje_id, eco, motivo, fuente, creado, estado) "
                             "VALUES (?, ?, ?, ?, ?, 'PENDIENTE')", [i, eco, motivo, fuente, bd.AHORA_TXT()]).rowcount
    return n


def pendientes_confirmar():
    """Viajes por confirmar que siguen abiertos (y que no estan pospuestos)."""
    with bd.conexion() as con:
        pc = pd.read_sql_query("SELECT * FROM por_confirmar WHERE estado = 'PENDIENTE' OR "
                               "(estado = 'POSPUESTO' AND pospuesto_hasta <= ?)", con, params=[bd.AHORA_TXT()])
    if pc.empty:
        return pc
    v = calculos.agregar_calculados(bd.leer_viajes("0000-01-01", "9999-12-31"))
    v = v[v["id"].isin(pc["viaje_id"]) & ~v["estatus"].isin(ESTATUS_CERRADOS)]
    t = pc.merge(v[["id", "eco", "tipo_operacion", "cliente", "origen", "destino", "cita_carga", "salida_carga",
                    "cita_descarga", "llegada_descarga", "estatus"]],
                 left_on="viaje_id", right_on="id", suffixes=("_pc", ""))
    return t.sort_values(["cita_carga", "viaje_id"]).reset_index(drop=True)


def resolver(viaje_id, accion, quien):
    """accion: TERMINO | DEVOLUCION | SIGUE | CANCELADO."""
    ahora = bd.AHORA_TXT()
    usuario = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    with bd.conexion() as con:
        if accion == "TERMINO":
            con.execute("UPDATE viajes SET finalizado = 'SI', modificado = ? WHERE id = ?", [ahora, viaje_id])
        elif accion == "CANCELADO":
            con.execute("UPDATE viajes SET cancelado = 'SI', modificado = ? WHERE id = ?", [ahora, viaje_id])
        elif accion == "DEVOLUCION":
            con.execute("UPDATE viajes SET devolucion = COALESCE(NULLIF(devolucion, ''), 'SI'), modificado = ? "
                        "WHERE id = ?", [ahora, viaje_id])
        horas = {"SIGUE": 6, "DEVOLUCION": 12}.get(accion)
        if horas:
            hasta = (pd.Timestamp.now() + timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
            con.execute("UPDATE por_confirmar SET estado = 'POSPUESTO', pospuesto_hasta = ?, resuelto_por = ?, "
                        "resuelto = ? WHERE viaje_id = ?", [hasta, usuario, accion, viaje_id])
        else:
            con.execute("UPDATE por_confirmar SET estado = 'RESUELTO', resuelto_por = ?, resuelto = ? "
                        "WHERE viaje_id = ?", [usuario, accion, viaje_id])
    bd.bitacora(quien, f"CONFIRMADO_{accion}", f"viaje #{viaje_id}", "Respuesta de TC en 🔔 Por confirmar")
    if accion == "DEVOLUCION":           # en Monitoreo la unidad se ve en devolucion (no disponible)
        with bd.conexion() as con:
            f = con.execute("SELECT eco FROM viajes WHERE id = ?", [viaje_id]).fetchone()
        if f and f[0]:
            bd.guardar_monitoreo({str(f[0]): {"estatus1": "↩️ DEVOLUCIÓN"}})


def viaje_actual(abiertos, ahora=None):
    """De los viajes abiertos (varios por unidad), el que la unidad trae AHORA, uno por 'clave':
    1) el que ya empezo (el mas reciente)  2) el ultimo cuya cita ya paso (o pasa en 6 h)  3) el proximo."""
    if abiertos.empty:
        return abiertos
    ahora = pd.Timestamp(ahora or pd.Timestamp.now())
    a = abiertos.copy()
    ref = pd.to_datetime(a["cita_carga"], errors="coerce").fillna(pd.to_datetime(a["fecha_plan"], errors="coerce"))
    empezo = a["llegada_carga"].notna() | a["salida_carga"].notna()
    ya = ref <= ahora + timedelta(hours=6)
    a["_grupo"] = 0
    a.loc[ya, "_grupo"] = 1
    a.loc[empezo, "_grupo"] = 2
    # dentro del grupo: 2 y 1 -> el mas reciente; 0 (futuros) -> el mas proximo
    seg = (ref - pd.Timestamp("2000-01-01")).dt.total_seconds().fillna(0)
    a["_orden"] = seg.where(a["_grupo"] > 0, -seg)
    a = a.sort_values(["_grupo", "_orden"])
    return a.drop_duplicates("clave", keep="last").drop(columns=["_grupo", "_orden"])
