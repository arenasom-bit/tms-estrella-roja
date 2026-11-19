# =====================================================================
#  dyr.py - DYR: Devoluciones y Rechazos (Pepsico)
#
#  Reemplaza el Excel "DYR PEPSICO":
#    - OPS captura el FOLIO CLIENTE y los datos de la devolucion.
#    - Lo FORMULADO (ECO, remolque, operador, origen, destino, localidad, modalidad, cita de carga,
#      salida de descarga, factura, CP y la descarga real) lo trae solo el TMS desde el Plan de carga.
#      Solo un MASTER puede corregirlo a mano.
#    - MESA DE CONTROL (folio interno ... fecha de entrega a liquidacion) y ESTATUS: solo MASTER.
#  REGLA: mientras el MASTER no la libere (ESTATUS = CERRADO), la UNIDAD y la CAJA de la devolucion
#  NO pueden quedar disponibles ni asignarse a otro viaje.
# =====================================================================
import pandas as pd

import base_datos as bd

# (columna, encabezado del Excel, grupo)  grupo: OPS = OPS y MASTER · FORM = formulado · MESA = mesa de control (MASTER)
CAMPOS = [
    ("folio_cliente", "FOLIO CLIENTE", "OPS"),
    ("eco", "ECO", "FORM"), ("remolque", "REMOLQUE", "FORM"), ("operador", "OPERADOR", "FORM"),
    ("origen", "ORIGEN", "FORM"), ("destino", "DESTINO", "FORM"), ("localidad", "LOCALIDAD DESTINO", "FORM"),
    ("modalidad", "MODALIDAD", "FORM"), ("cita_carga", "FECHA DE CITA DE CARGA", "FORM"),
    ("salida_descarga", "SALIDA DE DESCARGA", "FORM"),
    ("cantidad", "CANTIDAD DE MERCANCIA", "OPS"), ("descripcion", "DESCRIPCIÓN DE MERCANCIA", "OPS"),
    ("motivo", "MOTIVO DE LA DEVOLUCION Y/O RECHAZO", "OPS"),
    ("descarga_real", "FECHA Y HORA DE DESCARGA REAL", "OPS"),
    ("folio_peplink", "FOLIO PEPLINK", "OPS"), ("estatus_peplink", "ESTATUS PEPLINK", "OPS"),
    ("entrada_patio", "ENTRADA PATIO", "OPS"),
    ("folio_interno", "FOLIO INTERNO DE ENTREGA", "MESA"), ("cantidad_entregar", "CANTIDAD A ENTREGAR", "MESA"),
    ("cajas_cargo", "CAJAS A CARGO", "MESA"), ("motivo_cargo", "MOTIVO", "MESA"),
    ("unidad_entrega", "UNIDAD", "MESA"), ("caja_entrega", "CAJA", "MESA"), ("operador_entrega", "OPERADOR ENTREGA", "MESA"),
    ("estatus_entrega", "ESTATUS DE ENTREGA", "MESA"), ("fecha_entrega_pepsico", "FECHA DE ENTREGA A PEPSICO", "MESA"),
    ("fecha_entrega_liquidacion", "FECHA DE ENTREGA A LIQUIDACION", "MESA"),
    ("comentarios", "COMENTARIOS", "OPS"), ("factura", "FACTURA", "FORM"), ("cp", "CP", "FORM"),
    ("estatus", "ESTATUS DEVOLUCIONES", "MESA"),
]
COLS = [c for c, _, _ in CAMPOS]
TITULO = {c: t for c, t, _ in CAMPOS}
GRUPO = {c: g for c, _, g in CAMPOS}
FECHAS = {"cita_carga", "salida_descarga", "descarga_real", "fecha_entrega_pepsico", "fecha_entrega_liquidacion"}
MOTIVOS = ["DAÑADO", "FALTANTE DE ORIGEN", "MERMAS", "MOJADO", "SOBRANTE", "PRODUCTO NO FACTURADO", "PRODUCTO NO PEDIDO",
           "PRODUCTO CAMBIADO", "PRODUCTO NO SOLICITADO", "SIN CONFIRMACION DE CITA"]
ESTATUS = ["PROCESO", "VALIDACION", "CERRADO"]
ESTATUS_PEPLINK = ["ABIERTO", "CERRADO"]
ESTATUS_ENTREGA = ["PENDIENTE", "ENTREGADO"]
# del Plan de carga (por FOLIO CLIENTE) -> columna DYR
DEL_PLAN = {"eco": "eco", "remolque": "remolque_1", "operador": "operador", "origen": "origen", "destino": "destino",
            "localidad": "localidad_destino", "modalidad": "modalidad", "cita_carga": "cita_carga",
            "salida_descarga": "salida_descarga", "factura": "factura", "cp": "cp_cargado", "descarga_real": "llegada_descarga"}


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
    defs = ", ".join(f"{c} TEXT" for c in COLS)
    con.execute(f"CREATE TABLE IF NOT EXISTS dyr (id INTEGER PRIMARY KEY AUTOINCREMENT, {defs}, viaje_id INTEGER, "
                "creado TEXT, creado_por TEXT, modificado TEXT, modificado_por TEXT)")


def leer():
    with bd.conexion() as con:
        df = pd.read_sql_query(f"SELECT id, {', '.join(COLS)} FROM dyr ORDER BY id", con)
    for c in FECHAS:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def del_plan(folio):
    """Datos FORMULADOS: el viaje del plan con ese FOLIO CLIENTE (el mas reciente)."""
    f = _k(folio)
    if not f:
        return None, {}
    v = bd.leer_viajes("0000-01-01", "9999-12-31")
    v = v[v["folio_cliente"].map(_k) == f]
    if v.empty:
        return None, {}
    r = v.sort_values(["fecha_plan", "id"]).iloc[-1]
    datos = {}
    for c_dyr, c_plan in DEL_PLAN.items():
        x = r.get(c_plan)
        if x is not None and not (isinstance(x, float) and pd.isna(x)) and x is not pd.NaT and _k(x) not in ("", "NAT", "NAN"):
            datos[c_dyr] = x
    return int(r["id"]), datos


def _texto(c, v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if c in FECHAS:
        t = pd.to_datetime(v, errors="coerce")
        return None if pd.isna(t) else t.strftime("%Y-%m-%d %H:%M:%S")
    s = " ".join(str(v).split())
    return s.removesuffix(".0") or None


def guardar(original, editado, quien, es_master):
    """Guarda la tabla del DYR. OPS solo puede cambiar sus columnas; lo formulado se llena solo.
    Devuelve (nuevos, cambiados, borrados, avisos)."""
    usuario = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    ahora = bd.AHORA_TXT()
    permitido = set(COLS) if es_master else {c for c in COLS if GRUPO[c] == "OPS"}
    avisos, nuevos, cambiados, borrados = [], 0, 0, 0
    ids_antes = {int(i) for i in original["id"].dropna()}
    ids_despues = {int(i) for i in editado["id"].dropna()} if "id" in editado else set()
    ant = original.set_index("id") if len(original) else original
    with bd.conexion() as con:
        if es_master:
            for i in ids_antes - ids_despues:
                con.execute("DELETE FROM dyr WHERE id = ?", [i])
                borrados += 1
        elif ids_antes - ids_despues:
            avisos.append("Solo un MASTER puede borrar renglones del DYR (no se borró nada).")
        for _, r in editado.iterrows():
            folio = _texto("folio_cliente", r.get("folio_cliente"))
            if not folio:
                continue
            vid, plan = del_plan(folio)
            valores = {c: _texto(c, r.get(c)) for c in COLS if c in editado.columns}
            if pd.isna(r.get("id")) if "id" in editado else True:
                fila = {c: (valores.get(c) if c in permitido else None) for c in COLS}
                for c, x in plan.items():                    # formulado: siempre del plan (salvo que MASTER lo escriba)
                    if not fila.get(c):
                        fila[c] = _texto(c, x)
                fila["estatus"] = fila.get("estatus") or "PROCESO"
                if not plan:
                    avisos.append(f"Folio {folio}: no está en el Plan de carga (NO ENCONTRADO); captura los datos a mano.")
                marcas = ", ".join("?" for _ in COLS)
                con.execute(f"INSERT INTO dyr ({', '.join(COLS)}, viaje_id, creado, creado_por, modificado, modificado_por) "
                            f"VALUES ({marcas}, ?, ?, ?, ?, ?)", [fila[c] for c in COLS] + [vid, ahora, usuario, ahora, usuario])
                nuevos += 1
                continue
            i = int(r["id"])
            antes = {c: _texto(c, ant.loc[i].get(c)) for c in COLS} if i in ant.index else {}
            cambios = {c: valores[c] for c in valores if c in permitido and valores[c] != antes.get(c)}
            if "folio_cliente" in cambios or any(not antes.get(c) for c in plan):
                for c, x in plan.items():                    # al cambiar el folio (o si falta) se vuelve a traer del plan
                    if (c not in cambios or not es_master) and ("folio_cliente" in cambios or not antes.get(c)):
                        cambios[c] = _texto(c, x)
            prohibidos = [TITULO[c] for c in valores if c not in permitido and valores[c] != antes.get(c)]
            if prohibidos:
                avisos.append(f"Folio {folio}: solo un MASTER puede cambiar {', '.join(prohibidos[:4])}.")
            if cambios:
                sets = ", ".join(f"{c} = ?" for c in cambios)
                con.execute(f"UPDATE dyr SET {sets}, viaje_id = COALESCE(?, viaje_id), modificado = ?, modificado_por = ? "
                            "WHERE id = ?", list(cambios.values()) + [vid, ahora, usuario, i])
                cambiados += 1
    bd.bitacora(quien, "DYR_GUARDADO", "DYR", f"{nuevos} nuevos · {cambiados} cambiados · {borrados} borrados")
    return nuevos, cambiados, borrados, avisos


def bloqueados():
    """Unidades y cajas con devolucion SIN liberar (estatus distinto de CERRADO): {'eco': {k: folio}, 'remolque': {...}}"""
    try:
        df = leer()
    except Exception:
        return {"eco": {}, "remolque": {}}
    df = df[df["estatus"].map(_k) != "CERRADO"]
    eco, rem = {}, {}
    for _, r in df.iterrows():
        u = _k(r.get("unidad_entrega")) or _k(r.get("eco"))
        c = _k(r.get("caja_entrega")) or _k(r.get("remolque"))
        if u:
            eco[u] = _k(r.get("folio_cliente"))
        if c:
            rem[c] = _k(r.get("folio_cliente"))
    return {"eco": eco, "remolque": rem}


def ficha(folio):
    """Ficha de la devolucion (como AG6:AH13 del Excel): solo se escribe el FOLIO CLIENTE."""
    df = leer()
    r = df[df["folio_cliente"].map(_k) == _k(folio)]
    if r.empty:
        return None
    r = r.iloc[-1]
    fecha = r["descarga_real"]
    return [("FOLIO CLIENTE", _k(folio)),
            ("UNIDAD", _k(r.get("unidad_entrega")) or _k(r.get("eco")) or None),
            ("CAJA", _k(r.get("caja_entrega")) or _k(r.get("remolque")) or None),
            ("CANTIDAD", r.get("cantidad")),
            ("DESCRIPCIÓN", r.get("descripcion")),
            ("MOTIVO", r.get("motivo")),
            ("FECHA Y HORA DESCARGA REAL", f"{fecha:%d/%m/%Y %H:%M}" if pd.notna(fecha) else None),
            ("ESTATUS", r.get("estatus"))]


def importar_excel(archivo, quien):
    """Carga el Excel DYR PEPSICO (encabezados en la fila 3). Por FOLIO CLIENTE: agrega lo que no exista y
    llena lo vacio de lo que ya exista. Devuelve (nuevos, completados)."""
    from openpyxl import load_workbook
    wb = load_workbook(archivo, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    enc = [_k(x) for x in filas[2]]
    por_titulo = {_k(t): c for c, t, _ in CAMPOS}
    por_titulo.update({"MOTIVO DE LA DEVOLUCION Y/O RECHAZO": "motivo", "OPERADOR": "operador",
                       "UNIDAD": "unidad_entrega", "CAJA": "caja_entrega"})
    pos = {}
    for i, h in enumerate(enc):
        c = por_titulo.get(h)
        if h == "OPERADOR" and "operador" in pos.values():
            c = "operador_entrega"
        if h == "MOTIVO" and "motivo" in pos.values():
            c = "motivo_cargo"
        if c and c not in pos.values():
            pos[i] = c
    actuales = {_k(f): int(i) for i, f in zip(*[leer()[c] for c in ("id", "folio_cliente")])}
    usuario = quien.get("usuario") if isinstance(quien, dict) else str(quien)
    nuevos = completados = 0
    ahora = bd.AHORA_TXT()
    with bd.conexion() as con:
        for fila in filas[3:]:
            d = {c: _texto(c, fila[i]) for i, c in pos.items() if i < len(fila)}
            folio = _k(d.get("folio_cliente"))
            if not folio or folio.startswith("#"):
                continue
            d = {c: (None if (v or "").upper() in ("NO ENCONTRADO", "#N/A") else v) for c, v in d.items()}
            if folio in actuales:
                ant = con.execute(f"SELECT {', '.join(COLS)} FROM dyr WHERE id = ?", [actuales[folio]]).fetchone()
                if ant is None:
                    continue
                vacios = {c: d[c] for j, c in enumerate(COLS) if c in d and d[c] and not ant[j]}
                if vacios:
                    con.execute(f"UPDATE dyr SET {', '.join(c + ' = ?' for c in vacios)}, modificado = ? WHERE id = ?",
                                list(vacios.values()) + [ahora, actuales[folio]])
                    completados += 1
                continue
            vid, plan = del_plan(folio)
            for c, x in plan.items():
                if not d.get(c):
                    d[c] = _texto(c, x)
            d["estatus"] = (d.get("estatus") or "PROCESO").upper()
            cur = con.execute(f"INSERT INTO dyr ({', '.join(COLS)}, viaje_id, creado, creado_por, modificado, modificado_por) "
                              f"VALUES ({', '.join('?' for _ in COLS)}, ?, ?, ?, ?, ?)",
                              [d.get(c) for c in COLS] + [vid, ahora, usuario, ahora, usuario])
            nuevo_id = getattr(cur, "lastrowid", None) or con.execute("SELECT MAX(id) FROM dyr").fetchone()[0]
            actuales[folio] = int(nuevo_id)       # el mismo folio repetido en el Excel completa este renglon
            nuevos += 1
    bd.bitacora(quien, "DYR_EXCEL_CARGADO", "DYR", f"{nuevos} nuevos · {completados} completados")
    return nuevos, completados
