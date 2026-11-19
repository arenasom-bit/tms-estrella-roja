# =====================================================================
#  mantenimiento.py - Mantenimientos programados de tractos y cajas (perfil Mtto)
#
#  Mtto programa un mantenimiento (unidad o caja + fecha). Cuando OPS asigna esa unidad o caja en el
#  Plan y el mantenimiento esta proximo (o ya paso la fecha), el TMS avisa:
#    "La unidad que estas asignando tiene mantenimiento proximo: DD/MM/AAAA, para tu conocimiento y programacion."
#  OPS da Aceptar y se asigna normal. Cuando Mtto marca "Mantenimiento realizado", ya no hay aviso.
# =====================================================================
from datetime import date, timedelta

import pandas as pd

import base_datos as bd

DIAS_AVISO = 7          # se avisa si el mantenimiento es en los proximos 7 dias (o ya paso y no se ha hecho)


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


def crear_tabla(con):
    con.execute("CREATE TABLE IF NOT EXISTS mantenimientos (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, clave TEXT, "
                "fecha TEXT, notas TEXT, estado TEXT, creado TEXT, creado_por TEXT, realizado TEXT, realizado_por TEXT)")


def programar(tipo, clave, fecha, notas, quien):
    usuario = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    with bd.conexion() as con:
        con.execute("INSERT INTO mantenimientos (tipo, clave, fecha, notas, estado, creado, creado_por) "
                    "VALUES (?, ?, ?, ?, 'PROGRAMADO', ?, ?)", [tipo, _k(clave), str(fecha), notas or None,
                                                                 bd.AHORA_TXT(), usuario])
    bd.bitacora(quien, "MTTO_PROGRAMADO", f"{tipo.lower()} {clave}", f"Fecha: {pd.Timestamp(fecha):%d/%m/%Y} · {notas or ''}")


def realizado(mid, quien):
    usuario = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    with bd.conexion() as con:
        f = con.execute("SELECT tipo, clave, fecha FROM mantenimientos WHERE id = ?", [int(mid)]).fetchone()
        con.execute("UPDATE mantenimientos SET estado = 'REALIZADO', realizado = ?, realizado_por = ? WHERE id = ?",
                    [bd.AHORA_TXT(), usuario, int(mid)])
    if f:
        bd.bitacora(quien, "MTTO_REALIZADO", f"{str(f[0]).lower()} {f[1]}", f"Programado para {f[2]}")


def cancelar(mid, quien):
    with bd.conexion() as con:
        con.execute("UPDATE mantenimientos SET estado = 'CANCELADO', realizado = ? WHERE id = ?", [bd.AHORA_TXT(), int(mid)])
    bd.bitacora(quien, "MTTO_CANCELADO", f"mantenimiento #{mid}", "")


def leer(solo_pendientes=False):
    with bd.conexion() as con:
        df = pd.read_sql_query("SELECT * FROM mantenimientos" + (" WHERE estado = 'PROGRAMADO'" if solo_pendientes else "")
                               + " ORDER BY fecha, id", con)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def proximos(dias=DIAS_AVISO):
    """{('UNIDAD'|'REMOLQUE', clave): fecha} de mantenimientos pendientes en los proximos 'dias' (o vencidos)."""
    try:
        df = leer(solo_pendientes=True)
    except Exception:
        return {}
    lim = pd.Timestamp(date.today() + timedelta(days=dias))
    df = df[df["fecha"].notna() & (df["fecha"] <= lim)]
    out = {}
    for _, r in df.iterrows():
        k = (r["tipo"], _k(r["clave"]))
        if k not in out or r["fecha"] < out[k]:
            out[k] = r["fecha"]
    return out


def avisos(propuestas):
    """Mensajes para OPS por las unidades/cajas que esta asignando (propuestas de reglas.propuestas_desde_editor)."""
    prox = proximos()
    if not prox:
        return []
    msgs, vistos = [], set()
    for p in propuestas:
        for tipo, campo, nombre in (("UNIDAD", "eco", "unidad"), ("REMOLQUE", "remolque_1", "caja / remolque")):
            k = _k(p.get(campo))
            f = prox.get((tipo, k))
            if k and f is not None and (tipo, k) not in vistos:
                vistos.add((tipo, k))
                vencido = " (ya pasó la fecha y no se ha marcado como realizado)" if f < pd.Timestamp(date.today()) else ""
                msgs.append(f"La {nombre} **{p.get(campo)}** que estás asignando tiene mantenimiento próximo: "
                            f"**{f:%d/%m/%Y}**{vencido}, para tu conocimiento y programación.")
    return msgs
