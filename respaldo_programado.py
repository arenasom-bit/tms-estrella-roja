# ==========================================================
#  respaldo_programado.py - Respaldo de madrugada SIN depender de que alguien tenga abierta la pagina.
#  Lo corre GitHub (Actions) todos los dias a las 3:15 a.m. hora del centro de Mexico (ver .github/workflows).
#  Baja la base de internet, hace la copia y la guarda en internet (tabla respaldos_nube, maximo 150 dias).
#  Si ese dia ya se hizo el respaldo automatico (desde la pagina o la PC), no hace nada.
#  Tambien se puede correr a mano:  py respaldo_programado.py
# ==========================================================
import sys

import base_datos as bd
import nube
import respaldos

def falla(motivo):
    """El motivo sale en GitHub (Actions > Annotations) sin tener que abrir los registros."""
    print(f"::error::{motivo}")
    sys.exit(1)


if __name__ == "__main__":
    url, token = nube.url(), nube._dato("TURSO_TOKEN")
    if not url:
        falla("El secreto TURSO_URL esta vacio o no existe (revisa el nombre en Settings > Secrets > Actions).")
    if not token:
        falla("El secreto TURSO_TOKEN esta vacio o no existe (revisa el nombre en Settings > Secrets > Actions).")
    if "turso.io" not in url:
        falla("TURSO_URL no parece la direccion de la base (debe ser libsql://...turso.io). "
              "¿Quedo el token en TURSO_URL?")
    if token.count(".") != 2 or len(token) < 100:
        falla(f"TURSO_TOKEN no parece un token (largo {len(token)}). ¿Quedo la direccion en TURSO_TOKEN?")
    try:
        nube.version_remota()
    except Exception as e:
        falla(f"No se pudo conectar con la base de internet: {e}")
    bd.crear_tablas()                      # baja todo lo de internet a este tms.db temporal
    if not nube.estado()["lista"]:
        falla(f"No se pudo bajar la base de internet: {nube.estado()['error']}")
    if respaldos._ya_hay_hoy():
        print("Hoy ya hay respaldo automatico. Nada que hacer.")
    else:
        print(respaldos.hacer("AUTOMATICO", "SISTEMA"))
        bd.guardar_meta("respaldo_automatico", respaldos.ahora_mx().strftime("%Y-%m-%d"))
