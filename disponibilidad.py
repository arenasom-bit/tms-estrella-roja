# =====================================================================
#  disponibilidad.py - CONTROL DE DISPONIBILIDAD de la flota (reemplaza el Excel "CONTROL DE DISPONIBILIDAD")
#
#  Un renglon por unidad (como la hoja GRAL ER): operacion, gerencia, circuito, ECO, modalidad, operador titular,
#  estatus del operador, situacion de la unidad, remolques y comentarios.
#
#  Lo CAPTURA el JEFE OPS (y MASTER):
#    - lo que impide trabajar al OPERADOR: DESCANSO, VACACIONES, INCAPACIDAD... con su fecha de REGRESA
#      (ese dia vuelve solo a ACTIVO);
#    - lo que impide trabajar a la UNIDAD: SITUACION (INACTIVA, SEGURIDAD, AGENCIA, TALLER...).
#  Lo CALCULA el TMS con los viajes (nadie lo escribe): EN SERVICIO, PROGRAMADA o DISPONIBLE.
#  REGLA: unidad que no esta OPERANDO, u operador que no esta ACTIVO, NO se puede asignar a un viaje (reglas.py).
# =====================================================================
import re
import unicodedata
from datetime import date, datetime

import pandas as pd

import base_datos as bd
from columnas import ESTATUS_OPERADOR, SITUACIONES

OPERACIONES = ["DEDICADO", "SPOT SENCILLO", "DDS"]
SEGMENTOS = ["TITULAR", "POSTURERO", "PATIERO", "DOBLE OPERADOR"]
MODALIDADES = ["TRACTOCAMION SEN", "TRACTOCAMION FULL", "TRACTOCAMION M2", "TORTHON", "CAMIONETA"]
# lo que la unidad hace segun sus viajes (no se captura)
AUTOMATICOS = ["EN SERVICIO", "PROGRAMADA", "DISPONIBLE"]
# situaciones que solo Mantenimiento (o MASTER) quita
SOLO_MTTO_LIBERA = ["TALLER"]

CAMPOS = ["operacion", "gerencia", "segmento", "circuito", "modalidad", "r1", "dolly", "r2", "comentarios", "jefe"]


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
    t = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode()
    return " ".join(t.split()).upper()


def _fecha(v):
    """Texto/fecha -> date (o None)."""
    if v is None or _k(v) == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    f = pd.to_datetime(str(v)[:10], errors="coerce")
    return None if pd.isna(f) else f.date()


def crear_tabla(con):
    defs = ", ".join(f"{c} TEXT" for c in CAMPOS)
    con.execute(f"CREATE TABLE IF NOT EXISTS dispo_unidades (eco TEXT PRIMARY KEY, {defs}, modificado TEXT, modificado_por TEXT)")


# ---------------------------------------------------------------- operadores
def motivo_operador(estatus, regresa, hoy=None):
    """'' si el operador puede trabajar; si no, el motivo (ej. 'VACACIONES hasta 01/12')."""
    est = bd.estatus_operador_valido(estatus)
    if not est:
        return ""
    hoy = hoy or date.today()
    r = _fecha(regresa)
    if r and r <= hoy:
        return ""                                   # ya regreso: vuelve solo a ACTIVO
    return est + (f" (regresa {r:%d/%m/%Y})" if r else "")


def operadores_no_disponibles(hoy=None):
    """{nombre_normalizado: motivo, nomina: motivo} de los operadores que hoy NO pueden trabajar."""
    ops = bd.leer_catalogo("operadores")
    salida = {}
    for nom, nombre, est, reg in zip(ops["nomina"], ops["nombre"], ops["estatus"], ops["regresa"]):
        m = motivo_operador(est, reg, hoy)
        if m:
            for x in (nombre, nom):                     # con y sin acentos (reglas.py compara sin quitarlos)
                if _k(x):
                    salida[_k(x)] = m
                    salida[" ".join(str(x).split()).upper()] = m
    return salida


# ---------------------------------------------------------------- la tabla
def _leer_dispo():
    with bd.conexion() as con:
        return pd.read_sql_query(f"SELECT eco, {', '.join(CAMPOS)} FROM dispo_unidades", con)


def tabla(ctx=None):
    """DataFrame (un renglon por unidad) con lo capturado + ESTATUS calculado y su MOTIVO."""
    import reglas
    ctx = ctx or reglas.Contexto()
    uni = bd.leer_catalogo("unidades")
    dis = _leer_dispo()
    dis["_k"] = dis["eco"].map(_k)
    dis = dis.drop_duplicates("_k", keep="last").set_index("_k")
    ops = bd.leer_catalogo("operadores")
    est_op = {_k(n): (e, r) for n, e, r in zip(ops["nombre"], ops["estatus"], ops["regresa"]) if _k(n)}
    hoy = date.today()

    abiertos = ctx.abiertos
    viaje = {}                                            # eco -> 'EN SERVICIO' / 'PROGRAMADA' + descripcion
    for _, v in abiertos.iterrows():
        e = _k(v.get("eco"))
        if not e:
            continue
        cita = pd.to_datetime(v.get("cita_carga"), errors="coerce")
        en_curso = (_k(v.get("estatus")) not in ("", "PROGRAMADO")
                    or (pd.notna(cita) and cita <= pd.Timestamp.now()))     # ya le toco cargar: anda trabajando
        desc = f"viaje #{int(v['id'])}" + (f" · {v.get('cliente') or v.get('tipo_operacion') or ''}".rstrip(" ·")
                                            if v.get("cliente") or v.get("tipo_operacion") else "")
        if e not in viaje or (en_curso and viaje[e][0] != "EN SERVICIO"):
            viaje[e] = ("EN SERVICIO" if en_curso else "PROGRAMADA", desc)
    zam_ecos = ctx.ecos_activos_zam()

    filas = []
    for _, u in uni.iterrows():
        e = _k(u["eco"])
        if not e:
            continue
        d = dis.loc[e] if e in dis.index else None
        g = (lambda c: (d[c] if d is not None and pd.notna(d[c]) else None))
        operador = u.get("operador") if _k(u.get("operador")) else None
        eo, ro = est_op.get(_k(operador), (None, None)) if operador else (None, None)
        sit = _k(u.get("situacion")) or "OPERANDO"
        m_op = motivo_operador(eo, ro, hoy) if operador else ""
        if sit != "OPERANDO":
            estatus, motivo = sit, "Situación de la unidad"
        elif e in ctx.dyr["eco"]:
            estatus, motivo = "DEVOLUCION DYR", f"Folio {ctx.dyr['eco'][e]} sin liberar"
        elif e in viaje:
            estatus, motivo = viaje[e]
        elif e in zam_ecos:
            estatus, motivo = "EN SERVICIO", "Viaje abierto en ZAM"
        elif m_op:
            estatus, motivo = "SIN OPERADOR", f"{operador}: {m_op}"
        elif not operador:
            estatus, motivo = "DISPONIBLE", "Sin operador titular"
        else:
            estatus, motivo = "DISPONIBLE", ""
        filas.append({
            "operacion": g("operacion"), "gerencia": g("gerencia"), "circuito": g("circuito"), "eco": u["eco"],
            "modalidad": g("modalidad"), "segmento": g("segmento"), "operador": operador,
            "estatus_operador": (bd.estatus_operador_valido(eo) if m_op else None) or "ACTIVO",
            "regresa": _fecha(ro) if m_op else None,
            "situacion": sit, "estatus": estatus, "motivo": motivo,
            "r1": g("r1"), "dolly": g("dolly"), "r2": g("r2"), "comentarios": g("comentarios"), "jefe": g("jefe"),
        })
    df = pd.DataFrame(filas)
    if not df.empty:
        orden_op = {o: i for i, o in enumerate(OPERACIONES)}
        df["_o"] = [(orden_op.get(_k(o), 9), _k(c), len(str(e)), str(e))
                    for o, c, e in zip(df["operacion"], df["circuito"], df["eco"])]
        df = df.sort_values("_o").drop(columns="_o").reset_index(drop=True)
    return df


def resumen(df):
    """Conteo ESTATUS x OPERACION (como RESUMEN GRAL)."""
    if df.empty:
        return df
    t = df.assign(operacion=df["operacion"].fillna("(sin operación)").replace("", "(sin operación)"))
    r = pd.crosstab(t["estatus"], t["operacion"], margins=True, margins_name="TOTAL")
    orden = [c for c in OPERACIONES if c in r.columns] + [c for c in r.columns if c not in OPERACIONES and c != "TOTAL"] + ["TOTAL"]
    r = r[orden]
    r["% DEL TOTAL"] = (r["TOTAL"] / r.loc["TOTAL", "TOTAL"] * 100).round(1)
    return r


# ---------------------------------------------------------------- guardar lo capturado
def guardar(original, editado, quien, perfil):
    """Guarda lo que cambio en la tabla. perfil: MASTER / JEFE OPS / MTTO. Devuelve (cambios, errores)."""
    errores, cambios = [], []
    ant = original.set_index(original["eco"].map(_k))
    ops = bd.leer_catalogo("operadores")
    nombres = {_k(n): n for n in ops["nombre"] if _k(n)}
    # operador -> unidades (como quedaria todo despues de guardar)
    uni = bd.leer_catalogo("unidades")
    titular = {_k(e): _k(o) for e, o in zip(uni["eco"], uni["operador"]) if _k(o)}
    for _, r in editado.iterrows():
        titular[_k(r["eco"])] = _k(r.get("operador"))
    dueno = {}
    for e, o in titular.items():
        if o:
            dueno.setdefault(o, []).append(e)
    hoy_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    quien_txt = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    with bd.conexion() as con:
        for _, r in editado.iterrows():
            e = _k(r["eco"])
            if e not in ant.index:
                continue
            a = ant.loc[e]
            eco = str(a["eco"])
            # --- datos de la hoja (operacion, circuito, remolques...)
            camb = {c: (None if _k(r.get(c)) == "" else str(r.get(c)).strip()) for c in CAMPOS if _k(r.get(c)) != _k(a.get(c))}
            if camb:
                existe = con.execute("SELECT 1 FROM dispo_unidades WHERE eco = ?", [eco]).fetchone()
                if not existe:
                    con.execute("INSERT INTO dispo_unidades (eco) VALUES (?)", [eco])
                con.execute(f"UPDATE dispo_unidades SET {', '.join(c + ' = ?' for c in camb)}, modificado = ?, modificado_por = ? "
                            "WHERE eco = ?", list(camb.values()) + [hoy_txt, quien_txt, eco])
                cambios += [f"{eco}: {c} {a.get(c) or '—'} → {v or '—'}" for c, v in camb.items()]
            # --- operador titular
            op_n, op_a = _k(r.get("operador")), _k(a.get("operador"))
            if op_n != op_a:
                if op_n and op_n not in nombres:
                    errores.append(f"{eco}: el operador {r.get('operador')} no está en el catálogo de operadores.")
                elif op_n and len(dueno.get(op_n, [])) > 1:
                    errores.append(f"{eco}: {nombres[op_n]} ya es titular de la unidad "
                                   f"{', '.join(x for x in dueno[op_n] if x != e)}. Quítalo de allá primero.")
                else:
                    con.execute("UPDATE unidades SET operador = ? WHERE eco = ?", [nombres.get(op_n), eco])
                    cambios.append(f"{eco}: operador {a.get('operador') or '—'} → {nombres.get(op_n) or '—'}")
            # --- estatus del operador (vacaciones, descanso...) y su regreso
            op_final = nombres.get(op_n) if op_n in nombres else None
            est_n = bd.estatus_operador_valido(r.get("estatus_operador"))
            est_a = bd.estatus_operador_valido(a.get("estatus_operador"))
            reg_n, reg_a = _fecha(r.get("regresa")), _fecha(a.get("regresa"))
            if (est_n != est_a or reg_n != reg_a) and op_final:
                if est_n and reg_n and reg_n <= date.today():
                    errores.append(f"{eco}: la fecha de REGRESA de {op_final} debe ser después de hoy.")
                else:
                    con.execute("UPDATE operadores SET estatus = ?, regresa = ? WHERE nombre = ?",
                                [est_n, str(reg_n) if (est_n and reg_n) else None, op_final])
                    cambios.append(f"{op_final}: {est_a or 'ACTIVO'} → {est_n or 'ACTIVO'}"
                                   + (f" (regresa {reg_n:%d/%m/%Y})" if est_n and reg_n else ""))
            elif (est_n != est_a or reg_n != reg_a) and not op_final:
                errores.append(f"{eco}: para poner {est_n or 'ACTIVO'} primero asigna el operador titular.")
            # --- situacion de la unidad
            sit_n, sit_a = bd.situacion_valida(r.get("situacion")) or "OPERANDO", _k(a.get("situacion")) or "OPERANDO"
            if sit_n != sit_a:
                if sit_a in SOLO_MTTO_LIBERA and perfil not in ("MASTER", "MTTO"):
                    errores.append(f"{eco}: está en TALLER; solo Mantenimiento la libera.")
                else:
                    con.execute("UPDATE unidades SET situacion = ? WHERE eco = ?", [sit_n, eco])
                    cambios.append(f"{eco}: situación {sit_a} → {sit_n}")
    if cambios:
        bd.bitacora(quien, "DISPONIBILIDAD", f"{len(cambios)} cambios", "; ".join(cambios)[:3900])
    return cambios, errores


# ---------------------------------------------------------------- carga del Excel (hoja GRAL ER)
_MESES = {"ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
          "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
          "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10,
          "NOV": 11, "DIC": 12}


def fecha_regreso(texto, hoy=None):
    """'OP REGRESA 1 DE DICIEMBRE' / 'op regresa 19 de noviembre' -> date. None si no dice."""
    t = _k(texto)
    m = re.search(r"REGRESA\D{0,8}?(\d{1,2})\s*(?:DE\s+)?([A-Z]+)", t)
    if not m or m.group(2) not in _MESES:
        return None
    hoy = hoy or date.today()
    mes, dia = _MESES[m.group(2)], int(m.group(1))
    anio = hoy.year + (1 if mes < hoy.month - 6 else 0)
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def leer_excel(archivo):
    """Lee la hoja GRAL ER (encabezados en el renglon 2). Devuelve DataFrame con columnas normalizadas."""
    g = pd.read_excel(archivo, sheet_name="GRAL ER", header=1, dtype=object)
    g.columns = [_k(c) for c in g.columns]
    # dos columnas ESTATUS: la 1a es del operador, la 2a (pandas la llama ESTATUS.1) de la unidad
    g = g.rename(columns={"ESTATUS": "est_op", "ESTATUS.1": "est_uni", "OPERACION": "operacion", "GERENCIA": "gerencia", "OPERADOR": "operador",
                          "SEGMENTO": "segmento", "CIRCUITO": "circuito", "ECO": "eco", "MODALIDAD": "modalidad",
                          "R1": "r1", "DO": "dolly", "R2": "r2", "COMENTARIOS": "comentarios", "JEFE / COOR": "jefe"})
    g["_fila"] = g.index + 3
    return g


def importar_excel(archivo, quien, aplicar=True):
    """Carga la hoja GRAL ER del CONTROL DE DISPONIBILIDAD. Llena Disponibilidad, operador titular, estatus y regreso del
    operador y la SITUACION de la unidad (solo si en el TMS esta OPERANDO: no pisa lo que puso Flota o Mantenimiento).
    Devuelve dict con conteos y avisos (renglones dudosos del Excel)."""
    g = leer_excel(archivo)
    uni = bd.leer_catalogo("unidades")
    eco_tms = {_k(e): str(e) for e in uni["eco"]}
    sit_tms = {_k(e): (_k(s) or "OPERANDO") for e, s in zip(uni["eco"], uni["situacion"])}
    ops = bd.leer_catalogo("operadores")
    nombres = {_k(n): n for n in ops["nombre"] if _k(n)}
    avisos, vistos, op_de = [], {}, {}
    res = {"unidades": 0, "operadores": 0, "situaciones": 0, "ausencias": 0}
    sit_unidad = {"TALLER", "SINIESTRO", "SEGURIDAD", "AGENCIA", "DESHABILITACION", "HABILITACION", "INACTIVA"}
    planes = []
    estado_op = {}                                   # operador -> (estatus, regresa): TODOS los renglones, tengan ECO o no
    for _, r in g.iterrows():
        op_r = _k(r.get("operador"))
        if op_r in nombres:
            est = bd.estatus_operador_valido(r.get("est_op"))
            reg = fecha_regreso(r.get("comentarios")) if est else None
            if est and reg and reg <= date.today():
                est, reg = None, None                # ya regreso
            estado_op[op_r] = (est, reg)
        e = _k(r.get("eco"))
        if not e:
            continue
        fila = r["_fila"]
        if e not in eco_tms:
            avisos.append(f"Renglón {fila}: el ECO {e} no está en el catálogo de vehículos (no se cargó).")
            continue
        if e in vistos:
            avisos.append(f"Renglón {fila}: el ECO {e} ya venía en el renglón {vistos[e]} (se queda el último).")
        vistos[e] = fila
        op = _k(r.get("operador"))
        if op in ("", "SIN OPERADOR"):
            op = ""
        elif op not in nombres:
            avisos.append(f"Renglón {fila}: el operador {r.get('operador')} no está en el catálogo de operadores "
                          "(dalo de alta con su nómina).")
            op = ""
        elif op in op_de and op_de[op][0] != e:
            avisos.append(f"Renglón {fila}: {nombres[op]} también es titular del ECO {op_de[op][0]} "
                          f"(renglón {op_de[op][1]}); se deja en el primero.")
            op = ""
        if op:
            op_de[op] = (e, fila)
        planes.append((e, r, op))

    if not aplicar:
        return {**res, "avisos": avisos, "renglones": len(planes)}
    hoy_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    quien_txt = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    with bd.conexion() as con:
        for e, r, op in planes:
            eco = eco_tms[e]
            vals = {c: (None if _k(r.get(c)) == "" else " ".join(str(r.get(c)).split())) for c in CAMPOS}
            if vals["dolly"] and not re.search(r"\d", vals["dolly"] or ""):
                vals["dolly"] = None                     # en PEPSICO esa columna trae la ubicacion, no el dolly
            con.execute(f"INSERT OR REPLACE INTO dispo_unidades (eco, {', '.join(CAMPOS)}, modificado, modificado_por) "
                        f"VALUES (?, {', '.join('?' for _ in CAMPOS)}, ?, ?)",
                        [eco] + [vals[c] for c in CAMPOS] + [hoy_txt, quien_txt])
            res["unidades"] += 1
            if op:
                con.execute("UPDATE unidades SET operador = ? WHERE eco = ?", [nombres[op], eco])
            su = bd.situacion_valida(r.get("est_uni"))
            op_activo = not bd.estatus_operador_valido(r.get("est_op"))
            if su in sit_unidad and (su != "INACTIVA" or (op and op_activo)):
                if sit_tms.get(e) == "OPERANDO":
                    con.execute("UPDATE unidades SET situacion = ? WHERE eco = ?", [su, eco])
                    res["situaciones"] += 1
                elif sit_tms.get(e) != su:
                    avisos.append(f"ECO {e}: en el Excel dice {su} y en el TMS está {sit_tms.get(e)} "
                                  "(se respetó el TMS: lo cambia Flota/Mantenimiento).")
            elif _k(r.get("est_uni")) in AUTOMATICOS and sit_tms.get(e) not in ("OPERANDO", None):
                avisos.append(f"ECO {e}: en el Excel dice {_k(r.get('est_uni'))} pero en el TMS está {sit_tms.get(e)}.")
        for op, (est, reg) in estado_op.items():
            con.execute("UPDATE operadores SET estatus = ?, regresa = ? WHERE nombre = ?",
                        [est, str(reg) if reg else None, nombres[op]])
            res["operadores"] += 1
            res["ausencias"] += 1 if est else 0
    bd.bitacora(quien, "DISPONIBILIDAD_EXCEL", "GRAL ER",
                f"{res['unidades']} unidades · {res['operadores']} operadores · {res['ausencias']} ausencias · "
                f"{res['situaciones']} situaciones · {len(avisos)} avisos")
    return {**res, "avisos": avisos, "renglones": len(planes)}
