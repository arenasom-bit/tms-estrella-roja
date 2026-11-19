# =====================================================================
#  flota.py - Vista de la FLOTA para Admin de Flota (ADF) y Mantenimiento (Mtto)
#
#  Un renglon por tracto (o por caja): placas, situacion, donde esta (Omnitracs), hace cuanto actualiza,
#  ultimo operador con su telefono, ultimo cliente asignado y proximo mantenimiento.
# =====================================================================
from datetime import datetime

import pandas as pd

import base_datos as bd
import omnitracs
import operativo

# Lo que cada perfil puede poner en SITUACION
SITUACIONES_ADF = ["OPERANDO", "CORRALON", "SINIESTRO", "INACTIVA"]
SITUACIONES_MTTO = ["OPERANDO", "TALLER"]


def _k(v):
    return operativo._k(v)


def _ultimos_viajes(columna):
    """Ultimo viaje (por cita/fecha) de cada unidad o caja: operador, nomina, cliente, fecha."""
    v = bd.leer_viajes("0000-01-01", "9999-12-31")
    v = v[v[columna].map(_k) != ""].copy()
    v["_k"] = v[columna].map(omnitracs.normalizar_alias)
    v["_ref"] = pd.to_datetime(v["cita_carga"], errors="coerce").fillna(pd.to_datetime(v["fecha_plan"], errors="coerce"))
    v = v.sort_values(["_ref", "id"]).drop_duplicates("_k", keep="last")
    return v.set_index("_k")


def tabla(tipo):
    """tipo = 'UNIDAD' (tractos) o 'REMOLQUE' (cajas). Devuelve DataFrame listo para mostrar."""
    import mantenimiento
    ahora = datetime.now()
    if tipo == "UNIDAD":
        cat = bd.leer_catalogo("unidades").rename(columns={"eco": "clave"})
        ult = _ultimos_viajes("eco")
    else:
        cat = bd.leer_catalogo("remolques").rename(columns={"remolque": "clave"})
        ult = _ultimos_viajes("remolque_1")
    pos = operativo.posiciones()
    ops = bd.leer_catalogo("operadores")
    tel_nom = {_k(n): t for n, t in zip(ops["nomina"], ops["telefono"]) if _k(n) and _k(t)}
    tel_nombre = {_k(n): t for n, t in zip(ops["nombre"], ops["telefono"]) if _k(n) and _k(t)}
    mtto = mantenimiento.proximos(dias=3650)
    filas = []
    for _, u in cat.iterrows():
        clave = _k(u["clave"])
        if not clave:
            continue
        kn = omnitracs.normalizar_alias(clave)
        p = operativo.posicion_tracto(clave, pos) if tipo == "UNIDAD" else operativo.posicion_caja(clave, pos)
        v = ult.loc[kn] if kn in ult.index else None
        operador = _k(v.get("operador")) if v is not None else ""
        nomina = _k(v.get("nomina")) if v is not None else ""
        f_mtto = mtto.get((tipo, clave))
        filas.append({
            "clave": u["clave"], "placas": u.get("placas"), "situacion": u.get("situacion") or "OPERANDO",
            "vin": u.get("vin"),
            "reporto": p["fecha"] if p else pd.NaT,
            "hace": operativo.hace(p["fecha"], ahora) if p else "sin señal",
            "lugar": (p.get("lugar") or "") if p else "",
            "mapa": p["liga"] if p else None,
            "lat": p["lat"] if p else None, "lon": p["lon"] if p else None,
            "ultimo_operador": operador or None,
            "telefono": tel_nom.get(nomina) or tel_nombre.get(operador) or None,
            "ultimo_cliente": (_k(v.get("cliente")) or _k(v.get("tipo_operacion"))) if v is not None else None,
            "ultima_asignacion": v["_ref"] if v is not None else pd.NaT,
            "mtto": f_mtto if f_mtto is not None else pd.NaT,
        })
    df = pd.DataFrame(filas)
    for c in ("placas", "lugar", "ultimo_operador", "telefono", "ultimo_cliente"):
        if c in df:
            df[c] = df[c].fillna("").astype(str).replace({"None": "", "nan": ""})
    if not df.empty:
        df = df.assign(_o=df["clave"].map(lambda x: (len(str(x)), str(x)))).sort_values("_o").drop(columns="_o")
        df = df.reset_index(drop=True)
    return df


def guardar(original, editado, tipo, perfil, quien):
    """Guarda placas / situacion segun el perfil. Devuelve (cambios, errores)."""
    permitidas = SITUACIONES_MTTO if perfil == "MTTO" else SITUACIONES_ADF if perfil == "ADF" else None
    tabla_bd, llave = ("unidades", "eco") if tipo == "UNIDAD" else ("remolques", "remolque")
    ant = original.set_index("clave")
    cambios, errores = [], []
    with bd.conexion() as con:
        for _, r in editado.iterrows():
            c = r["clave"]
            if c not in ant.index:
                continue
            a = ant.loc[c]
            sit_a, sit_n = _k(a["situacion"]) or "OPERANDO", _k(r["situacion"]) or "OPERANDO"
            if sit_n != sit_a:
                if perfil == "MTTO" and not (sit_a == "TALLER" and sit_n == "OPERANDO" or sit_a == "OPERANDO" and sit_n == "TALLER"):
                    errores.append(f"{c}: Mantenimiento solo pasa de TALLER a OPERANDO (o manda a TALLER).")
                elif perfil == "ADF" and (sit_a == "TALLER" or sit_n not in permitidas):
                    errores.append(f"{c}: {'la libera Mantenimiento (está en TALLER)' if sit_a == 'TALLER' else 'situación no permitida'}.")
                else:
                    con.execute(f"UPDATE {tabla_bd} SET situacion = ? WHERE {llave} = ?", [bd.situacion_valida(sit_n), str(c)])
                    cambios.append(f"{c}: situación {sit_a} → {sit_n}")
            if perfil != "MTTO" and _k(r.get("placas")) != _k(a.get("placas")):
                con.execute(f"UPDATE {tabla_bd} SET placas = ? WHERE {llave} = ?", [_k(r.get("placas")) or None, str(c)])
                cambios.append(f"{c}: placas {a.get('placas') or '—'} → {r.get('placas') or '—'}")
    if cambios:
        bd.bitacora(quien, "FLOTA_CAMBIO", "tractos" if tipo == "UNIDAD" else "cajas", "; ".join(cambios)[:3900])
    return cambios, errores
