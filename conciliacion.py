# ==========================================================
#  conciliacion.py - El TMS "conversa" con ZAM (sin sustituirlo)
#
#  1) COINCIDENCIAS: un viaje del plan SIN pedido que coincide con un viaje abierto de ZAM
#     (misma unidad + horarios parecidos) -> se ofrece traer el numero de viaje ZAM como PEDIDO.
#  2) CIERRES: un viaje cuyo PEDIDO es un viaje ZAM que ya aparece finalizado o cancelado en ZAM
#     -> se marca solo con ✅ finalizado / ❌ cancelado en el Plan del dia.
#  3) MISMO VIAJE: reglas.py usa coincide_viaje() para no marcar como "unidad ocupada"
#     al viaje de ZAM que en realidad es este mismo viaje.
# ==========================================================
from functools import lru_cache

import pandas as pd

import base_datos as bd
import configuracion as cfg


def _k(valor):
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return " ".join(str(valor).split()).upper()


@lru_cache(maxsize=8192)
def _texto_a_fecha(texto):
    return pd.to_datetime(texto, errors="coerce")


def _t(valor):
    """Fecha/hora o None (rapido: las fechas en texto se convierten una sola vez)."""
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, pd.Timestamp):
        return valor
    try:
        t = _texto_a_fecha(valor) if isinstance(valor, str) else pd.to_datetime(valor, errors="coerce")
    except (TypeError, ValueError):
        return None
    return None if pd.isna(t) else t


def _cerca(a, b, tol_horas):
    """True si ambas fechas existen y estan a menos de tol_horas una de otra."""
    a, b = _t(a), _t(b)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= tol_horas * 3600


def tolerancia():
    return float(getattr(cfg, "ZAM_TOLERANCIA_HORAS", 4))


def coincide_viaje(plan, z, nomina_plan=None, tol=None):
    """¿El viaje del plan (dict con eco, pedido, cita_carga, cita_descarga) y el viaje ZAM (fila) son el mismo?
    - Si el PEDIDO del plan es el numero de viaje ZAM -> si.
    - Si no: debe coincidir la unidad (si el plan ya la trae), no contradecirse el operador
      y al menos una cita debe estar cerca de la hora planeada en ZAM."""
    tol = tolerancia() if tol is None else tol
    pedido = _k(plan.get("pedido"))
    if pedido and pedido == _k(z["shipment_id"]):
        return True
    if pedido:                       # ya tiene otro pedido: es otro viaje
        return False
    eco = _k(plan.get("eco"))
    if eco and eco != _k(z["eco"]):
        return False
    nomina = _k(nomina_plan)
    if nomina and _k(z["nomina"]) and nomina != _k(z["nomina"]):
        return False
    horas = (_cerca(plan.get("cita_carga"), z["salida_plan"], tol)
             or _cerca(plan.get("cita_descarga"), z["llegada_plan"], tol))
    if horas:
        return True
    # sin horas comparables: solo si unidad y operador coinciden claramente
    sin_horas = _t(plan.get("cita_carga")) is None and _t(plan.get("cita_descarga")) is None
    return bool(sin_horas and eco and nomina and nomina == _k(z["nomina"]))


# ---------------------------------------------------------- 1) coincidencias para traer PEDIDO
def _nomina_de(nombre, mapa):
    return mapa.get(_k(nombre), "")


def buscar_coincidencias(plan):
    """plan: DataFrame del plan (con id, pedido, eco, operador, nomina, cita_carga, cita_descarga, estatus, ...).
    Devuelve DataFrame con: id, shipment_id, eco, operador, cita_carga, salida_zam, motivo."""
    vacio = pd.DataFrame(columns=["id", "shipment_id", "eco", "operador", "cita_carga", "salida_zam", "motivo"])
    z = bd.leer_zam()
    if plan is None or plan.empty or z.empty:
        return vacio
    ops = bd.leer_catalogo("operadores")
    nomina_de = {_k(n): _k(m) for n, m in zip(ops["nombre"], ops["nomina"]) if _k(n)}
    ocupados = set(bd.viajes_con_pedido(z["shipment_id"].tolist()))      # pedidos ya usados en el TMS
    ignorados = bd.zam_ignorados()

    candidatos = plan[(plan["pedido"].map(_k) == "") & (plan["eco"].map(_k) != "")
                      & (~plan["estatus"].isin(["DISPONIBLE", "CANCELADO"]))]
    posibles = []
    for _, v in candidatos.iterrows():
        nomina = _k(v.get("nomina")) or nomina_de.get(_k(v.get("operador")), "")
        for _, zz in z.iterrows():
            if str(zz["shipment_id"]) in ocupados or f"{int(v['id'])}:{zz['shipment_id']}" in ignorados:
                continue
            if _k(zz["eco"]) != _k(v["eco"]):
                continue
            if not coincide_viaje({"eco": v["eco"], "pedido": None, "cita_carga": v.get("cita_carga"),
                                   "cita_descarga": v.get("cita_descarga")}, zz, nomina):
                continue
            puntos = 1 + int(_cerca(v.get("cita_carga"), zz["salida_plan"], tolerancia())) \
                + int(_cerca(v.get("cita_descarga"), zz["llegada_plan"], tolerancia())) \
                + int(bool(nomina) and nomina == _k(zz["nomina"]))
            motivo = ["misma unidad"]
            if nomina and nomina == _k(zz["nomina"]):
                motivo.append("mismo operador")
            if _cerca(v.get("cita_carga"), zz["salida_plan"], tolerancia()):
                motivo.append("hora de carga parecida")
            if _cerca(v.get("cita_descarga"), zz["llegada_plan"], tolerancia()):
                motivo.append("hora de descarga parecida")
            posibles.append((puntos, int(v["id"]), str(zz["shipment_id"]), v, zz, " · ".join(motivo)))

    # cada viaje del plan y cada viaje ZAM se usan una sola vez (gana el que mas coincide)
    posibles.sort(key=lambda x: -x[0])
    usados_plan, usados_zam, filas = set(), set(), []
    for _, id_, ship, v, zz, motivo in posibles:
        if id_ in usados_plan or ship in usados_zam:
            continue
        usados_plan.add(id_)
        usados_zam.add(ship)
        filas.append({"id": id_, "shipment_id": ship, "eco": v["eco"], "operador": v.get("operador"),
                      "cita_carga": v.get("cita_carga"), "salida_zam": zz["salida_plan"], "motivo": motivo})
    return pd.DataFrame(filas, columns=vacio.columns) if filas else vacio


def aplicar_pedidos(pares):
    """pares: lista de (id_viaje, shipment_id). Pone el numero de viaje ZAM como PEDIDO."""
    for id_, ship in pares:
        bd.actualizar_viaje(id_, {"pedido": ship})
    return len(pares)


def ignorar(pares):
    bd.ignorar_zam([f"{int(i)}:{s}" for i, s in pares])


def ecos_zam_que_son_del_plan(plan):
    """ECOs que tienen viaje abierto en ZAM pero ese viaje corresponde a un viaje del plan (no es un viaje 'ajeno'):
    se pueden sugerir para ese viaje."""
    z = bd.leer_zam()
    if plan is None or plan.empty or z.empty:
        return set()
    abiertos = plan[~plan["estatus"].isin(["DISPONIBLE", "CANCELADO"])]
    salida = set()
    for _, zz in z.iterrows():
        for _, v in abiertos.iterrows():
            if coincide_viaje({"eco": v.get("eco"), "pedido": v.get("pedido"), "cita_carga": v.get("cita_carga"),
                               "cita_descarga": v.get("cita_descarga")}, zz, v.get("nomina")):
                salida.add(_k(zz["eco"]))
                break
    return salida - {""}


# ---------------------------------------------------------- 2) cierres desde ZAM
def aplicar_cierres():
    """Viajes del TMS con PEDIDO = numero de viaje ZAM: si ZAM ya lo finalizo/cancelo, se marca igual aqui.
    Devuelve [(id, pedido, 'FINALIZADO'|'CANCELADO'), ...] de lo que se cambio."""
    if not getattr(cfg, "ZAM_AUTO_CERRAR", True):
        return []
    todos = bd.leer_zam_todos()
    if todos.empty:
        return []
    estado = {str(s): _k(e).lower() for s, e in zip(todos["shipment_id"], todos["status"])}
    fin = {e.lower() for e in getattr(cfg, "ZAM_ESTATUS_FINALIZADO", [])}
    can = {e.lower() for e in getattr(cfg, "ZAM_ESTATUS_CANCELADO", [])}
    viajes = bd.leer_viajes("0000-01-01", "9999-12-31")
    hechos = []
    for _, v in viajes.iterrows():
        ped = _k(v.get("pedido"))
        if not ped or v.get("finalizado") or v.get("cancelado"):
            continue
        est = estado.get(ped)
        if est in fin:
            bd.actualizar_viaje(int(v["id"]), {"finalizado": True})
            hechos.append((int(v["id"]), ped, "FINALIZADO"))
        elif est in can:
            bd.actualizar_viaje(int(v["id"]), {"cancelado": True})
            hechos.append((int(v["id"]), ped, "CANCELADO"))
    return hechos
