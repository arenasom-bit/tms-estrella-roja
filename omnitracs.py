# ==========================================================
#  omnitracs.py - Le pregunta a Omnitracs donde estan los camiones
#  (es lo mismo que hacia tu script de Google, pero en Python)
# ==========================================================
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

import base_datos as bd
import configuracion as cfg

ZONA_MX = timezone(timedelta(hours=-6))  # GMT-6


def credenciales_configuradas():
    return not any(
        str(x).startswith("ESCRIBE_AQUI")
        for x in (cfg.OMNITRACS_USUARIO, cfg.OMNITRACS_CONTRASENA, cfg.OMNITRACS_CLIENTE)
    )


def _nombre(tag):
    return tag.split("}")[-1]


def _texto_hijo(nodo, nombre):
    for h in nodo:
        if _nombre(h.tag) == nombre:
            return (h.text or "").strip()
    return ""


def _hijos(nodo, nombre):
    return [h for h in nodo if _nombre(h.tag) == nombre]


def construir_xml(inicio, fin):
    f = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:ns2="http://sei.rest.services.client.maya.omnitracs.mx/">
  <soap:Header/>
  <soap:Body>
    <ns2:getPositions>
      <request>
        <clientCode>{cfg.OMNITRACS_CLIENTE}</clientCode>
        <endDate>{f(fin)}</endDate>
        <password>{cfg.OMNITRACS_CONTRASENA}</password>
        <startDate>{f(inicio)}</startDate>
        <userName>{cfg.OMNITRACS_USUARIO}</userName>
      </request>
    </ns2:getPositions>
  </soap:Body>
</soap:Envelope>"""


def _numero(texto):
    try:
        return float(str(texto).replace(",", ".").strip())
    except ValueError:
        return None


def leer_respuesta(xml_texto):
    """Convierte la respuesta XML en una lista: una (la mas reciente) posicion por unidad."""
    if "faultstring" in xml_texto or "soap:Fault" in xml_texto:
        raise RuntimeError("Omnitracs devolvio un error. Revisa usuario, contrasena y codigo de cliente.")
    raiz = ET.fromstring(xml_texto)
    ultimas = {}
    for nodo in raiz.iter():
        if _nombre(nodo.tag) != "vehicles":
            continue
        alias = _texto_hijo(nodo, "alias")
        if not alias:
            continue
        for pos in _hijos(nodo, "positions"):
            crudo = _texto_hijo(pos, "date")
            try:
                fecha = datetime.strptime(crudo[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
                fecha += timedelta(hours=cfg.AJUSTE_HORAS_RESPUESTA)
            except ValueError:
                continue
            ign = (_texto_hijo(pos, "ignitionOn") or _texto_hijo(pos, "ignition")).lower() == "true"
            registro = {
                "alias": alias,
                "fecha": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                "_dt": fecha,
                "ignicion": "Encendido" if ign else "Apagado",
                "velocidad": _numero(_texto_hijo(pos, "speed")) or 0.0,
                "latitud": _numero(_texto_hijo(pos, "latitude")),
                "longitud": _numero(_texto_hijo(pos, "longitude")),
                # si el command manda la ubicacion en texto (colonia, municipio, estado), se usa tal cual
                "ubicacion": next((_texto_hijo(pos, t) for t in ("location", "address", "description", "reference",
                                                               "geoReference", "place", "landmark")
                                   if _texto_hijo(pos, t)), ""),
            }
            if alias not in ultimas or fecha > ultimas[alias]["_dt"]:
                ultimas[alias] = registro
    return list(ultimas.values())


def consultar_posiciones():
    fin = datetime.now(ZONA_MX)
    inicio = fin - timedelta(minutes=cfg.MINUTOS_HISTORIAL)
    resp = requests.post(
        cfg.OMNITRACS_URL,
        data=construir_xml(inicio, fin).encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"getPositions"'},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Omnitracs respondio con error HTTP {resp.status_code}")
    return leer_respuesta(resp.text)


def actualizar_en_bd():
    """Consulta a Omnitracs y guarda. Devuelve cuantas unidades se actualizaron."""
    try:
        lista = consultar_posiciones()
        bd.guardar_posiciones(lista)
        bd.guardar_meta("ultimo_error", "")
        return len(lista)
    except Exception as e:
        bd.guardar_meta("ultimo_error", f"{datetime.now():%H:%M:%S} - {e}")
        raise


def normalizar_alias(texto):
    return re.sub(r"[^A-Z0-9]", "", str(texto).upper())


def clave_eco(alias, ecos_normalizados):
    """Empata el alias de Omnitracs con un ECO del TMS.
    Prueba exacto y, si no, sin letras al final (2105C -> 2105, 8048R -> 8048).
    Devuelve el ECO normalizado encontrado o \"\" si no hay."""
    k = normalizar_alias(alias)
    if k in ecos_normalizados:
        return k
    k2 = re.sub(r"[A-Z]+$", "", k)
    return k2 if k2 and k2 in ecos_normalizados else ""


def clasificar_alias(alias, tractos_norm, remolques_norm):
    """Clasifica un alias de Omnitracs segun tus catalogos.
    Devuelve (tipo, clave):
      - "REMOLQUE": el alias es exactamente un remolque del catalogo (ej. 2105C)
      - "TRACTO"  : es un ECO del catalogo de vehiculos (exacto, o sin letras al final: 8048R -> 8048)
      - "FUERA"   : no esta en ningun catalogo
    El remolque exacto se revisa primero porque hay remolques tipo 2105C cuyo tracto es el 2105."""
    k = normalizar_alias(alias)
    if k in remolques_norm:
        return "REMOLQUE", k
    c = clave_eco(alias, tractos_norm)
    if c:
        return "TRACTO", c
    return "FUERA", ""


def en_movimiento(velocidad, minimo=None):
    """Una unidad se cuenta en movimiento si su velocidad es mayor al minimo (aunque la ignicion diga Apagado)."""
    minimo = getattr(cfg, "VELOCIDAD_MINIMA_MOVIMIENTO", 1) if minimo is None else minimo
    try:
        return float(velocidad) >= minimo
    except (TypeError, ValueError):
        return False
