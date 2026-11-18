# =====================================================================
#  paradas.py - Viajes con VARIAS paradas (La Comer: tiendas · Dliver: destinos)
#
#  Cada viaje puede tener sus paradas en orden: LUGAR, CITA, ENTRADA, SALIDA, NOTAS.
#  Se conecta solo con el Plan de carga:
#    - ENTRADA de la 1a parada  -> LLEGADA DESCARGA del viaje (si estaba vacia)
#    - SALIDA de la ultima parada (con todas las paradas cerradas) -> SALIDA DESCARGA del viaje
#      => el viaje se cierra solo y la unidad pasa a 🟤 VACÍO en Monitoreo.
#  Lo que capturan aqui reemplaza las columnas TIENDA 1..10 / DESTINOS-REALIZADOS de los Excel.
# =====================================================================
import pandas as pd

import base_datos as bd

COLUMNAS = ["orden", "lugar", "cita", "entrada", "salida", "notas"]


def _t(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    t = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(t) else t.to_pydatetime().replace(microsecond=0)


def leer(viaje_id):
    with bd.conexion() as con:
        df = pd.read_sql_query("SELECT orden, lugar, cita, entrada, salida, notas FROM paradas WHERE viaje_id = ? "
                               "ORDER BY orden", con, params=[int(viaje_id)])
    for c in ("cita", "entrada", "salida"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def guardar(viaje_id, df, quien):
    """Reemplaza las paradas del viaje y sincroniza el Plan. Devuelve (n_paradas, cambios_en_viaje: dict)."""
    filas = []
    for _, r in df.iterrows():
        lugar = str(r.get("lugar") or "").strip()
        if not lugar or lugar.upper() in ("NONE", "NAN"):
            continue
        filas.append({"lugar": lugar, "cita": _t(r.get("cita")), "entrada": _t(r.get("entrada")),
                      "salida": _t(r.get("salida")), "notas": str(r.get("notas") or "").strip() or None,
                      "_o": r.get("orden") if pd.notna(r.get("orden")) else 999})
    filas.sort(key=lambda x: (float(x["_o"]) if str(x["_o"]).replace(".", "", 1).isdigit() else 999))
    txt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S") if d else None
    with bd.conexion() as con:
        con.execute("DELETE FROM paradas WHERE viaje_id = ?", [int(viaje_id)])
        for i, f in enumerate(filas, 1):
            con.execute("INSERT INTO paradas (viaje_id, orden, lugar, cita, entrada, salida, notas, modificado) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [int(viaje_id), i, f["lugar"], txt(f["cita"]), txt(f["entrada"]), txt(f["salida"]), f["notas"],
                         bd.AHORA_TXT()])
        v = con.execute("SELECT llegada_descarga, salida_descarga FROM viajes WHERE id = ?", [int(viaje_id)]).fetchone()
    cambios = {}
    if v and filas:
        if not v[0] and filas[0]["entrada"]:
            cambios["llegada_descarga"] = filas[0]["entrada"]
        if not v[1] and all(f["salida"] for f in filas):
            cambios["salida_descarga"] = max(f["salida"] for f in filas)
    if cambios:
        bd.actualizar_viaje(int(viaje_id), cambios)
        bd.finalizar_viajes_completos()
    hechas = sum(1 for f in filas if f["salida"])
    bd.bitacora(quien, "PARADAS_GUARDADAS", f"viaje #{viaje_id}",
                f"{hechas}/{len(filas)} paradas realizadas" + (f" · al Plan: {', '.join(cambios)}" if cambios else ""))
    return len(filas), cambios


def resumen(ids):
    """{viaje_id: '3/6 · 🛒 TIENDA X'} con lo realizado y la parada en la que va."""
    ids = [int(i) for i in ids if pd.notna(i)]
    if not ids:
        return {}
    marcas = ",".join("?" for _ in ids)
    with bd.conexion() as con:
        df = pd.read_sql_query(f"SELECT viaje_id, orden, lugar, entrada, salida FROM paradas WHERE viaje_id IN ({marcas}) "
                               "ORDER BY viaje_id, orden", con, params=ids)
    out = {}
    for vid, g in df.groupby("viaje_id"):
        hechas = int(g["salida"].notna().sum())
        actual = g[g["salida"].isna()]
        if actual.empty:
            out[int(vid)] = f"✅ {hechas}/{len(g)}"
        else:
            a = actual.iloc[0]
            donde = "en" if pd.notna(a["entrada"]) else "→"
            out[int(vid)] = f"{hechas}/{len(g)} · {donde} {a['lugar']}"
    return out
