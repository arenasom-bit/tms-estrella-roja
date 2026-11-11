# ==========================================================
#  actualizar_posiciones.py  (OPCIONAL)
#  Deja esta ventana abierta y las posiciones se actualizan
#  solas cada minuto, aunque nadie tenga abierto el TMS.
# ==========================================================
import time

import base_datos as bd
import configuracion as cfg
import omnitracs

bd.crear_tablas()
print("Actualizando posiciones cada", cfg.SEGUNDOS_ENTRE_CONSULTAS, "segundos. Cierra esta ventana para detener.")
while True:
    try:
        n = omnitracs.actualizar_en_bd()
        print(time.strftime("%H:%M:%S"), "-", n, "unidades actualizadas")
    except Exception as e:
        print(time.strftime("%H:%M:%S"), "- ERROR:", e)
    time.sleep(cfg.SEGUNDOS_ENTRE_CONSULTAS)
