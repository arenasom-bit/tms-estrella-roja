# =====================================================================
#  cargar_telefonos.py - Teléfonos de operadores desde un CSV (nomina, nombre, telefono)
#
#  - Si el operador ya existe (misma NÓMINA) y NO tiene teléfono: se le pone.
#  - Si no existe: se da de alta con nómina, nombre y teléfono.
#  - Nunca borra ni cambia un teléfono que ya estaba capturado.
#
#  Uso:   py cargar_telefonos.py operadores_telefonos.csv
#  (el CSV tiene datos personales: NO se sube a GitHub)
# =====================================================================
import csv
import sys

import base_datos as bd


def _k(v):
    v = str(v or "").strip()
    return v[:-2] if v.endswith(".0") else v


def leer_csv(ruta):
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return [(_k(r["nomina"]), str(r["nombre"]).strip().upper(), str(r["telefono"]).strip())
                for r in csv.DictReader(f) if _k(r.get("nomina")) and str(r.get("telefono") or "").strip()]


def cargar(filas, quien="SISTEMA"):
    bd.crear_tablas()
    llenados = nuevos = 0
    with bd.conexion() as con:
        for nomina, nombre, tel in filas:
            fila = con.execute("SELECT telefono FROM operadores WHERE nomina = ?", [nomina]).fetchone()
            if fila is None:
                nuevos += con.execute("INSERT OR IGNORE INTO operadores (nomina, nombre, telefono) VALUES (?, ?, ?)",
                                      [nomina, nombre, tel]).rowcount
            elif not (fila[0] or "").strip():
                llenados += con.execute("UPDATE operadores SET telefono = ? WHERE nomina = ? "
                                        "AND COALESCE(TRIM(telefono), '') = ''", [tel, nomina]).rowcount
    bd.bitacora(quien, "CATALOGO_TELEFONOS", "operadores", f"{llenados} teléfonos llenados · {nuevos} operadores nuevos")
    return llenados, nuevos


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: py cargar_telefonos.py operadores_telefonos.csv")
        sys.exit(1)
    l, n = cargar(leer_csv(sys.argv[1]))
    print(f"Listo: {l} teléfonos llenados · {n} operadores nuevos")
