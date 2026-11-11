# ==========================================================
#  nube.py - La base de datos del TMS en internet (Turso)
#
#  Como funciona (sin cambiar nada del resto del TMS):
#   - El TMS sigue trabajando con su archivo tms.db (rapido).
#   - Cada vez que se GUARDA algo, el mismo cambio se manda a la base en internet.
#   - Al abrir el TMS (y en cada pantalla, si alguien mas cambio algo) se baja lo de internet.
#  Asi la pagina en la nube (streamlit.app) y la de la PC ven LO MISMO y nada se pierde
#  aunque la pagina de la nube se reinicie.
#
#  Se activa poniendo TURSO_URL y TURSO_TOKEN en:
#    - la nube: Streamlit > tu app > Settings > Secrets
#    - la PC:   configuracion_local.py
#  Sin esos datos, el TMS trabaja como siempre (solo con tms.db).
#
#  Primera vez:  py nube.py subir     (sube TODO lo de tu tms.db a la base de internet)
# ==========================================================
import base64
import os
import re
import sqlite3
import sys
import threading
import time

import requests

try:
    import configuracion_local as _local
except ImportError:
    _local = None

# Tablas que NO se suben (se rehacen solas en cada computadora: posiciones de Omnitracs, nombres de lugares, etc.)
SOLO_LOCALES = {"posiciones", "pos_referencia", "lugares", "meta", "nube_pendiente", "sqlite_sequence", "nube_meta",
                "respaldos_nube"}     # respaldos_nube vive solo en internet (no se baja a cada computadora)
ESCRITURA = {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}
_candado = threading.RLock()
_estado = {"version": None, "ultimo_envio": None, "error": "", "ultima_revision": 0.0,
           "lista": False, "revisada": False}          # lista = la nube ya tiene los datos (se subieron con 'py nube.py subir')


_cache = {}


def _dato(nombre):
    if nombre in _cache:
        return _cache[nombre]
    v = os.environ.get(nombre) or getattr(_local, nombre, None)
    if not v:
        try:                                   # en Streamlit Cloud los Secrets tambien llegan por aqui
            import streamlit as st
            v = st.secrets.get(nombre)
        except Exception:
            v = None
    v = (v or "").strip()
    if v:                                      # solo se recuerda si ya existe (los Secrets pueden llegar despues)
        _cache[nombre] = v
    return v


def diagnostico():
    """Para la pantalla: que datos de conexion ve el TMS (solo SI/NO y nombres, nunca los valores)."""
    claves = []
    try:
        import streamlit as st
        claves = sorted(str(k) for k in st.secrets.keys())
    except Exception as e:
        claves = [f"(no se pudieron leer los Secrets: {type(e).__name__})"]
    return {"TURSO_URL": bool(_dato("TURSO_URL")), "TURSO_TOKEN": bool(_dato("TURSO_TOKEN")), "secrets": claves,
            "error": _estado["error"]}


def url():
    u = _dato("TURSO_URL")
    return ("https://" + u[len("libsql://"):]) if u.startswith("libsql://") else u.rstrip("/")


def activa():
    return bool(url() and _dato("TURSO_TOKEN"))


def estado():
    return dict(_estado)


# ---------------------------------------------------------- hablar con la base de internet
def _arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def _valor(c):
    t = c.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(c["value"])
    if t == "float":
        return float(c["value"])
    if t == "blob":
        return base64.b64decode(c.get("base64", ""))
    return c.get("value")


def _stmt(sql, params=()):
    return {"sql": sql, "args": [_arg(p) for p in (params or ())]}


def _pipeline(peticiones, timeout=20):
    r = requests.post(url() + "/v2/pipeline", json={"requests": peticiones + [{"type": "close"}]},
                      headers={"Authorization": f"Bearer {_dato('TURSO_TOKEN')}"}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"La base en internet respondió HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["results"][:-1]


def consultar(sql, params=()):
    """SELECT en la base de internet -> (columnas, filas)."""
    res = _pipeline([{"type": "execute", "stmt": _stmt(sql, params)}])[0]
    if res["type"] != "ok":
        raise RuntimeError(res.get("error", {}).get("message", "error"))
    r = res["response"]["result"]
    return [c["name"] for c in r["cols"]], [[_valor(c) for c in fila] for fila in r["rows"]]


def ejecutar(sentencias):
    """Sentencias directas en internet, en una transaccion, SIN avisar a las demas computadoras (no cambia la version).
    Se usa para los respaldos, que no forman parte de los datos del dia a dia."""
    pasos = [{"stmt": _stmt("BEGIN")}]
    for sql, params in sentencias:
        pasos.append({"stmt": _stmt(sql, params), "condition": {"type": "ok", "step": len(pasos) - 1}})
    ultimo = len(pasos) - 1
    pasos.append({"stmt": _stmt("COMMIT"), "condition": {"type": "ok", "step": ultimo}})
    pasos.append({"stmt": _stmt("ROLLBACK"), "condition": {"type": "not", "cond": {"type": "ok", "step": ultimo}}})
    res = _pipeline([{"type": "batch", "batch": {"steps": pasos}}], timeout=120)[0]
    if res["type"] != "ok":
        raise RuntimeError(res.get("error", {}).get("message", "error"))
    errores = [e for e in res["response"]["result"].get("step_errors", []) if e]
    if errores:
        raise RuntimeError(errores[0].get("message", "error"))


def _lote(sentencias):
    """Manda varias sentencias en UNA transaccion (todas o ninguna). Devuelve la version nueva."""
    pasos = [{"stmt": _stmt("BEGIN")}]
    for sql, params in sentencias + [("CREATE TABLE IF NOT EXISTS nube_meta (clave TEXT PRIMARY KEY, valor INTEGER)", ()),
                                      ("INSERT INTO nube_meta (clave, valor) VALUES ('version', 1) "
                                       "ON CONFLICT(clave) DO UPDATE SET valor = valor + 1", ())]:
        pasos.append({"stmt": _stmt(sql, params), "condition": {"type": "ok", "step": len(pasos) - 1}})
    ultimo = len(pasos) - 1
    pasos.append({"stmt": _stmt("COMMIT"), "condition": {"type": "ok", "step": ultimo}})
    pasos.append({"stmt": _stmt("ROLLBACK"), "condition": {"type": "not", "cond": {"type": "ok", "step": ultimo}}})
    pasos.append({"stmt": _stmt("SELECT valor FROM nube_meta WHERE clave = 'version'"),
                  "condition": {"type": "ok", "step": ultimo + 1}})
    res = _pipeline([{"type": "batch", "batch": {"steps": pasos}}])[0]
    if res["type"] != "ok":
        raise RuntimeError(res.get("error", {}).get("message", "error"))
    r = res["response"]["result"]
    errores = [e for e in r.get("step_errors", []) if e]
    if errores:
        raise RuntimeError(errores[0].get("message", "error al guardar en la nube"))
    final = r["step_results"][-1]
    return int(_valor(final["rows"][0][0])) if final and final.get("rows") else None


# ---------------------------------------------------------- mandar lo que se guarda
def _tabla_de(sql):
    m = re.search(r"\b(?:INTO|UPDATE|FROM|TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?|ON)\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.I)
    return m.group(1).lower() if m else ""


def es_para_nube(sql):
    palabra = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    return palabra in ESCRITURA and _tabla_de(sql) not in SOLO_LOCALES


def _pendientes_locales(con):
    con.execute("CREATE TABLE IF NOT EXISTS nube_pendiente (id INTEGER PRIMARY KEY AUTOINCREMENT, sentencia TEXT)")
    return con


def enviar(sentencias, ruta_bd):
    """Manda a internet lo que se acaba de guardar. Si no hay internet, lo deja en fila y lo reintenta despues."""
    import json
    with _candado:
        with sqlite3.connect(ruta_bd, timeout=30) as con:
            _pendientes_locales(con)
            atrasadas = con.execute("SELECT id, sentencia FROM nube_pendiente ORDER BY id").fetchall()
            todo = [tuple(json.loads(s)) for _, s in atrasadas] + list(sentencias)
            if not todo:
                return
            try:
                v = _lote([(s, p) for s, p in todo])
                if atrasadas:
                    con.execute("DELETE FROM nube_pendiente WHERE id <= ?", [atrasadas[-1][0]])
                if v is not None:
                    # si nadie mas cambio la nube entre medio, seguimos al dia sin volver a bajar todo
                    if _estado["version"] is not None and v == _estado["version"] + 1:
                        _estado["version"] = v
                _estado["ultimo_envio"], _estado["error"] = time.strftime("%Y-%m-%d %H:%M:%S"), ""
            except Exception as e:
                for s, p in sentencias:
                    con.execute("INSERT INTO nube_pendiente (sentencia) VALUES (?)", [json.dumps([s, list(p or ())])])
                _estado["error"] = f"{time.strftime('%H:%M:%S')} - {e}"


def pendientes(ruta_bd):
    try:
        with sqlite3.connect(ruta_bd, timeout=30) as con:
            return _pendientes_locales(con).execute("SELECT COUNT(*) FROM nube_pendiente").fetchone()[0]
    except Exception:
        return 0


class ConexionNube(sqlite3.Connection):
    """Conexion normal a tms.db que ademas apunta lo que se escribe para mandarlo a internet al guardar."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._para_nube = []
        self.ruta_bd = a[0] if a else k.get("database")

    def execute(self, sql, params=()):
        if not (activa() and _estado["lista"] and es_para_nube(sql)):
            return super().execute(sql, params)
        palabra = sql.lstrip().split(None, 1)[0].upper()
        ya_existia = False
        if palabra == "CREATE" and "IF NOT EXISTS" in sql.upper():
            m = re.search(r"IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.I)
            ya_existia = bool(m) and super().execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", [m.group(1)]).fetchone() is not None
        cur = super().execute(sql, params)
        if palabra in ("INSERT", "UPDATE", "DELETE", "REPLACE") and cur.rowcount == 0:
            return cur                                   # no cambio nada: no hay nada que mandar
        if not ya_existia:
            self._para_nube.append((sql, list(params or ())))
        return cur

    def commit(self):
        super().commit()
        if self._para_nube:
            envio, self._para_nube = self._para_nube, []
            enviar(envio, self.ruta_bd)

    def rollback(self):
        super().rollback()
        self._para_nube = []


# ---------------------------------------------------------- bajar lo de internet
def version_remota():
    try:
        _, filas = consultar("SELECT valor FROM nube_meta WHERE clave = 'version'")
        return int(filas[0][0]) if filas else 0
    except RuntimeError as e:
        if "no such table" in str(e).lower():
            return 0
        raise


def bajar(ruta_bd):
    """Copia a tms.db todas las tablas de la base de internet (las SOLO_LOCALES no se tocan)."""
    with _candado:
        _, esquemas = consultar("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")
        tablas = [(n, s) for t, n, _, s in esquemas if t == "table" and n.lower() not in SOLO_LOCALES]
        if not any(n == "viajes" for n, _ in tablas):
            _estado["lista"] = False
            return False                              # la nube esta vacia: no se borra nada de aqui
        datos = {}
        for nombre, _ in tablas:
            cols, filas = consultar(f'SELECT * FROM "{nombre}"')
            datos[nombre] = (cols, filas)
        v = version_remota()
        con = sqlite3.connect(ruta_bd, timeout=30)
        try:
            con.execute("BEGIN")
            for nombre, sql in tablas:
                con.execute(f'DROP TABLE IF EXISTS "{nombre}"')
                con.execute(sql)
                cols, filas = datos[nombre]
                if filas:
                    marcas = ", ".join("?" for _ in cols)
                    con.executemany(f'INSERT INTO "{nombre}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
                                    f"VALUES ({marcas})", filas)
            for t, n, tabla, sql in esquemas:
                if t == "index" and tabla.lower() not in SOLO_LOCALES:
                    con.execute(sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                                if "IF NOT EXISTS" not in sql.upper() else sql)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        _estado["version"], _estado["lista"] = v, True
        return True


def _diag(evento, detalle=""):
    """Anota en internet (tabla nube_diag) que paso al conectar, para poder revisarlo a distancia. Nunca falla."""
    try:
        import platform
        ejecutar([("CREATE TABLE IF NOT EXISTS nube_diag (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, "
                   "equipo TEXT, evento TEXT, detalle TEXT)", ()),
                  ("INSERT INTO nube_diag (fecha, equipo, evento, detalle) VALUES (?, ?, ?, ?)",
                   (time.strftime("%Y-%m-%d %H:%M:%S"), f"{platform.node()} py{platform.python_version()} "
                    f"sqlite{sqlite3.sqlite_version}", evento, str(detalle)[:3000])),
                  ("DELETE FROM nube_diag WHERE id NOT IN (SELECT id FROM nube_diag ORDER BY id DESC LIMIT 300)", ())])
    except Exception:
        pass


def refrescar(ruta_bd, cada_segundos=5):
    """Si alguien cambio la base de internet, baja lo nuevo. Barato: solo pregunta el numero de version."""
    if not activa():
        return False
    if time.time() - _estado["ultima_revision"] < cada_segundos and _estado["version"] is not None:
        return False
    _estado["ultima_revision"], _estado["revisada"] = time.time(), True
    try:
        if pendientes(ruta_bd):
            enviar([], ruta_bd)                      # primero lo que no se habia podido mandar
        v = version_remota()
        if v != _estado["version"] or not _estado["lista"]:
            ok = bajar(ruta_bd)
            if not _estado["lista"]:
                _estado["error"] = f"{time.strftime('%H:%M:%S')} - la base en internet no tiene la tabla de viajes (version {v})"
                _diag("bajar_sin_viajes", f"version remota {v}")
            elif not _estado.get("diag_ok"):
                _estado["diag_ok"] = True
                _diag("conectado", f"version {v} · ruta {ruta_bd}")
            return ok
        return False
    except Exception as e:
        import traceback
        _estado["error"] = f"{time.strftime('%H:%M:%S')} - {type(e).__name__}: {e}"
        _diag("error", traceback.format_exc())
        return False


# ---------------------------------------------------------- primera vez: subir tu tms.db
def subir(ruta_bd, forzar=False):
    """Sube TODAS las tablas de tms.db a la base de internet. Si la nube ya tiene viajes, pide forzar=True."""
    if not activa():
        raise RuntimeError("Faltan TURSO_URL y TURSO_TOKEN (ponlos en configuracion_local.py).")
    try:
        _, n = consultar("SELECT COUNT(*) FROM viajes")
        ya = n[0][0] if n else 0
    except RuntimeError:
        ya = 0
    if ya and not forzar:
        raise RuntimeError(f"La base en internet ya tiene {ya} viajes. Para reemplazarlos usa: py nube.py subir --forzar")
    con = sqlite3.connect(ruta_bd)
    esquemas = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table','index') "
                           "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL").fetchall()
    sentencias, total = [], 0
    for t, nombre, tabla, sql in esquemas:
        if t != "table" or nombre.lower() in SOLO_LOCALES:
            continue
        sentencias += [(f'DROP TABLE IF EXISTS "{nombre}"', ()), (sql, ())]
        cur = con.execute(f'SELECT * FROM "{nombre}"')
        cols = [d[0] for d in cur.description]
        ins = f'INSERT INTO "{nombre}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({", ".join("?" for _ in cols)})'
        for fila in cur.fetchall():
            sentencias.append((ins, tuple(fila)))
            total += 1
    for t, nombre, tabla, sql in esquemas:
        if t == "index" and tabla.lower() not in SOLO_LOCALES:
            sentencias.append((sql, ()))
    con.close()
    # en pedazos para no mandar un paquete enorme (las tablas se crean en el primero)
    v = None
    for i in range(0, len(sentencias), 400):
        v = _lote(sentencias[i:i + 400])
    _estado["version"], _estado["lista"] = v, True
    return total


if __name__ == "__main__":
    from pathlib import Path
    import configuracion as cfg
    ruta = Path(__file__).parent / cfg.ARCHIVO_BD
    if len(sys.argv) >= 2 and sys.argv[1] == "subir":
        try:
            n = subir(ruta, forzar="--forzar" in sys.argv)
            print(f"Listo: se subieron {n} registros a la base en internet.")
        except Exception as e:
            print("No se pudo:", e)
    elif len(sys.argv) >= 2 and sys.argv[1] == "probar":
        try:
            print("Conectado. Versión de la base en internet:", version_remota())
        except Exception as e:
            print("No se pudo conectar:", e)
    else:
        print("Uso:\n  py nube.py probar          (revisa la conexion)\n  py nube.py subir           (primera vez: sube tu tms.db)")
