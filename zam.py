# ==========================================================
#  zam.py - Lee la vista de viajes abiertos de ZAM (ConsultaVista)
#  y actualiza el catalogo del TMS.
#
#  Columnas de la vista -> uso en el TMS
#    shipment_id            -> numero de viaje ZAM
#    status                 -> 'open' = viaje abierto (unidad y operador ACTIVOS)
#    driver_id              -> NOMINA del operador
#    assets_id              -> PLACAS del tracto
#    id_unidad              -> ECO
#    stops1_* / stops2_*    -> origen / destino (ids de ubicacion) y horarios planeados
#
#  Uso por consola (opcional):  py zam.py
# ==========================================================
import threading
import time
from datetime import datetime
from html.parser import HTMLParser

import requests

import base_datos as bd
import conciliacion
import configuracion as cfg

# columna de la vista -> columna del TMS
MAPA = {
    "shipment_id": "shipment_id",
    "org_id": "org_id",
    "status": "status",
    "driver_id": "nomina",
    "assets_id": "placas",
    "id_unidad": "eco",
    "stops1_location_id": "origen_id",
    "stops1_planned_departure": "salida_plan",
    "stops2_location_id": "destino_id",
    "stops2_planned_arrival": "llegada_plan",
}
OBLIGATORIAS = {"shipment_id", "status", "driver_id", "id_unidad"}


class _LectorTabla(HTMLParser):
    """Junta el texto de cada celda de la primera tabla de la pagina."""

    def __init__(self):
        super().__init__()
        self.filas, self._fila, self._celda = [], None, None
        self._tablas = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._tablas += 1
        elif self._tablas == 1 and tag == "tr":
            self._fila = []
        elif self._tablas == 1 and tag in ("td", "th") and self._fila is not None:
            self._celda = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._celda is not None:
            self._fila.append(" ".join("".join(self._celda).split()))
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            if any(self._fila):
                self.filas.append(self._fila)
            self._fila = None

    def handle_data(self, data):
        if self._celda is not None:
            self._celda.append(data)


def leer_html(html):
    """Convierte el HTML de la vista en una lista de viajes con columnas del TMS."""
    lector = _LectorTabla()
    lector.feed(html)
    if not lector.filas:
        raise RuntimeError("La vista no trae ninguna tabla. Revisa que la página abra en el navegador.")
    encabezados = [h.strip().lower() for h in lector.filas[0]]
    faltan = OBLIGATORIAS - set(encabezados)
    if faltan:
        raise RuntimeError("La vista cambió de formato; faltan las columnas: " + ", ".join(sorted(faltan)))
    viajes = []
    for fila in lector.filas[1:]:
        crudo = dict(zip(encabezados, fila))
        v = {MAPA[k]: (crudo.get(k) or "").strip() or None for k in MAPA}
        if v["status"]:
            v["status"] = v["status"].lower()
        for k in ("salida_plan", "llegada_plan"):       # 2026-10-29T13:00:00-06:00 -> 2026-10-29 13:00:00
            if v[k]:
                v[k] = v[k][:19].replace("T", " ")
        if v["shipment_id"]:
            viajes.append(v)
    return viajes


def consultar_vista():
    resp = requests.get(cfg.ZAM_URL, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"La vista ZAM respondió con error HTTP {resp.status_code}")
    return leer_html(resp.text)


def actualizar_catalogo():
    """Consulta la vista y actualiza el TMS. Devuelve el resumen; guarda el error si falla."""
    try:
        viajes = consultar_vista()
        bd.guardar_zam_todos(viajes)                      # todos los estatus (para detectar cierres/cancelaciones)
        abiertos = [v for v in viajes if v["status"] == "open"]
        res = bd.sincronizar_zam(abiertos)
        cerrados = conciliacion.aplicar_cierres()         # pedidos que ZAM ya finalizo o cancelo
        for id_v, ped, nuevo in cerrados:
            bd.bitacora("SISTEMA", "ZAM_CIERRE_AUTOMATICO", f"viaje #{id_v} · pedido {ped}", f"ZAM lo reporta {nuevo}")
        if cerrados:
            bd.guardar_meta("zam_ultimo_resumen", (bd.leer_meta("zam_ultimo_resumen") or "")
                            + f" · {len(cerrados)} viajes cerrados según ZAM")
        return res
    except Exception as e:
        bd.guardar_meta("zam_ultimo_error", f"{datetime.now():%d/%m/%Y %H:%M:%S} - {e}")
        raise


# ---------------------------------------------------------- actualizacion automatica
_candado = threading.Lock()


def actualizar_ahora():
    """Actualizacion a solicitud (boton). Usa el mismo candado que la automatica
    para que nunca corran dos al mismo tiempo."""
    with _candado:
        return actualizar_catalogo()


def _ciclo():
    """Corre en segundo plano dentro del servidor del TMS: revisa cada 20 segundos
    y actualiza cuando ya pasaron MINUTOS_ENTRE_SYNC_ZAM desde la ultima vez."""
    while True:
        try:
            if bd.segundos_desde_sync_zam() >= cfg.MINUTOS_ENTRE_SYNC_ZAM * 60 - 5:
                with _candado:
                    actualizar_catalogo()
        except Exception:
            pass  # el error queda guardado en meta y se muestra en pantalla
        time.sleep(20)


def iniciar_actualizacion_automatica():
    """Arranca el hilo una sola vez por proceso."""
    hilo = threading.Thread(target=_ciclo, name="sync_zam", daemon=True)
    hilo.start()
    return hilo


if __name__ == "__main__":
    bd.crear_tablas()
    print(f"Actualizando catálogo desde ZAM cada {cfg.MINUTOS_ENTRE_SYNC_ZAM} minutos. "
          "Cierra esta ventana para detener.")
    while True:
        try:
            r = actualizar_catalogo()
            print(time.strftime("%H:%M:%S"), "-", bd.leer_meta("zam_ultimo_resumen"))
        except Exception as e:
            print(time.strftime("%H:%M:%S"), "- ERROR:", e)
        time.sleep(cfg.MINUTOS_ENTRE_SYNC_ZAM * 60)
