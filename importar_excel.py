# ==========================================================
#  importar_excel.py  (se usa UNA sola vez, solo si tienes un plan en Excel)
#  Pasa los viajes de tu plan de carga en Excel al TMS.
#  Ahora busca cada columna por su NOMBRE (fila 2), no por su posicion.
#  Uso:  py importar_excel.py Libro1.xlsx
# ==========================================================
import sys
from datetime import date, datetime

from openpyxl import load_workbook

import base_datos as bd
from columnas import COLUMNAS


def limpiar(valor, tipo):
    if valor is None or (isinstance(valor, str) and (valor.strip() == "" or valor.startswith("="))):
        return None
    if tipo == "fecha":
        return valor if isinstance(valor, datetime) else None
    if tipo in ("entero", "dinero"):
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


def main(ruta):
    bd.crear_tablas()
    if bd.hay_viajes():
        r = input("Ya hay viajes en el TMS. ¿Importar de todos modos? (escribe SI): ")
        if r.strip().upper() != "SI":
            print("Cancelado.")
            return
    ws = load_workbook(ruta).worksheets[0]

    # posicion de cada columna segun el encabezado de la fila 2 (si se repite, vale la primera)
    posicion = {}
    for i, celda in enumerate(ws[2]):
        if celda.value is not None:
            posicion.setdefault(str(celda.value).strip().upper(), i)

    importados = 0
    for fila in ws.iter_rows(min_row=3, values_only=True):
        valores = {}
        for c in COLUMNAS:
            i = posicion.get(c.titulo.upper())
            if c.tipo == "calculado" or i is None or i >= len(fila):
                continue
            v = limpiar(fila[i], c.tipo)
            if isinstance(v, str) and "\t" in v:      # direccion pegada con su link
                v = v.split("\t")[0].strip()
            if v is not None:
                valores[c.clave] = v
        if not valores.get("eco") and not valores.get("operador"):
            continue
        cita = valores.get("cita_carga")
        dia = cita.date() if cita else date.today()
        bd.insertar_viaje(valores, dia)
        bd.catalogo_agregar("unidades", {"eco": valores.get("eco"), "operador": valores.get("operador")})
        bd.catalogo_agregar("remolques", {"remolque": valores.get("remolque_1")})
        bd.catalogo_agregar("origenes", {"nombre": valores.get("origen"),
                                         "direccion": valores.get("direccion_origen"),
                                         "link": valores.get("link_origen")})
        dd = valores.get("direccion_destino")
        ld = valores.get("link_destino")
        bd.catalogo_agregar("destinos", {"nombre": valores.get("destino"),
                                         "localidad": valores.get("localidad_destino"),
                                         "direccion": None if dd == "PTE" else dd,
                                         "link": None if ld == "PTE" else ld})
        importados += 1
    print(f"Listo: {importados} viajes importados.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: py importar_excel.py NombreDeTuArchivo.xlsx")
    else:
        main(sys.argv[1])
