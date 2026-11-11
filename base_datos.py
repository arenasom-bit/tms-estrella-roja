# ==========================================================
#  base_datos.py - La "bodega" donde se guarda todo (archivo tms.db)
# ==========================================================
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import configuracion as cfg
from columnas import COLUMNAS, TIPO_POR_CLAVE, CATALOGOS, SITUACIONES

RUTA_BD = Path(__file__).parent / cfg.ARCHIVO_BD
AHORA_TXT = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def situacion_valida(valor):
    """Acepta 'taller', 'Corralón', etc. Devuelve la opcion correcta o None."""
    if valor is None:
        return None
    t = " ".join(str(valor).strip().upper().replace("Ó", "O").split())
    t = {"INOPERANDO": "NO DISPONIBLE", "INOPERATIVO": "NO DISPONIBLE", "BAJA": "NO DISPONIBLE",
         "EN TALLER": "TALLER", "SINIESTRADO": "SINIESTRO"}.get(t, t)
    return t if t in SITUACIONES else None


def _situacion_por_defecto(con):
    """Vehiculo o remolque sin situacion = OPERANDO."""
    con.execute("UPDATE unidades SET situacion = 'OPERANDO' WHERE situacion IS NULL OR TRIM(situacion) = ''")
    if "situacion" in {f[1] for f in con.execute("PRAGMA table_info(remolques)")}:
        con.execute("UPDATE remolques SET situacion = 'OPERANDO' WHERE situacion IS NULL OR TRIM(situacion) = ''")


@contextmanager
def conexion():
    con = sqlite3.connect(RUTA_BD, timeout=30)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------- crear tablas
def crear_tablas():
    guardables = [c for c in COLUMNAS if c.tipo != "calculado"]
    with conexion() as con:
        con.execute("PRAGMA journal_mode=WAL")
        cols_sql = ", ".join(
            f"{c.clave} {'REAL' if c.tipo in ('entero', 'dinero') else 'TEXT'}"
            for c in guardables
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS viajes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_plan TEXT NOT NULL, "
            f"{cols_sql}, creado TEXT, modificado TEXT)"
        )
        # Si agregaste columnas nuevas en columnas.py, se crean aqui solas
        existentes = {f[1] for f in con.execute("PRAGMA table_info(viajes)")}
        for c in guardables:
            if c.clave not in existentes:
                tipo = "REAL" if c.tipo in ("entero", "dinero") else "TEXT"
                con.execute(f"ALTER TABLE viajes ADD COLUMN {c.clave} {tipo}")
        con.execute("CREATE INDEX IF NOT EXISTS ix_viajes_dia ON viajes(fecha_plan)")

        for tabla, cols in CATALOGOS.items():
            defs = ", ".join(f"{k} TEXT" + (" PRIMARY KEY" if i == 0 else "") for i, k in enumerate(cols))
            con.execute(f"CREATE TABLE IF NOT EXISTS {tabla} ({defs})")
            existentes_cat = {f[1] for f in con.execute(f"PRAGMA table_info({tabla})")}
            for k in cols:
                if k not in existentes_cat:
                    con.execute(f"ALTER TABLE {tabla} ADD COLUMN {k} TEXT")

        con.execute(
            "CREATE TABLE IF NOT EXISTS posiciones (alias TEXT PRIMARY KEY, fecha TEXT, "
            "ignicion TEXT, velocidad REAL, latitud REAL, longitud REAL, actualizado TEXT)"
        )
        if "ubicacion" not in {f[1] for f in con.execute("PRAGMA table_info(posiciones)")}:
            con.execute("ALTER TABLE posiciones ADD COLUMN ubicacion TEXT")     # texto que manda el command (si lo manda)
        con.execute("CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT)")
        # viajes abiertos que reporta la vista ZAM (se reemplaza completa en cada actualizacion)
        con.execute(
            "CREATE TABLE IF NOT EXISTS zam_viajes (shipment_id TEXT PRIMARY KEY, org_id TEXT, status TEXT, "
            "nomina TEXT, placas TEXT, eco TEXT, origen_id TEXT, salida_plan TEXT, destino_id TEXT, "
            "llegada_plan TEXT, actualizado TEXT)"
        )
        # TODOS los viajes que trae la vista ZAM (abiertos, cerrados, cancelados...). Se acumulan (no se borran)
        # para poder saber si un pedido ya termino o se cancelo en ZAM.
        con.execute(
            "CREATE TABLE IF NOT EXISTS zam_todos (shipment_id TEXT PRIMARY KEY, status TEXT, "
            "nomina TEXT, placas TEXT, eco TEXT, origen_id TEXT, salida_plan TEXT, destino_id TEXT, "
            "llegada_plan TEXT, actualizado TEXT)"
        )
        con.execute("CREATE TABLE IF NOT EXISTS zam_ignorados (clave TEXT PRIMARY KEY)")
        # observaciones del monitoreo (una por unidad, como la columna OBSERVACIONES del Excel)
        con.execute("CREATE TABLE IF NOT EXISTS monitoreo_obs (eco TEXT PRIMARY KEY, texto TEXT, actualizado TEXT)")
        for col in ("estatus1", "estatus2", "estatus_actualizado"):          # ESTATUS 1 y 2 manuales (como en el Excel)
            if col not in {f[1] for f in con.execute("PRAGMA table_info(monitoreo_obs)")}:
                con.execute(f"ALTER TABLE monitoreo_obs ADD COLUMN {col} TEXT")
        # desde cuando esta detenida cada unidad (para la columna DETENIDA DESDE)
        con.execute("CREATE TABLE IF NOT EXISTS pos_referencia (alias TEXT PRIMARY KEY, lat REAL, lon REAL, desde TEXT)")
        _situacion_por_defecto(con)
        _semilla_estadias(con)
        # usuarios y bitacora (quien hizo que)
        con.execute("CREATE TABLE IF NOT EXISTS tms_usuarios (usuario TEXT PRIMARY KEY, nombre TEXT, perfil TEXT, "
                    "pwd_hash TEXT, activo INTEGER DEFAULT 1, creado TEXT, creado_por TEXT, ultimo_acceso TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS tms_bitacora (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, usuario TEXT, "
                    "nombre TEXT, perfil TEXT, accion TEXT, objeto TEXT, detalle TEXT)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_tms_bitacora_fecha ON tms_bitacora (fecha)")
        for u, n, ph in USUARIOS_MASTER:
            con.execute("INSERT OR IGNORE INTO tms_usuarios (usuario, nombre, perfil, pwd_hash, activo, creado, creado_por) "
                        "VALUES (?, ?, 'MASTER', ?, 1, ?, 'SISTEMA')", [u, n, ph, AHORA_TXT()])


def _semilla_estadias(con):
    """La primera vez llena el catalogo de estadias con la lista de configuracion.py."""
    if con.execute("SELECT COUNT(*) FROM estadias").fetchone()[0] == 0:
        for cliente, horas in getattr(cfg, "ESTADIAS_INICIALES", {}).items():
            con.execute("INSERT OR IGNORE INTO estadias (cliente, aplica, horas_libres) VALUES (?, 'SI', ?)",
                        [cliente, str(horas)])


# ---------------------------------------------------------- utilidades
def normalizar(valor, tipo=None):
    """Convierte cualquier valor a algo guardable. Vacio -> None."""
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, (pd.Timestamp, datetime)):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    if tipo == "check":  # casilla: True/"SI" -> "SI", cualquier otra cosa -> vacio
        return "SI" if str(valor).strip().upper() in ("TRUE", "SI", "1", "1.0") else None
    if tipo in ("entero", "dinero"):
        return float(valor)
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = str(valor).strip()
    return texto or None


def preparar_tipos(df):
    for c in COLUMNAS:
        if c.clave not in df.columns:
            continue
        if c.tipo == "fecha":
            df[c.clave] = pd.to_datetime(df[c.clave], errors="coerce")
        elif c.tipo in ("entero", "dinero"):
            df[c.clave] = pd.to_numeric(df[c.clave], errors="coerce")
        elif c.tipo == "check":
            df[c.clave] = df[c.clave].map(lambda v: str(v).strip().upper() in ("SI", "TRUE", "1"))
        elif c.tipo != "calculado":
            df[c.clave] = df[c.clave].astype(object).where(df[c.clave].notna(), None)
    return df


# ---------------------------------------------------------- viajes
def leer_viajes(desde, hasta):
    with conexion() as con:
        df = pd.read_sql_query(
            "SELECT * FROM viajes WHERE fecha_plan BETWEEN ? AND ? "
            "ORDER BY fecha_plan, cita_carga, id",
            con, params=[str(desde), str(hasta)],
        )
    return preparar_tipos(df)


def insertar_viaje(valores, dia):
    valores = {k: normalizar(v, TIPO_POR_CLAVE.get(k)) for k, v in valores.items()
               if k in TIPO_POR_CLAVE}
    valores["fecha_plan"] = str(dia)
    valores["creado"] = valores["modificado"] = AHORA_TXT()
    cols = ", ".join(valores)
    marcas = ", ".join("?" for _ in valores)
    with conexion() as con:
        con.execute(f"INSERT INTO viajes ({cols}) VALUES ({marcas})", list(valores.values()))
    autocompletar_desde_catalogos()


def guardar_plan(original, editado, dia, claves_editables, defaults=None):
    """Compara lo que habia contra lo que quedo en pantalla y guarda SOLO los cambios."""
    claves_editables = [k for k in claves_editables if k in editado.columns]
    ids_orig = {int(i) for i in original["id"]} if len(original) else set()
    ids_edit = {int(i) for i in editado["id"].dropna()}
    borrados = ids_orig - ids_edit
    orig = original.set_index("id") if len(original) else original
    resumen = {"nuevos": 0, "cambios": 0, "borrados": 0}
    ahora = AHORA_TXT()

    with conexion() as con:
        for id_ in borrados:
            con.execute("DELETE FROM viajes WHERE id = ?", [id_])
            resumen["borrados"] += 1

        for _, fila in editado.iterrows():
            if pd.isna(fila["id"]):  # fila nueva
                vals = {k: normalizar(fila[k], TIPO_POR_CLAVE[k]) for k in claves_editables}
                vals = {k: v for k, v in vals.items() if v is not None}
                if not vals:
                    continue
                if vals.get("finalizado") and vals.get("cancelado"):
                    vals.pop("cancelado")  # no pueden ir las dos: gana la palomita
                for k, v in (defaults or {}).items():
                    vals.setdefault(k, v)
                vals["fecha_plan"] = str(dia)
                vals["creado"] = vals["modificado"] = ahora
                cols = ", ".join(vals)
                marcas = ", ".join("?" for _ in vals)
                con.execute(f"INSERT INTO viajes ({cols}) VALUES ({marcas})", list(vals.values()))
                resumen["nuevos"] += 1
            else:
                id_ = int(fila["id"])
                if id_ not in ids_orig:
                    continue
                cambios = {}
                for k in claves_editables:
                    nuevo = normalizar(fila[k], TIPO_POR_CLAVE[k])
                    viejo = normalizar(orig.loc[id_, k], TIPO_POR_CLAVE[k])
                    if nuevo != viejo:
                        cambios[k] = nuevo
                if "finalizado" in claves_editables and "cancelado" in claves_editables:
                    fin = normalizar(fila["finalizado"], "check")
                    can = normalizar(fila["cancelado"], "check")
                    if fin and can:  # las dos marcadas: gana la que se acaba de marcar
                        if normalizar(orig.loc[id_, "finalizado"], "check"):
                            cambios["finalizado"], cambios["cancelado"] = None, "SI"
                        else:
                            cambios["finalizado"], cambios["cancelado"] = "SI", None
                if cambios:
                    cambios["modificado"] = ahora
                    sets = ", ".join(f"{k} = ?" for k in cambios)
                    con.execute(f"UPDATE viajes SET {sets} WHERE id = ?", list(cambios.values()) + [id_])
                    resumen["cambios"] += 1
    autocompletar_desde_catalogos()
    return resumen


def autocompletar_desde_catalogos():
    """Si hay un ECO u ORIGEN o DESTINO conocido, llena solo los datos vacios."""
    blanco = "({c} IS NULL OR {c} = '')"
    reglas = [
        ("operador", "unidades", "eco", "eco", "operador"),
        ("nomina", "operadores", "operador", "nombre", "nomina"),
        ("cliente", "tipos_operacion", "tipo_operacion", "nombre", "cliente"),
        ("direccion_origen", "origenes", "origen", "nombre", "direccion"),
        ("link_origen", "origenes", "origen", "nombre", "link"),
        ("localidad_destino", "destinos", "destino", "nombre", "localidad"),
        ("direccion_destino", "destinos", "destino", "nombre", "direccion"),
        ("link_destino", "destinos", "destino", "nombre", "link"),
    ]
    with conexion() as con:
        for col_viaje, tabla, col_clave_viaje, col_clave_cat, col_cat in reglas:
            con.execute(
                f"UPDATE viajes SET {col_viaje} = "
                f"(SELECT t.{col_cat} FROM {tabla} t WHERE t.{col_clave_cat} = viajes.{col_clave_viaje}) "
                f"WHERE {blanco.format(c=col_viaje)} AND {col_clave_viaje} IN "
                f"(SELECT {col_clave_cat} FROM {tabla} WHERE {col_cat} IS NOT NULL AND {col_cat} <> '')"
            )
        _situacion_por_defecto(con)


def estatus_manual(ids):
    """Estatus escrito a mano (guardado en la BD) de cada viaje: {id: estatus o None}."""
    if not ids:
        return {}
    with conexion() as con:
        filas = con.execute(f"SELECT id, estatus FROM viajes WHERE id IN ({', '.join('?' for _ in ids)})",
                            list(ids)).fetchall()
    return {int(i): e for i, e in filas}


def hay_viajes():
    with conexion() as con:
        return con.execute("SELECT COUNT(*) FROM viajes").fetchone()[0] > 0


# ---------------------------------------------------------- catalogos
def leer_catalogo(tabla):
    cols = ", ".join(CATALOGOS[tabla])
    with conexion() as con:
        return pd.read_sql_query(f"SELECT {cols} FROM {tabla} ORDER BY 1", con)


def opciones_catalogo(tabla, columna=None):
    columna = columna or CATALOGOS[tabla][0]
    with conexion() as con:
        filas = con.execute(
            f"SELECT DISTINCT {columna} FROM {tabla} "
            f"WHERE {columna} IS NOT NULL AND {columna} <> '' ORDER BY {columna}"
        ).fetchall()
    return [f[0] for f in filas]


def reemplazar_catalogo(tabla, df):
    cols = CATALOGOS[tabla]
    with conexion() as con:
        con.execute(f"DELETE FROM {tabla}")
        for _, fila in df.iterrows():
            vals = [normalizar(fila.get(c)) for c in cols]
            if vals[0] is None:
                continue
            if "situacion" in cols:
                vals[cols.index("situacion")] = situacion_valida(vals[cols.index("situacion")])
            marcas = ", ".join("?" for _ in cols)
            con.execute(f"INSERT OR REPLACE INTO {tabla} ({', '.join(cols)}) VALUES ({marcas})", vals)
    autocompletar_desde_catalogos()


def catalogo_upsert(tabla, df):
    """Carga masiva: agrega lo nuevo y completa lo existente. NUNCA borra ni pisa con vacios."""
    cols = CATALOGOS[tabla]
    llave = cols[0]
    nuevos = actualizados = 0
    with conexion() as con:
        for _, fila in df.iterrows():
            vals = {c: normalizar(fila.get(c)) for c in cols}
            if vals[llave] is None:
                continue
            if "situacion" in cols:
                vals["situacion"] = situacion_valida(vals["situacion"])
            actual = con.execute(
                f"SELECT {', '.join(cols)} FROM {tabla} WHERE {llave} = ?", [vals[llave]]
            ).fetchone()
            if actual is None:
                marcas = ", ".join("?" for _ in cols)
                con.execute(f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({marcas})",
                            [vals[c] for c in cols])
                nuevos += 1
            else:
                actual = dict(zip(cols, actual))
                cambios = {c: v for c, v in vals.items()
                           if c != llave and v is not None and v != actual[c]}
                if cambios:
                    sets = ", ".join(f"{c} = ?" for c in cambios)
                    con.execute(f"UPDATE {tabla} SET {sets} WHERE {llave} = ?",
                                list(cambios.values()) + [vals[llave]])
                    actualizados += 1
    autocompletar_desde_catalogos()
    return {"nuevos": nuevos, "actualizados": actualizados}


def catalogo_agregar(tabla, valores):
    """Agrega un registro al catalogo solo si no existia."""
    cols = CATALOGOS[tabla]
    vals = [normalizar(valores.get(c)) for c in cols]
    if vals[0] is None:
        return
    marcas = ", ".join("?" for _ in cols)
    with conexion() as con:
        con.execute(f"INSERT OR IGNORE INTO {tabla} ({', '.join(cols)}) VALUES ({marcas})", vals)


# ---------------------------------------------------------- posiciones Omnitracs
def guardar_meta(clave, valor):
    with conexion() as con:
        con.execute("INSERT OR REPLACE INTO meta (clave, valor) VALUES (?, ?)", [clave, valor])


def leer_meta(clave):
    with conexion() as con:
        fila = con.execute("SELECT valor FROM meta WHERE clave = ?", [clave]).fetchone()
    return fila[0] if fila else None


def segundos_desde_ultima_consulta():
    ultima = leer_meta("ultima_consulta")
    if not ultima:
        return 10**9
    return (datetime.now() - datetime.strptime(ultima, "%Y-%m-%d %H:%M:%S")).total_seconds()


def _metros(lat1, lon1, lat2, lon2):
    from math import cos, radians, hypot
    return hypot((lat2 - lat1) * 111_320, (lon2 - lon1) * 111_320 * cos(radians((lat1 + lat2) / 2)))


def guardar_posiciones(lista):
    ahora = AHORA_TXT()
    umbral = getattr(cfg, "MON_METROS_PARA_CONSIDERAR_MOVIDA", 300)
    with conexion() as con:
        ref = {a: (la, lo, d) for a, la, lo, d in con.execute("SELECT alias, lat, lon, desde FROM pos_referencia")}
        for p in lista:
            la, lo = p.get("latitud"), p.get("longitud")
            if la is not None and lo is not None:
                r = ref.get(p["alias"])
                if r is None or _metros(r[0], r[1], la, lo) > umbral:      # se movio: nueva referencia
                    con.execute("INSERT OR REPLACE INTO pos_referencia VALUES (?, ?, ?, ?)",
                                [p["alias"], la, lo, p["fecha"] or ahora])
            con.execute(
                "INSERT OR REPLACE INTO posiciones (alias, fecha, ignicion, velocidad, latitud, longitud, actualizado, ubicacion) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [p["alias"], p["fecha"], p["ignicion"], p["velocidad"], p["latitud"], p["longitud"], ahora,
                 p.get("ubicacion") or None],
            )
    guardar_meta("ultima_consulta", ahora)


def leer_posiciones():
    with conexion() as con:
        return pd.read_sql_query("SELECT * FROM posiciones ORDER BY alias", con)


# ---------------------------------------------------------- vista ZAM (viajes abiertos)
COLUMNAS_ZAM = ["shipment_id", "org_id", "status", "nomina", "placas", "eco",
                "origen_id", "salida_plan", "destino_id", "llegada_plan"]


def sincronizar_zam(viajes):
    """Guarda los viajes de la vista ZAM y actualiza catalogos.
    - Reemplaza la tabla zam_viajes con lo que trae la vista hoy.
    - ECO nuevo -> se da de alta en unidades con situacion OPERANDO.
    - Placas (assets_id) -> se guardan/actualizan en el ECO.
    - Nomina nueva -> se da de alta en operadores (sin nombre: completalo en el catalogo).
    Nunca borra unidades ni operadores. Devuelve un resumen."""
    ahora = AHORA_TXT()
    res = {"viajes": 0, "eco_nuevos": 0, "placas_actualizadas": 0, "nominas_nuevas": 0}
    with conexion() as con:
        con.execute("DELETE FROM zam_viajes")
        for v in viajes:
            vals = [normalizar(v.get(c)) for c in COLUMNAS_ZAM]
            if vals[0] is None:
                continue
            con.execute(f"INSERT OR REPLACE INTO zam_viajes ({', '.join(COLUMNAS_ZAM)}, actualizado) "
                        f"VALUES ({', '.join('?' for _ in COLUMNAS_ZAM)}, ?)", vals + [ahora])
            res["viajes"] += 1

            eco, placas, nomina = normalizar(v.get("eco")), normalizar(v.get("placas")), normalizar(v.get("nomina"))
            if eco:
                actual = con.execute("SELECT placas FROM unidades WHERE eco = ?", [eco]).fetchone()
                if actual is None:
                    con.execute("INSERT INTO unidades (eco, situacion, placas) VALUES (?, 'OPERANDO', ?)",
                                [eco, placas])
                    res["eco_nuevos"] += 1
                elif placas and actual[0] != placas:
                    con.execute("UPDATE unidades SET placas = ? WHERE eco = ?", [placas, eco])
                    res["placas_actualizadas"] += 1
            if nomina:
                existe = con.execute("SELECT 1 FROM operadores WHERE nomina = ?", [nomina]).fetchone()
                if existe is None:
                    con.execute("INSERT INTO operadores (nomina) VALUES (?)", [nomina])
                    res["nominas_nuevas"] += 1
    guardar_meta("zam_ultima_sync", ahora)
    guardar_meta("zam_ultimo_error", "")
    guardar_meta("zam_ultimo_resumen",
                 f"{res['viajes']} viajes abiertos · {res['eco_nuevos']} ECO nuevos · "
                 f"{res['placas_actualizadas']} placas actualizadas · {res['nominas_nuevas']} nóminas nuevas")
    return res


def leer_zam():
    with conexion() as con:
        return pd.read_sql_query("SELECT * FROM zam_viajes ORDER BY eco", con)


def segundos_desde_sync_zam():
    ultima = leer_meta("zam_ultima_sync")
    if not ultima:
        return 10**9
    return (datetime.now() - datetime.strptime(ultima, "%Y-%m-%d %H:%M:%S")).total_seconds()


# ---------------------------------------------------------- estadias
def leer_estadias():
    """Clientes con estadia: lista de (cliente, horas_libres). Solo los que dicen aplica = SI."""
    with conexion() as con:
        filas = con.execute("SELECT cliente, aplica, horas_libres FROM estadias").fetchall()
    salida = []
    for cliente, aplica, horas in filas:
        if str(aplica or "").strip().upper() not in ("SI", "SÍ", "TRUE", "1"):
            continue
        try:
            salida.append((cliente, float(horas)))
        except (TypeError, ValueError):
            continue
    return salida


# ---------------------------------------------------------- viajes: cambios puntuales
def actualizar_viaje(id_, cambios):
    """Cambia columnas de UN viaje (ej. pedido, finalizado). Ignora columnas que no existen."""
    cambios = {k: normalizar(v, TIPO_POR_CLAVE.get(k)) for k, v in cambios.items() if k in TIPO_POR_CLAVE}
    if not cambios:
        return
    cambios["modificado"] = AHORA_TXT()
    sets = ", ".join(f"{k} = ?" for k in cambios)
    with conexion() as con:
        con.execute(f"UPDATE viajes SET {sets} WHERE id = ?", list(cambios.values()) + [int(id_)])


def viajes_con_pedido(pedidos):
    """Pedidos (de la lista) que ya existen en el TMS: {pedido: [id, ...]}."""
    pedidos = [str(p) for p in pedidos if p]
    if not pedidos:
        return {}
    with conexion() as con:
        filas = con.execute(
            f"SELECT pedido, id FROM viajes WHERE pedido IN ({', '.join('?' for _ in pedidos)})", pedidos).fetchall()
    res = {}
    for p, i in filas:
        res.setdefault(p, []).append(i)
    return res


# ---------------------------------------------------------- todos los viajes de ZAM
def guardar_zam_todos(viajes):
    """Acumula TODOS los viajes de la vista (cualquier estatus). Sirve para detectar cierres."""
    ahora = AHORA_TXT()
    cols = [c for c in COLUMNAS_ZAM if c != "org_id"]
    with conexion() as con:
        for v in viajes:
            vals = [normalizar(v.get(c)) for c in cols]
            if vals[0] is None:
                continue
            con.execute(f"INSERT OR REPLACE INTO zam_todos ({', '.join(cols)}, actualizado) "
                        f"VALUES ({', '.join('?' for _ in cols)}, ?)", vals + [ahora])


def leer_zam_todos():
    with conexion() as con:
        return pd.read_sql_query("SELECT * FROM zam_todos", con)


def zam_ignorados():
    with conexion() as con:
        return {f[0] for f in con.execute("SELECT clave FROM zam_ignorados")}


def ignorar_zam(claves):
    with conexion() as con:
        for k in claves:
            con.execute("INSERT OR IGNORE INTO zam_ignorados (clave) VALUES (?)", [k])


# ---------------------------------------------------------- monitoreo
def leer_referencias():
    """{alias: 'desde cuando esta en el mismo lugar'}"""
    with conexion() as con:
        return {a: d for a, d in con.execute("SELECT alias, desde FROM pos_referencia")}


def leer_monitoreo_manual(horas=None):
    """Lo que TC escribio a mano en el Monitoreo: {eco: {'texto', 'estatus1', 'estatus2'}}.
    Los ESTATUS manuales solo valen 'horas' (por defecto MON_ESTATUS_MANUAL_HORAS) para que no se queden viejos."""
    horas = getattr(cfg, "MON_ESTATUS_MANUAL_HORAS", 12) if horas is None else horas
    limite = datetime.now() - timedelta(hours=horas)
    with conexion() as con:
        filas = con.execute("SELECT eco, texto, estatus1, estatus2, estatus_actualizado FROM monitoreo_obs").fetchall()
    salida = {}
    for eco, texto, e1, e2, cuando in filas:
        try:
            vigente = bool(cuando) and datetime.strptime(cuando, "%Y-%m-%d %H:%M:%S") >= limite
        except ValueError:
            vigente = False
        salida[str(eco)] = {"texto": texto or "", "estatus1": e1 if vigente else None,
                            "estatus2": e2 if vigente else None}
    return salida


def leer_observaciones():
    return {e: v["texto"] for e, v in leer_monitoreo_manual().items()}


def guardar_monitoreo(cambios):
    """cambios: {eco: {'texto': ..., 'estatus1': ..., 'estatus2': ...}} (solo las llaves que cambiaron).
    Texto vacio = borra la observacion · estatus None = vuelve al automatico."""
    ahora = AHORA_TXT()
    with conexion() as con:
        for eco, c in cambios.items():
            f = con.execute("SELECT texto, estatus1, estatus2 FROM monitoreo_obs WHERE eco = ?", [str(eco)]).fetchone()
            texto, e1, e2 = f if f else (None, None, None)
            if "texto" in c:
                texto = (c["texto"] or "").strip() or None
            if "estatus1" in c:
                e1 = c["estatus1"] or None
            if "estatus2" in c:
                e2 = c["estatus2"] or None
            if not (texto or e1 or e2):
                con.execute("DELETE FROM monitoreo_obs WHERE eco = ?", [str(eco)])
                continue
            cuando = ahora if ("estatus1" in c or "estatus2" in c) else None
            if f:
                con.execute("UPDATE monitoreo_obs SET texto=?, estatus1=?, estatus2=?, actualizado=?, "
                            "estatus_actualizado=COALESCE(?, estatus_actualizado) WHERE eco=?",
                            [texto, e1, e2, ahora, cuando, str(eco)])
            else:
                con.execute("INSERT INTO monitoreo_obs (eco, texto, estatus1, estatus2, actualizado, estatus_actualizado) "
                            "VALUES (?, ?, ?, ?, ?, ?)", [str(eco), texto, e1, e2, ahora, cuando])


def guardar_observaciones(cambios):
    guardar_monitoreo({e: {"texto": t} for e, t in cambios.items()})


def poner_situacion(eco, situacion):
    """Cambia la SITUACION de la unidad en el catalogo de vehiculos (OPERANDO, CORRALON, ...)."""
    with conexion() as con:
        return con.execute("UPDATE unidades SET situacion = ? WHERE eco = ?", [situacion_valida(situacion), str(eco)]).rowcount


def ultimo_tipo_por_eco(dias=30):
    """Tipo de operacion del viaje mas reciente de cada unidad: {eco: tipo_operacion}."""
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    with conexion() as con:
        filas = con.execute(
            "SELECT eco, tipo_operacion FROM viajes WHERE eco IS NOT NULL AND eco <> '' AND tipo_operacion IS NOT NULL "
            "AND fecha_plan >= ? ORDER BY fecha_plan, id", [desde]).fetchall()
    return {str(e): t for e, t in filas}          # el ultimo de cada eco gana


# ---------------------------------------------------------- finalizar solos los viajes completos
def finalizar_viajes_completos(devolver_viajes=False):
    """Viaje con TODOS sus tiempos (ver TIEMPOS_PARA_FINALIZAR) -> queda ✅ finalizado.
    Al quedar finalizado, la unidad y el operador vuelven a estar DISPONIBLES. Devuelve cuantos se finalizaron."""
    from columnas import TIEMPOS_PARA_FINALIZAR
    llenos = " AND ".join(f"{c} IS NOT NULL AND {c} <> ''" for c in TIEMPOS_PARA_FINALIZAR)
    donde = f"COALESCE(finalizado, 0) = 0 AND COALESCE(cancelado, 0) = 0 AND {llenos}"
    with conexion() as con:
        lista = con.execute(f"SELECT id, pedido, eco FROM viajes WHERE {donde}").fetchall() if devolver_viajes else None
        cur = con.execute(f"UPDATE viajes SET finalizado = 1, modificado = ? WHERE {donde}", [AHORA_TXT()])
        return lista if devolver_viajes else cur.rowcount


# ---------------------------------------------------------- links de origen / destino
def link_conocido(lado, nombre):
    """Link de Maps ya conocido para un origen o destino ('origen' | 'destino'):
    primero el del catalogo; si no hay, el del viaje mas reciente que ya lo traia."""
    if not nombre:
        return ""
    tabla, col_viaje = ("origenes", "link_origen") if lado == "origen" else ("destinos", "link_destino")
    with conexion() as con:
        f = con.execute(f"SELECT link FROM {tabla} WHERE nombre = ? AND link IS NOT NULL AND link <> ''", [nombre]).fetchone()
        if f:
            return f[0]
        f = con.execute(f"SELECT {col_viaje} FROM viajes WHERE {lado} = ? AND {col_viaje} IS NOT NULL AND {col_viaje} <> '' "
                        f"ORDER BY fecha_plan DESC, id DESC LIMIT 1", [nombre]).fetchone()
    return f[0] if f else ""


def aprender_link(lado, nombre, link):
    """Si el catalogo no tenia link para ese origen/destino, lo guarda para la proxima vez."""
    link = (link or "").strip()
    if not nombre or not link:
        return
    tabla = "origenes" if lado == "origen" else "destinos"
    with conexion() as con:
        con.execute(f"UPDATE {tabla} SET link = ? WHERE nombre = ? AND (link IS NULL OR link = '')", [link, nombre])


# ---------------------------------------------------------- respaldo diario
def respaldo_diario(carpeta="respaldos", conservar=14):
    """Copia tms.db a respaldos/tms_AAAAMMDD.db una vez al dia y borra los mas viejos."""
    import shutil
    from pathlib import Path
    hoy = datetime.now().strftime("%Y%m%d")
    if leer_meta("ultimo_respaldo") == hoy:
        return
    destino = RUTA_BD.parent / carpeta
    destino.mkdir(exist_ok=True)
    with conexion() as con, sqlite3.connect(destino / f"tms_{hoy}.db") as copia:
        con.backup(copia)
    for viejo in sorted(destino.glob("tms_*.db"))[:-conservar]:
        viejo.unlink()
    guardar_meta("ultimo_respaldo", hoy)


# ---------------------------------------------------------- usuarios y acceso
# Los usuarios MASTER (los unicos que crean usuarios). Las contrasenas NO estan en el codigo: solo su huella (hash).
USUARIOS_MASTER = [('JLMAR', 'Jose Luis Mar Sierra', 'pbkdf2$200000$466e55827687abe0c0f2a921607d3d6e$e44c39775fce50c07fad98910f884ad8002cf002c826e2a677bd4dfc34f59209'), ('OAARE', 'Omar Ali Arenas', 'pbkdf2$200000$246b2ec12ad9d1901498c544bf479839$4675fb9590dcb4404aea17a6e96ed351ca15fed8baef4acb150ad8793e2dddbc')]
PERFILES_CREABLES = ["TC", "OPS", "ATC", "TODOS"]        # un MASTER solo puede crear estos perfiles


def hash_pwd(pwd, sal=None, iteraciones=200_000):
    import hashlib, secrets
    sal = sal or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(sal), iteraciones).hex()
    return f"pbkdf2${iteraciones}${sal}${h}"


def verificar_pwd(pwd, almacenado):
    import hashlib, hmac
    try:
        _, it, sal, h = almacenado.split("$")
        nuevo = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(sal), int(it)).hex()
        return hmac.compare_digest(nuevo, h)
    except (ValueError, AttributeError):
        return False


def bitacora(quien, accion, objeto="", detalle=""):
    """Anota quien hizo que. 'quien' = dict de la sesion ({'usuario','nombre','perfil'}) o un texto (ej. 'SISTEMA')."""
    if isinstance(quien, dict):
        u, n, p = quien.get("usuario"), quien.get("nombre"), quien.get("actuando_como") or quien.get("perfil")
    else:
        u, n, p = str(quien), str(quien), None
    with conexion() as con:
        con.execute("INSERT INTO tms_bitacora (fecha, usuario, nombre, perfil, accion, objeto, detalle) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [AHORA_TXT(), u, n, p, accion, str(objeto)[:300], str(detalle)[:4000]])


def autenticar(usuario, pwd, max_fallos=5, minutos=10):
    """Devuelve (datos_usuario | None, mensaje). Bloquea 10 min tras 5 intentos fallidos seguidos."""
    usuario = (usuario or "").strip().upper()
    desde = (datetime.now() - timedelta(minutes=minutos)).strftime("%Y-%m-%d %H:%M:%S")
    with conexion() as con:
        ultimo_ok = con.execute("SELECT COALESCE(MAX(fecha), '') FROM tms_bitacora WHERE usuario = ? AND accion = 'LOGIN'", [usuario]).fetchone()[0]
        fallos = con.execute("SELECT COUNT(*) FROM tms_bitacora WHERE usuario = ? AND accion = 'LOGIN_FALLIDO' AND fecha >= ? AND fecha > ?",
                             [usuario, desde, ultimo_ok]).fetchone()[0]
        fila = con.execute("SELECT usuario, nombre, perfil, pwd_hash, activo FROM tms_usuarios WHERE usuario = ?", [usuario]).fetchone()
    if fallos >= max_fallos:
        return None, f"Demasiados intentos. Espera {minutos} minutos e intenta de nuevo."
    if not fila or not verificar_pwd(pwd or "", fila[3]):
        bitacora(usuario or "(vacío)", "LOGIN_FALLIDO", "", "Usuario o contraseña incorrectos")
        return None, "Usuario o contraseña incorrectos."
    if not fila[4]:
        bitacora(usuario, "LOGIN_FALLIDO", "", "Usuario desactivado")
        return None, "Este usuario está desactivado. Habla con un usuario MASTER."
    with conexion() as con:
        con.execute("UPDATE tms_usuarios SET ultimo_acceso = ? WHERE usuario = ?", [AHORA_TXT(), usuario])
    datos = {"usuario": fila[0], "nombre": fila[1], "perfil": fila[2]}
    bitacora(datos, "LOGIN", "", "Inicio de sesión")
    return datos, ""


def listar_usuarios():
    with conexion() as con:
        return pd.read_sql_query("SELECT usuario, nombre, perfil, activo, creado, creado_por, ultimo_acceso FROM tms_usuarios "
                                 "ORDER BY perfil = 'MASTER' DESC, usuario", con)


def crear_usuario(quien, usuario, nombre, pwd, perfil):
    """Solo MASTER. Perfiles permitidos: TC, OPS, ATC, TODOS. Devuelve (ok, mensaje)."""
    import re
    if not isinstance(quien, dict) or quien.get("perfil") != "MASTER":
        return False, "Solo un usuario MASTER puede crear usuarios."
    usuario, nombre = (usuario or "").strip().upper(), (nombre or "").strip()
    if perfil not in PERFILES_CREABLES:
        return False, "Perfil no permitido. Elige TC, OPS, ATC o TODOS."
    if not re.fullmatch(r"[A-Z0-9_.]{3,20}", usuario):
        return False, "El usuario debe tener de 3 a 20 letras o números (sin espacios)."
    if not nombre:
        return False, "Falta el nombre."
    if len(pwd or "") < 8:
        return False, "La contraseña debe tener al menos 8 caracteres."
    with conexion() as con:
        if con.execute("SELECT 1 FROM tms_usuarios WHERE usuario = ?", [usuario]).fetchone():
            return False, f"El usuario {usuario} ya existe."
        con.execute("INSERT INTO tms_usuarios (usuario, nombre, perfil, pwd_hash, activo, creado, creado_por) VALUES (?, ?, ?, ?, 1, ?, ?)",
                    [usuario, nombre, perfil, hash_pwd(pwd), AHORA_TXT(), quien["usuario"]])
    bitacora(quien, "USUARIO_CREADO", usuario, f"Nombre: {nombre} · Perfil: {perfil}")
    return True, f"Usuario {usuario} creado con perfil {perfil}."


def cambiar_usuario(quien, usuario, activo=None, nueva_pwd=None):
    """Solo MASTER: activar/desactivar o restablecer contrasena de usuarios que NO son MASTER."""
    if not isinstance(quien, dict) or quien.get("perfil") != "MASTER":
        return False, "Solo un usuario MASTER puede hacer esto."
    with conexion() as con:
        f = con.execute("SELECT perfil FROM tms_usuarios WHERE usuario = ?", [usuario]).fetchone()
        if not f:
            return False, "No existe ese usuario."
        if f[0] == "MASTER":
            return False, "Los usuarios MASTER no se modifican desde aquí."
        if nueva_pwd is not None:
            if len(nueva_pwd) < 8:
                return False, "La contraseña debe tener al menos 8 caracteres."
            con.execute("UPDATE tms_usuarios SET pwd_hash = ? WHERE usuario = ?", [hash_pwd(nueva_pwd), usuario])
        if activo is not None:
            con.execute("UPDATE tms_usuarios SET activo = ? WHERE usuario = ?", [1 if activo else 0, usuario])
    bitacora(quien, "USUARIO_MODIFICADO", usuario,
             ("Contraseña restablecida" if nueva_pwd is not None else ("Activado" if activo else "Desactivado")))
    return True, "Listo."


def leer_bitacora(desde, hasta):
    with conexion() as con:
        return pd.read_sql_query("SELECT fecha, usuario, nombre, perfil, accion, objeto, detalle FROM tms_bitacora "
                                 "WHERE fecha >= ? AND fecha < ? ORDER BY id DESC",
                                 con, params=[f"{desde} 00:00:00", f"{hasta + timedelta(days=1)} 00:00:00"])
