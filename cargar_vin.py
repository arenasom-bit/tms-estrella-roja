# =====================================================================
#  cargar_vin.py - Pone el VIN de tractos y remolques desde el Excel de flota
#                  (hojas TRACTO y REMOLQUES con columnas ECO | ... | VIN)
#
#  SOLO llena/actualiza el VIN: no cambia placas, situacion ni nada mas,
#  y no da de alta unidades nuevas.
#
#  Uso:   py cargar_vin.py UNIDADES.xlsx
# =====================================================================
import sys

import base_datos as bd
import catalogos_excel


def cargar_vin(archivo):
    bd.crear_tablas()                        # crea la columna VIN si todavia no existe
    _, frames = catalogos_excel.leer_archivo(archivo)
    resumen = {}
    for tabla, llave in (("unidades", "eco"), ("remolques", "remolque")):
        df = frames.get(tabla)
        if df is None or "vin" not in df.columns:
            continue
        cambiados = sin_unidad = 0
        with bd.conexion() as con:
            existentes = {str(r[0]).strip().upper(): r[0] for r in con.execute(f"SELECT {llave} FROM {tabla}")}
            for clave, vin in zip(df[llave], df["vin"]):
                if not clave or not vin:
                    continue
                real = existentes.get(str(clave).strip().upper())
                if real is None:
                    sin_unidad += 1
                    continue
                vin = str(vin).strip().upper()
                cambiados += con.execute(f"UPDATE {tabla} SET vin = ? WHERE {llave} = ? AND IFNULL(vin, '') <> ?",
                                         [vin, real, vin]).rowcount
        resumen[tabla] = {"vin_actualizados": cambiados, "no_estan_en_catalogo": sin_unidad}
    bd.bitacora("SISTEMA", "CATALOGO_VIN", archivo, str(resumen))
    return resumen


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: py cargar_vin.py UNIDADES.xlsx")
        sys.exit(1)
    for tabla, r in cargar_vin(sys.argv[1]).items():
        print(f"  {tabla:10s} VIN actualizados: {r['vin_actualizados']:4d}   no estan en el catalogo: {r['no_estan_en_catalogo']}")
