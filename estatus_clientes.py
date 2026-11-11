# ==========================================================
#  estatus_clientes.py - Los "ESTATUS ACTUALIZADO" que TC le manda a cada cliente
#  (antes: Libro1.xlsx, el estatus de PEPSICO DEDICADO y el de PEPSICO SPOT)
#
#  Cada cliente tiene su PLANTILLA (logo, colores y columnas como en su Excel).
#   - A mano: ECO, VIAJE, STATUS, COMENTARIO (y lo que quieras corregir).
#   - Solas:  HR. ACTUALIZADO y UBICACION salen del command (Omnitracs) de esa unidad.
#   - Si el VIAJE es un pedido / folio de cliente / # viaje del plan de carga, ORIGEN, DESTINO
#     y las CITAS se llenan solas (si escribes algo encima, manda lo que escribiste).
#   - PEPSICO DEDICADO: 24 renglones fijos, casi todo sale del plan de carga (cita + 2 h).
#   - PEPSICO SPOT: 3 tablas (SPOT, CELAYA DEDICADO, CELAYA SPOT), con boton para agregar renglones.
#  Un viaje con sus horas completas, finalizado/cancelado o ya cerrado en ZAM deja la unidad DISPONIBLE
#  (y su remolque y operador ya no se le cuelgan).
# ==========================================================
import base64
import json
import re
from collections import namedtuple
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import pandas as pd

import base_datos as bd
import calculos
import configuracion as cfg
import omnitracs

CARPETA_LOGOS = Path(__file__).parent / "assets" / "clientes"
HORAS_EXTRA_CITA_PEPSICO = getattr(cfg, "ESTATUS_PEPSICO_HORAS_EXTRA", 2)   # el Excel le suma 2 h a las citas
FILAS_PEPSICO_DEDICADO = 24
MAX_ESTATUS = 100

# modo: "manual" (solo lo que escribes) · "auto" (lo calcula el TMS, no se edita) · "semi" (automatico, pero editable)
Col = namedtuple("Col", "clave titulo modo")
Seccion = namedtuple("Seccion", "clave rotulo columnas")

GENERICAS = [
    Col("hr", "HR. ACTUALIZADO", "auto"), Col("cita_carga", "CITA DE CARGA", "semi"),
    Col("eco", "ECO", "manual"), Col("viaje", "VIAJE", "manual"),
    Col("origen", "ORIGEN", "semi"), Col("destino", "DESTINO", "semi"), Col("status", "STATUS", "manual"),
    Col("ubicacion", "UBICACIÓN", "auto"), Col("comentario", "COMENTARIO", "manual"),
    Col("cita_entrega", "CITA DE ENTREGA", "semi"),
]


def _genericas(cita_entrega="CITA DE ENTREGA", con_cita_carga=True):
    cols = [c._replace(titulo=cita_entrega) if c.clave == "cita_entrega" else c for c in GENERICAS]
    return [c for c in cols if con_cita_carga or c.clave != "cita_carga"]


COPPEL_COLS = [Col("hr", "HR. ACTUALIZADO", "auto"), Col("eco", "ECO", "manual"), Col("caja", "CAJA", "manual"),
               Col("origen", "ORIGEN", "manual"), Col("destino", "DESTINO", "manual"), Col("status", "STATUS", "manual"),
               Col("ubicacion", "UBICACIÓN", "auto"), Col("comentario", "COMENTARIO", "manual"),
               Col("cita_entrega", "CITA DESCARGA", "manual")]

BACHOCO_COLS = [Col("eco", "UNIDAD", "manual"), Col("origen", "ORIGEN", "semi"), Col("destino", "DESTINO", "semi"),
                Col("cita_carga", "CITA CARGA", "semi"), Col("salida", "SALIDA", "semi"),
                Col("cita_entrega", "CITA DESCARGA", "semi"), Col("ubicacion", "UBICACIÓN", "auto"),
                Col("status", "ESTATUS", "manual"), Col("operador", "OPERADOR", "semi"),
                Col("telefono", "TELÉFONO", "semi")]

PEPSICO_DED_COLS = [Col("eco", "ECO", "manual"), Col("operador", "OPERADOR", "semi"), Col("remolque", "REMOLQUE", "semi"),
                    Col("origen", "ORIGEN", "semi"), Col("destino", "DESTINO", "semi"),
                    Col("folio", "FOLIO DEL CLIENTE", "semi"), Col("status", "ESTATUS", "semi"),
                    Col("ubicacion", "UBICACIÓN UNIDAD", "auto"), Col("cita_carga", "CITA DE CARGA", "semi"),
                    Col("llegada_carga", "LLEGADA A CARGA", "semi"), Col("cita_entrega", "CITA DE DESCARGA", "semi"),
                    Col("llegada_descarga", "LLEGADA A DESCARGA", "semi"), Col("comentario", "OBSERVACIONES", "manual")]

PEPSICO_SPOT_COLS = [Col("eco", "ECO", "manual"), Col("operador", "OPERADOR", "semi"), Col("remolque", "REMOLQUE", "semi"),
                     Col("placas_remolque", "PLACAS DE REMOLQUE", "manual"), Col("origen", "ORIGEN", "semi"),
                     Col("destino", "DESTINO", "semi"), Col("modalidad", "MODALIDAD", "semi"),
                     Col("folio", "FOLIO DEL CLIENTE", "semi"), Col("status", "ESTATUS", "semi"),
                     Col("ubicacion", "UBICACIÓN UNIDAD", "auto"), Col("cita_carga", "CITA DE CARGA", "semi"),
                     Col("llegada_carga", "LLEGADA A CARGA", "semi"), Col("cita_entrega", "CITA DE DESCARGA", "semi"),
                     Col("comentario", "OBSERVACIONES", "manual"), Col("barras", "BARRAS", "semi"),
                     Col("rechazo", "RECHAZO", "manual"), Col("peplink", "PEPLINK", "manual"),
                     Col("comentario2", "OBSERVACIONES ", "manual")]

CELAYA_COLS = [Col("eco", "UNIDAD", "manual"), Col("remolque", "REMOLQUE", "semi"),
               Col("fecha_carga", "FECHA DE CARGA", "semi"), Col("hora_carga", "HORA DE CARGA", "semi"),
               Col("folio", "OC", "semi"), Col("destino", "DESTINO", "semi"), Col("status", "ESTATUS", "semi"),
               Col("fecha_arribo", "FECHA DE ARRIBO", "semi"), Col("estadia", "ESTADÍAS", "semi")]

# ---------------------------------------------------------- plantillas (una por cliente, como su hoja de Excel)
# colores: titulo (fondo, letra) · encabezado (fondo, letra) · dato (letra) · estatus (letra, rojo como en el Excel)
AZUL_TEMA, CELESTE_TEMA, VERDE_TEMA, MARINO_TEMA = "#215F9A", "#46B5E1", "#3A7D22", "#0E2841"
PLANTILLAS = {
    "GOLDEN FOODS": dict(logos=["golden_foods.png"], titulo="STATUS ACTUALIZADO", t_bg="#DA0000", t_fg="#FFFFFF",
                         e_bg=AZUL_TEMA, e_fg="#FFFFFF", d_fg=MARINO_TEMA, cliente=["GOLDEN"],
                         secciones=[Seccion("tabla", "", _genericas())]),
    "INNOVATIVE": dict(logos=["innovative.png"], titulo="STATUS ACTUALIZADO", t_bg="#FFFFFF", t_fg="#92D050",
                       t_borde="#92D050", e_bg="#92D050", e_fg="#000000", d_fg="#000000", cliente=["INNOVATIVE"],
                       secciones=[Seccion("tabla", "", _genericas())]),
    "LA SALINERA": dict(logos=["la_salinera.png"], titulo="STATUS ACTUALIZADO", t_bg=CELESTE_TEMA, t_fg="#000000",
                        e_bg=CELESTE_TEMA, e_fg="#000000", d_fg=MARINO_TEMA, cliente=["SALINERA"],
                        secciones=[Seccion("tabla", "", _genericas())]),
    "GRUPO VIDA": dict(logos=["grupo_vida.png"], titulo="STATUS ACTUALIZADO", t_bg=VERDE_TEMA, t_fg="#FFFFFF",
                       e_bg=VERDE_TEMA, e_fg="#E8E8E8", d_fg="#196B24", cliente=["VIDA"],
                       secciones=[Seccion("tabla", "", _genericas("CITA DESCARGA"))]),
    "LICORERA": dict(logos=["licorera.png"], titulo="STATUS ACTUALIZADO", t_bg=AZUL_TEMA, t_fg="#E8E8E8",
                     e_bg=AZUL_TEMA, e_fg="#E8E8E8", d_fg=MARINO_TEMA, cliente=["LICORERA", "BRAVO"],
                     secciones=[Seccion("tabla", "", _genericas())]),
    "SAMSUNG": dict(logos=["samsung.png"], titulo="STATUS ACTUALIZADO", t_bg=AZUL_TEMA, t_fg="#E8E8E8",
                    e_bg=AZUL_TEMA, e_fg="#E8E8E8", d_fg=MARINO_TEMA, cliente=["SAMSUNG"],
                    secciones=[Seccion("tabla", "", _genericas())]),
    "BACHOCO": dict(logos=["bachoco.jpg", "dliver.png"], titulo="", t_bg="#F6C6AD", t_fg="#E97132",
                    e_bg="#F6C6AD", e_fg="#E97132", d_fg="#000000", cliente=["BACHOCO"],
                    secciones=[Seccion("dedicado", "DEDICADO", BACHOCO_COLS), Seccion("spot", "SPOT", BACHOCO_COLS)]),
    "WATCOM": dict(logos=["watcom.png"], titulo="STATUS ACTUALIZADO", t_bg=CELESTE_TEMA, t_fg="#FFFFFF",
                   e_bg=CELESTE_TEMA, e_fg="#FFFFFF", d_fg="#156082", cliente=["WATCOM"],
                   secciones=[Seccion("tabla", "", _genericas("CITA DESCARGA"))]),
    "K+N": dict(logos=["kn.png"], titulo="STATUS ACTUALIZADO", t_bg="#133659", t_fg="#E8E8E8",
                e_bg="#133659", e_fg="#E8E8E8", d_fg=MARINO_TEMA, cliente=["KUEHNE", "NAGEL"],
                secciones=[Seccion("tabla", "", _genericas())]),
    "COPPEL": dict(logos=["coppel.png"], titulo="ESTATUS ACTUALIZADO", t_bg="#F6F000", t_fg=MARINO_TEMA,
                   e_bg="#FFFF00", e_fg=MARINO_TEMA, d_fg="#156082", cliente=["COPPEL"], repetir_titulo=True,
                   secciones=[Seccion("puebla", "Tabla 1 · PUEBLA", COPPEL_COLS),
                              Seccion("texcoco", "Tabla 2 · TEXCOCO", COPPEL_COLS)]),
    "MANANTIALES": dict(logos=["manantiales.png"], titulo="STATUS ACTUALIZADO", t_bg=AZUL_TEMA, t_fg="#E8E8E8",
                        e_bg=AZUL_TEMA, e_fg="#E8E8E8", d_fg=MARINO_TEMA, cliente=["MANANTIALES"],
                        secciones=[Seccion("tabla", "", _genericas())]),
    "PEPSICO DEDICADO": dict(logos=["dliver.png"], titulo="DEDICADO PEPSICO", t_bg="#000000", t_fg="#FFFFFF",
                             e_bg="#002060", e_fg="#FFFFFF", d_fg="#000000", cliente=["PEPSICO"], pepsico="dedicado",
                             fijas=FILAS_PEPSICO_DEDICADO, tipos=["PEPSICO DEDICADO"],
                             secciones=[Seccion("dedicado", "", PEPSICO_DED_COLS)]),
    "PEPSICO SPOT": dict(logos=["dliver.png"], titulo="PEPSICO SPOT", t_bg="#000000", t_fg="#FFFFFF",
                         e_bg="#002060", e_fg="#FFFFFF", d_fg="#000000", cliente=["PEPSICO"], pepsico="spot",
                         secciones=[Seccion("spot", "PEPSICO SPOT", PEPSICO_SPOT_COLS),
                                    Seccion("celaya_ded", "PEPSICO CELAYA DEDICADO", CELAYA_COLS),
                                    Seccion("celaya_spot", "PEPSICO CELAYA SPOT", CELAYA_COLS)]),
}
# encabezados con color propio (como en los Excel de PepsiCo)
ENCABEZADO_ESPECIAL = {"cita_carga": ("#C00000", "#FFFFFF"), "cita_entrega": ("#C00000", "#FFFFFF"),
                       "llegada_carga": ("#000000", "#FFFFFF"), "llegada_descarga": ("#000000", "#FFFFFF"),
                       "comentario": ("#000000", "#FFFFFF")}
ENCABEZADO_CELAYA = {"fecha_arribo": ("#C0E4F5", "#000000"), "estadia": ("#C0E4F5", "#000000")}

# Lo que ya traia el Libro1 de COPPEL (se comparte con el cliente: se carga la primera vez)
SEMILLA_COPPEL = {
    "puebla": [
        ("1906", "85330289C", "VILLAHERMOSA", "PUEBLA", "VACIO"), ("2309", "17FR0528C", "OAXACA 1", "PUEBLA", "VACIO"),
        ("1907", "153301238C", "PUEBLA", "OAXACA 1", "VACIO"), ("2042", "85330317C", "PUEBLA", "OAXACA 1", "VACIO"),
        ("1908", "85330299C", "PUEBLA", "OAXACA 2", "VACIO"), ("1930", "153301291C", "PUEBLA", "VILLA HERMOSA", "VACIO"),
        ("1907", "153301291C", "PUEBLA", "VILLA HERMOSA", "DESCARGANDO"),
        ("1917", "85330481C", "PUEBLA", "OAXACA 1", "TRANSITO CARGADO"),
        ("2026", "17FR0535", "PUEBLA", "OAXACA 2", "TRANSITO CARGADO"), ("2013", "17FR0532", "PUEBLA", "OAXACA 1", "DESCARGANDO"),
        ("2309", "17FR0538", "PUEBLA", "TEZIUTLAN", "REGRESO VACIO"),
        ("2505", "17FR0521", "PUEBLA", "OAXACA 2", "TRANSITO CARGADO")],
    "texcoco": [
        ("2003", "", "", "", "VACIO"), ("1922", "85330478C", "TEXCOCO", "TUXPAN", "DESCARGANDO"),
        ("2107", "17FR0531C", "TEXCOCO", "XOCHIMILCO", "ASIGNADO"), ("2109", "17FR0525C", "TEXCOCO", "CUAUTLA", "ASIGNADO"),
        ("2202", "153301231C", "TEXCOCO", "POZARICA", "ASIGNADO"), ("2202", "85330116C", "TEXCOCO", "CUAUTLA", ""),
        ("2308", "", "TEXCOCO", "ZACATLAN", "ASIGNADO"), ("2009", "8533095C", "TEXCOCO", "XOCHIMILCO", ""),
        ("2308", "17FR0531", "TEXCOCO", "TUXPAN", ""), ("2105", "85330116C", "TEXCOCO", "CUAUTLA", "PENSION"),
        ("1928", "8533095C", "TEXCOCO", "XOCHIMILCO", "PENSION")],
}
# Unidades del estatus de PEPSICO DEDICADO (Libro2): los 24 renglones fijos
SEMILLA_PEPSICO_DEDICADO = ["2209", "1921", "2043", "2302", "1927", "2020", "1913", "2201", "2204", "2301", "1924",
                            "1925", "1915", "1919", "2504", "1911", "2038", "2205", "2014", "1918", "2306", "2508",
                            "2501", "2110"]
SEMILLA_PEPSICO_SPOT = {"spot": ["2503", "2032", "2003", "2310"], "celaya_ded": ["2104", "2007", "2036", "2034"],
                        "celaya_spot": ["2034", "2208", "2011"]}


# ---------------------------------------------------------- utilidades
def _t(v):
    """Texto limpio; vacio/NaN -> ''."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _k(v):
    return _t(v).upper()


def _fecha(v):
    if v is None or _t(v) == "":
        return None
    t = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(t) else t


def fmt_fecha(v, solo="todo"):
    t = _fecha(v)
    if t is None:
        return ""
    return {"todo": f"{t:%d/%m/%Y %H:%M}", "dia": f"{t:%d/%m/%Y}", "hora": f"{t:%H:%M}"}[solo]


def _cerrado(v):
    """Viaje que ya no ocupa a la unidad: ✅/❌, estatus DISPONIBLE/CANCELADO, o ya tiene su salida de descarga."""
    return (bool(v.get("finalizado")) or bool(v.get("cancelado")) or _k(v.get("estatus")) in ("DISPONIBLE", "CANCELADO")
            or _fecha(v.get("salida_descarga")) is not None)


# ---------------------------------------------------------- base de datos
def crear_tabla():
    with bd.conexion() as con:
        con.execute("CREATE TABLE IF NOT EXISTS estatus_filas (id INTEGER PRIMARY KEY AUTOINCREMENT, plantilla TEXT, "
                    "seccion TEXT, orden REAL, datos TEXT, actualizado TEXT, actualizado_por TEXT)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_estatus_plantilla ON estatus_filas (plantilla, seccion, orden)")
        con.execute("CREATE TABLE IF NOT EXISTS estatus_plantillas (plantilla TEXT PRIMARY KEY, sembrada TEXT)")
    _sembrar()


def _sembrar():
    """La primera vez: COPPEL con lo de su Excel, PEPSICO con sus unidades. Los demas clientes empiezan vacios."""
    with bd.conexion() as con:
        hechas = {f[0] for f in con.execute("SELECT plantilla FROM estatus_plantillas")}
        ahora = bd.AHORA_TXT()

        def poner(plantilla, seccion, filas):
            for i, d in enumerate(filas):
                con.execute("INSERT INTO estatus_filas (plantilla, seccion, orden, datos, actualizado, actualizado_por) "
                            "VALUES (?, ?, ?, ?, ?, 'SISTEMA')", [plantilla, seccion, i, json.dumps(d, ensure_ascii=False), ahora])

        for nombre in PLANTILLAS:
            if nombre in hechas:
                continue
            if nombre == "COPPEL":
                for sec, filas in SEMILLA_COPPEL.items():
                    poner(nombre, sec, [{k: v for k, v in zip(("eco", "caja", "origen", "destino", "status"), f) if v}
                                        for f in filas])
            elif nombre == "PEPSICO DEDICADO":
                poner(nombre, "dedicado", [{"eco": e} for e in SEMILLA_PEPSICO_DEDICADO])
            elif nombre == "PEPSICO SPOT":
                for sec, ecos in SEMILLA_PEPSICO_SPOT.items():
                    poner(nombre, sec, [{"eco": e} for e in ecos])
            con.execute("INSERT OR IGNORE INTO estatus_plantillas (plantilla, sembrada) VALUES (?, ?)", [nombre, ahora])


def leer_filas(plantilla, seccion):
    with bd.conexion() as con:
        filas = con.execute("SELECT id, datos FROM estatus_filas WHERE plantilla = ? AND seccion = ? ORDER BY orden, id",
                            [plantilla, seccion]).fetchall()
    salida = []
    for id_, datos in filas:
        try:
            d = json.loads(datos or "{}")
        except ValueError:
            d = {}
        salida.append((int(id_), d))
    if PLANTILLAS[plantilla].get("fijas"):                         # siempre el mismo numero de renglones
        n = PLANTILLAS[plantilla]["fijas"]
        if len(salida) < n:
            for _ in range(n - len(salida)):
                agregar_fila(plantilla, seccion)
            return leer_filas(plantilla, seccion)
        salida = salida[:n]
    return salida


def agregar_fila(plantilla, seccion, datos=None, quien="SISTEMA"):
    with bd.conexion() as con:
        orden = con.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM estatus_filas WHERE plantilla = ? AND seccion = ?",
                            [plantilla, seccion]).fetchone()[0]
        con.execute("INSERT INTO estatus_filas (plantilla, seccion, orden, datos, actualizado, actualizado_por) "
                    "VALUES (?, ?, ?, ?, ?, ?)", [plantilla, seccion, orden, json.dumps(datos or {}, ensure_ascii=False),
                                                  bd.AHORA_TXT(), quien])


def borrar_filas(ids):
    if ids:
        with bd.conexion() as con:
            con.execute(f"DELETE FROM estatus_filas WHERE id IN ({', '.join('?' for _ in ids)})", list(ids))


def guardar_datos(cambios, quien):
    """cambios: {id: datos (dict completo de lo escrito a mano)}"""
    with bd.conexion() as con:
        for id_, d in cambios.items():
            limpio = {k: v for k, v in d.items() if _t(v) != ""}
            con.execute("UPDATE estatus_filas SET datos = ?, actualizado = ?, actualizado_por = ? WHERE id = ?",
                        [json.dumps(limpio, ensure_ascii=False), bd.AHORA_TXT(), quien, int(id_)])


# ---------------------------------------------------------- de donde sale lo automatico
class Fuentes:
    """Foto del plan de carga, ZAM, catalogos y Omnitracs (se arma una vez por pantalla)."""

    def __init__(self, dias=45):
        hoy = datetime.now().date()
        v = calculos.agregar_calculados(bd.leer_viajes(hoy - timedelta(days=dias), hoy + timedelta(days=15)))
        v = v.copy()
        v["_eco"] = v["eco"].map(_k)
        v["_ref"] = v["cita_carga"].fillna(pd.to_datetime(v["fecha_plan"], errors="coerce"))
        zam_abiertos = set(bd.leer_zam()["shipment_id"].map(_k))
        zam_vistos = set(bd.leer_zam_todos()["shipment_id"].map(_k))
        # cerrado en ZAM: el # VIAJE (o pedido) ya se vio en ZAM y ya no esta entre los abiertos
        cerrado_zam = v.apply(lambda f: any(x and x in zam_vistos and x not in zam_abiertos
                                            for x in (_k(f.get("num_viaje")), _k(f.get("pedido")))), axis=1) \
            if len(v) and zam_abiertos else pd.Series(False, index=v.index)
        v["_cerrado"] = v.apply(lambda f: _cerrado(f), axis=1) | cerrado_zam if len(v) else pd.Series(dtype=bool)
        self.viajes = v.sort_values(["_ref", "id"])
        uni = bd.leer_catalogo("unidades")
        self.habitual = {_k(e): o for e, o in zip(uni["eco"], uni["operador"]) if _t(o)}
        ops = bd.leer_catalogo("operadores")
        self.telefono = {_k(n): t for n, t in zip(ops["nombre"], ops["telefono"]) if _t(t)}
        self.pos = _posiciones_por_eco()
        self.lugares = leer_lugares()

    # --- viajes
    def por_folio(self, folio):
        f = _k(folio)
        if not f:
            return None
        v = self.viajes
        m = v[(v["pedido"].map(_k) == f) | (v["folio_cliente"].map(_k) == f) | (v["num_viaje"].map(_k) == f)
              | (v["cp_cargado"].map(_k) == f)]
        return m.iloc[-1] if len(m) else None

    def de_eco(self, eco, tipos=None, cliente=None, excluir_tipos=None):
        """(viaje activo, ultimo viaje) de la unidad. Activo = el mas reciente que no esta cerrado."""
        e = _k(eco)
        if not e:
            return None, None
        m = self.viajes[self.viajes["_eco"] == e]
        if tipos:
            m = m[m["tipo_operacion"].isin(tipos)]
        if excluir_tipos:
            m = m[~m["tipo_operacion"].isin(excluir_tipos)]
        if cliente:
            m = m[m["cliente"].map(_k).str.contains("|".join(cliente), regex=True) | m["tipo_operacion"].map(_k).str.contains("|".join(cliente), regex=True)]
        if m.empty:
            return None, None
        act = m[~m["_cerrado"]]
        return (act.iloc[-1] if len(act) else None), m.iloc[-1]

    # --- command (Omnitracs)
    def command(self, eco):
        """(hora del ultimo reporte, ubicacion en texto) de la unidad."""
        p = self.pos.get(omnitracs.normalizar_alias(eco)) if _t(eco) else None
        if p is None:
            return "", ""
        hora = fmt_fecha(p["fecha"])
        lugar = _t(p.get("ubicacion")) or self.lugares.get(clave_lugar(p["latitud"], p["longitud"]), "")
        if not lugar and pd.notna(p["latitud"]) and pd.notna(p["longitud"]):
            lugar = f"{float(p['latitud']):.4f}, {float(p['longitud']):.4f}"
        return hora, lugar


def _posiciones_por_eco():
    """{ECO normalizado: posicion mas reciente} (empata 2105C -> 2105 si no es remolque)."""
    pos = bd.leer_posiciones()
    if pos.empty:
        return {}
    remolques = {omnitracs.normalizar_alias(r) for r in bd.opciones_catalogo("remolques")}
    tractos = {omnitracs.normalizar_alias(e) for e in bd.opciones_catalogo("unidades")}
    salida = {}
    for _, p in pos.sort_values("fecha").iterrows():
        tipo, clave = omnitracs.clasificar_alias(p["alias"], tractos, remolques)
        k = clave if tipo == "TRACTO" else omnitracs.normalizar_alias(p["alias"])
        exacto = omnitracs.normalizar_alias(p["alias"]) == k
        if k not in salida or exacto or not salida[k]["_exacto"]:
            salida[k] = {**p.to_dict(), "_exacto": exacto}
    return salida


# ---------------------------------------------------------- ubicacion en texto (colonia, municipio, estado)
def clave_lugar(lat, lon):
    try:
        return f"{float(lat):.3f},{float(lon):.3f}"
    except (TypeError, ValueError):
        return ""


def leer_lugares():
    with bd.conexion() as con:
        con.execute("CREATE TABLE IF NOT EXISTS lugares (clave TEXT PRIMARY KEY, texto TEXT, actualizado TEXT)")
        return {c: t for c, t in con.execute("SELECT clave, texto FROM lugares WHERE texto IS NOT NULL AND texto <> ''")}


def _texto_lugar(direccion):
    a = direccion or {}
    colonia = next((a[k] for k in ("neighbourhood", "suburb", "quarter", "hamlet", "village", "residential", "road")
                    if a.get(k)), "")
    municipio = next((a[k] for k in ("city", "town", "municipality", "county", "village") if a.get(k)), "")
    estado = a.get("state", "")
    partes = [p for p in (colonia, municipio) if p]
    texto = ",".join(dict.fromkeys(partes))
    return f"{texto}, {estado}" if texto and estado else (texto or estado)


def geocodificar_pendientes(maximo=25):
    """Pone nombre (colonia, municipio, estado) a las coordenadas de Omnitracs que aun no lo tienen.
    Usa OpenStreetMap (1 consulta por segundo) y lo guarda para no volver a preguntar. Devuelve cuantas resolvio."""
    import time
    import requests
    if not getattr(cfg, "UBICACION_EN_TEXTO", True):
        return 0
    conocidas = leer_lugares()
    pos = bd.leer_posiciones()
    pendientes = []
    for la, lo in zip(pos["latitud"], pos["longitud"]):
        c = clave_lugar(la, lo)
        if c and c not in conocidas and c not in pendientes:
            pendientes.append(c)
    hechas = 0
    for c in pendientes[:maximo]:
        la, lo = c.split(",")
        try:
            r = requests.get("https://nominatim.openstreetmap.org/reverse",
                             params={"lat": la, "lon": lo, "format": "jsonv2", "zoom": 16, "accept-language": "es"},
                             headers={"User-Agent": "TMS-Plan-de-Carga/1.0"}, timeout=15)
            texto = _texto_lugar(r.json().get("address")) if r.status_code == 200 else ""
        except Exception:
            break                                     # sin internet o bloqueado: se intenta en la siguiente vuelta
        with bd.conexion() as con:
            con.execute("INSERT OR REPLACE INTO lugares (clave, texto, actualizado) VALUES (?, ?, ?)",
                        [c, texto, bd.AHORA_TXT()])
        hechas += 1
        time.sleep(1.1)
    return hechas


def iniciar_geocodificacion():
    """Hilo en segundo plano: cada minuto nombra las ubicaciones nuevas."""
    import threading
    import time

    def ciclo():
        while True:
            try:
                geocodificar_pendientes()
            except Exception:
                pass
            time.sleep(60)
    h = threading.Thread(target=ciclo, name="ubicaciones", daemon=True)
    h.start()
    return h


# ---------------------------------------------------------- armar cada tabla
def _poner(fila, clave, auto, manual):
    """Valor final + si salio del TMS (para pintar la pantalla)."""
    m = _t(manual.get(clave))
    fila[clave] = m if m else _t(auto)
    return fila


def _armar_generica(d, f, cols, plantilla):
    fila, auto = {}, {}
    hr, lugar = f.command(d.get("eco"))
    auto.update(hr=hr, ubicacion=lugar)
    claves = {c.clave for c in cols}
    if "viaje" in claves:
        v = f.por_folio(d.get("viaje"))
        if v is not None:
            auto.update(origen=v.get("origen"), destino=v.get("destino"), cita_carga=fmt_fecha(v.get("cita_carga")),
                        cita_entrega=fmt_fecha(v.get("cita_descarga")))
    if plantilla == "BACHOCO":
        act, ult = f.de_eco(d.get("eco"), cliente=["BACHOCO"])
        v = act
        if v is not None:
            auto.update(origen=v.get("origen"), destino=v.get("destino"), cita_carga=fmt_fecha(v.get("cita_carga")),
                        salida=fmt_fecha(v.get("salida_carga")), cita_entrega=fmt_fecha(v.get("cita_descarga")),
                        operador=v.get("operador"))
        auto.setdefault("operador", f.habitual.get(_k(d.get("eco")), "") if _t(d.get("eco")) else "")
        op = _t(d.get("operador")) or _t(auto.get("operador"))
        auto["telefono"] = f.telefono.get(_k(op), "")
    for c in cols:
        _poner(fila, c.clave, auto.get(c.clave, "") if c.modo != "manual" else "", d)
    return fila, auto


def _armar_pepsico(d, f, cols, seccion):
    """PEPSICO: todo sale del plan de carga (viaje activo de la unidad, o el folio que escribas)."""
    eco = d.get("eco")
    fila, auto = {}, {}
    hr, lugar = f.command(eco)
    auto["ubicacion"], auto["hr"] = lugar, hr
    if seccion == "dedicado":
        act, ult = f.de_eco(eco, tipos=["PEPSICO DEDICADO"])
    elif seccion == "celaya_ded":
        act, ult = f.de_eco(eco, tipos=["PEPSICO DED CELAYA"])
    elif seccion == "celaya_spot":
        act, ult = f.de_eco(eco, cliente=["PEPSICO"], excluir_tipos=["PEPSICO DEDICADO", "PEPSICO DED CELAYA"])
    else:
        act, ult = f.de_eco(eco, cliente=["PEPSICO"], excluir_tipos=["PEPSICO DEDICADO", "PEPSICO DED CELAYA"])
    v = f.por_folio(d.get("folio")) if _t(d.get("folio")) else act
    if v is not None and bool(v.get("_cerrado")) and _t(d.get("folio")) == "":
        v = None
    extra = timedelta(hours=HORAS_EXTRA_CITA_PEPSICO) if seccion == "dedicado" else timedelta(0)
    if v is not None:
        cc, cd = _fecha(v.get("cita_carga")), _fecha(v.get("cita_descarga"))
        auto.update(
            operador=v.get("operador"), remolque=v.get("remolque_1"), origen=v.get("origen"), destino=v.get("destino"),
            folio=v.get("folio_cliente"), modalidad=v.get("modalidad"),
            status="DISPONIBLE" if bool(v.get("_cerrado")) else _t(v.get("estatus")) or "PROGRAMADO",
            cita_carga=fmt_fecha(cc + extra) if cc is not None else "",
            cita_entrega=fmt_fecha(cd + extra) if cd is not None else "",
            llegada_carga=fmt_fecha(v.get("llegada_carga")), llegada_descarga=fmt_fecha(v.get("llegada_descarga")),
            barras=_t(v.get("barras_real")), fecha_carga=fmt_fecha(v.get("cita_carga"), "dia"),
            hora_carga=fmt_fecha(v.get("cita_carga"), "hora"), fecha_arribo=fmt_fecha(v.get("llegada_descarga")),
            estadia=_t(v.get("estadia")) if _t(v.get("estadia")) not in ("N/A",) else "",
            _viaje=int(v["id"]), _ref=v.get("_ref"))
    elif _t(eco):
        auto.update(status="DISPONIBLE",
                    operador=(ult.get("operador") if ult is not None else None) or f.habitual.get(_k(eco), ""))
    # ESTATUS escrito a mano: vale para el folio con el que se escribio (si cambia el viaje, vuelve al automatico)
    man = dict(d)
    if _t(man.get("status")) and _t(man.get("_status_folio")) and _t(man.get("_status_folio")) != _t(auto.get("folio")):
        man.pop("status", None)
    for c in cols:
        _poner(fila, c.clave, auto.get(c.clave, "") if c.modo != "manual" else "", man)
    fila["status"] = fila.get("status", "")[:MAX_ESTATUS]
    return fila, auto


def armar(plantilla, seccion, fuentes=None):
    """DataFrame de la tabla (una fila por renglon guardado) + auto (lo que calculo el TMS, para comparar al guardar)."""
    conf = PLANTILLAS[plantilla]
    sec = next(s for s in conf["secciones"] if s.clave == seccion)
    f = fuentes or Fuentes()
    filas, autos = [], {}
    for id_, d in leer_filas(plantilla, seccion):
        if conf.get("pepsico"):
            fila, auto = _armar_pepsico(d, f, sec.columnas, seccion)
        else:
            fila, auto = _armar_generica(d, f, sec.columnas, plantilla)
        fila["id"] = id_
        filas.append(fila)
        autos[id_] = (auto, d)
    df = pd.DataFrame(filas, columns=["id"] + [c.clave for c in sec.columnas])
    if conf.get("pepsico") and len(df):
        df = _sin_duplicados(df, autos)
    return df, autos


def _sin_duplicados(df, autos):
    """Una caja (remolque) u operador no puede estar en dos renglones: se queda en el del viaje activo mas reciente."""
    for col in ("remolque", "operador"):
        if col not in df.columns:
            continue
        for val, grupo in df[df[col].map(_k) != ""].groupby(df[col].map(_k)):
            if len(grupo) < 2:
                continue

            def peso(i):
                a, d = autos[df.loc[i, "id"]]
                escrito = _t(d.get(col)) != ""
                ref = a.get("_ref")
                ref = pd.Timestamp(ref) if ref is not None and not pd.isna(ref) else pd.Timestamp.min
                return (escrito, a.get("_viaje") is not None, ref)
            gana = max(grupo.index, key=peso)
            for i in grupo.index:
                if i != gana and _t(autos[df.loc[i, "id"]][1].get(col)) == "":
                    df.loc[i, col] = ""
    return df


def resumen_estatus(df):
    """Conteo por estatus (como el cuadro ESTATUS UNIDADES del Excel)."""
    if df.empty or "status" not in df.columns:
        return pd.DataFrame(columns=["ESTATUS", "UNIDADES", "%"])
    conocidos = ["TRANSITO CARGADO", "REGRESO CON DEVOLUCION", "REMOLQUE EN CARGA", "REGRESO VACIO", "EN RESGUARDO",
                 "DESCARGANDO", "CARGANDO", "PROGRAMADO", "DISPONIBLE"]

    def grupo(x):
        x = _k(x)
        return next((c for c in conocidos if x.startswith(c)), "OTRO" if x else "SIN ESTATUS")
    s = df.loc[df["eco"].map(_t) != "", "status"].map(grupo)
    c = s.value_counts()
    return pd.DataFrame({"ESTATUS": c.index, "UNIDADES": c.values, "%": (c.values / c.sum() * 100).round(1)})


# ---------------------------------------------------------- pantalla limpia para la captura
def _logo_html(nombre, alto):
    ruta = CARPETA_LOGOS / nombre
    if not ruta.exists():
        return ""
    tipo = "jpeg" if ruta.suffix.lower() in (".jpg", ".jpeg") else "png"
    return (f"<img src='data:image/{tipo};base64,{base64.b64encode(ruta.read_bytes()).decode()}' "
            f"style='max-height:{alto}px;max-width:200px;object-fit:contain'>")


CSS_CAPTURA = """
<style>
@import url('https://fonts.googleapis.com/css2?family=ADLaM+Display&family=Roboto+Condensed:wght@400;700&display=swap');
.er-est { font-family: 'Aptos Narrow', 'Arial Narrow', 'Roboto Condensed', Arial, sans-serif; background:#FFFFFF;
          padding: 14px 16px 18px 16px; display:inline-block; min-width: 100%; box-sizing: border-box; }
.er-est .cab { display:flex; align-items:center; gap:18px; margin: 4px 0 12px 0; }
.er-est .cab .titulo { flex:1; text-align:center; font-family:'ADLaM Display', 'Aptos Narrow', Arial, sans-serif;
          font-weight:700; font-size:20px; padding:4px 10px; letter-spacing:.5px; }
.er-est table { border-collapse:collapse; width:100%; }
.er-est th { font-weight:700; font-size:12px; padding:5px 6px; border:1px solid #000; text-align:center; white-space:nowrap; }
.er-est td { font-weight:700; font-size:12px; padding:5px 6px; border:1px solid #000; text-align:center;
             vertical-align:middle; }
.er-est td.st { color:#C00000; }
.er-est td.ubi { max-width:260px; }
.er-est td.nw { white-space:nowrap; }
.er-est td.op { min-width:170px; }
.er-est .rotulo { text-align:center; font-weight:700; padding:6px; margin-top:14px; border:1px solid #000; border-bottom:none; }
.er-est.pep td { font-weight:400; font-size:11.5px; }
.er-est.pep td.cita { background:#F2F2F2; }
.er-est .bloque + .bloque { margin-top: 26px; }
</style>
"""


def html_captura(plantilla, tablas):
    """tablas: {seccion: DataFrame}. Devuelve el HTML del estatus tal como se manda al cliente."""
    conf = PLANTILLAS[plantilla]
    pep = bool(conf.get("pepsico"))
    logos = "".join(_logo_html(l, 70 if pep else 80) for l in conf["logos"][:1])
    logos_der = "".join(_logo_html(l, 60) for l in conf["logos"][1:])
    borde_t = f"border:2px solid {conf['t_borde']};" if conf.get("t_borde") else ""
    titulo = (f"<div class='titulo' style='background:{conf['t_bg']};color:{conf['t_fg']};{borde_t}'>"
              f"{escape(conf['titulo'])}</div>") if conf["titulo"] else "<div style='flex:1'></div>"
    cabecera = f"<div class='cab'>{logos}{titulo}{logos_der}</div>"
    partes = [CSS_CAPTURA, f"<div class='er-est{' pep' if pep else ''}'>"]
    if not conf.get("repetir_titulo"):
        partes.append(cabecera)
    for sec in conf["secciones"]:
        df = tablas.get(sec.clave)
        if df is None:
            continue
        df = df[df["eco"].map(_t) != ""] if not conf.get("fijas") else df
        if conf.get("repetir_titulo"):
            if df.empty:
                continue
            partes.append("<div class='bloque'>" + cabecera)
        else:
            partes.append("<div class='bloque'>")
        if sec.rotulo and not conf.get("repetir_titulo"):
            celaya = sec.clave.startswith("celaya")
            bg, fg = ((MARINO_TEMA, "#FFFFFF") if celaya else (conf["t_bg"], conf["t_fg"]))
            if plantilla == "PEPSICO SPOT" and sec.clave == "spot":
                pass                                    # el titulo ya es "PEPSICO SPOT"
            else:
                tam = 22 if plantilla == "BACHOCO" else 16
                partes.append(f"<div class='rotulo' style='background:{bg};color:{fg};font-size:{tam}px'>"
                              f"{escape(sec.rotulo)}</div>")
        enc = []
        for c in sec.columnas:
            bg, fg = conf["e_bg"], conf["e_fg"]
            if pep and sec.clave.startswith("celaya"):
                bg, fg = ENCABEZADO_CELAYA.get(c.clave, ("#156082", "#FFFFFF"))
            elif pep:
                bg, fg = ENCABEZADO_ESPECIAL.get(c.clave, (bg, fg))
            enc.append(f"<th style='background:{bg};color:{fg}'>{escape(c.titulo.strip())}</th>")
        partes.append("<table><tr>" + "".join(enc) + "</tr>")
        for _, r in df.iterrows():
            celdas = []
            for c in sec.columnas:
                val = escape(_t(r.get(c.clave)))
                clases = []
                if c.clave == "status" and not pep:
                    clases.append("st")
                if c.clave == "ubicacion":
                    clases.append("ubi")
                if c.clave in ("hr", "cita_carga", "cita_entrega", "llegada_carga", "llegada_descarga", "salida",
                               "fecha_arribo", "fecha_carga", "eco", "remolque", "caja", "folio"):
                    clases.append("nw")
                if c.clave == "operador":
                    clases.append("op")
                if pep and c.clave in ("cita_carga", "cita_entrega", "llegada_descarga"):
                    clases.append("cita")
                color = "" if (c.clave == "status" and not pep) else f"color:{conf['d_fg']};"
                celdas.append(f"<td class='{' '.join(clases)}' style='{color}'>{val}</td>")
            partes.append("<tr>" + "".join(celdas) + "</tr>")
        partes.append("</table></div>")
    partes.append("</div>")
    return "".join(partes)
