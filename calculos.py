# ==========================================================
#  calculos.py - Las "formulas" de tu Excel, ahora en Python
# ==========================================================
import re
import unicodedata

import pandas as pd

import base_datos as bd


def duracion(inicio, fin, ahora):
    """Si hay inicio y fin -> fin-inicio. Si solo hay inicio -> ahora-inicio."""
    fin_efectivo = fin.fillna(ahora)
    return (fin_efectivo - inicio).where(inicio.notna())


def a_texto(serie):
    """Convierte una duracion en texto tipo 5:30 h"""
    def f(td):
        if pd.isna(td):
            return ""
        minutos = int(td.total_seconds() // 60)
        signo = "-" if minutos < 0 else ""
        minutos = abs(minutos)
        return f"{signo}{minutos // 60}:{minutos % 60:02d} h"
    return serie.map(f)


# ---------------------------------------------------------- estadias (tiempo extra segun el cliente)
def _palabras(texto):
    """'Kuehne + Nagel' -> {'KUEHNE', 'NAGEL'} (sin acentos ni simbolos)."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return frozenset(re.sub(r"[^A-Z0-9]+", " ", t).split())


def horas_libres_de(cliente, estadias):
    """Horas libres del cliente, o None si el cliente no esta en la tabla de estadias.
    Empata por palabras: 'GRUPO VIDA' aplica a 'GRUPO INDUSTRIAL VIDA'; 'SAMSUNG' a 'SAMSUNG SDS MEXICO'."""
    p = _palabras(cliente)
    if not p:
        return None
    mejor = None
    for nombre, horas in estadias:
        q = _palabras(nombre)
        if q and q <= p and (mejor is None or len(q) > mejor[0]):
            mejor = (len(q), horas)
    return mejor[1] if mejor else None


def _excedente(llegada, salida, libre_h, ahora):
    """Tiempo que pasa del tiempo libre en UNA parada (en horas, >= 0). Vacio si no hay llegada.
    'ahora' es el tope (por viaje): si el viaje ya registro su salida de descarga, el reloj se detuvo ahi."""
    fin = salida.fillna(ahora)
    horas = (fin - llegada).dt.total_seconds() / 3600
    return (horas - libre_h).clip(lower=0).where(llegada.notna())


def calcular_estadia(df, ahora, estadias=None):
    """Columna ESTADIA: tiempo que excede el tiempo libre del cliente, sumando carga y descarga.
      - cliente que no esta en el catalogo de estadias -> 'N/A'
      - cliente con estadia pero todavia sin hora de llegada -> vacio
      - dentro del tiempo libre -> '0:00 h'
    Si la unidad sigue en la parada (llegada sin salida) cuenta hasta este momento.
    En cuanto se captura la SALIDA DE DESCARGA el conteo se detiene: ya no se genera mas estadia."""
    estadias = bd.leer_estadias() if estadias is None else estadias
    cliente = df["cliente"] if "cliente" in df.columns else pd.Series(None, index=df.index, dtype=object)
    libre = cliente.map(lambda c: horas_libres_de(c, estadias))
    texto = pd.Series("N/A", index=df.index, dtype=object)
    # reloj de cada viaje: hasta ahora, o hasta su salida de descarga si ya la tiene (ahi se detiene todo)
    tope = df["salida_descarga"].fillna(ahora)
    for h in sorted({x for x in libre.dropna().unique()}):
        m = libre == h
        carga = _excedente(df["llegada_carga"], df["salida_carga"], h, tope)
        desc = _excedente(df["llegada_descarga"], df["salida_descarga"], h, tope)
        total = carga.fillna(0) + desc.fillna(0)
        hay_datos = carga.notna() | desc.notna()
        minutos = (total * 60).round()
        t = minutos.map(lambda x: f"{int(x) // 60}:{int(x) % 60:02d} h" if pd.notna(x) else "")
        texto = texto.mask(m, t.where(hay_datos, ""))
    return texto


def calcular_estatus(df):
    u = df["cita_carga"].notna()
    v = df["llegada_carga"].notna()
    w = df["salida_carga"].notna()
    z = df["cita_descarga"].notna()
    aa = df["llegada_descarga"].notna()
    ab = df["salida_descarga"].notna()
    est = pd.Series("", index=df.index, dtype=object)
    est = est.mask(u, "PROGRAMADO")
    est = est.mask(u & v, "CARGANDO")
    est = est.mask(u & v & w, "TRANSITO CARGADO")
    est = est.mask(u & v & w & z & aa, "DESCARGANDO")
    est = est.mask(u & v & w & z & aa & ab, "DISPONIBLE")
    return est


def agregar_calculados(df, ahora=None):
    df = df.copy()
    ahora = ahora or pd.Timestamp.now()
    auto = calcular_estatus(df)
    manual = df["estatus"] if "estatus" in df.columns else pd.Series(None, index=df.index, dtype=object)
    # si alguien escribio un estatus a mano, manda ese; si no, el automatico
    df["estatus"] = manual.where(manual.notna() & (manual.astype(str).str.strip() != ""), auto)
    # ✅ finalizado -> DISPONIBLE (unidad libre) | ❌ cancelado -> CANCELADO (unidad libre)
    # Si desmarcas la casilla, vuelve el estatus normal (manual o automatico).
    if "finalizado" in df.columns:
        df["estatus"] = df["estatus"].mask(df["finalizado"].fillna(False).astype(bool), "DISPONIBLE")
    if "cancelado" in df.columns:
        df["estatus"] = df["estatus"].mask(df["cancelado"].fillna(False).astype(bool), "CANCELADO")
    df["dif"] = df["barras_real"] - df["barras_rep"]
    df["tiempo_carga"] = a_texto(duracion(df["llegada_carga"], df["salida_carga"], ahora))
    df["tiempo_descarga"] = a_texto(duracion(df["llegada_descarga"], df["salida_descarga"], ahora))
    df["tiempo_transito"] = a_texto(duracion(df["inicio_ruta"], df["llegada_destino"], ahora))
    df["t_servicio"] = a_texto(duracion(df["llegada_carga"], df["salida_descarga"], ahora))
    df["estadia"] = calcular_estadia(df, ahora)
    return df
