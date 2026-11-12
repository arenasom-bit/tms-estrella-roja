# ==========================================================
#  importar_plan.py - Pone AL DIA el plan de carga del TMS con el Excel de planes
#  (el libro con pestañas PEPSICO DEDICADO, PEPSICO CELAYA, SPOT, PEPSICO, KN,
#   COPPEL TEXCOCO, COPPEL PUEBLA, LA COMER, DHL NATURA, DHL CENTRO, DHL MTY, RYC).
#
#  Por cada renglon del Excel busca el MISMO viaje en el TMS:
#     1) mismo PEDIDO   2) mismo CP CARGADO / CP ZAM / FOLIO ZAM   3) mismo # VIAJE
#     4) misma unidad (ECO) y cita de carga / despacho a menos de 12 h (mismo tipo de operacion)
#  - Si lo encuentra: lo ACTUALIZA con lo que trae el Excel (nunca borra un dato con un vacio).
#  - Si no lo encuentra: lo AGREGA al plan del dia de su cita de carga.
#  ESTATUS DISPONIBLE -> ✅ finalizado · CANCELADO -> ❌ cancelado. En las hojas sin ESTATUS,
#  si ya trae SALIDA DE DESCARGA el viaje queda ✅ finalizado.
#
#  Se puede correr las veces que quieras: no duplica.
#  Uso:  py importar_plan.py "PLAN DE CARGA.xlsx"          (o desde Catalogos > Actualizar plan)
# ==========================================================
import re
import sys
import unicodedata
from datetime import date, datetime, time, timedelta

import pandas as pd
from openpyxl import load_workbook

import base_datos as bd
from columnas import TIPO_POR_CLAVE

# ---------------------------------------------------------- encabezado del Excel -> columna del TMS
COMUNES = {
    "ECO": "eco", "UNIDAD": "eco", "OPERADOR": "operador",
    "ORIGEN": "origen", "DESTINO": "destino", "LOCALIDAD DESTINO": "localidad_destino",
    "PEDIDO": "pedido", "FOLIO CLIENTE": "folio_cliente", "FLETE": "flete", "$ FLETE": "flete",
    "REMOLQUE 1": "remolque_1", "REMOLQUE": "remolque_1", "FOLIO DE CONFIRMACION": "folio_confirmacion",
    "SELLO 1": "sello_1", "SELLO 2": "sello_2", "FUMIGACION": "fumigacion",
    "BARRAS REP": "barras_rep", "BARRAS REAL": "barras_real", "BARRAS ENTREGADAS": "barras_entregadas",
    "DEVOLUCION": "devolucion",
    "CITA DE CARGA": "cita_carga", "CITA CARGA": "cita_carga",
    "LLEGADA CARGA": "llegada_carga", "SALIDA CARGA": "salida_carga",
    "MOTIVO DE INCUMPLIMIENTO CARGA": "motivo_carga",
    "CITA DE DESCARGA": "cita_descarga", "LLEGADA DESCARGA": "llegada_descarga", "SALIDA DESCARGA": "salida_descarga",
    "MOTIVO DE INCUMPLIMIENTO DESCARGA": "motivo_descarga",
    "INICIO DE RUTA": "inicio_ruta", "LLEGADA A DESTINO": "llegada_destino",
    "MOTIVO DE INCUMPLIMIENTO TRANSITO": "motivo_transito",
    "INSTRUCCIONES ESPECIALES": "instrucciones", "FACTURA": "factura",
    "POSICIONAM": "posicionam", "POSICIONAMIENTO": "posicionam",
    "CP CARGADO": "cp_cargado", "CP ZAM": "cp_cargado", "FOLIO ZAM": "cp_cargado", "CP VACIO": "cp_vacio",
    "# VIAJE": "num_viaje", "VIAJE CARGADO": "num_viaje", "VIAJE VACIO": "viaje_vacio",
    "DIRECCION ORIGEN": "direccion_origen", "LINK ORIGEN": "link_origen",
    "DIRECCION DESTINO": "direccion_destino", "LINK DESTINO": "link_destino",
    "CLIENTE (PAGADOR)": "_cliente", "ESTATUS": "_estatus",
    "FECHA Y HORA DE DESPACHO": "_despacho", "FECHA Y HORA DE REGISTRO": "_registro",
}

# Cada pestaña: tipo de operacion, cliente por defecto, encabezados propios y si su MODALIDAD es la del TMS
HOJAS = {
    "PEPSICO DEDICADO": {"tipo": "PEPSICO DEDICADO", "cliente": "COMERCIALIZADORA PEPSICO MEXICO", "modalidad": True},
    "PEPSICO CELAYA": {"tipo": "PEPSICO DED CELAYA", "cliente": "COMERCIALIZADORA PEPSICO MEXICO", "modalidad": True},
    "PEPSICO": {"tipo": "SPOT REF SEN", "cliente": "COMERCIALIZADORA PEPSICO MEXICO"},
    "SPOT": {"tipo": "SPOT REF SEN", "cliente": None, "extra": {"TIPO DE VIAJE": "modalidad"}},
    "KN": {"tipo": "DDS", "cliente": "KUEHNE + NAGEL",
           "extra": {"CONTROL DE RUTA": "folio_cliente", "ADENDA": "folio_confirmacion"}},
    "COPPEL TEXCOCO": {"tipo": "COPPEL DEDICADO SEN", "cliente": "COPPEL", "origen": "TEXCOCO",
                       "extra": {"BITACORA": "folio_cliente", "HORA LLEGADA CARGA": "llegada_carga",
                                 "HORA REAL DE SALIDA": "salida_carga", "HORA DE LLEGADA A DESCARGA": "llegada_descarga",
                                 "SALIDA DE DESCARGA": "salida_descarga"}},
    "COPPEL PUEBLA": {"tipo": "COPPEL DEDICADO SEN", "cliente": "COPPEL", "origen": "PUEBLA",
                      "extra": {"HORA LLEGADA CARGA": "llegada_carga", "HORA REAL DE SALIDA": "salida_carga",
                                "HORA DE LLEGADA A DESCARGA": "llegada_descarga", "SALIDA DE DESCARGA": "salida_descarga"}},
    "LA COMER": {"tipo": "LA COMER REF TH", "tipo_m2": "LA COMER REF M2", "cliente": "COMERCIAL CITY FRESKO",
                 "origen": "CEDIS LA COMER VALLEJO",
                 "extra": {"SALIDA DE CEDIS": "salida_carga", "ULTIMA TIENDA DESCARGA": "destino",
                           "SALIDA ULTIMA TIENDA": "salida_descarga"}},
    "DHL NATURA": {"tipo": "DHL NATURA SEN", "cliente": "DHL EXPRESS MEXICO",
                   "extra": {"LLEGADA REAL POSICIONAMIENTO": "llegada_carga",
                             "MOTIVO DE INCUMPLIMIENTO POSICIONAMIENTO": "motivo_carga"}},
    "DHL CENTRO": {"tipo": "DHL CENTRO SEN", "cliente": "DHL EXPRESS MEXICO"},
    "DHL MTY": {"tipo": "DHL MTY SEN", "cliente": "DHL EXPRESS MEXICO", "extra": {"REAL DE SALIDA": "salida_carga"}},
    "RYC": {"tipo": "RYC REF SEN", "cliente": "BACHOCO COMERCIAL"},
}
HOJAS_REPETIDAS = ["PEPSICO DEDICADO (2)"]      # copia exacta de otra pestaña: se ignora

FECHAS = {k for k, t in TIPO_POR_CLAVE.items() if t == "fecha"}
NUMEROS = {k for k, t in TIPO_POR_CLAVE.items() if t in ("entero", "dinero")}
LLAVES = ["pedido", "cp_cargado", "num_viaje"]
TOLERANCIA_H = 12


def _sin_acentos(t):
    return unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()


def _enc(t):
    return " ".join(_sin_acentos(t).upper().split())


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


def _palabras(t):
    return set(re.sub(r"[^A-Z0-9]+", " ", _enc(t)).split())


def _limpio(v, clave):
    """Valor del Excel listo para el TMS (o None si no sirve)."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "" or v.startswith("=") or v.startswith("#") or v.upper() in ("PTE", "PENDIENTE", "-", "N/A", "NA"):
            return None
        if "\t" in v:
            v = v.split("\t")[0].strip()
    if clave in FECHAS or clave in ("_despacho", "_registro"):
        if isinstance(v, datetime):           # Excel guarda 08:04:59.995 -> 08:05
            v = (pd.Timestamp(v).round("min")).to_pydatetime()
            return v if v.year >= 2020 else None
        if isinstance(v, str):
            t = pd.to_datetime(v, dayfirst=True, errors="coerce")
            return None if pd.isna(t) or t.year < 2020 else t.to_pydatetime()
        return None
    if isinstance(v, (timedelta, time)):
        return None
    if clave in NUMEROS:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if isinstance(v, datetime):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip() or None


def _cliente_catalogo(nombre, clientes):
    """'BACHOCO' -> 'BACHOCO COMERCIAL', 'SANBORNS' -> 'SANBORN HERMANOS' (por palabras)."""
    if not nombre:
        return None
    p = _palabras(nombre)
    for c in clientes:
        if _k(c) == _k(nombre):
            return c
    for c in clientes:
        q = _palabras(c)
        if p & q or any(a.startswith(b) or b.startswith(a) for a in p for b in q if len(a) > 4 and len(b) > 4):
            return c
    return _k(nombre)


# ---------------------------------------------------------- leer el libro
def leer_libro(archivo, hojas=None):
    """Devuelve una lista de viajes (dicts con columnas del TMS + '_hoja', '_fila').
    hojas: solo esas pestañas (por nombre); None = todas las que reconoce."""
    wb = load_workbook(archivo, read_only=True, data_only=True)
    clientes = bd.opciones_catalogo("clientes")
    viajes = []
    for ws in wb.worksheets:
        nombre = _enc(ws.title)
        if nombre in HOJAS_REPETIDAS or nombre not in HOJAS:
            continue
        if hojas is not None and nombre not in {_enc(h) for h in hojas}:
            continue
        conf = HOJAS[nombre]
        filas = ws.iter_rows(values_only=True)
        next(filas, None)                              # fila 1: quien llena (OPS/ATC/TC)
        enc = next(filas, None) or ()
        mapa = {**COMUNES, **{_enc(k): v for k, v in conf.get("extra", {}).items()}}
        pos = {}
        for i, h in enumerate(enc):
            if h is None:
                continue
            clave = mapa.get(_enc(h))
            if _enc(h) == "MODALIDAD":
                clave = "modalidad" if conf.get("modalidad") else "_modalidad"
            if clave and clave not in pos.values():
                pos[i] = clave
        vacias = 0
        for n, fila in enumerate(filas, start=3):
            v = {}
            for i, clave in pos.items():
                if i < len(fila):
                    x = _limpio(fila[i], clave)
                    if x is not None:
                        v[clave] = x
            tiene_dato = any(v.get(x) for x in LLAVES + ["cita_carga", "_despacho", "_registro", "origen", "destino"])
            if not v.get("eco") or not tiene_dato:
                vacias += 1
                if vacias > 60:
                    break
                continue
            vacias = 0
            v["_hoja"], v["_fila"] = ws.title, n
            v["tipo_operacion"] = conf["tipo"]
            if conf.get("tipo_m2") and _k(v.get("_modalidad")) == "M2":
                v["tipo_operacion"] = conf["tipo_m2"]
            v["cliente"] = _cliente_catalogo(v.pop("_cliente", None), clientes) or conf.get("cliente")
            if conf.get("origen") and not v.get("origen"):
                v["origen"] = conf["origen"]
            if not v.get("cita_carga"):
                v["cita_carga"] = v.get("_despacho") or v.get("_registro")
            for x in ("remolque_1", "eco"):
                if v.get(x):
                    v[x] = _k(v[x])
            if v.get("pedido") and not re.fullmatch(r"\d{5,}", str(v["pedido"])):
                v.pop("pedido")
            viajes.append(v)
    return viajes


# ---------------------------------------------------------- empatar contra el TMS
def _cuando(v):
    for c in ("cita_carga", "_despacho", "_registro"):
        t = v.get(c)
        if t is not None and not (isinstance(t, float) and pd.isna(t)):
            return pd.Timestamp(t)
    return None


def _buscar(v, tms, usados):
    for llave in LLAVES:
        val = _k(v.get(llave))
        if val:
            m = tms[(tms[llave].map(_k) == val) & (~tms["id"].isin(usados))]
            if len(m):
                return m.sort_values("id").iloc[0], llave
    eco, t = _k(v.get("eco")), _cuando(v)
    if eco and t is not None:
        m = tms[(tms["eco"].map(_k) == eco) & (~tms["id"].isin(usados))
                & (tms["tipo_operacion"].fillna(v["tipo_operacion"]) == v["tipo_operacion"])]
        if len(m):
            dif = (pd.to_datetime(m["cita_carga"], errors="coerce") - t).abs()
            m = m.assign(_d=dif)[dif <= pd.Timedelta(hours=TOLERANCIA_H)]
            # que no choque con una llave distinta ya capturada
            for llave in LLAVES:
                if v.get(llave):
                    m = m[(m[llave].map(_k) == "") | (m[llave].map(_k) == _k(v[llave]))]
            if len(m):
                return m.sort_values("_d").iloc[0], "eco+cita"
    if eco and t is None and not any(v.get(x) for x in LLAVES):     # renglon sin fecha ni folios
        m = tms[(tms["eco"].map(_k) == eco) & (~tms["id"].isin(usados))
                & (tms["tipo_operacion"] == v["tipo_operacion"])
                & (tms["origen"].map(_k) == _k(v.get("origen"))) & (tms["destino"].map(_k) == _k(v.get("destino")))]
        if len(m):
            return m.sort_values("id").iloc[0], "eco+ruta"
    return None, None


def _igual(a, b, clave):
    if clave in FECHAS:
        ta, tb = pd.to_datetime(a, errors="coerce"), pd.to_datetime(b, errors="coerce")
        return (pd.isna(ta) and pd.isna(tb)) or (not pd.isna(ta) and not pd.isna(tb) and ta == tb)
    if clave in NUMEROS:
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    return _k(a) == _k(b)


def _cierre(v):
    est = _k(v.get("_estatus"))
    if est == "CANCELADO":
        return "cancelado"
    if est == "DISPONIBLE" or (not est and v.get("salida_descarga")):
        return "finalizado"
    return None


def _lleno(x):
    if x is None:
        return False
    try:
        if pd.isna(x):
            return False
    except (TypeError, ValueError):
        pass
    return str(x).strip() != ""


def depurar_duplicados(aplicar=True, quien="SISTEMA"):
    """Junta en UNO los viajes repetidos (mismo PEDIDO, CP CARGADO o # VIAJE).
    Se queda el mas antiguo, le pasa los datos que solo tenian las copias y borra las copias.
    Devuelve [(id_que_queda, [ids_borrados]), ...]."""
    with bd.conexion() as con:
        df = pd.read_sql_query("SELECT * FROM viajes ORDER BY id", con)
    if df.empty:
        return []
    padre = {int(i): int(i) for i in df["id"]}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for llave in LLAVES:
        primero = {}
        for i, val in zip(df["id"], df[llave].map(_k)):
            if not val:
                continue
            if val in primero:
                a, b = raiz(primero[val]), raiz(int(i))
                if a != b:
                    padre[max(a, b)] = min(a, b)
            else:
                primero[val] = int(i)
    grupos = {}
    for i in df["id"]:
        grupos.setdefault(raiz(int(i)), []).append(int(i))
    datos = df.set_index("id")
    columnas = [c for c in df.columns if c not in ("id", "creado", "modificado", "fecha_plan")]
    hechos = []
    for queda, ids in grupos.items():
        if len(ids) < 2:
            continue
        copias = [i for i in ids if i != queda]
        base = datos.loc[queda]
        cambios = {}
        for c in columnas:
            if c in ("finalizado", "cancelado"):
                continue
            if not _lleno(base[c]):
                for i in copias:                         # la copia mas reciente que traiga el dato
                    if _lleno(datos.loc[i, c]):
                        cambios[c] = datos.loc[i, c]
        marcas = {c: any(_k(datos.loc[i, c]) in ("SI", "1", "TRUE") for i in ids) for c in ("finalizado", "cancelado")}
        if marcas["finalizado"]:
            cambios["finalizado"], cambios["cancelado"] = "SI", None
        elif marcas["cancelado"]:
            cambios["cancelado"] = "SI"
        hechos.append((queda, copias))
        if aplicar:
            with bd.conexion() as con:
                if cambios:
                    sets = ", ".join(f"{c} = ?" for c in cambios)
                    con.execute(f"UPDATE viajes SET {sets}, modificado = ? WHERE id = ?",
                                list(cambios.values()) + [bd.AHORA_TXT(), queda])
                con.execute(f"DELETE FROM viajes WHERE id IN ({', '.join('?' for _ in copias)})", copias)
            bd.bitacora(quien, "PLAN_DUPLICADO_DEPURADO", f"viaje #{queda}",
                        f"Se juntaron sus copias repetidas: {', '.join('#' + str(i) for i in copias)}")
    return hechos


def sincronizar(archivo, aplicar=True, quien="SISTEMA", hojas=None):
    """Compara el Excel con el TMS. aplicar=False solo dice que haria. Devuelve un resumen (dict)."""
    bd.crear_tablas()
    dup = depurar_duplicados(aplicar=aplicar, quien=quien)
    excel = leer_libro(archivo, hojas)
    tms = bd.leer_viajes("0000-01-01", "9999-12-31")
    if not aplicar and dup:                       # en prueba: hacer como si ya se hubieran juntado
        tms = tms[~tms["id"].isin({i for _, c in dup for i in c})]
    for c in LLAVES + ["eco", "tipo_operacion", "cita_carga"]:
        if c not in tms.columns:
            tms[c] = None
    usados, nuevos, cambios, sin_cambio = set(), [], [], 0
    res = {"duplicados": sum(len(c) for _, c in dup), "leidos": len(excel), "nuevos": 0, "actualizados": 0, "sin_cambio": 0, "finalizados": 0,
           "cancelados": 0, "por_hoja": {}, "detalle": []}
    for v in excel:
        hoja = res["por_hoja"].setdefault(v["_hoja"], {"leidos": 0, "nuevos": 0, "actualizados": 0})
        hoja["leidos"] += 1
        fila, como = _buscar(v, tms, usados)
        datos = {k: x for k, x in v.items() if not k.startswith("_") and k in TIPO_POR_CLAVE and x is not None}
        cierre = _cierre(v)
        if fila is None:
            if cierre:
                datos[cierre] = "SI"
            nuevos.append(datos)
            hoja["nuevos"] += 1
            res["detalle"].append((v["_hoja"], v["_fila"], "NUEVO", f"ECO {v.get('eco')} · {v.get('origen') or ''} → "
                                   f"{v.get('destino') or ''} · cita {_cuando(v):%d/%m %H:%M}" if _cuando(v) else ""))
            continue
        usados.add(int(fila["id"]))
        dif = {k: x for k, x in datos.items() if not _igual(fila.get(k), x, k)}
        ya_cerrado = _k(fila.get("finalizado")) in ("SI", "1", "TRUE") or _k(fila.get("cancelado")) in ("SI", "1", "TRUE")
        if cierre and not ya_cerrado:
            dif[cierre] = "SI"
        if dif:
            cambios.append((int(fila["id"]), dif))
            hoja["actualizados"] += 1
            res["detalle"].append((v["_hoja"], v["_fila"], f"ACTUALIZA viaje #{int(fila['id'])} ({como})",
                                   ", ".join(sorted(dif))))
        else:
            sin_cambio += 1
    res["nuevos"], res["actualizados"], res["sin_cambio"] = len(nuevos), len(cambios), sin_cambio
    res["finalizados"] = sum(1 for d in nuevos if d.get("finalizado")) + sum(1 for _, d in cambios if d.get("finalizado"))
    res["cancelados"] = sum(1 for d in nuevos if d.get("cancelado")) + sum(1 for _, d in cambios if d.get("cancelado"))
    if aplicar:
        for id_, dif in cambios:
            bd.actualizar_viaje(id_, dif)
        for d in nuevos:
            t = d.get("cita_carga")
            dia = t.date() if isinstance(t, datetime) else date.today()
            bd.insertar_viaje(d, dia)
            bd.catalogo_agregar("unidades", {"eco": d.get("eco"), "operador": d.get("operador")})
            bd.catalogo_agregar("remolques", {"remolque": d.get("remolque_1")})
            bd.catalogo_agregar("origenes", {"nombre": d.get("origen"), "direccion": d.get("direccion_origen"),
                                             "link": d.get("link_origen")})
            bd.catalogo_agregar("destinos", {"nombre": d.get("destino"), "localidad": d.get("localidad_destino"),
                                             "direccion": d.get("direccion_destino"), "link": d.get("link_destino")})
        bd.autocompletar_desde_catalogos()
        bd.bitacora(quien, "PLAN_EXCEL_SINCRONIZADO", str(getattr(archivo, "name", archivo))[:120],
                    f"{res['duplicados']} copias repetidas juntadas · {res['leidos']} renglones · {res['nuevos']} nuevos · {res['actualizados']} actualizados · "
                    f"{res['finalizados']} finalizados · {res['cancelados']} cancelados")
    return res


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Uso: py importar_plan.py "PLAN DE CARGA.xlsx" [--probar]')
        sys.exit(1)
    probar = "--probar" in sys.argv
    r = sincronizar(sys.argv[1], aplicar=not probar)
    print(("PRUEBA (no se guardó nada): " if probar else "Listo: ")
          + f"{r['duplicados']} copias repetidas juntadas · {r['leidos']} renglones leídos · {r['nuevos']} nuevos · {r['actualizados']} actualizados · "
            f"{r['sin_cambio']} ya estaban al día · {r['finalizados']} finalizados · {r['cancelados']} cancelados")
    for h, d in r["por_hoja"].items():
        print(f"  {h}: {d}")
