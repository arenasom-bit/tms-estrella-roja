# ==========================================================
#  respaldos.py - Copias de seguridad del TMS (solo usuarios MASTER las ven)
#
#  - AUTOMATICO: una vez al dia, en la madrugada (RESPALDO_HORA_INICIO a RESPALDO_HORA_FIN, hora del centro
#    de Mexico), cuando nadie usa el TMS. Se guarda:
#       * en la base de internet (tabla respaldos_nube): no se pierde aunque la pagina de la nube se reinicie
#       * en la carpeta respaldos/ de la computadora que lo hizo
#  - Se conservan como maximo RESPALDOS_DIAS (150 = 5 meses); los mas viejos se borran solos.
#  - Desde Usuarios y bitacora > Respaldos un MASTER puede: hacer uno al momento (ej. antes de modificar
#    el TMS), descargarlo o RESTAURARLO (antes de restaurar se hace otro respaldo, por si acaso).
# ==========================================================
import gzip
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import base_datos as bd
import configuracion as cfg
import nube

ZONA_MX = timezone(timedelta(hours=-6))          # centro de Mexico (sin horario de verano)
CARPETA = bd.RUTA_BD.parent / "respaldos"
CREAR = ("CREATE TABLE IF NOT EXISTS respaldos_nube (fecha TEXT PRIMARY KEY, motivo TEXT, quien TEXT, "
         "viajes INTEGER, bytes INTEGER, datos BLOB)")


def ahora_mx():
    return datetime.now(ZONA_MX).replace(tzinfo=None)


def dias():
    return int(getattr(cfg, "RESPALDOS_DIAS", 150))


def en_madrugada(t=None):
    t = t or ahora_mx()
    return int(getattr(cfg, "RESPALDO_HORA_INICIO", 2)) <= t.hour < int(getattr(cfg, "RESPALDO_HORA_FIN", 5))


# ---------------------------------------------------------- foto de la base
def _foto():
    """Copia exacta y consistente de tms.db, comprimida. Devuelve (bytes_gz, numero_de_viajes)."""
    with tempfile.TemporaryDirectory() as d:
        destino = Path(d) / "foto.db"
        with sqlite3.connect(bd.RUTA_BD, timeout=60) as origen, sqlite3.connect(destino) as copia:
            origen.backup(copia)
        with sqlite3.connect(destino) as c:
            try:
                n = c.execute("SELECT COUNT(*) FROM viajes").fetchone()[0]
            except sqlite3.Error:
                n = 0
        return gzip.compress(destino.read_bytes(), compresslevel=9), n


def _limpiar_local():
    limite = (ahora_mx() - timedelta(days=dias())).strftime("%Y%m%d")
    for f in sorted(CARPETA.glob("tms_*.db")):
        fecha = f.stem.split("_")[1][:8]
        if fecha.isdigit() and fecha < limite:
            f.unlink()


def hacer(motivo="AUTOMATICO", quien="SISTEMA"):
    """Hace el respaldo (internet + carpeta local). Devuelve un texto con lo que hizo."""
    gz, n = _foto()
    t = ahora_mx()
    hechos = []
    CARPETA.mkdir(exist_ok=True)
    (CARPETA / f"tms_{t:%Y%m%d_%H%M}.db").write_bytes(gzip.decompress(gz))
    _limpiar_local()
    hechos.append("carpeta respaldos")
    if nube.activa() and nube.estado()["lista"]:
        limite = (t - timedelta(days=dias())).strftime("%Y-%m-%d")
        nube.ejecutar([(CREAR, ()),
                       ("INSERT OR REPLACE INTO respaldos_nube (fecha, motivo, quien, viajes, bytes, datos) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (f"{t:%Y-%m-%d %H:%M:%S}", motivo,
                                                      quien.get("usuario", "") if isinstance(quien, dict) else str(quien),
                                                      n, len(gz), gz)),
                       ("DELETE FROM respaldos_nube WHERE fecha < ?", (limite,))])
        hechos.append("base en internet")
    bd.guardar_meta("respaldo_ultimo", f"{t:%Y-%m-%d %H:%M:%S}")
    bd.bitacora(quien if isinstance(quien, dict) else str(quien), "RESPALDO", motivo,
                f"{n} viajes · {len(gz) / 1024:.0f} KB comprimido · guardado en: {', '.join(hechos)}")
    return f"Respaldo del {t:%d/%m/%Y %H:%M} ({n} viajes) guardado en: {', '.join(hechos)}."


def listar():
    """Respaldos disponibles: lista de dicts (fecha, motivo, quien, viajes, kb, donde)."""
    salida = []
    if nube.activa() and nube.estado()["lista"]:
        try:
            _, filas = nube.consultar("SELECT fecha, motivo, quien, viajes, bytes FROM respaldos_nube ORDER BY fecha DESC")
            salida = [{"fecha": f, "motivo": m, "quien": q, "viajes": v, "kb": round((b or 0) / 1024), "donde": "internet"}
                      for f, m, q, v, b in filas]
        except RuntimeError:
            salida = []
    if not salida and CARPETA.exists():
        for f in sorted(CARPETA.glob("tms_*.db"), reverse=True):
            partes = f.stem.split("_")
            try:
                fecha = datetime.strptime(partes[1] + (partes[2] if len(partes) > 2 else "0000"), "%Y%m%d%H%M")
            except ValueError:
                continue
            salida.append({"fecha": f"{fecha:%Y-%m-%d %H:%M:%S}", "motivo": "", "quien": "", "viajes": None,
                           "kb": round(f.stat().st_size / 1024), "donde": f"carpeta: {f.name}"})
    return salida


def _bytes_de(r):
    if r["donde"] == "internet":
        _, filas = nube.consultar("SELECT datos FROM respaldos_nube WHERE fecha = ?", [r["fecha"]])
        return gzip.decompress(filas[0][0])
    return (CARPETA / r["donde"].split(": ", 1)[1]).read_bytes()


def descargar(r):
    """Bytes del archivo .db de ese respaldo (se abre con cualquier visor de SQLite o se copia como tms.db)."""
    return _bytes_de(r)


def restaurar(r, quien):
    """Regresa TODO el TMS a como estaba en ese respaldo. Antes hace un respaldo de como esta ahora."""
    hacer("ANTES DE RESTAURAR", quien)
    datos = _bytes_de(r)
    with tempfile.TemporaryDirectory() as d:
        archivo = Path(d) / "restaurar.db"
        archivo.write_bytes(datos)
        if nube.activa() and nube.estado()["lista"]:
            nube.subir(archivo, forzar=True)               # internet queda como el respaldo...
            nube.bajar(bd.RUTA_BD)                         # ...y esta computadora tambien
        else:
            with sqlite3.connect(archivo) as origen, sqlite3.connect(bd.RUTA_BD, timeout=60) as destino:
                origen.backup(destino)
    bd.bitacora(quien, "RESPALDO_RESTAURADO", r["fecha"], f"Se regresó el TMS al respaldo del {r['fecha']} ({r['donde']})")


# ---------------------------------------------------------- automatico de madrugada
def _ya_hay_hoy():
    hoy = ahora_mx().strftime("%Y-%m-%d")
    if nube.activa() and nube.estado()["lista"]:
        try:
            nube.ejecutar([(CREAR, ())])
            _, filas = nube.consultar("SELECT COUNT(*) FROM respaldos_nube WHERE fecha LIKE ? AND motivo = 'AUTOMATICO'",
                                      [hoy + "%"])
            return filas[0][0] > 0             # otra computadora ya lo hizo hoy
        except RuntimeError:
            pass
    return (bd.leer_meta("respaldo_automatico") or "") == hoy


def revisar():
    """Si es de madrugada y hoy no se ha respaldado, respalda. Devuelve el texto o None."""
    if not en_madrugada() or _ya_hay_hoy():
        return None
    texto = hacer("AUTOMATICO", "SISTEMA")
    bd.guardar_meta("respaldo_automatico", ahora_mx().strftime("%Y-%m-%d"))
    return texto


def iniciar():
    """Hilo en segundo plano (uno por servidor): revisa cada 10 minutos."""
    def ciclo():
        while True:
            try:
                revisar()
            except Exception:
                pass
            time.sleep(600)
    h = threading.Thread(target=ciclo, name="respaldos", daemon=True)
    h.start()
    return h
