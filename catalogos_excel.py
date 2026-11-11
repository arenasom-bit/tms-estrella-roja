# ==========================================================
#  catalogos_excel.py - Plantilla de catalogos en Excel
#
#  - generar_plantilla(): crea el Excel (vacio o con tus datos actuales)
#  - cargar_catalogos(): lee un Excel y lo mete al TMS
#    Acepta 2 tipos de archivo:
#      1) la PLANTILLA de catalogos (una hoja por catalogo)
#      2) un REPORTE DE VIAJES (como tu Libro1_1.xlsx): saca de ahi
#         operadores con nomina, unidades, remolques, clientes,
#         tipos de operacion, origenes y destinos.
#  Cargar NUNCA borra nada: solo agrega lo nuevo y completa lo que falte.
#
#  Uso por consola (opcional):  py catalogos_excel.py MiArchivo.xlsx
# ==========================================================
import io
import sys

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

import base_datos as bd
from columnas import CATALOGOS, SITUACIONES

# Otros nombres que aceptan las hojas y los encabezados (ej. el Excel de flota con hojas TRACTO y REMOLQUES:
# ECO | PLACAS | ESTATUS FLOTAS)
HOJAS_ALIAS = {"TRACTO": "VEHICULOS", "TRACTOS": "VEHICULOS", "UNIDADES": "VEHICULOS", "VEHÍCULOS": "VEHICULOS"}
ENCABEZADOS_ALIAS = {
    "unidades": {"ESTATUS FLOTAS": "situacion", "ESTATUS FLOTA": "situacion", "ESTATUS": "situacion",
                 "SITUACIÓN": "situacion", "UNIDAD": "eco"},
    "remolques": {"ECO": "remolque", "CAJA": "remolque", "ESTATUS FLOTAS": "situacion", "ESTATUS FLOTA": "situacion",
                  "ESTATUS": "situacion", "SITUACIÓN": "situacion"},
}
SIN_DATO = {"N/A", "NA", "-", "PTE", "S/P", "SIN PLACAS"}

# nombre de hoja -> (tabla, {columna_bd: encabezado en el Excel})
HOJAS = {
    "VEHICULOS": ("unidades", {"eco": "ECO", "operador": "OPERADOR HABITUAL", "situacion": "SITUACION",
                               "placas": "PLACAS"}),
    "REMOLQUES": ("remolques", {"remolque": "REMOLQUE", "tipo": "TIPO", "placas": "PLACAS", "situacion": "SITUACION"}),
    "OPERADORES": ("operadores", {"nomina": "NOMINA", "nombre": "NOMBRE", "telefono": "TELEFONO"}),
    "CLIENTES": ("clientes", {"nombre": "CLIENTE", "rfc": "RFC"}),
    "ORIGENES": ("origenes", {"nombre": "ORIGEN", "direccion": "DIRECCION", "link": "LINK"}),
    "DESTINOS": ("destinos", {"nombre": "DESTINO", "localidad": "LOCALIDAD",
                              "direccion": "DIRECCION", "link": "LINK"}),
    "TIPOS DE OPERACION": ("tipos_operacion", {"nombre": "TIPO OPERACION", "cliente": "CLIENTE"}),
    "ESTADIAS": ("estadias", {"cliente": "CLIENTE", "aplica": "APLICA ESTADIA", "horas_libres": "TIEMPO LIBRE (H)"}),
}

INSTRUCCIONES = [
    "PLANTILLA DE CATALOGOS DEL TMS",
    "",
    "1. Cada hoja es un catalogo: VEHICULOS, REMOLQUES, OPERADORES, CLIENTES, ORIGENES, DESTINOS, TIPOS DE OPERACION y ESTADIAS (tiempo libre por cliente).",
    "2. NO cambies los encabezados (la fila 1 de cada hoja) ni los nombres de las hojas.",
    "3. La PRIMERA columna de cada hoja es obligatoria y no se repite (ECO, REMOLQUE, NOMINA, CLIENTE, ORIGEN, DESTINO, TIPO OPERACION).",
    "4. Llena una fila por registro. Puedes dejar vacias las demas columnas.",
    "5. En el TMS, pestana Catalogos > 'Cargar catalogos desde Excel', sube este archivo.",
    "6. Cargar NUNCA borra nada: agrega lo nuevo y completa lo que falte. Si un registro ya existe, solo se llenan sus datos vacios o cambiados.",
    "7. Para BORRAR un registro hazlo directo en la tabla del TMS (pestana Catalogos).",
    "8. SITUACION de cada vehiculo y remolque: OPERANDO, CORRALON, SINIESTRO, TALLER o NO DISPONIBLE (si la dejas vacia se toma como OPERANDO; INOPERANDO cuenta como NO DISPONIBLE).",
    "   Tambien se acepta el Excel de flota con hojas TRACTO y REMOLQUES (ECO | PLACAS | ESTATUS FLOTAS).",
    "9. Escribe los nombres EXACTAMENTE como quieres verlos en las listas (mayusculas, sin espacios de mas).",
]


# ---------------------------------------------------------- construir el Excel
def construir_libro(frames):
    """frames: {tabla: DataFrame con columnas de la BD}. Devuelve los bytes del .xlsx"""
    wb = Workbook()
    ws = wb.active
    ws.title = "INSTRUCCIONES"
    for i, linea in enumerate(INSTRUCCIONES, start=1):
        ws.cell(row=i, column=1, value=linea)
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 130

    relleno = PatternFill("solid", fgColor="1F4E78")
    for hoja, (tabla, mapa) in HOJAS.items():
        ws = wb.create_sheet(hoja)
        ws.append(list(mapa.values()))
        for celda in ws[1]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = relleno
            celda.alignment = Alignment(horizontal="center")
        df = frames.get(tabla)
        anchos = [len(t) + 4 for t in mapa.values()]
        if df is not None:
            for fila in df.itertuples(index=False):
                vals = [None if pd.isna(v) else str(v) for v in fila]
                ws.append(vals)
                anchos = [max(a, min(len(v or ""), 60) + 2) for a, v in zip(anchos, vals)]
        for i, a in enumerate(anchos, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = a
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        if tabla == "unidades":  # lista desplegable de situacion
            dv = DataValidation(type="list", formula1='"' + ",".join(SITUACIONES) + '"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add("C2:C2000")
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_plantilla(con_datos=False):
    """con_datos=False -> plantilla vacia. con_datos=True -> con lo que hay hoy en el TMS."""
    frames = {}
    for tabla in CATALOGOS:
        frames[tabla] = bd.leer_catalogo(tabla) if con_datos else pd.DataFrame(columns=CATALOGOS[tabla])
    return construir_libro(frames)


# ---------------------------------------------------------- leer un reporte de viajes
def _txt(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = " ".join(str(v).split())
    return t or None


def _unicos(serie):
    """Valores unicos sin repetir por mayusculas/espacios."""
    vistos = {}
    for v in serie.map(_txt).dropna():
        vistos.setdefault(v.upper(), v)
    return sorted(vistos.values(), key=str.upper)


def _frame(tabla, **columnas):
    cols = CATALOGOS[tabla]
    n = len(next(iter(columnas.values())))
    return pd.DataFrame({c: columnas.get(c, [None] * n) for c in cols})


def catalogos_desde_historico(df):
    """Saca los catalogos de un reporte de viajes (columnas como las de Libro1_1.xlsx)."""
    frames = {}

    ops = df[["Operador", "Operador(nombre_operador)"]].copy()
    ops["nomina"] = ops["Operador"].map(_txt)
    ops["nombre"] = ops["Operador(nombre_operador)"].map(_txt)
    ops = ops.dropna(subset=["nomina", "nombre"]).drop_duplicates("nomina").sort_values("nombre")
    frames["operadores"] = _frame("operadores", nomina=ops["nomina"].tolist(), nombre=ops["nombre"].tolist())

    unidades = _unicos(df["Unidad"])
    frames["unidades"] = _frame("unidades", eco=unidades)

    remolques = _unicos(pd.concat([df.get("Remolque 1", pd.Series(dtype=object)),
                                   df.get("Remolque 2", pd.Series(dtype=object))]))
    frames["remolques"] = _frame("remolques", remolque=remolques)

    frames["clientes"] = _frame("clientes", nombre=_unicos(df["Cliente Paga"]))

    tipos, clientes = [], []
    base = df.assign(_tipo=df["Tipo Operación"].map(_txt), _cli=df["Cliente Paga"].map(_txt))
    for tipo, g in base.dropna(subset=["_tipo"]).groupby("_tipo"):
        frec = g["_cli"].value_counts(normalize=True)
        # solo asignamos cliente si ese tipo de operacion casi siempre es del mismo cliente
        clientes.append(frec.index[0] if len(frec) and frec.iloc[0] >= 0.95 else None)
        tipos.append(tipo)
    frames["tipos_operacion"] = _frame("tipos_operacion", nombre=tipos, cliente=clientes)

    # ORIGEN del plan = Remitente del reporte ; DESTINO del plan = Destinatario
    frames["origenes"] = _frame("origenes", nombre=_unicos(df["Remitente"]))
    frames["destinos"] = _frame("destinos", nombre=_unicos(df["Destinatario"]))
    return frames


# ---------------------------------------------------------- leer cualquier archivo
def leer_archivo(archivo):
    """Devuelve (tipo_de_archivo, {tabla: DataFrame})."""
    xl = pd.ExcelFile(archivo)
    hojas = {}
    for h in xl.sheet_names:
        nombre = h.strip().upper()
        hojas.setdefault(HOJAS_ALIAS.get(nombre, nombre), h)
    frames = {}
    for hoja, (tabla, mapa) in HOJAS.items():
        if hoja not in hojas:
            continue
        df = xl.parse(hojas[hoja], dtype=str)
        df.columns = [" ".join(str(c).split()).upper() for c in df.columns]
        inverso = {v.upper(): k for k, v in mapa.items()}
        inverso.update({k.upper(): k for k in mapa})
        for enc, col in ENCABEZADOS_ALIAS.get(tabla, {}).items():
            if enc not in inverso and col not in df.columns:
                inverso[enc] = col
        df = df.rename(columns=inverso)
        df = df.loc[:, ~df.columns.duplicated()]
        if mapa and list(mapa)[0] in df.columns:
            df = df[[c for c in mapa if c in df.columns]].copy()
            for c in df.columns:                       # textos limpios; "N/A" = sin dato
                df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v))
                                  or " ".join(str(v).split()).upper() in SIN_DATO
                                  else " ".join(str(v).split()))
            llave = list(mapa)[0]
            if llave in ("eco", "remolque"):
                df[llave] = df[llave].map(lambda v: v.upper().removesuffix(".0") if v else v)
            frames[tabla] = df[df[llave].notna()]
    if frames:
        return "plantilla", frames

    df = xl.parse(xl.sheet_names[0])
    if {"Unidad", "Operador(nombre_operador)", "Cliente Paga"} <= set(df.columns):
        return "reporte de viajes", catalogos_desde_historico(df)
    raise ValueError(
        "No reconozco el archivo. Usa la plantilla de catalogos (con sus hojas VEHICULOS, OPERADORES, etc.) "
        "o un reporte de viajes con las columnas Unidad, Operador(nombre_operador) y Cliente Paga.")


ORDEN_CARGA = ["clientes", "operadores", "unidades", "remolques", "tipos_operacion", "origenes", "destinos", "estadias"]


def cargar_catalogos(archivo):
    """Lee el Excel y lo carga. Devuelve (tipo_de_archivo, {tabla: {nuevos, actualizados}})."""
    tipo, frames = leer_archivo(archivo)
    resumen = {}
    for tabla in ORDEN_CARGA:
        if tabla in frames:
            resumen[tabla] = bd.catalogo_upsert(tabla, frames[tabla])
    return tipo, resumen


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: py catalogos_excel.py MiArchivo.xlsx")
    else:
        bd.crear_tablas()
        tipo, res = cargar_catalogos(sys.argv[1])
        print("Archivo reconocido como:", tipo)
        for t, r in res.items():
            print(f"  {t:16s} nuevos: {r['nuevos']:4d}   actualizados: {r['actualizados']:4d}")
