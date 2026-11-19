# =====================================================================
#  zam_excel.py - Pone el Plan de carga de acuerdo con el REPORTE DE VIAJES de ZAM (Excel)
#  (columnas: Viaje, Estatus Viaje, Num. Guía, Flete, Unidad, Remolque 1, Operador, Operador(nombre_operador),
#   Cliente Paga, Remitente, Destinatario, Tipo Operación, Cita Cliente, Fecha Despacho, Inicio Viaje, Fin Viaje)
#
#  Por cada viaje de ZAM busca el MISMO viaje en el Plan:
#    1) # VIAJE o PEDIDO = Viaje ZAM   2) CP CARGADO = Num. Guía
#    3) misma unidad + mismo tipo de operacion + fecha a menos de 30 h (y el viaje del plan sin # VIAJE)
#  - Si lo encuentra: llena SOLO lo que esta vacio (# VIAJE, guia, operador, nomina, remolque, flete, cliente,
#    inicio de ruta...). Nunca borra ni cambia lo que ya capturaron.
#  - Si no lo encuentra: lo AGREGA al plan (origen = Remitente, destino = Destinatario).
#  - Estatus: Liquidado/Realizado = ZAM ya lo termino. Si al viaje le faltan horas de descarga va a 🔔 Por confirmar
#    (salvo viajes de mas de 3 dias, que se cierran). Cancelado = ❌.
#  Se puede correr las veces que quieras: no duplica.
# =====================================================================
from datetime import timedelta

import pandas as pd

import base_datos as bd
import calculos
from columnas import ESTATUS_CERRADOS

COLUMNAS = {"Viaje": "num_viaje", "Num. Guía": "cp_cargado", "Flete": "flete", "Unidad": "eco",
            "Remolque 1": "remolque_1", "Operador": "nomina", "Operador(nombre_operador)": "operador",
            "Cliente Paga": "cliente", "Remitente": "origen", "Destinatario": "destino",
            "Tipo Operación": "tipo_operacion", "Inicio Viaje": "inicio_ruta", "Fin Viaje": "llegada_destino"}
TERMINADO = {"LIQUIDADO", "REALIZADO", "TERMINADO", "FINALIZADO", "ENTREGADO"}
CANCELADO = {"CANCELADO", "CANCELADA"}
HORAS = 30


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


def _vacio(v):
    return _k(v) in ("", "NONE", "NAN", "NAT")


def leer(archivo):
    z = pd.read_excel(archivo)
    falta = [c for c in ("Viaje", "Unidad", "Fecha Despacho") if c not in z.columns]
    if falta:
        raise ValueError(f"No parece el reporte de viajes de ZAM: faltan las columnas {', '.join(falta)}")
    filas = []
    for _, r in z.iterrows():
        d = {}
        for col, campo in COLUMNAS.items():
            if col in z.columns and not _vacio(r[col]):
                v = r[col]
                if campo in ("inicio_ruta", "llegada_destino"):
                    v = pd.to_datetime(v, errors="coerce")
                    v = None if pd.isna(v) else v.to_pydatetime().replace(microsecond=0)
                elif campo == "flete":
                    v = float(v)
                else:
                    v = _k(v)
                if v is not None:
                    d[campo] = v
        if not d.get("num_viaje"):
            continue
        desp = pd.to_datetime(r.get("Fecha Despacho"), errors="coerce")
        cita = pd.to_datetime(r.get("Cita Cliente"), errors="coerce")
        d["_despacho"] = None if pd.isna(desp) else desp.to_pydatetime().replace(microsecond=0)
        d["_cita"] = None if pd.isna(cita) else cita.to_pydatetime()
        d["_estatus"] = _k(r.get("Estatus Viaje"))
        filas.append(d)
    return filas


def _indice(plan):
    por = {c: {} for c in ("num_viaje", "pedido", "cp_cargado")}
    for i, a, b, c in zip(plan["id"], plan["num_viaje"], plan["pedido"], plan["cp_cargado"]):
        for col, v in (("num_viaje", a), ("pedido", b), ("cp_cargado", c)):
            if not _vacio(v):
                por[col].setdefault(_k(v), []).append(int(i))
    return por


def sincronizar(archivo, aplicar=True, quien="SISTEMA"):
    """Devuelve resumen. aplicar=False solo dice lo que haria."""
    bd.crear_tablas()
    filas = leer(archivo)
    plan = calculos.agregar_calculados(bd.leer_viajes("0000-01-01", "9999-12-31"))
    plan["_eco"] = plan["eco"].map(_k)
    plan["_tipo"] = plan["tipo_operacion"].map(_k)
    plan["_ref"] = pd.to_datetime(plan["cita_carga"], errors="coerce").fillna(pd.to_datetime(plan["fecha_plan"], errors="coerce"))
    plan = plan.set_index("id", drop=False)
    plan.index.name = None
    idx = _indice(plan)
    usados, cambios, nuevos, confirmar, cerrar, cancelar = set(), [], [], [], [], []
    res = {"zam": len(filas), "por_viaje": 0, "por_guia": 0, "por_unidad": 0, "nuevos": 0, "llenados": 0,
           "campos": {}, "por_confirmar": 0, "cerrados": 0, "cancelados": 0}
    ahora = pd.Timestamp.now()
    # 1a pasada: llaves exactas (# VIAJE / PEDIDO / GUIA) para TODOS; asi nadie le "gana" el viaje a otro por fecha
    exacto = {}
    for n, d in enumerate(filas):
        for col, nombre in (("num_viaje", "por_viaje"), ("pedido", "por_viaje"), ("cp_cargado", "por_guia")):
            llave = d.get("num_viaje") if col in ("num_viaje", "pedido") else d.get("cp_cargado")
            ids = [i for i in idx[col].get(_k(llave), []) if i not in usados] if llave else []
            if ids:
                exacto[n] = (ids[0], nombre)
                usados.add(ids[0])
                break
    for n, d in enumerate(filas):
        vid, como = exacto.get(n, (None, None))
        if vid is None and d.get("eco") and d.get("_despacho"):
            m = plan[(plan["_eco"] == d["eco"]) & (plan["_tipo"] == _k(d.get("tipo_operacion")))
                     & plan["num_viaje"].map(_vacio) & ~plan["id"].isin(usados)]
            if len(m):
                dif = (m["_ref"] - pd.Timestamp(d["_despacho"])).abs()
                m = m[dif <= pd.Timedelta(hours=HORAS)]
                if len(m):
                    vid, como = int((m["_ref"] - pd.Timestamp(d["_despacho"])).abs().idxmin()), "por_unidad"
        est = d["_estatus"]
        if vid is None:
            nuevo = {k: v for k, v in d.items() if not k.startswith("_")}
            nuevo["cita_carga"] = d.get("_despacho")
            fin = d.get("llegada_destino")
            if est in CANCELADO:
                nuevo["cancelado"] = "SI"
            elif est in TERMINADO:
                nuevo["finalizado"] = "SI"
            nuevos.append((nuevo, (d.get("_despacho") or ahora.to_pydatetime()).date()))
            continue
        usados.add(vid)
        res[como] += 1
        v = plan.loc[vid]
        dif = {k: x for k, x in d.items() if not k.startswith("_") and x is not None and _vacio(v.get(k))}
        if dif:
            cambios.append((vid, dif))
            for c in dif:
                res["campos"][c] = res["campos"].get(c, 0) + 1
        abierto = v["estatus"] not in ESTATUS_CERRADOS
        if abierto and est in CANCELADO:
            cancelar.append(vid)
        elif abierto and est in TERMINADO:
            completo = not (_vacio(v.get("salida_descarga")) and _vacio(v.get("llegada_descarga")))
            viejo = pd.notna(v["_ref"]) and v["_ref"] < ahora - timedelta(days=3)
            (cerrar if (completo or viejo) else confirmar).append(vid)
    res.update(nuevos=len(nuevos), llenados=len(cambios), por_confirmar=len(confirmar), cerrados=len(cerrar),
               cancelados=len(cancelar))
    if aplicar:
        for vid, dif in cambios:
            bd.actualizar_viaje(vid, dif)
        for d, dia in nuevos:
            bd.insertar_viaje(d, dia)
        with bd.conexion() as con:
            for vid in cerrar:
                con.execute("UPDATE viajes SET finalizado = 'SI', modificado = ? WHERE id = ?", [bd.AHORA_TXT(), vid])
            for vid in cancelar:
                con.execute("UPDATE viajes SET cancelado = 'SI', modificado = ? WHERE id = ?", [bd.AHORA_TXT(), vid])
        import coherencia
        coherencia.poner_por_confirmar([(i, None, "ZAM lo reporta terminado pero faltan horas de descarga (¿devolución?)")
                                        for i in confirmar], "ZAM")
        bd.autocompletar_desde_catalogos()
        bd.bitacora(quien, "ZAM_EXCEL_SINCRONIZADO", str(getattr(archivo, "name", archivo))[:120],
                    f"{res['zam']} viajes ZAM · enlazados: {res['por_viaje']} por viaje, {res['por_guia']} por guía, "
                    f"{res['por_unidad']} por unidad · {res['llenados']} con datos llenados · {res['nuevos']} nuevos · "
                    f"{res['cerrados']} cerrados · {res['por_confirmar']} por confirmar · {res['cancelados']} cancelados")
    return res


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print('Uso: py zam_excel.py "viajes_por_dia.xlsx" [--probar]')
        sys.exit(1)
    r = sincronizar(sys.argv[1], aplicar="--probar" not in sys.argv)
    print(r)
