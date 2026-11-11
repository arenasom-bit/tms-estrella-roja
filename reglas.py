# ==========================================================
#  reglas.py - Reglas para ASIGNAR un viaje
#
#  No se puede asignar un viaje cuando:
#   1) La unidad (ECO) no esta en SITUACION = OPERANDO en el catalogo
#      (o no existe en el catalogo).
#   2) La unidad, el operador o el remolque siguen ACTIVOS:
#        - tienen otro viaje en el TMS sin ✅ finalizar ni ❌ cancelar, o
#        - (unidad / operador) tienen un viaje 'open' en la vista ZAM.
#  Para liberarlos: marca ✅ o ❌ en su viaje anterior y guarda.
# ==========================================================
import pandas as pd

import base_datos as bd
import calculos
import conciliacion
from columnas import ESTATUS_CERRADOS


def _k(valor):
    """Llave para comparar: texto sin espacios de mas y en mayusculas. Vacio -> ''."""
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return " ".join(str(valor).split()).upper()


def _es_si(valor):
    return _k(valor) in ("SI", "TRUE", "1")


class Contexto:
    """Foto de la flota al momento de validar (catalogos + viajes abiertos)."""

    def __init__(self):
        uni = bd.leer_catalogo("unidades")
        self.situacion = {_k(e): (_k(s) or "OPERANDO") for e, s in zip(uni["eco"], uni["situacion"])}
        self.operador_habitual = {_k(e): o for e, o in zip(uni["eco"], uni["operador"]) if _k(o)}
        ops = bd.leer_catalogo("operadores")
        self.nomina_de = {_k(n): _k(m) for n, m in zip(ops["nombre"], ops["nomina"]) if _k(n)}
        self.nombre_de = {_k(m): n for m, n in zip(ops["nomina"], ops["nombre"]) if _k(m) and _k(n)}

        # viajes del TMS que siguen abiertos (todas las fechas)
        todos = calculos.agregar_calculados(bd.leer_viajes("0000-01-01", "9999-12-31"))
        self.abiertos = todos[~todos["estatus"].isin(ESTATUS_CERRADOS)].copy()

        # viajes 'open' de la vista ZAM
        self.zam = bd.leer_zam()

    # ---------------------------------------------------- quien ocupa que
    def _viaje_tms(self, columna, llave, excluir_id):
        if not llave:
            return None
        d = self.abiertos
        if excluir_id is not None:
            d = d[d["id"] != excluir_id]
        m = d[d[columna].map(_k) == llave]
        return m.iloc[0] if len(m) else None

    def _viaje_tms_operador(self, nombre, nomina, excluir_id):
        d = self.abiertos
        if excluir_id is not None:
            d = d[d["id"] != excluir_id]
        mask = pd.Series(False, index=d.index)
        if nombre:
            mask |= d["operador"].map(_k) == nombre
        if nomina:
            mask |= d["nomina"].map(_k) == nomina
        m = d[mask]
        return m.iloc[0] if len(m) else None

    def _viaje_zam(self, columna, llave, plan=None, nomina_plan=None):
        """Viaje ZAM abierto que ocupa esa unidad/nomina. Si se pasa 'plan' (el viaje que se quiere guardar),
        se ignora el viaje ZAM que es ESE MISMO viaje (mismo pedido, o misma unidad y horas parecidas)."""
        if not llave or self.zam.empty:
            return None
        for _, z in self.zam[self.zam[columna].map(_k) == llave].iterrows():
            if plan is not None and conciliacion.coincide_viaje(plan, z, nomina_plan):
                continue
            return z
        return None

    # ---------------------------------------------------- disponibles para listas
    def eco_asignable(self, eco):
        k = _k(eco)
        return (self.situacion.get(k) == "OPERANDO" and self._viaje_tms("eco", k, None) is None
                and self._viaje_zam("eco", k) is None)

    def operador_asignable(self, nombre):
        k = _k(nombre)
        nomina = self.nomina_de.get(k, "")
        return (self._viaje_tms_operador(k, nomina, None) is None
                and self._viaje_zam("nomina", nomina) is None)

    def remolque_asignable(self, remolque):
        return self._viaje_tms("remolque_1", _k(remolque), None) is None

    def ecos_activos_zam(self):
        return set(self.zam["eco"].map(_k)) - {""} if not self.zam.empty else set()


def _desc_tms(v):
    return f"viaje #{int(v['id'])} del {pd.to_datetime(v['fecha_plan']):%d/%m/%Y} (estatus {v['estatus'] or 'sin estatus'})"


def _desc_zam(v):
    return f"viaje ZAM {v['shipment_id']} abierto"


def validar(propuestas, ctx=None):
    """propuestas: lista de dicts con id (None si es nuevo), etiqueta, eco, operador, nomina, remolque_1.
    Solo deben venir viajes que van a quedar ABIERTOS y cuya asignacion cambio.
    Devuelve una lista de mensajes de error (vacia = se puede guardar)."""
    ctx = ctx or Contexto()
    errores = []
    usados = {"eco": {}, "operador": {}, "remolque": {}}   # para choques dentro del mismo guardado

    for p in propuestas:
        etq = p["etiqueta"]
        id_ = p.get("id")
        eco = _k(p.get("eco"))
        remolque = _k(p.get("remolque_1"))
        nombre = _k(p.get("operador")) or _k(ctx.operador_habitual.get(eco))   # vacio -> el habitual
        nomina = _k(p.get("nomina")) or ctx.nomina_de.get(nombre, "")

        if eco:
            nomina_p = _k(p.get("nomina")) or ctx.nomina_de.get(_k(p.get("operador")) or _k(ctx.operador_habitual.get(eco)), "")
            sit = ctx.situacion.get(eco)
            if sit is None:
                errores.append(f"{etq}: la unidad {p['eco']} no está en el catálogo de vehículos, "
                               "no se puede confirmar que esté OPERANDO.")
            elif sit != "OPERANDO":
                errores.append(f"{etq}: la unidad {p['eco']} está en situación {sit}; "
                               "solo se asignan unidades OPERANDO.")
            v = ctx._viaje_tms("eco", eco, id_)
            if v is not None:
                errores.append(f"{etq}: la unidad {p['eco']} sigue activa en el {_desc_tms(v)}. "
                               "Finalízalo ✅ o cancélalo ❌ primero.")
            z = ctx._viaje_zam("eco", eco, p, nomina_p)
            if z is not None:
                errores.append(f"{etq}: la unidad {p['eco']} tiene el {_desc_zam(z)}.")
            if eco in usados["eco"]:
                errores.append(f"{etq}: la unidad {p['eco']} también va en {usados['eco'][eco]}.")
            usados["eco"][eco] = etq

        if nombre or nomina:
            mostrar = p.get("operador") or ctx.operador_habitual.get(eco) or nomina
            v = ctx._viaje_tms_operador(nombre, nomina, id_)
            if v is not None:
                errores.append(f"{etq}: el operador {mostrar} sigue activo en el {_desc_tms(v)}. "
                               "Finalízalo ✅ o cancélalo ❌ primero.")
            z = ctx._viaje_zam("nomina", nomina, p, nomina)
            if z is not None:
                errores.append(f"{etq}: el operador {mostrar} (nómina {nomina}) tiene el {_desc_zam(z)}.")
            llave = nomina or nombre
            if llave in usados["operador"]:
                errores.append(f"{etq}: el operador {mostrar} también va en {usados['operador'][llave]}.")
            usados["operador"][llave] = etq

        if remolque:
            v = ctx._viaje_tms("remolque_1", remolque, id_)
            if v is not None:
                errores.append(f"{etq}: el remolque {p['remolque_1']} sigue activo en el {_desc_tms(v)}. "
                               "Finalízalo ✅ o cancélalo ❌ primero.")
            if remolque in usados["remolque"]:
                errores.append(f"{etq}: el remolque {p['remolque_1']} también va en {usados['remolque'][remolque]}.")
            usados["remolque"][remolque] = etq
    return errores


def _estatus_tras_guardar(editado, original):
    """Calcula el estatus que tendra cada fila del editor DESPUES de guardar
    (misma logica que calculos.agregar_calculados). El estatus que muestra el editor
    ya viene calculado, asi que si nadie lo cambio se usa el valor manual guardado en la BD."""
    df = editado.copy()
    ids = [int(i) for i in df["id"].dropna()] if "id" in df.columns else []
    manual_bd = bd.estatus_manual(ids)
    orig = original.set_index("id")["estatus"] if len(original) and "estatus" in original.columns else {}
    nuevos = []
    for _, fila in df.iterrows():
        id_ = None if pd.isna(fila.get("id")) else int(fila["id"])
        en_editor = fila.get("estatus")
        if id_ is not None and id_ in orig and _k(en_editor) == _k(orig[id_]):
            nuevos.append(manual_bd.get(id_))      # no lo tocaron: vale lo guardado
        else:
            nuevos.append(en_editor)               # lo cambiaron a mano (o es fila nueva)
    df["estatus"] = nuevos
    for c in ("finalizado", "cancelado"):
        if c in df.columns:
            df[c] = df[c].map(_es_si)
    return calculos.agregar_calculados(bd.preparar_tipos(df))["estatus"]


def validar_editor(original, editado):
    """Valida el guardado del 'Plan del dia' tomando en cuenta lo que quedara DESPUES de guardar:
    si en el mismo guardado finalizas/cancelas/borras un viaje, sus recursos ya cuentan como libres."""
    if editado.empty:
        return []
    estatus_final = _estatus_tras_guardar(editado, original)
    ctx = Contexto()
    ids_pantalla = {int(i) for i in original["id"].dropna()} if len(original) else set()
    ids_pantalla |= {int(i) for i in editado["id"].dropna()}
    # viajes de la BD que no estan en pantalla siguen igual
    otros = ctx.abiertos[~ctx.abiertos["id"].isin(ids_pantalla)]
    # viajes en pantalla (ya existentes) que quedaran abiertos, con sus valores editados
    en_pantalla = editado[editado["id"].notna()].copy()
    en_pantalla["estatus"] = estatus_final.loc[en_pantalla.index]
    en_pantalla = en_pantalla[~en_pantalla["estatus"].map(_k).isin(ESTATUS_CERRADOS)]
    if "fecha_plan" in en_pantalla.columns:
        en_pantalla["fecha_plan"] = en_pantalla["fecha_plan"].fillna(str(pd.Timestamp.today().date()))
    cols = ["id", "fecha_plan", "estatus", "eco", "operador", "nomina", "remolque_1"]
    ctx.abiertos = pd.concat([otros[cols], en_pantalla[[c for c in cols if c in en_pantalla.columns]]],
                             ignore_index=True)
    return validar(propuestas_desde_editor(original, editado, estatus_final), ctx)


def propuestas_desde_editor(original, editado, estatus_final=None):
    """Del editor del plan saca los viajes a validar: nuevos, con ECO/operador/remolque
    cambiado, o reabiertos (estaban cerrados y ya no). Solo los que quedan ABIERTOS."""
    if editado.empty:
        return []
    if estatus_final is None:
        estatus_final = _estatus_tras_guardar(editado, original)
    orig = original.set_index("id") if len(original) else None
    campos = ["eco", "operador", "nomina", "remolque_1"]
    salida = []
    for n, (idx, fila) in enumerate(editado.iterrows(), start=1):
        g = lambda c: fila[c] if c in fila.index else None
        if _k(estatus_final.loc[idx]) in ESTATUS_CERRADOS:
            continue
        id_ = None if pd.isna(fila.get("id")) else int(fila["id"])
        if id_ is not None and orig is not None and id_ in orig.index:
            o = orig.loc[id_]
            cambio = any(_k(g(c)) != _k(o.get(c)) for c in campos if c in fila.index)
            reabierto = _k(o.get("estatus")) in ESTATUS_CERRADOS
            if not (cambio or reabierto):
                continue
            etiqueta = f"Viaje #{id_}"
        else:
            if not any(_k(g(c)) for c in campos):
                continue
            etiqueta = f"Fila nueva {n}"
        salida.append({"id": id_, "etiqueta": etiqueta, **{c: g(c) for c in campos},
                       "pedido": g("pedido"), "cita_carga": g("cita_carga"), "cita_descarga": g("cita_descarga")})
    return salida


# ---------------------------------------------------------- ayudas para la tabla del Plan del dia
def autocompletar_editor(editado, previo, ctx):
    """Rellena lo que se puede deducir de lo que la persona acaba de capturar en la tabla:
      - escribe/elige NOMINA   -> se llena el OPERADOR
      - escribe/elige OPERADOR -> se llena la NOMINA
      - elige una UNIDAD sin operador -> se sugiere su operador habitual (solo si esta libre)
    'previo' es la tabla tal como estaba antes de este cambio (sirve para saber CUAL campo se movio).
    Devuelve (tabla_nueva, hubo_cambios)."""
    df = editado.copy()
    for c in ("eco", "operador", "nomina"):
        if c not in df.columns:
            return df, False
    antes = df[["eco", "operador", "nomina"]].map(_k)
    for idx in df.index:
        eco, op, nom = (_k(df.at[idx, c]) for c in ("eco", "operador", "nomina"))
        if previo is not None and idx in previo.index:
            p_eco, p_op, p_nom = (_k(previo.at[idx, c]) if c in previo.columns else "" for c in ("eco", "operador", "nomina"))
        else:
            p_eco = p_op = p_nom = ""
        op_cambio, nom_cambio = op != p_op, nom != p_nom

        if op_cambio:                                   # (si cambiaron los dos, manda el operador)
            if op:
                nuevo = ctx.nomina_de.get(op)
                df.at[idx, "nomina"] = nuevo if nuevo else None
            else:
                df.at[idx, "nomina"] = None
        elif nom_cambio:
            if nom:
                nombre = ctx.nombre_de.get(nom)
                if nombre:
                    df.at[idx, "operador"] = nombre
            else:
                df.at[idx, "operador"] = None

        if eco and eco != p_eco and not _k(df.at[idx, "operador"]):
            habitual = ctx.operador_habitual.get(eco)
            if habitual and ctx.operador_asignable(habitual):
                df.at[idx, "operador"] = habitual
                df.at[idx, "nomina"] = ctx.nomina_de.get(_k(habitual)) or None
    despues = df[["eco", "operador", "nomina"]].map(_k)
    return df, not antes.equals(despues)


def contar_cambios(original, editado, claves):
    """Cuantos viajes tienen cambios sin guardar (nuevos + modificados + borrados)."""
    ids_orig = {int(i) for i in original["id"]} if len(original) else set()
    ids_edit = {int(i) for i in editado["id"].dropna()}
    total = len(ids_orig - ids_edit)
    orig = original.set_index("id") if len(original) else None
    tipos = {c.clave: c.tipo for c in __import__("columnas").COLUMNAS}
    for _, fila in editado.iterrows():
        if pd.isna(fila["id"]):
            if any(bd.normalizar(fila[k], tipos[k]) is not None for k in claves if k in fila.index):
                total += 1
        elif int(fila["id"]) in ids_orig:
            o = orig.loc[int(fila["id"])]
            if any(bd.normalizar(fila[k], tipos[k]) != bd.normalizar(o[k], tipos[k]) for k in claves if k in fila.index):
                total += 1
    return total


def diferencias(original, editado, claves):
    """Lista de lo que cambio en la tabla, para la bitacora: [(accion, objeto, detalle), ...]
    accion = MODIFICAR | CREAR | BORRAR. Se llama ANTES de guardar (con la tabla original y la editada)."""
    from columnas import COLUMNAS, TITULO_POR_CLAVE
    tipos = {c.clave: c.tipo for c in COLUMNAS}
    ver = lambda v: "—" if bd.normalizar(v, "texto") is None else str(v)
    salida = []
    ids_edit = {int(i) for i in editado["id"].dropna()}
    orig = original.set_index("id") if len(original) else None
    for _, o in original.iterrows():
        if int(o["id"]) not in ids_edit:
            salida.append(("BORRAR", f"viaje #{int(o['id'])} · pedido {ver(o.get('pedido'))} · eco {ver(o.get('eco'))}",
                           f"Se borró el viaje: {ver(o.get('origen'))} → {ver(o.get('destino'))}"))
    for _, f in editado.iterrows():
        if pd.isna(f["id"]):
            datos = [f"{TITULO_POR_CLAVE[k]}: {ver(f[k])}" for k in claves
                     if k in f.index and bd.normalizar(f[k], tipos[k]) is not None]
            if datos:
                salida.append(("CREAR", f"viaje nuevo · pedido {ver(f.get('pedido'))}", "; ".join(datos)))
        elif int(f["id"]) in {int(i) for i in original["id"]}:
            o = orig.loc[int(f["id"])]
            cambios = [f"{TITULO_POR_CLAVE[k]}: {ver(o[k])} → {ver(f[k])}" for k in claves if k in f.index
                       and bd.normalizar(f[k], tipos[k]) != bd.normalizar(o[k], tipos[k])]
            if cambios:
                salida.append(("MODIFICAR", f"viaje #{int(f['id'])} · pedido {ver(o.get('pedido'))} · eco {ver(o.get('eco'))}",
                               "; ".join(cambios)))
    return salida
