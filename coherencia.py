# =====================================================================
#  coherencia.py - Que Plan de carga, ZAM y Monitoreo digan LO MISMO
#
#  1) VIAJES SUPERADOS: una unidad no puede seguir en un viaje viejo si ya hizo otro despues.
#     - Viaje abierto SIN avance (sin llegada ni salida): se cierra ✅ si la misma unidad ya
#       tiene un viaje posterior cuya cita ya paso (o que ya avanzo / ya se finalizo).
#     - Viaje abierto CON avance (ya cargo, va en transito...): se cierra ✅ solo si la misma
#       unidad ya empezo (llegada/salida de carga) o finalizo un viaje posterior.
#  2) ZAM: si ZAM ya finalizo o cancelo el viaje (por PEDIDO o # VIAJE), se cierra igual aqui.
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
            salida.append((int(v["id"]), v["eco"],
                           f"la unidad ya hizo el viaje #{int(p['id'])} (cita {p['_ref']:%d/%m %H:%M})"))
    return salida


def cerrar(ids_motivos, quien="SISTEMA", accion="AUTO_CIERRE_VIAJE"):
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
        cerrados = conciliacion.aplicar_cierres()
        for id_v, ped, nuevo in cerrados:
            bd.bitacora("SISTEMA", "ZAM_CIERRE_AUTOMATICO", f"viaje #{id_v} · viaje ZAM {ped}", f"ZAM lo reporta {nuevo}")
        res["zam"] = len(cerrados)
    except Exception:
        pass
    try:
        res["superados"] = cerrar(viajes_superados(), quien)
    except Exception:
        pass
    return res


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
