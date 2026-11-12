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

if __name__ == "__main__":
    if not nube.activa():
        print("Faltan TURSO_URL y TURSO_TOKEN.")
        sys.exit(1)
    bd.crear_tablas()                      # baja todo lo de internet a este tms.db temporal
    if not nube.estado()["lista"]:
        print("No se pudo bajar la base de internet:", nube.estado()["error"])
        sys.exit(1)
    if respaldos._ya_hay_hoy():
        print("Hoy ya hay respaldo automatico. Nada que hacer.")
    else:
        print(respaldos.hacer("AUTOMATICO", "SISTEMA"))
        bd.guardar_meta("respaldo_automatico", respaldos.ahora_mx().strftime("%Y-%m-%d"))
