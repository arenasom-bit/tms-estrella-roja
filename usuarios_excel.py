# ==========================================================
#  usuarios_excel.py - Alta masiva de usuarios desde Excel (solo MASTER)
#
#  Columnas: USUARIO | NOMBRE | PERFIL | CONTRASEÑA | ACTIVO
#   - Usuario nuevo  -> se crea (la contraseña es obligatoria, minimo 8 caracteres).
#   - Usuario que ya existe -> se actualizan NOMBRE, PERFIL y ACTIVO; la contraseña SOLO si escribes una.
#   - Los usuarios MASTER nunca se crean ni se cambian desde aqui.
#  Cada cambio queda en la bitacora.
# ==========================================================
import io
import re

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

import base_datos as bd

COLUMNAS = ["USUARIO", "NOMBRE", "PERFIL", "CONTRASEÑA", "ACTIVO"]
AYUDA = [
    "CARGA MASIVA DE USUARIOS DEL TMS (solo usuarios MASTER)",
    "",
    "1. Llena una fila por persona en la hoja USUARIOS. No cambies los encabezados.",
    "2. USUARIO: de 3 a 20 letras o numeros, sin espacios (ej. JPEREZ). No se repite.",
    "3. PERFIL: ATC, OPS, TC o TODOS (solo consulta).",
    "4. CONTRASEÑA: minimo 8 caracteres. Obligatoria para usuarios nuevos.",
    "   Para un usuario que ya existe, dejala vacia si no quieres cambiarsela.",
    "5. ACTIVO: SI o NO (vacio = SI). NO = ya no puede entrar.",
    "6. Sube el archivo en Usuarios y bitacora > Carga masiva de usuarios.",
    "7. Borra este archivo cuando termines: tiene contraseñas.",
]


def _t(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def plantilla(con_actuales=False):
    """Excel para llenar (vacio o con los usuarios actuales, SIN contraseñas)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "USUARIOS"
    ws.append(COLUMNAS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="005DB4")
        c.alignment = Alignment(horizontal="center")
    if con_actuales:
        u = bd.listar_usuarios()
        for _, r in u[u["perfil"] != "MASTER"].iterrows():
            ws.append([r["usuario"], r["nombre"], r["perfil"], "", "SI" if r["activo"] else "NO"])
    for col, ancho in zip("ABCDE", (16, 34, 12, 18, 10)):
        ws.column_dimensions[col].width = ancho
    dv = DataValidation(type="list", formula1='"' + ",".join(bd.PERFILES_CREABLES) + '"', allow_blank=False)
    dv2 = DataValidation(type="list", formula1='"SI,NO"', allow_blank=True)
    ws.add_data_validation(dv)
    ws.add_data_validation(dv2)
    dv.add("C2:C1000")
    dv2.add("E2:E1000")
    ayuda = wb.create_sheet("INSTRUCCIONES")
    for linea in AYUDA:
        ayuda.append([linea])
    ayuda.column_dimensions["A"].width = 100
    ayuda["A1"].font = Font(bold=True, size=13, color="005DB4")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def leer(archivo):
    xl = pd.ExcelFile(archivo)
    hoja = next((h for h in xl.sheet_names if h.strip().upper() == "USUARIOS"), xl.sheet_names[0])
    df = xl.parse(hoja, dtype=str)
    df.columns = [" ".join(str(c).split()).upper().replace("CONTRASENA", "CONTRASEÑA") for c in df.columns]
    if "USUARIO" not in df.columns:
        raise ValueError("No encuentro la columna USUARIO. Usa la plantilla de carga masiva.")
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = None
    return df[COLUMNAS]


def revisar(df):
    """Que pasaria con cada fila, sin guardar nada: lista de (fila_excel, usuario, accion, detalle, datos|None)."""
    actuales = bd.listar_usuarios().set_index("usuario")
    vistos, salida = set(), []
    for i, r in df.iterrows():
        fila = i + 2
        usuario = _t(r["USUARIO"]).upper()
        if not usuario and not any(_t(r[c]) for c in COLUMNAS):
            continue
        nombre, perfil, pwd = _t(r["NOMBRE"]), _t(r["PERFIL"]).upper(), _t(r["CONTRASEÑA"])
        activo = _t(r["ACTIVO"]).upper() not in ("NO", "0", "FALSE")
        problema = None
        if not re.fullmatch(r"[A-Z0-9_.]{3,20}", usuario):
            problema = "USUARIO: de 3 a 20 letras o números, sin espacios"
        elif usuario in vistos:
            problema = "el usuario está repetido en el Excel"
        elif usuario in actuales.index and actuales.loc[usuario, "perfil"] == "MASTER":
            problema = "es MASTER: no se cambia desde aquí"
        elif perfil not in bd.PERFILES_CREABLES:
            problema = f"PERFIL debe ser {', '.join(bd.PERFILES_CREABLES)}"
        elif usuario not in actuales.index and len(pwd) < 8:
            problema = "usuario nuevo: falta CONTRASEÑA de al menos 8 caracteres"
        elif pwd and len(pwd) < 8:
            problema = "la CONTRASEÑA debe tener al menos 8 caracteres"
        elif usuario not in actuales.index and not nombre:
            problema = "falta NOMBRE"
        vistos.add(usuario)
        if problema:
            salida.append((fila, usuario, "❌ NO SE CARGA", problema, None))
            continue
        datos = {"usuario": usuario, "nombre": nombre, "perfil": perfil, "pwd": pwd, "activo": activo}
        if usuario not in actuales.index:
            salida.append((fila, usuario, "➕ NUEVO", f"{nombre} · {perfil}" + ("" if activo else " · desactivado"), datos))
            continue
        a = actuales.loc[usuario]
        cambios = []
        if nombre and nombre != a["nombre"]:
            cambios.append(f"nombre → {nombre}")
        if perfil != a["perfil"]:
            cambios.append(f"perfil {a['perfil']} → {perfil}")
        if activo != bool(a["activo"]):
            cambios.append("se activa" if activo else "se desactiva")
        if pwd:
            cambios.append("contraseña nueva")
        salida.append((fila, usuario, "✏️ SE ACTUALIZA" if cambios else "= SIN CAMBIOS", ", ".join(cambios), datos))
    return salida


def cargar(df, quien):
    """Aplica la carga. Solo MASTER. Devuelve (nuevos, actualizados, con_error, detalle)."""
    if not isinstance(quien, dict) or quien.get("perfil") != "MASTER":
        raise PermissionError("Solo un usuario MASTER puede cargar usuarios.")
    detalle = revisar(df)
    nuevos = actualizados = errores = 0
    with bd.conexion() as con:
        for fila, usuario, accion, texto, d in detalle:
            if d is None:
                errores += 1
                continue
            if accion.startswith("➕"):
                con.execute("INSERT INTO tms_usuarios (usuario, nombre, perfil, pwd_hash, activo, creado, creado_por) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [usuario, d["nombre"], d["perfil"], bd.hash_pwd(d["pwd"]), 1 if d["activo"] else 0,
                             bd.AHORA_TXT(), quien["usuario"]])
                nuevos += 1
            elif accion.startswith("✏️"):
                sets, vals = ["perfil = ?", "activo = ?"], [d["perfil"], 1 if d["activo"] else 0]
                if d["nombre"]:
                    sets.append("nombre = ?")
                    vals.append(d["nombre"])
                if d["pwd"]:
                    sets.append("pwd_hash = ?")
                    vals.append(bd.hash_pwd(d["pwd"]))
                con.execute(f"UPDATE tms_usuarios SET {', '.join(sets)} WHERE usuario = ? AND perfil <> 'MASTER'",
                            vals + [usuario])
                actualizados += 1
    for fila, usuario, accion, texto, d in detalle:
        if d is not None and not accion.startswith("="):
            bd.bitacora(quien, "USUARIO_CARGA_MASIVA", usuario, f"{accion} {texto}".strip())
    return nuevos, actualizados, errores, detalle
