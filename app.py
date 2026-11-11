# ==========================================================
#  app.py - La pantalla de tu TMS (lo que ves en el navegador)
# ==========================================================
import hmac
from datetime import date, datetime, time, timedelta

import pandas as pd
import pydeck as pdk
import streamlit as st

import base_datos as bd
import calculos
import catalogos_excel
import conciliacion
import configuracion as cfg
import estilo
import omnitracs
import reglas
import zam
from columnas import (CATALOGO_DE, CATALOGOS, COLUMNAS, CONTEXTO, ESTATUS_CERRADOS, FICHA_REQUERIDOS, LISTAS,
                      PENDIENTES, PERMISOS, PESTANAS, ROLES, SITUACIONES, TIPO_POR_CLAVE, TITULO_POR_CLAVE)

st.set_page_config(page_title="TMS · Estrella Roja",
                   page_icon=str(estilo.ISOTIPO) if estilo.ISOTIPO.exists() else "🚛", layout="wide")
bd.crear_tablas()
try:
    bd.respaldo_diario()          # copia de seguridad de tms.db (1 por dia, guarda 14)
except Exception:
    pass
estilo.aplicar()


@st.cache_resource(show_spinner=False)
def _hilo_zam():
    """Un solo hilo por servidor que actualiza el catalogo desde ZAM cada N minutos."""
    return zam.iniciar_actualizacion_automatica()


if getattr(cfg, "SYNC_ZAM_AUTOMATICO", True):
    _hilo_zam()

if "version" not in st.session_state:
    st.session_state.version = 0

# ------------------------------------------------------------------ FICHA DEL PEDIDO (pantalla para captura)
def _vacio(v):
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() == ""


def base_url():
    """Liga base de este TMS (para armar la liga de la ficha). Se detecta sola o sale de configuracion.py."""
    fija = str(getattr(cfg, "URL_BASE_TMS", "") or "").strip().rstrip("/")
    if fija:
        return fija
    try:
        h = st.context.headers
        host = h.get("Host")
        if host:
            return f"{h.get('X-Forwarded-Proto', 'http')}://{host}"
    except Exception:
        pass
    return ""


def faltantes_ficha(fila):
    """Datos que faltan para habilitar la ficha de un viaje (lista de titulos)."""
    return [TITULO_POR_CLAVE[k] for k in FICHA_REQUERIDOS if _vacio(fila.get(k))]


def fmt_cita(v):
    return None if _vacio(v) else pd.to_datetime(v).strftime("%d/%m/%Y %H:%M")


def mostrar_ficha(id_txt):
    """Pantalla tipo 'ficha de pedido' (como el formato en Excel) lista para tomarle captura."""
    try:
        id_ = int(id_txt)
    except (TypeError, ValueError):
        st.error("Liga de ficha no válida.")
        return
    todos = bd.leer_viajes("0000-01-01", "9999-12-31")
    v = todos[todos["id"] == id_]
    if v.empty:
        st.error("Ese viaje ya no existe en el TMS.")
        return
    v = v.iloc[0]
    falta = faltantes_ficha(v)
    if falta:
        st.warning("La ficha está incompleta. Falta: " + ", ".join(falta))
    g = lambda k: None if _vacio(v.get(k)) else v.get(k)
    estilo.ficha([
        ("PEDIDO", g("pedido")), ("UNIDAD", g("eco")), ("OPERADOR", g("operador")), ("MODALIDAD", g("modalidad")),
        ("REMOLQUE", g("remolque_1")), ("ORIGEN", g("origen")), ("DOMICILIO ORIGEN", g("direccion_origen")),
        ("DESTINO", g("destino")), ("CLIENTE", g("cliente")), ("DOMICILIO DESTINO", g("direccion_destino")),
        ("CITA CARGA", fmt_cita(v.get("cita_carga"))), ("FOLIO CLIENTE", g("folio_cliente")),
        ("FUMIGACION", g("fumigacion")), ("CITA DESCARGA", fmt_cita(v.get("cita_descarga"))),
        ("INSTRUCCIONES ESPECIALES", g("instrucciones")),
    ], destacado="PEDIDO")
    st.markdown('<p class="er-ficha-tip">📸 Toma la captura de esta pantalla (en Windows: <b>Win + Shift + S</b>). '
                '&nbsp;·&nbsp; <a href="?" target="_self">← Volver al TMS</a></p>', unsafe_allow_html=True)

# ------------------------------------------------------------------ acceso con usuario y contraseña
ficha_id = st.query_params.get("ficha")        # si viene en la liga, se abre la FICHA del pedido y nada mas
st.sidebar.markdown("### Plan de carga TMS")

if "usuario" not in st.session_state:
    estilo.encabezado("Plan de carga TMS", "Inicia sesión para continuar")
    with st.form("login"):
        u_in = st.text_input("Usuario")
        p_in = st.text_input("Contraseña", type="password")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        datos_u, msg = bd.autenticar(u_in, p_in)
        if datos_u:
            st.session_state.usuario = datos_u
            st.rerun()
        st.error(msg)
    st.stop()

USR = st.session_state.usuario                   # {'usuario', 'nombre', 'perfil'}; perfil MASTER = administrador
es_master = USR["perfil"] == "MASTER"
if es_master:
    rol = st.sidebar.selectbox("Trabajar como", ROLES, help="Como usuario MASTER puedes ver el TMS con cualquier perfil. "
                               "Todo lo que hagas queda en la bitácora con tu nombre.")
else:
    rol = USR["perfil"]
USR["actuando_como"] = rol
st.sidebar.write(f"👤 **{USR['nombre']}**  \n{USR['usuario']} · {'MASTER' if es_master else rol}")
if st.sidebar.button("Cerrar sesión"):
    bd.bitacora(USR, "LOGOUT", "", "Cierre de sesión")
    del st.session_state["usuario"]
    st.rerun()

if ficha_id:
    mostrar_ficha(ficha_id)
    st.stop()

perm = PERMISOS[rol]

dia = st.sidebar.date_input("Día del plan", value=date.today(), format="DD/MM/YYYY")
tipo_sel = st.sidebar.selectbox("Tipo de operación", ["(Todos)"] + bd.opciones_catalogo("tipos_operacion"))
abiertos = st.sidebar.checkbox(
    "Incluir viajes sin terminar de días anteriores", value=True,
    help="Un viaje queda 'abierto' hasta que lo marques con ✅ (finalizado) o ❌ (cancelado), o su estatus sea DISPONIBLE.",
)


# ------------------------------------------------------------------ funciones de ayuda

def cargar_plan(dia, abiertos):
    desde = dia - timedelta(days=cfg.DIAS_ATRAS_ABIERTOS) if abiertos else dia
    df = calculos.agregar_calculados(bd.leer_viajes(desde, dia))
    if abiertos:
        df = df[(df["fecha_plan"] == str(dia)) | (~df["estatus"].isin(ESTATUS_CERRADOS))]
    return df.reset_index(drop=True)


def posiciones_por_eco(ecos_extra=()):
    """Ultima posicion de Omnitracs por alias, clasificada en TRACTO / REMOLQUE / FUERA de catalogo.
    'clave' solo se llena en tractos (es el ECO con el que se empata contra el plan)."""
    pos = bd.leer_posiciones()
    if pos.empty:
        return pos.assign(clave=pd.Series(dtype=object), tipo=pd.Series(dtype=object),
                          en_mov=pd.Series(dtype=bool), exacto=pd.Series(dtype=bool))
    tractos = {omnitracs.normalizar_alias(e) for e in bd.opciones_catalogo("unidades")}
    tractos |= {omnitracs.normalizar_alias(e) for e in ecos_extra if e}
    remolques = {omnitracs.normalizar_alias(r) for r in bd.opciones_catalogo("remolques")}
    clas = pos["alias"].map(lambda a: omnitracs.clasificar_alias(a, tractos, remolques))
    pos["tipo"] = clas.map(lambda x: x[0])
    pos["clave"] = clas.map(lambda x: x[1] if x[0] == "TRACTO" else "")
    pos["en_mov"] = pos["velocidad"].map(omnitracs.en_movimiento)
    pos["exacto"] = pos["alias"].map(omnitracs.normalizar_alias) == pos["clave"]
    return pos


def una_por_eco(pos):
    """Si un ECO tiene varios aliases, se queda con el exacto y el mas reciente."""
    con_clave = pos[pos["clave"] != ""].sort_values(["exacto", "fecha"])
    return con_clave.drop_duplicates("clave", keep="last")


def claves_visibles(rol):
    if rol == "TODOS":
        return [c.clave for c in COLUMNAS]
    return [c.clave for c in COLUMNAS if c.rol == rol or c.clave in CONTEXTO]


def claves_editables(rol):
    """Columnas que el perfil puede editar. TODOS = solo consulta (ninguna)."""
    if not PERMISOS[rol]["editar"]:
        return []
    return [c.clave for c in COLUMNAS if c.rol != "AUTO" and c.rol in (rol, "TODOS")]


def con_usados(opciones, serie):
    """Agrega a la lista los valores que ya existen en los datos aunque no esten en el catalogo."""
    extra = [x for x in serie.dropna().unique() if x and x not in opciones]
    return list(opciones) + extra


def config_columnas(visibles, df, opciones=None):
    """opciones: {clave: [valores]} para las listas de sugerencias (ej. solo unidades que se pueden usar)."""
    opciones = opciones or {}
    cc = {"fecha_plan": st.column_config.TextColumn("DÍA PLAN", disabled=True),
          "ficha": st.column_config.LinkColumn(
              "FICHA", display_text="📸 Ver ficha", width="small",
              help="Se habilita cuando el viaje (ya guardado) tiene todos los datos de la ficha. "
                   "Abre la pantalla lista para tomarle captura.")}
    for c in COLUMNAS:
        if c.clave not in visibles:
            continue
        t = c.titulo
        if c.tipo == "fecha":
            cc[c.clave] = st.column_config.DatetimeColumn(t, format="DD/MM/YYYY HH:mm", step=60)
        elif c.tipo == "entero":
            cc[c.clave] = st.column_config.NumberColumn(t, format="%d", step=1)
        elif c.tipo == "dinero":
            cc[c.clave] = st.column_config.NumberColumn(t, format="$%.2f MXN")
        elif c.tipo == "lista":
            cc[c.clave] = st.column_config.SelectboxColumn(t, options=con_usados(LISTAS[c.clave], df[c.clave]))
        elif c.tipo == "catalogo":
            tabla, col = CATALOGO_DE[c.clave]
            base_op = opciones.get(c.clave, bd.opciones_catalogo(tabla, col))
            cc[c.clave] = st.column_config.SelectboxColumn(t, options=con_usados(base_op, df[c.clave]))
        elif c.tipo == "check":
            cc[c.clave] = st.column_config.CheckboxColumn(
                t, width="small", default=False,
                help=("FINALIZAR viaje: la unidad queda libre en la flota (estatus DISPONIBLE)."
                      if c.clave == "finalizado" else
                      "CANCELAR viaje: la unidad queda libre en la flota (estatus CANCELADO)."))
        elif c.clave in ("link_origen", "link_destino"):
            lado = "Origen" if c.clave == "link_origen" else "Destino"
            cc[c.clave] = st.column_config.LinkColumn(t, display_text=f"📍 {lado}",
                                                      help="Liga de Google Maps. Clic para abrir; ATC puede editarla.")
        elif c.tipo == "calculado":
            cc[c.clave] = (st.column_config.NumberColumn(t + " 🔒", format="%d", disabled=True)
                           if c.clave == "dif" else st.column_config.TextColumn(t + " 🔒", disabled=True))
        else:
            cc[c.clave] = st.column_config.TextColumn(t)
    return cc


def estado_zam():
    """Texto corto con la ultima actualizacion del catalogo desde ZAM."""
    ultima = bd.leer_meta("zam_ultima_sync")
    if not ultima:
        return "Catálogo ZAM: aún no se actualiza"
    return f"Catálogo ZAM: {datetime.strptime(ultima, '%Y-%m-%d %H:%M:%S'):%d/%m/%Y %H:%M}"


def _hace(segundos):
    """Convierte segundos en texto: 'hace 3 min 20 s'."""
    s = int(max(segundos, 0))
    if s < 60:
        return f"hace {s} s"
    if s < 3600:
        return f"hace {s // 60} min {s % 60:02d} s"
    return f"hace {s // 3600} h {(s % 3600) // 60:02d} min"


@st.fragment(run_every=15)
def panel_zam():
    """Recuadro de la barra lateral: contador desde la ultima actualizacion y boton manual.
    Se refresca solo cada 15 segundos sin recargar el resto de la pantalla."""
    intervalo = cfg.MINUTOS_ENTRE_SYNC_ZAM * 60
    ultima = bd.leer_meta("zam_ultima_sync")
    st.markdown("**🔄 Catálogo ZAM**")
    if ultima:
        pasados = bd.segundos_desde_sync_zam()
        st.caption(f"Última actualización: {datetime.strptime(ultima, '%Y-%m-%d %H:%M:%S'):%d/%m/%Y %H:%M:%S}  \n"
                   f"**{_hace(pasados)}** · automática cada {cfg.MINUTOS_ENTRE_SYNC_ZAM} min")
        if pasados > intervalo * 2 + 60:
            st.warning("La actualización automática lleva más tiempo del esperado. Usa el botón.")
        else:
            st.caption(f"Próxima en {_hace(intervalo - pasados).replace('hace ', '')}"
                       if pasados < intervalo else "Próxima: en curso…")
    else:
        st.caption("Aún no se ha actualizado.")
    if bd.leer_meta("zam_ultimo_error"):
        st.error(f"Último error: {bd.leer_meta('zam_ultimo_error')}")
    if st.button("Actualizar ahora", key="btn_zam_lateral", type="primary", use_container_width=True):
        try:
            with st.spinner("Consultando ZAM..."):
                zam.actualizar_ahora()
            st.session_state.version += 1
            st.session_state.mensaje = "Catálogo actualizado desde ZAM ✅  " + (bd.leer_meta("zam_ultimo_resumen") or "")
            st.rerun(scope="app")     # recarga toda la pantalla con el catalogo nuevo
        except Exception as e:
            st.error(f"No se pudo actualizar desde ZAM: {e}")



if perm["zam"]:
    with st.sidebar:
        st.divider()
        panel_zam()
else:
    with st.sidebar:
        st.divider()
        st.caption(estado_zam())
        if not perm["editar"]:
            st.caption("🔒 Perfil de solo consulta: puedes ver el Plan del día y exportar.")

estilo.encabezado("Plan de carga TMS", f"Operación del {dia:%d/%m/%Y} · vista {rol}")

# mensaje de "guardado" (se muestra arriba, en cualquier pestaña)
if st.session_state.get("mensaje"):
    st.success(st.session_state.pop("mensaje"), icon="🚛")

# ------------------------------------------------------------------ pestañas (cada perfil ve solo las suyas)
claves_tabs = perm["pestanas"] + (["seg"] if es_master else [])
T = dict(zip(claves_tabs, st.tabs([PESTANAS[k] for k in claves_tabs])))


def pendiente(df, rol):
    """Viajes abiertos que a este perfil todavia le falta trabajar."""
    abierto = ~df["estatus"].isin(ESTATUS_CERRADOS)
    vacio = lambda col: df[col].isna() | (df[col].astype(str).str.strip() == "")
    if rol == "ATC":
        return abierto & vacio("pedido")
    if rol == "OPS":
        return abierto & (vacio("eco") | vacio("operador"))
    if rol == "TC":
        return abierto & (df["llegada_carga"].isna() | df["salida_carga"].isna()
                          | df["llegada_descarga"].isna() | df["salida_descarga"].isna())
    return pd.Series(True, index=df.index)


def agregar_ficha(df):
    """Columna FICHA: liga a la pantalla de captura, solo si el viaje ya tiene todos sus datos."""
    df = df.copy()
    base = base_url()
    df["ficha"] = [f"{base}/?ficha={int(r['id'])}" if not faltantes_ficha(r) else None
                   for _, r in df.iterrows()] if len(df) else pd.Series(dtype=object)
    return df


@st.dialog("¿Borrar todo el contenido?")
def confirmar_borrado():
    st.write("Se van a **vaciar todos los campos** del pedido que estás capturando. "
             "Esto no se puede deshacer. ¿Realmente quieres borrar el contenido?")
    c1, c2 = st.columns(2)
    if c1.button("Sí, borrar todo", type="primary", use_container_width=True):
        st.session_state.nuevo_ver = st.session_state.get("nuevo_ver", 0) + 1
        st.rerun()
    if c2.button("No, conservar", use_container_width=True):
        st.rerun()


# ================================================================== PLAN DEL DÍA
if "plan" in T:
  with T["plan"]:
    titulo_tipo = "" if tipo_sel == "(Todos)" else f"  ·  {tipo_sel}"
    st.subheader(f"Plan de carga del {dia:%d/%m/%Y}  ·  vista: {rol}{titulo_tipo}")

    if perm["editar"]:                              # viajes con todos sus tiempos -> ✅ finalizado solos
        auto = bd.finalizar_viajes_completos(devolver_viajes=True)
        n_auto = len(auto)
        for id_v, ped, eco_v in auto:
            bd.bitacora("SISTEMA", "FINALIZADO_AUTOMATICO", f"viaje #{id_v} · pedido {ped or '—'} · eco {eco_v or '—'}",
                        "Tenía todos sus tiempos capturados")
        if n_auto:
            st.toast(f"✅ {n_auto} viaje(s) tenían todos sus tiempos y se finalizaron solos. "
                     "Su unidad y operador ya están disponibles.", icon="🏁")
    df_total = cargar_plan(dia, abiertos)          # todo el plan (sin filtrar por tipo)
    df = df_total
    if tipo_sel != "(Todos)":
        df = df[df["tipo_operacion"] == tipo_sel].reset_index(drop=True)

    # --- DISPONIBILIDAD DE LA FLOTA (siempre con toda la flota, sin filtrar por tipo) ---
    # Una unidad esta LIBRE si esta OPERANDO en el catalogo y no tiene un viaje abierto:
    #   - viaje con ✅ finalizado, ❌ cancelado o estatus DISPONIBLE  -> libre (esta en el plan)
    #   - sin viaje en el plan                                         -> libre (solo catalogo)
    plan_flota = cargar_plan(dia, True)
    plan_flota["_k"] = plan_flota["eco"].fillna("").astype(str).str.strip()
    ecos_en_plan = set(plan_flota.loc[plan_flota["_k"] != "", "_k"])
    ecos_ocupados = set(plan_flota.loc[(plan_flota["_k"] != "")
                                       & (~plan_flota["estatus"].isin(ESTATUS_CERRADOS)), "_k"])

    flota = bd.leer_catalogo("unidades")
    sit = flota["situacion"].fillna("").astype(str).str.strip().str.upper()
    flota["situacion"] = sit.where(sit != "", "OPERANDO")
    flota["_k"] = flota["eco"].astype(str).str.strip()
    zam_activos = reglas.Contexto().ecos_activos_zam()          # ECO con viaje 'open' en ZAM
    flota_zam = flota["_k"].str.upper().isin(zam_activos)
    libres = flota[(flota["situacion"] == "OPERANDO") & (~flota["_k"].isin(ecos_ocupados))
                   & (~flota_zam)].copy()
    libres["motivo"] = libres["_k"].map(lambda k: "Terminó o canceló su viaje" if k in ecos_en_plan
                                        else "Sin viaje en el plan")
    disp_plan = int((libres["motivo"] != "Sin viaje en el plan").sum())
    disp_cat = int((libres["motivo"] == "Sin viaje en el plan").sum())
    total_flota = len(flota)
    pct_disp = (len(libres) / total_flota * 100) if total_flota else 0.0
    operando = int((flota["situacion"] == "OPERANDO").sum())
    pct_operando = (len(libres) / operando * 100) if operando else 0.0

    # --- EN MOVIMIENTO: unidades que Omnitracs reporta con velocidad (aunque la ignicion diga apagado) ---
    norm = omnitracs.normalizar_alias
    pos_eco = una_por_eco(posiciones_por_eco(df["eco"].dropna().tolist()))
    claves_plan = {norm(e) for e in df["eco"].dropna() if str(e).strip()}
    claves_libres = {norm(e) for e in libres["eco"]}
    mov_plan = int((pos_eco["clave"].isin(claves_plan) & pos_eco["en_mov"]).sum())
    mov_libres = int((pos_eco["clave"].isin(claves_libres) & pos_eco["en_mov"]).sum())

    estados = ["PROGRAMADO", "CARGANDO", "TRANSITO CARGADO", "DESCARGANDO"]
    f1 = st.columns(5)
    f1[0].metric("Viajes", len(df))
    for col, e in zip(f1[1:], estados):
        col.metric(e.title(), int((df["estatus"] == e).sum()))
    f2 = st.columns(6)
    f2[0].metric("Disponible (en plan)", disp_plan,
                 help="Unidades OPERANDO cuyo viaje ya se finalizó (✅), canceló (❌) o está DISPONIBLE.")
    f2[1].metric("Disponible (catálogo)", disp_cat,
                 help="Unidades OPERANDO del catálogo que no aparecen en el plan de carga.")
    f2[2].metric("Total disponibles", len(libres))
    f2[3].metric("% Disponibilidad", f"{pct_disp:.1f}%",
                 help=f"{len(libres)} unidades libres de {total_flota} en toda la flota. "
                      f"Sobre las {operando} que están OPERANDO: {pct_operando:.1f}%.")
    f2[4].metric("En movimiento (plan)", mov_plan,
                 help=f"Unidades del plan con {getattr(cfg, 'VELOCIDAD_MINIMA_MOVIMIENTO', 1)} km/h o más.")
    f2[5].metric("Movimiento sin viaje", mov_libres,
                 help="Unidades libres que Omnitracs ve en movimiento.")
    st.caption("La disponibilidad se calcula con toda la flota (no depende del filtro de tipo de operación). "
               "Marca ✅ para finalizar un viaje o ❌ para cancelarlo y la unidad queda libre al guardar.")

    with st.expander(f"🚚 Unidades disponibles ({len(libres)}) · {pct_disp:.1f}% de la flota"):
        resumen = " · ".join(f"{x.title()}: {int((flota['situacion'] == x).sum())}" for x in SITUACIONES)
        st.caption("Flota por situación → " + resumen
                   + f" · En viaje TMS: {len(ecos_ocupados & set(flota['_k']))}"
                   + f" · Con viaje abierto en ZAM: {int(flota_zam.sum())}")
        st.dataframe(libres[["eco", "operador", "motivo"]].rename(
            columns={"eco": "ECO", "operador": "OPERADOR HABITUAL", "motivo": "MOTIVO"}), hide_index=True)

    # ------------------------------------------------------------ tabla del plan
    claves_ed = claves_editables(rol)
    visibles = ["fecha_plan"] + claves_visibles(rol)
    visibles.insert(visibles.index("cancelado") + 1, "ficha")
    deshabilitadas = ["fecha_plan", "ficha"] + [c.clave for c in COLUMNAS if c.clave not in claves_ed]

    if perm["editar"]:
        mis_cols = [TITULO_POR_CLAVE[k] for k in claves_ed if k not in ("finalizado", "cancelado")]
        st.caption(f"✏️ **Tu perfil ({rol}) captura:** {', '.join(mis_cols)}, y puedes marcar ✅ / ❌.  "
                   "Las demás columnas son de consulta; las que tienen 🔒 se calculan solas.")
    else:
        st.info("🔒 Perfil de solo consulta: aquí puedes ver el plan y exportarlo, pero no modificarlo. "
                "Para capturar, entra con el perfil que te corresponde (ATC, OPS o TC).")

    # filtro rapido: "lo que me toca"
    df_tab = df
    if rol in PENDIENTES:
        vista_plan = st.radio("Mostrar", ["Todos los viajes", f"⏳ Pendientes: {PENDIENTES[rol]}"],
                              horizontal=True, label_visibility="collapsed", key="filtro_pendientes")
        if vista_plan.startswith("⏳"):
            df_tab = df[pendiente(df, rol)]
            st.caption(f"{len(df_tab)} de {len(df)} viajes tienen pendiente algo de tu perfil.")
    df_tab = agregar_ficha(df_tab.reset_index(drop=True))

    # ------------------------------ solo consulta (perfil TODOS)
    if not perm["editar"]:
        st.dataframe(df_tab, column_order=visibles, column_config=config_columnas(visibles, df_tab),
                     hide_index=True, height=520)
    else:
        # ------------------------------ edicion directa en la tabla
        ctx_plan = reglas.Contexto()
        ecos_cat = {reglas._k(e): e for e in bd.opciones_catalogo("unidades")}
        ecos_ok = [e for e in ecos_cat.values() if ctx_plan.eco_asignable(e)]
        ecos_ok += [ecos_cat[k] for k in conciliacion.ecos_zam_que_son_del_plan(plan_flota) if k in ecos_cat
                    and ecos_cat[k] not in ecos_ok and ctx_plan.situacion.get(k) == "OPERANDO"]
        opciones = {
            "eco": sorted(ecos_ok),                                    # solo unidades que se pueden usar
            "operador": [o for o in bd.opciones_catalogo("operadores", "nombre") if ctx_plan.operador_asignable(o)],
            "remolque_1": [r for r in bd.opciones_catalogo("remolques") if ctx_plan.remolque_asignable(r)],
        }

        ctx_key = f"{dia}|{rol}|{tipo_sel}|{abiertos}|{st.session_state.get('filtro_pendientes')}|{st.session_state.version}"
        guardado = st.session_state.get("plan_base")
        base = guardado[1] if guardado and guardado[0] == ctx_key else df_tab       # con cambios sin guardar, o fresco
        ver = st.session_state.get("plan_ver", 0)
        clave_editor = f"editor_{ctx_key}_{ver}"

        zona_zam = st.container()      # (se llena despues de la tabla: coincidencias con ZAM)
        zona_botones = st.container()
        editado = st.data_editor(
            base,
            key=clave_editor,
            column_order=visibles,
            column_config=config_columnas(visibles, base, opciones),
            disabled=deshabilitadas,
            num_rows="dynamic" if perm["agregar_borrar"] else "fixed",
            hide_index=True,
            height=520,
        )

        # nomina <-> operador y operador habitual: se rellenan solos (sin perder lo que ya capturaste)
        editado_ac, hubo = reglas.autocompletar_editor(editado, base, ctx_plan)
        if hubo:
            st.session_state.plan_base = (ctx_key, editado_ac)
            st.session_state.plan_ver = ver + 1
            st.rerun()

        pendientes_guardar = reglas.contar_cambios(df_tab, editado, claves_ed)

        with zona_botones:
            b1, b2, b3 = st.columns([1.2, 1.2, 5])
            guardar_arriba = b1.button("💾 Guardar cambios", type="primary", key="guardar_arriba")
            descartar = b2.button("↩️ Descartar", key="descartar_cambios", disabled=pendientes_guardar == 0)
            b3.markdown(f"🟠 **{pendientes_guardar} viaje(s) con cambios sin guardar**" if pendientes_guardar
                        else "🟢 Sin cambios pendientes")
        if descartar:
            st.session_state.pop("plan_base", None)
            st.session_state.version += 1
            st.rerun()

        # ------------------------------ coincidencias con ZAM (traer PEDIDO)
        if perm["zam"]:
            coinc = conciliacion.buscar_coincidencias(plan_flota)
            if not coinc.empty:
                with zona_zam:
                    with st.container(border=True):
                        st.markdown(f"🔗 **ZAM tiene {len(coinc)} viaje(s) que coinciden con viajes de tu plan "
                                    "que no tienen PEDIDO.** ¿Quieres que agregue el número de viaje ZAM como PEDIDO?")
                        vista_c = coinc.assign(agregar=True)[["agregar", "id", "eco", "operador", "cita_carga",
                                                              "shipment_id", "salida_zam", "motivo"]]
                        sel = st.data_editor(
                            vista_c, hide_index=True, key=f"coinc_{'_'.join(coinc['shipment_id'])}",
                            disabled=["id", "eco", "operador", "cita_carga", "shipment_id", "salida_zam", "motivo"],
                            column_config={
                                "agregar": st.column_config.CheckboxColumn("¿AGREGAR?", width="small"),
                                "id": st.column_config.NumberColumn("VIAJE #", format="%d", width="small"),
                                "eco": "ECO", "operador": "OPERADOR",
                                "cita_carga": st.column_config.DatetimeColumn("CITA CARGA (TMS)", format="DD/MM/YYYY HH:mm"),
                                "shipment_id": "PEDIDO (VIAJE ZAM)",
                                "salida_zam": "SALIDA PLANEADA ZAM", "motivo": "POR QUÉ COINCIDE"})
                        marcados = [(int(r["id"]), r["shipment_id"]) for _, r in sel.iterrows() if r["agregar"]]
                        if pendientes_guardar:
                            st.warning("Primero guarda o descarta los cambios de la tabla para poder agregar pedidos.")
                        z1, z2, _ = st.columns([2, 2, 4])
                        if z1.button("✅ Sí, agregar los marcados", type="primary", key="coinc_si",
                                     disabled=bool(pendientes_guardar) or not marcados):
                            n = conciliacion.aplicar_pedidos(marcados)
                            for id_v, ship in marcados:
                                bd.bitacora(USR, "ZAM_PEDIDO_AGREGADO", f"viaje #{id_v}", f"Pedido {ship} tomado de ZAM")
                            st.session_state.pop("plan_base", None)
                            st.session_state.version += 1
                            st.session_state.mensaje = f"Pedido agregado desde ZAM en {n} viaje(s) ✅"
                            st.rerun()
                        if z2.button("No, no coinciden", key="coinc_no", disabled=not marcados):
                            conciliacion.ignorar(marcados)
                            bd.bitacora(USR, "ZAM_COINCIDENCIA_IGNORADA", "", ", ".join(f"viaje #{i} ≠ ZAM {z}" for i, z in marcados))
                            st.session_state.version += 1
                            st.rerun()

        st.caption(
            ("Para borrar un viaje: selecciona la fila (cuadrito de la izquierda) y presiona Suprimir. "
             "Para agregar: usa la fila vacía del final. " if perm["agregar_borrar"] else "")
            + "Las listas de ECO, OPERADOR y REMOLQUE solo sugieren lo que se puede usar. "
              "Si eliges ECO sin operador se sugiere su operador habitual, y NÓMINA y OPERADOR se llenan entre sí. "
              "El ESTATUS se calcula solo, pero puedes cambiarlo a mano; si borras tu valor, vuelve al automático. "
              "Nada se guarda hasta que presiones el botón.")

        guardar_abajo = st.button("💾 Guardar cambios", type="primary", key="guardar_abajo")
        if guardar_arriba or guardar_abajo:
            with st.spinner("Revisando unidades, operadores y remolques..."):
                errores = reglas.validar_editor(df_tab, editado)
            if errores:
                st.error("**No se guardó nada.** Corrige estas asignaciones (tus cambios siguen en la tabla):\n\n"
                         + "\n".join(f"- {e}" for e in errores))
            else:
                defaults = {} if tipo_sel == "(Todos)" else {"tipo_operacion": tipo_sel}
                lo_que_cambio = reglas.diferencias(df_tab, editado, claves_ed)      # para la bitácora
                r = bd.guardar_plan(df_tab, editado, dia, claves_ed, defaults)
                for accion, objeto, detalle in lo_que_cambio:
                    bd.bitacora(USR, f"PLAN_{accion}", objeto, detalle)
                auto = bd.finalizar_viajes_completos(devolver_viajes=True)
                n_auto = len(auto)
                for id_v, ped, eco_v in auto:
                    bd.bitacora("SISTEMA", "FINALIZADO_AUTOMATICO", f"viaje #{id_v} · pedido {ped or '—'} · eco {eco_v or '—'}",
                                "Tenía todos sus tiempos capturados")
                st.session_state.pop("plan_base", None)
                st.session_state.version += 1
                st.session_state.mensaje = (
                    f"Guardado ✅  Nuevos: {r['nuevos']} · Modificados: {r['cambios']} · Borrados: {r['borrados']}"
                    + (f" · 🏁 {n_auto} viaje(s) completo(s) finalizado(s) solos: su unidad y operador ya están disponibles"
                       if n_auto else ""))
                st.rerun()

    # ------------------------------ fichas de pedido (alternativa a la liga de la columna)
    listas_ficha = df_tab[df_tab["ficha"].notna()]
    with st.expander(f"📸 Fichas de pedido para captura ({len(listas_ficha)} listas)"):
        st.caption("Una ficha se habilita cuando el viaje ya guardado tiene: "
                   + ", ".join(TITULO_POR_CLAVE[k] for k in FICHA_REQUERIDOS) + ".")
        if listas_ficha.empty:
            st.info("Ningún viaje de esta vista tiene todos los datos todavía.")
        else:
            etiquetas = {int(r["id"]): f"{r['pedido']} · ECO {r['eco']} · {r['operador']} · {r['origen']} → {r['destino']}"
                         for _, r in listas_ficha.iterrows()}
            elegido = st.selectbox("Pedido", list(etiquetas), format_func=etiquetas.get, key="ficha_sel")
            st.link_button("📸 Abrir ficha (se abre en otra pestaña)",
                           listas_ficha.loc[listas_ficha["id"] == elegido, "ficha"].iloc[0])

# ================================================================== NUEVO PEDIDO (solo ATC)
def _link_automatico(lado, clave_lugar, clave_link):
    """Al elegir origen/destino, pone solo el link que ya se conoce (catálogo o pedidos anteriores)."""
    st.session_state[clave_link] = bd.link_conocido(lado, st.session_state.get(clave_lugar)) or ""


if "nuevo" in T:
  with T["nuevo"]:
    st.subheader("Capturar un pedido nuevo")
    st.caption("Captura solo los datos del pedido. Al generarlo queda en el **Plan del día** para que OPS asigne "
               "unidad, operador y remolque, y TC registre sus tiempos.")
    nv = st.session_state.get("nuevo_ver", 0)
    k = lambda nombre: f"np_{nombre}_{nv}"           # cada "Borrar contenido" estrena campos nuevos
    tipos = bd.opciones_catalogo("tipos_operacion")
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        tipo_op = c1.selectbox("TIPO OPERACION", tipos, index=tipos.index(tipo_sel) if tipo_sel in tipos else None,
                               placeholder="Elige una opción", key=k("tipo"))
        cliente = c2.selectbox("CLIENTE (si no lo eliges, se usa el del tipo de operación)",
                               bd.opciones_catalogo("clientes"), index=None, placeholder="Elige una opción", key=k("cliente"))
        modalidad = c3.selectbox("MODALIDAD", LISTAS["modalidad"], index=None, placeholder="Elige una opción",
                                 key=k("modalidad"))
        c1, c2 = st.columns(2)
        origen = c1.selectbox("ORIGEN", bd.opciones_catalogo("origenes"), index=None, placeholder="Elige una opción",
                              key=k("origen"), on_change=_link_automatico, args=("origen", k("origen"), k("link_origen")))
        destino = c2.selectbox("DESTINO", bd.opciones_catalogo("destinos"), index=None, placeholder="Elige una opción",
                               key=k("destino"), on_change=_link_automatico, args=("destino", k("destino"), k("link_destino")))
        c1, c2 = st.columns(2)
        link_o = c1.text_input("LINK DE ORIGEN (opcional)", key=k("link_origen"),
                               placeholder="Pega aquí la liga de Google Maps (si ya la conocemos, se pone sola)")
        link_d = c2.text_input("LINK DE DESCARGA (opcional)", key=k("link_destino"),
                               placeholder="Pega aquí la liga de Google Maps (si ya la conocemos, se pone sola)")
        if origen and link_o and link_o == bd.link_conocido("origen", origen):
            c1.caption("✅ Link ya conocido de pedidos anteriores")
        if destino and link_d and link_d == bd.link_conocido("destino", destino):
            c2.caption("✅ Link ya conocido de pedidos anteriores")
        c1, c2, c3 = st.columns(3)
        pedido = c1.text_input("PEDIDO", key=k("pedido"))
        folio = c2.text_input("FOLIO CLIENTE", key=k("folio"))
        flete = c3.number_input("FLETE", min_value=0.0, step=100.0, format="%.2f", key=k("flete"))
        c1, c2, c3, c4 = st.columns(4)
        f_carga = c1.date_input("Día cita de carga", value=dia, format="DD/MM/YYYY", key=k("f_carga"))
        h_carga = c2.time_input("Hora cita de carga", value=time(8, 0), key=k("h_carga"))
        f_desc = c3.date_input("Día cita de descarga", value=dia + timedelta(days=1), format="DD/MM/YYYY", key=k("f_desc"))
        h_desc = c4.time_input("Hora cita de descarga", value=time(8, 0), key=k("h_desc"))
        instr = st.text_area("INSTRUCCIONES ESPECIALES", key=k("instr"))
        g1, g2, _ = st.columns([1.3, 1.3, 4])
        generar = g1.button("✅ Generar pedido", type="primary", key=k("generar"))
        borrar = g2.button("🧹 Borrar contenido", key=k("borrar"))

    if borrar:
        confirmar_borrado()
    if generar:
        faltan = [n for n, v in (("TIPO OPERACION", tipo_op), ("ORIGEN", origen), ("DESTINO", destino)) if not v]
        c_carga, c_desc = datetime.combine(f_carga, h_carga), datetime.combine(f_desc, h_desc)
        links_mal = [n for n, v in (("LINK DE ORIGEN", link_o), ("LINK DE DESCARGA", link_d))
                     if v.strip() and not v.strip().lower().startswith(("http://", "https://"))]
        if faltan:
            st.error("Falta capturar: **" + ", ".join(faltan) + "**.")
        elif c_desc < c_carga:
            st.error("La cita de descarga no puede ser antes que la cita de carga.")
        elif links_mal:
            st.error("Revisa " + " y ".join(links_mal) + ": la liga debe empezar con http:// o https://")
        else:
            repetido = bd.viajes_con_pedido([pedido.strip()]) if pedido.strip() else {}
            bd.insertar_viaje({
                "tipo_operacion": tipo_op, "cliente": cliente, "origen": origen, "destino": destino,
                "modalidad": modalidad, "pedido": pedido, "folio_cliente": folio, "flete": flete or None,
                "cita_carga": c_carga, "cita_descarga": c_desc, "instrucciones": instr,
                "link_origen": link_o.strip() or None, "link_destino": link_d.strip() or None,
            }, dia)
            bd.bitacora(USR, "PEDIDO_CREADO", f"pedido {pedido.strip() or '—'}",
                        f"{tipo_op} · {origen} → {destino} · cita carga {c_carga:%d/%m %H:%M} · cita descarga {c_desc:%d/%m %H:%M}")
            bd.aprender_link("origen", origen, link_o)        # la próxima vez el link sale solo
            bd.aprender_link("destino", destino, link_d)
            st.session_state.nuevo_ver = nv + 1          # formulario limpio para el siguiente pedido
            st.session_state.version += 1
            st.session_state.mensaje = (
                f"Pedido generado ✅ Quedó en el Plan del día del {dia:%d/%m/%Y}, listo para que OPS asigne unidad y operador."
                + (f"  ⚠️ Ojo: el pedido {pedido.strip()} ya existía en otro viaje." if repetido else ""))
            st.rerun()

# ================================================================== MONITOREO
# Reemplaza el Excel de seguimiento: todo sale del plan de carga + ZAM + Omnitracs.
ESTATUS1 = {"DISPONIBLE": "⚪ DISPONIBLE", "PROGRAMADO": "🟡 ASIGNADO", "CARGANDO": "🟠 EN CARGA",
            "TRANSITO CARGADO": "🔵 CARGADO", "DESCARGANDO": "🟣 EN DESCARGA"}
ESTATUS1_EXTRA = ["🟤 VACÍO", "↩️ DEVOLUCIÓN", "🤝 PRÉSTAMO"]          # los que el Excel tenía y no son automáticos
AUTOMATICO = "🔄 AUTOMÁTICO"
# Estos estatus viven en el catálogo de Vehículos (SITUACIÓN): al elegirlos allá también cambia, y la unidad no se puede asignar
ESTATUS1_SITUACION = {"CORRALON": "🚫 CORRALÓN", "TALLER": "🔧 TALLER", "SINIESTRO": "🚨 SINIESTRO",
                      "NO DISPONIBLE": "⛔ NO DISPONIBLE"}
SITUACION_DE_TEXTO = {v: k for k, v in ESTATUS1_SITUACION.items()}
OPCIONES_ESTATUS1 = [AUTOMATICO] + list(ESTATUS1.values()) + ESTATUS1_EXTRA + list(ESTATUS1_SITUACION.values())
OPCIONES_ESTATUS2 = [AUTOMATICO, "🟢 EN TRÁNSITO", "🔴 DETENIDA", "🅿️ RESGUARDO"]


def _hm(minutos):
    m = int(abs(minutos))
    return f"{m // 60}:{m % 60:02d} h"


def _para_cita(cita, llego, ahora):
    """Texto tipo Excel 'TIEMPO RESTANTE': faltan / atrasada / ya llegó."""
    if pd.notna(llego):
        return "✔ ya llegó"
    if pd.isna(cita):
        return ""
    minutos = (cita - ahora).total_seconds() / 60
    return f"faltan {_hm(minutos)}" if minutos >= 0 else f"⚠️ atrasada {_hm(minutos)}"


def _alerta(f, ahora):
    """(prioridad, texto). 0 = rojo · 1 = naranja · 2 = en tiempo · 3 = sin viaje (sin alerta)."""
    if not f["_viaje"]:
        return 3, ""
    rojos, naranjas = [], []
    if pd.notna(f["_cita_desc"]) and pd.isna(f["_lleg_desc"]):
        falta = (f["_cita_desc"] - ahora).total_seconds() / 60
        if falta < 0:
            rojos.append(f"🔴 Descarga atrasada {_hm(falta)}")
        elif falta <= cfg.MON_CITA_PROXIMA_HORAS * 60:
            naranjas.append(f"🟠 Descarga en {_hm(falta)}")
    if f["estatus_viaje"] == "PROGRAMADO" and pd.notna(f["_cita_carga"]) and pd.isna(f["_lleg_carga"]) \
            and ahora > f["_cita_carga"]:
        rojos.append(f"🔴 Cita de carga vencida {_hm((f['_cita_carga'] - ahora).total_seconds() / 60)}")
    if f["_sin_pos"]:
        naranjas.append("🟠 Sin posición")
    elif f["_reporto_min"] is not None and f["_reporto_min"] > cfg.MON_SIN_REPORTAR_MIN:
        naranjas.append(f"🟠 Sin reportar {_hm(f['_reporto_min'])}")
    if (f["estatus_viaje"] == "TRANSITO CARGADO" and f["estatus2"].endswith("DETENIDA")
            and f["_detenida_min"] is not None and f["_detenida_min"] > cfg.MON_DETENIDA_TRANSITO_MIN):
        naranjas.append(f"🟠 Cargada detenida {_hm(f['_detenida_min'])}")
    if rojos:
        return 0, " · ".join(rojos + naranjas)
    if naranjas:
        return 1, " · ".join(naranjas)
    return 2, "🟢 En tiempo"


def armar_monitoreo(dia, tipo_sel, incluir_libres):
    """Una fila por tracto (como el Excel): operador, remolque, cliente, estatus, posición, citas y alerta."""
    ahora = pd.Timestamp.now()
    plan = cargar_plan(dia, True)
    abiertos = plan[~plan["estatus"].isin(ESTATUS_CERRADOS)].copy()
    abiertos["clave"] = abiertos["eco"].map(lambda e: omnitracs.normalizar_alias(e) if e else "")
    abiertos = abiertos[abiertos["clave"] != ""].sort_values("cita_carga").drop_duplicates("clave")

    # --- universo: catálogo de unidades (+ unidades del plan que no estén en el catálogo)
    uni = bd.leer_catalogo("unidades")[["eco", "operador", "situacion"]].rename(
        columns={"operador": "operador_habitual"})
    faltan = abiertos[~abiertos["clave"].isin(uni["eco"].map(omnitracs.normalizar_alias))]
    if len(faltan):
        uni = pd.concat([uni, pd.DataFrame({"eco": faltan["eco"], "operador_habitual": None,
                                            "situacion": "OPERANDO"})], ignore_index=True)
    uni["clave"] = uni["eco"].map(omnitracs.normalizar_alias)
    uni["situacion"] = uni["situacion"].fillna("OPERANDO").replace("", "OPERANDO")

    # --- posición Omnitracs (una por ECO)
    pos = una_por_eco(posiciones_por_eco(abiertos["eco"].tolist()))
    pos = pos[["clave", "alias", "fecha", "ignicion", "velocidad", "latitud", "longitud", "en_mov"]]
    t = uni.merge(pos, on="clave", how="left")
    cols_viaje = ["clave", "id", "pedido", "operador", "tipo_operacion", "cliente", "origen", "destino", "remolque_1",
                  "estatus", "cita_carga", "llegada_carga", "cita_descarga", "llegada_descarga", "estadia"]
    t = t.merge(abiertos[cols_viaje].rename(columns={"estatus": "estatus_viaje", "operador": "operador_viaje"}),
                on="clave", how="left")
    t["_viaje"] = t["id"].notna()

    # --- segmento: solo el cliente / tipo de operación elegido
    if tipo_sel != "(Todos)":
        ultimo = bd.ultimo_tipo_por_eco()
        del_tipo = t["tipo_operacion"] == tipo_sel
        libres_del_tipo = (~t["_viaje"]) & t["eco"].map(lambda e: ultimo.get(str(e)) == tipo_sel)
        t = t[del_tipo | (libres_del_tipo if incluir_libres else False)].copy()
    if t.empty:
        return t

    # --- datos de apoyo
    ops = bd.leer_catalogo("operadores").drop_duplicates("nombre").set_index("nombre")["telefono"].to_dict()
    t["operador"] = t["operador_viaje"].where(t["operador_viaje"].notna(), t["operador_habitual"])
    t["telefono"] = t["operador"].map(lambda o: ops.get(o) if o else None)
    t["cliente"] = t["cliente"].where(t["cliente"].notna(), t["tipo_operacion"])

    # --- ESTATUS 1 y ESTATUS 2
    def estatus1(f):
        if f["situacion"] in ESTATUS1_SITUACION:
            return ESTATUS1_SITUACION[f["situacion"]]
        if f["_viaje"]:
            return ESTATUS1.get(f["estatus_viaje"], f["estatus_viaje"] or "🟡 ASIGNADO")
        return ESTATUS1["DISPONIBLE"]
    t["estatus1"] = t.apply(estatus1, axis=1)
    t["_sin_pos"] = t["alias"].isna()

    def estatus2(f):
        if f["_sin_pos"]:
            return "❔ SIN POSICIÓN"
        if f["en_mov"]:
            return "🟢 EN TRÁNSITO"
        return "🅿️ RESGUARDO" if "APAG" in str(f["ignicion"]).upper() else "🔴 DETENIDA"
    t["estatus2"] = t.apply(estatus2, axis=1)
    t["estatus1_auto"], t["estatus2_auto"] = t["estatus1"], t["estatus2"]
    manual = bd.leer_monitoreo_manual()                      # lo que TC puso a mano (vale unas horas)
    m1 = t["eco"].map(lambda e: manual.get(str(e), {}).get("estatus1"))
    m2 = t["eco"].map(lambda e: manual.get(str(e), {}).get("estatus2"))
    t["estatus1"] = m1.where(m1.notna() & ~t["situacion"].isin(list(ESTATUS1_SITUACION)), t["estatus1"])
    t["estatus2"] = m2.where(m2.notna(), t["estatus2"])

    # --- tiempos de reporte y de detenida
    fecha = pd.to_datetime(t["fecha"], errors="coerce")
    t["_reporto_min"] = ((ahora - fecha).dt.total_seconds() / 60).where(fecha.notna())
    t["_reporto_min"] = t["_reporto_min"].astype(object).where(t["_reporto_min"].notna(), None)
    desde = t["alias"].map(bd.leer_referencias())
    desde = pd.to_datetime(desde, errors="coerce")
    det = ((ahora - desde).dt.total_seconds() / 60).where(desde.notna() & ~t["en_mov"].fillna(False).astype(bool))
    t["_detenida_min"] = det.astype(object).where(det.notna(), None)
    t["reporto"] = t["_reporto_min"].map(lambda m: f"hace {_hm(m)}" if m is not None else "")
    t["detenida_desde"] = t["_detenida_min"].map(lambda m: _hm(m) if m is not None else "")

    # --- citas y alertas
    t["_cita_desc"], t["_lleg_desc"] = t["cita_descarga"], t["llegada_descarga"]
    t["_cita_carga"], t["_lleg_carga"] = t["cita_carga"], t["llegada_carga"]
    t["para_carga"] = t.apply(lambda f: _para_cita(f["cita_carga"], f["llegada_carga"], ahora) if f["_viaje"] else "", axis=1)
    t["para_descarga"] = t.apply(lambda f: _para_cita(f["cita_descarga"], f["llegada_descarga"], ahora) if f["_viaje"] else "", axis=1)
    al = t.apply(lambda f: _alerta(f, ahora), axis=1)
    t["_prio"], t["alerta"] = [a[0] for a in al], [a[1] for a in al]

    # --- mapa / observaciones
    t["mapa"] = [f"https://www.google.com/maps?q={la},{lo}" if pd.notna(la) and pd.notna(lo) else None
                 for la, lo in zip(t["latitud"], t["longitud"])]
    t["observaciones"] = t["eco"].map(lambda e: manual.get(str(e), {}).get("texto", ""))
    t["en_mov"] = t["en_mov"].fillna(False).astype(bool)
    return t.sort_values(["_prio", "eco"]).reset_index(drop=True)


COLOR_MAPA = {0: [214, 40, 40, 230], 1: [245, 130, 32, 230], 2: [34, 160, 80, 230], 3: [110, 120, 130, 220]}


def _vista_mapa(m):
    """Encuadre del mapa calculado a mano (pydeck falla cuando hay 1 o 2 puntos). Funciona con cualquier cantidad."""
    import math
    lat, lon = m["lat"].astype(float), m["lon"].astype(float)
    if len(m) >= 20:                                   # con muchas unidades, ignora las más alejadas (2.5 % por lado)
        lat0, lat1, lon0, lon1 = lat.quantile(0.025), lat.quantile(0.975), lon.quantile(0.025), lon.quantile(0.975)
    else:
        lat0, lat1, lon0, lon1 = lat.min(), lat.max(), lon.min(), lon.max()
    span = max(lat1 - lat0, lon1 - lon0, 0.02) * 1.35  # grados; mínimo para que 1 punto no quede pegado
    zoom = max(3.0, min(12.0, math.log2(972 / span)))
    return pdk.ViewState(latitude=float((lat0 + lat1) / 2), longitude=float((lon0 + lon1) / 2), zoom=zoom)


def dibujar_mapa(d, con_alias):
    """Mapa: un punto por unidad con su alias. Color = semáforo (rojo/naranja/verde) o gris si no tiene viaje."""
    m = d.dropna(subset=["latitud", "longitud"]).rename(columns={"latitud": "lat", "longitud": "lon"})
    if m.empty:
        st.info("No hay coordenadas para mostrar en el mapa.")
        return
    m = m[["alias", "lat", "lon", "color", "tip"]].copy()
    capas = [pdk.Layer("ScatterplotLayer", data=m, get_position="[lon, lat]", get_fill_color="color",
                       get_radius=300, radius_min_pixels=5, radius_max_pixels=14, pickable=True)]
    if con_alias:
        capas.append(pdk.Layer("TextLayer", data=m, get_position="[lon, lat]", get_text="alias",
                               get_size=13, get_color=[20, 20, 20, 255], get_pixel_offset=[0, -14],
                               get_text_anchor="'middle'", get_alignment_baseline="'bottom'",
                               font_weight=700, pickable=False))
    vista = _vista_mapa(m)
    st.pydeck_chart(pdk.Deck(layers=capas, initial_view_state=vista, map_style="light", tooltip={"text": "{tip}"}))


def tabla_a_excel(df):
    import io
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Monitoreo")
    return buf.getvalue()


def _refrescar_monitoreo():
    """Vuelve a dibujar el Monitoreo (solo el fragmento si se puede; si no, toda la pantalla)."""
    try:
        st.rerun(scope="fragment")
    except st.errors.StreamlitAPIException:
        st.rerun()


def _aplicar_monitoreo(cambios, situaciones, resumen):
    """Guarda lo escrito por TC en el Monitoreo, sincroniza la SITUACIÓN del catálogo y lo anota en la bitácora."""
    bd.guardar_monitoreo(cambios)
    for eco_u, sit in situaciones.items():
        antes_sit = bd.leer_catalogo("unidades").set_index("eco")["situacion"].get(eco_u)
        bd.poner_situacion(eco_u, sit)
        bd.bitacora(USR, "UNIDAD_SITUACION", f"unidad {eco_u}", f"Situación en Vehículos: {antes_sit} → {sit}")
    for eco_u, c in cambios.items():
        partes = []
        if "texto" in c:
            partes.append(f"OBSERVACIONES: {c['texto'] or '(borrada)'}")
        partes += [f"{campo}: {a} → {n}" for e, campo, a, n, _ in resumen if e == eco_u]
        bd.bitacora(USR, "MONITOREO_CAMBIO", f"unidad {eco_u}", "; ".join(partes))
    st.session_state.mon_ver = st.session_state.get("mon_ver", 0) + 1
    st.session_state.pop("mon_cache", None)
    extra = f" · {len(situaciones)} situación(es) cambiadas en Vehículos" if situaciones else ""
    st.toast(f"Guardado ({len(cambios)} unidades){extra}", icon="📝")


@st.fragment(run_every=cfg.SEGUNDOS_ENTRE_CONSULTAS)
def panel_monitoreo(dia, tipo_sel, puede_editar):
    if (cfg.CONSULTAR_AUTOMATICAMENTE and omnitracs.credenciales_configuradas()
            and bd.segundos_desde_ultima_consulta() >= cfg.SEGUNDOS_ENTRE_CONSULTAS - 2):
        try:
            omnitracs.actualizar_en_bd()
        except Exception:
            pass  # el error queda guardado y se muestra abajo

    if not omnitracs.credenciales_configuradas():
        st.warning("Falta poner tu usuario, contraseña y código de cliente de Omnitracs en configuracion.py")
    err = bd.leer_meta("ultimo_error")
    if err:
        st.error(f"Último error al consultar Omnitracs: {err}")
    st.caption(f"Última actualización: {bd.leer_meta('ultima_consulta') or 'nunca'}")

    if bd.leer_posiciones().empty:
        st.info("Todavía no hay posiciones guardadas.")
        return

    # ------------------------------ qué se está viendo
    segmento = tipo_sel != "(Todos)"
    incluir = True
    if segmento:
        incluir = st.checkbox("➕ Incluir unidades libres que últimamente trabajaron este cliente", value=True,
                              key="mon_incluir_libres",
                              help="Son unidades sin viaje abierto cuyo último viaje (últimos 30 días) fue de este tipo de operación. "
                                   "Sirven para decidir a quién asignar el siguiente pedido.")
    vista = st.radio("Ver", ["🚚 Tractos", "🛞 Remolques", "❓ Unidades fuera de catálogo"],
                     horizontal=True, key="vista_mon", label_visibility="collapsed")

    pos_todas = posiciones_por_eco()
    if vista.startswith("🚚"):
        clave_ed = f"mon_ed_{tipo_sel}_{incluir}_{st.session_state.get('mon_ver', 0)}"
        pendiente_obs = bool(st.session_state.get(clave_ed, {}).get("edited_rows"))
        if pendiente_obs and "mon_cache" in st.session_state:      # no mover las filas mientras se escribe
            tabla = st.session_state.mon_cache
        else:
            tabla = armar_monitoreo(dia, tipo_sel, incluir)
            st.session_state.mon_cache = tabla
        if tabla.empty:
            st.info(f"No hay unidades de {tipo_sel} en el plan. Quita el filtro de la izquierda para ver todas.")
            return

        # ------------------------------ tarjetas (resumen para decidir rápido)
        titulo = f" · {tipo_sel}" if segmento else ""
        st.markdown(f"**📊 Resumen{titulo}**")
        k = st.columns(6)
        k[0].metric("Unidades", len(tabla))
        k[1].metric("🟢 En tránsito", int((tabla["estatus2"] == "🟢 EN TRÁNSITO").sum()))
        k[2].metric("🔴 Detenidas", int((tabla["estatus2"] == "🔴 DETENIDA").sum()))
        k[3].metric("⚪ Disponibles", int((tabla["estatus1"] == ESTATUS1["DISPONIBLE"]).sum()),
                    help="Unidades sin viaje abierto, listas para asignar.")
        k[4].metric("🔴 Alertas rojas", int((tabla["_prio"] == 0).sum()), help="Citas vencidas.")
        k[5].metric("🟠 Alertas naranja", int((tabla["_prio"] == 1).sum()),
                    help="Cita próxima, sin reportar, o cargada detenida mucho tiempo.")

        filtro = st.radio("Filtro rápido", ["Todas", "🔴🟠 Con alerta", "🟢 En tránsito", "🔴 Detenidas",
                                            "⚪ Disponibles"], horizontal=True, key="mon_filtro")
        datos = tabla
        if filtro.startswith("🔴🟠"):
            datos = tabla[tabla["_prio"].isin([0, 1])]
        elif filtro.startswith("🟢"):
            datos = tabla[tabla["estatus2"] == "🟢 EN TRÁNSITO"]
        elif filtro.startswith("🔴 "):
            datos = tabla[tabla["estatus2"] == "🔴 DETENIDA"]
        elif filtro.startswith("⚪"):
            datos = tabla[tabla["estatus1"] == ESTATUS1["DISPONIBLE"]]

        orden = ["alerta", "eco", "estatus1", "estatus2", "operador", "telefono", "cliente", "pedido", "origen", "destino",
                 "remolque_1", "velocidad", "ignicion", "reporto", "detenida_desde", "cita_carga", "para_carga",
                 "cita_descarga", "para_descarga", "estadia", "mapa", "observaciones"]
        st.caption(f"{len(datos)} unidades en esta vista. Ordenadas: primero las que necesitan atención. "
                   + ("Puedes cambiar **ESTATUS 1, ESTATUS 2 y OBSERVACIONES** y presionar Guardar."
                      if puede_editar else "Solo el perfil **TC** puede cambiar los estatus y las observaciones."))
        cc = {"alerta": st.column_config.TextColumn("ALERTA", width="medium"),
              "eco": st.column_config.TextColumn("ECO", width="small"),
              "estatus1": st.column_config.SelectboxColumn(
                  "ESTATUS 1" + (" ✏️" if puede_editar else ""), width="medium",
                  options=list(dict.fromkeys(OPCIONES_ESTATUS1 + datos["estatus1"].dropna().tolist())),
                  help="Elige 🔄 AUTOMÁTICO para que el TMS lo calcule solo. CORRALÓN también pone la unidad en corralón en el catálogo."),
              "estatus2": st.column_config.SelectboxColumn(
                  "ESTATUS 2" + (" ✏️" if puede_editar else ""), width="medium",
                  options=list(dict.fromkeys(OPCIONES_ESTATUS2 + datos["estatus2"].dropna().tolist()))),
              "operador": "OPERADOR", "telefono": "TELÉFONO",
              "cliente": "CLIENTE", "pedido": "PEDIDO", "origen": "ORIGEN", "destino": "DESTINO",
              "remolque_1": "REMOLQUE", "velocidad": st.column_config.NumberColumn("VEL.", format="%d", width="small"),
              "ignicion": "IGNICIÓN", "reporto": "REPORTÓ", "detenida_desde": "DETENIDA DESDE",
              "cita_carga": st.column_config.DatetimeColumn("CITA CARGA", format="DD/MM HH:mm"),
              "para_carga": "PARA CARGA",
              "cita_descarga": st.column_config.DatetimeColumn("CITA DESCARGA", format="DD/MM HH:mm"),
              "para_descarga": "PARA DESCARGA", "estadia": "ESTADÍA",
              "mapa": st.column_config.LinkColumn("MAPA", display_text="🗺️ Ver", width="small"),
              "observaciones": st.column_config.TextColumn("OBSERVACIONES ✏️", width="large")}
        editado = st.data_editor(
            datos[orden], column_config=cc, hide_index=True, key=clave_ed, height=520,
            disabled=[c for c in orden if c not in ("observaciones", "estatus1", "estatus2") or not puede_editar])
        if puede_editar:
            gc1, gc2, _ = st.columns([1.4, 1.4, 4])
            if gc1.button("💾 Guardar cambios", type="primary", key="mon_guardar"):
                cambios, situaciones, resumen = {}, {}, []
                for idx, r in editado.iterrows():
                    antes = datos.loc[idx]
                    eco_u, c = str(antes["eco"]), {}
                    if (r["observaciones"] or "") != (antes["observaciones"] or ""):
                        c["texto"] = r["observaciones"]
                    if r["estatus1"] != antes["estatus1"]:
                        nuevo1 = r["estatus1"]
                        if nuevo1 == AUTOMATICO and antes["situacion"] in ESTATUS1_SITUACION:
                            pass                                          # ya es el automático de su situación
                        elif nuevo1 in SITUACION_DE_TEXTO:                # vive en el catálogo de unidades
                            situaciones[eco_u] = SITUACION_DE_TEXTO[nuevo1]
                            c["estatus1"] = None
                            resumen.append((eco_u, "ESTATUS 1", antes["estatus1"], nuevo1, "también cambia la SITUACIÓN en Vehículos"))
                        else:
                            c["estatus1"] = None if nuevo1 == AUTOMATICO else nuevo1
                            if antes["situacion"] in ESTATUS1_SITUACION:   # sale de taller/corralón/...
                                situaciones[eco_u] = "OPERANDO"
                            resumen.append((eco_u, "ESTATUS 1", antes["estatus1"], nuevo1,
                                            "la unidad vuelve a OPERANDO en Vehículos" if eco_u in situaciones else ""))
                    if r["estatus2"] != antes["estatus2"]:
                        c["estatus2"] = None if r["estatus2"] == AUTOMATICO else r["estatus2"]
                        resumen.append((eco_u, "ESTATUS 2", antes["estatus2"], r["estatus2"], ""))
                    if c:
                        cambios[eco_u] = c
                if resumen:                                                # hay estatus por cambiar: primero se pregunta
                    st.session_state.mon_confirmar = {"cambios": cambios, "situaciones": situaciones, "resumen": resumen}
                else:
                    _aplicar_monitoreo(cambios, situaciones, [])
                    _refrescar_monitoreo()
        pend_c = st.session_state.get("mon_confirmar")
        if puede_editar and pend_c:
            with st.container(border=True):
                st.warning("**¿Cambiar estos estatus o mantener los actuales?**")
                st.dataframe(pd.DataFrame(pend_c["resumen"], columns=["ECO", "CAMPO", "ESTÁ AHORA", "SE CAMBIARÍA A", "NOTA"]),
                             hide_index=True)
                q1, q2, _ = st.columns([1.6, 2.2, 3])
                if q1.button("✅ Sí, cambiar", type="primary", key="mon_si"):
                    _aplicar_monitoreo(pend_c["cambios"], pend_c["situaciones"], pend_c["resumen"])
                    st.session_state.pop("mon_confirmar", None)
                    _refrescar_monitoreo()
                if q2.button("❌ No, mantener el estatus actual", key="mon_no"):
                    solo_texto = {e: {"texto": c["texto"]} for e, c in pend_c["cambios"].items() if "texto" in c}
                    _aplicar_monitoreo(solo_texto, {}, [])
                    bd.bitacora(USR, "MONITOREO_ESTATUS_NO_CAMBIADO", "", "; ".join(f"{e} {c}: se mantuvo {a}" for e, c, a, _, _ in pend_c["resumen"]))
                    st.session_state.pop("mon_confirmar", None)
                    st.toast("Se mantuvieron los estatus actuales (las observaciones sí se guardaron).", icon="↩️")
                    _refrescar_monitoreo()
        gc2 = gc2 if puede_editar else st.container()
        gc2.download_button("⬇️ Descargar esta vista (Excel)",
                            tabla_a_excel(datos[orden].rename(columns=str.upper)),
                            file_name=f"monitoreo_{datetime.now():%Y%m%d_%H%M}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        mapa = datos.copy()
        mapa["color"] = mapa["_prio"].map(COLOR_MAPA)
        mapa["tip"] = (mapa["alias"].fillna(mapa["eco"]).astype(str) + "\n" + mapa["estatus1"] + " · " + mapa["estatus2"]
                       + "\n" + mapa["operador"].fillna("").astype(str) + "\n→ " + mapa["destino"].fillna("").astype(str)
                       + "\n" + mapa["alerta"])
        mapa["alias"] = mapa["alias"].fillna(mapa["eco"])
        st.caption("🗺️ Mapa: 🔴 alerta roja · 🟠 alerta naranja · 🟢 en tiempo · ⚪ sin viaje")
        con_alias = st.checkbox("Mostrar alias en el mapa", value=True, key="alias_mapa",
                                help="Si hay muchos puntos juntos, acerca el mapa para leer cada alias.")
        dibujar_mapa(mapa, con_alias)
    else:
        # remolques / fuera de catálogo
        tractos_norm = {omnitracs.normalizar_alias(e) for e in bd.opciones_catalogo("unidades")}
        d = pos_todas[pos_todas["tipo"] == ("REMOLQUE" if vista.startswith("🛞") else "FUERA")].copy()
        if segmento and vista.startswith("🛞"):
            plan_a = cargar_plan(dia, True)
            plan_a = plan_a[(~plan_a["estatus"].isin(ESTATUS_CERRADOS)) & (plan_a["tipo_operacion"] == tipo_sel)]
            rem = {omnitracs.normalizar_alias(r) for r in plan_a["remolque_1"].dropna()}
            d = d[d["alias"].map(omnitracs.normalizar_alias).isin(rem)]
            st.caption(f"Solo remolques de viajes abiertos de {tipo_sel}.")
        d["movimiento"] = d["en_mov"].map({True: "🟢 En movimiento", False: "⚪ Detenida"})
        st.caption(f"{len(d)} unidades en esta vista.")
        st.dataframe(d[["alias", "movimiento", "fecha", "ignicion", "velocidad", "latitud", "longitud"]]
                     .rename(columns=str.upper), hide_index=True)
        d["color"] = d["en_mov"].map(lambda v: [34, 160, 80, 230] if v else [214, 40, 40, 230])
        d["tip"] = d["alias"].astype(str) + "\n" + d["movimiento"] + "\nVelocidad: " + d["velocidad"].astype(str) + " km/h"
        dibujar_mapa(d, st.checkbox("Mostrar alias en el mapa", value=True, key="alias_mapa2"))

    with st.expander("📡 Cobertura de Omnitracs (toda la flota)"):
        tractos_all = pos_todas[pos_todas["tipo"] == "TRACTO"]
        rem_all = pos_todas[pos_todas["tipo"] == "REMOLQUE"]
        fuera = pos_todas[pos_todas["tipo"] == "FUERA"]
        pct = lambda rep, total: f"{rep / total * 100:.1f}%" if total else "0.0%"
        tot_tr, tot_rem = len(bd.opciones_catalogo("unidades")), len(bd.opciones_catalogo("remolques"))
        rep_tr = int(tractos_all["clave"].nunique())
        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("Tractos reportando", rep_tr, help=f"{rep_tr} de {tot_tr} tractos del catálogo.")
        a2.metric("% tractos", pct(rep_tr, tot_tr))
        a3.metric("Remolques reportando", len(rem_all), help=f"{len(rem_all)} de {tot_rem} remolques del catálogo.")
        a4.metric("% remolques", pct(len(rem_all), tot_rem))
        a5.metric("Fuera de catálogo", len(fuera))


if "mon" in T:
  with T["mon"]:
    st.subheader("Monitoreo de unidades" + ("" if tipo_sel == "(Todos)" else f"  ·  {tipo_sel}"))
    if tipo_sel != "(Todos)":
        st.caption(f"Solo se muestran las unidades de **{tipo_sel}** (filtro de la izquierda). "
                   "Para ver toda la flota, cambia el filtro a (Todos).")
    if st.button("🔄 Actualizar ahora"):
        try:
            n = omnitracs.actualizar_en_bd()
            st.success(f"{n} unidades actualizadas ✅")
        except Exception as e:
            st.error(f"No se pudo: {e}")
    panel_monitoreo(dia, tipo_sel, perm["monitoreo_editar"])


# ================================================================== CATÁLOGOS
@st.cache_data(ttl=300, show_spinner=False)
def _excel_catalogos(con_datos, version):
    return catalogos_excel.generar_plantilla(con_datos)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

if "cat" in T:
  with T["cat"]:
    st.subheader("Catálogos")
    st.caption("Se llenan una vez y ahorran mucho tecleo: aparecen como listas desplegables en el plan.")

    with st.expander("📥 Cargar catálogos desde Excel (plantilla o reporte de viajes)"):
        st.write("1. Descarga la plantilla (vacía, o con lo que ya tienes) · 2. Llénala o agrégale filas · "
                 "3. Súbela aquí. **Cargar nunca borra nada**: solo agrega lo nuevo y completa lo que falte.")
        c1, c2 = st.columns(2)
        c1.download_button("⬇️ Plantilla vacía", _excel_catalogos(False, 0),
                           file_name="plantilla_catalogos.xlsx", mime=XLSX)
        c2.download_button("⬇️ Catálogos actuales (respaldo)", _excel_catalogos(True, st.session_state.version),
                           file_name=f"catalogos_{date.today()}.xlsx", mime=XLSX)
        archivo = st.file_uploader("Sube tu Excel", type=["xlsx"])
        if archivo is not None and st.button("Cargar catálogos", type="primary"):
            try:
                tipo_arch, resumen = catalogos_excel.cargar_catalogos(archivo)
                partes = [f"{t}: {r['nuevos']} nuevos, {r['actualizados']} actualizados" for t, r in resumen.items()]
                bd.bitacora(USR, "CATALOGO_EXCEL_CARGADO", tipo_arch, " · ".join(partes))
                st.session_state.version += 1
                st.session_state.mensaje = f"Archivo ({tipo_arch}) cargado ✅  " + " · ".join(partes)
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo cargar: {e}")

    def editor_catalogo(tabla, config=None):
        df_c = bd.leer_catalogo(tabla)
        st.caption(f"{len(df_c)} registros. La lupa de la tabla sirve para buscar.")
        ed = st.data_editor(df_c, key=f"cat_{tabla}_{st.session_state.version}", num_rows="dynamic",
                            hide_index=True, column_config=config or {})
        if st.button("💾 Guardar", key=f"btn_{tabla}"):
            antes_n = len(df_c)
            bd.reemplazar_catalogo(tabla, ed)
            bd.bitacora(USR, "CATALOGO_GUARDADO", tabla, f"Registros antes: {antes_n} · después: {len(ed)}")
            st.session_state.version += 1
            st.session_state.mensaje = "Catálogo guardado ✅"
            st.rerun()

    sub = st.tabs(["🚚 Vehículos", "🛞 Remolques", "👷 Operadores", "🏢 Clientes",
                   "📍 Orígenes", "🏁 Destinos", "🏷️ Tipos de operación", "⏱️ Estadías", "🔄 Viajes abiertos ZAM"])
    with sub[0]:
        st.caption("Solo las unidades en SITUACIÓN = OPERANDO se pueden asignar a un viaje.")
        editor_catalogo("unidades", {
            "eco": "ECO",
            "operador": st.column_config.SelectboxColumn(
                "OPERADOR HABITUAL", options=bd.opciones_catalogo("operadores", "nombre")),
            "situacion": st.column_config.SelectboxColumn("SITUACIÓN", options=SITUACIONES),
            "placas": "PLACAS"})
    with sub[1]:
        editor_catalogo("remolques", {"remolque": "REMOLQUE", "tipo": "TIPO"})
    with sub[2]:
        editor_catalogo("operadores", {"nomina": "NÓMINA", "nombre": "NOMBRE", "telefono": "TELÉFONO"})
    with sub[3]:
        editor_catalogo("clientes", {"nombre": "CLIENTE", "rfc": "RFC"})
    with sub[4]:
        editor_catalogo("origenes", {"nombre": "ORIGEN", "direccion": "DIRECCIÓN", "link": "LINK"})
    with sub[5]:
        editor_catalogo("destinos", {"nombre": "DESTINO", "localidad": "LOCALIDAD",
                                     "direccion": "DIRECCIÓN", "link": "LINK"})
    with sub[6]:
        editor_catalogo("tipos_operacion", {
            "nombre": "TIPO OPERACIÓN",
            "cliente": st.column_config.SelectboxColumn("CLIENTE", options=bd.opciones_catalogo("clientes"))})
    with sub[7]:
        st.caption("Tiempo libre (en horas, por parada: carga y descarga) de cada cliente. Lo que pase de ese tiempo "
                   "se cuenta como ESTADÍA en el Plan del día. Un cliente que NO esté aquí, o con APLICA = NO, "
                   "aparece como N/A. El nombre se empata por palabras (GRUPO VIDA aplica a GRUPO INDUSTRIAL VIDA).")
        editor_catalogo("estadias", {
            "cliente": "CLIENTE",
            "aplica": st.column_config.SelectboxColumn("APLICA ESTADIA", options=["SI", "NO"]),
            "horas_libres": "TIEMPO LIBRE (H)"})
    with sub[8]:
        st.caption(f"Se actualiza solo cada {cfg.MINUTOS_ENTRE_SYNC_ZAM} minutos desde {cfg.ZAM_URL}. "
                   "Da de alta ECOs (como OPERANDO) y nóminas nuevas, guarda las placas del tracto y "
                   "marca como ACTIVOS a la unidad y al operador de cada viaje 'open'.")
        c1, c2 = st.columns([1, 3])
        if c1.button("🔄 Actualizar ahora", key="btn_zam"):
            try:
                with st.spinner("Consultando viajes abiertos en ZAM..."):
                    zam.actualizar_ahora()
                st.session_state.version += 1
                st.session_state.mensaje = "Catálogo actualizado desde ZAM ✅  " + (bd.leer_meta("zam_ultimo_resumen") or "")
                st.rerun()
            except Exception as e:
                st.error(f"No se pudo actualizar desde ZAM: {e}")
        c2.write(f"**{estado_zam()}** · {bd.leer_meta('zam_ultimo_resumen') or ''}")
        todos_zam = bd.leer_zam_todos()
        if not todos_zam.empty:
            conteo = todos_zam["status"].fillna("(vacío)").value_counts()
            st.caption("Estatus que trae la vista ZAM (para saber cuáles cuentan como finalizado/cancelado, ver configuracion.py): "
                       + " · ".join(f"{k}: {v}" for k, v in conteo.items()))
        z = bd.leer_zam()
        if z.empty:
            st.info("Todavía no hay viajes de ZAM guardados.")
        else:
            ops = bd.leer_catalogo("operadores")
            z = z.merge(ops[["nomina", "nombre"]], on="nomina", how="left")
            sin_nombre = int(z["nombre"].isna().sum())
            if sin_nombre:
                st.warning(f"{sin_nombre} nóminas de ZAM no tienen nombre en el catálogo de operadores. "
                           "Complétalo en la pestaña 👷 Operadores.")
            st.dataframe(z[["shipment_id", "eco", "placas", "nomina", "nombre", "origen_id", "salida_plan",
                            "destino_id", "llegada_plan"]].rename(columns={
                "shipment_id": "VIAJE ZAM", "eco": "ECO", "placas": "PLACAS", "nomina": "NÓMINA",
                "nombre": "OPERADOR", "origen_id": "ORIGEN (ID)", "salida_plan": "SALIDA PLANEADA",
                "destino_id": "DESTINO (ID)", "llegada_plan": "LLEGADA PLANEADA"}), hide_index=True)

# ================================================================== EXPORTAR
if "exp" in T:
  with T["exp"]:
    st.subheader("Exportar a CSV")
    rango = st.date_input("¿Qué días quieres?", value=(dia, dia), format="DD/MM/YYYY")
    if isinstance(rango, (tuple, list)) and len(rango) == 2:
        desde, hasta = rango
        datos = calculos.agregar_calculados(bd.leer_viajes(desde, hasta))
        claves = [c.clave for c in COLUMNAS]
        elegidas = st.multiselect("Columnas", claves, default=claves,
                                  format_func=lambda k: {"finalizado": "FINALIZADO ✅", "cancelado": "CANCELADO ❌"}.get(k, TITULO_POR_CLAVE[k]))
        salida = datos[["fecha_plan"] + elegidas].copy()
        for k in elegidas:
            if TIPO_POR_CLAVE[k] == "fecha":
                salida[k] = salida[k].dt.strftime("%Y-%m-%d %H:%M")
        for k in elegidas:
            if TIPO_POR_CLAVE[k] == "check":
                salida[k] = salida[k].map({True: "SI", False: "NO"})
        salida = salida.rename(columns={**TITULO_POR_CLAVE, "fecha_plan": "DÍA PLAN",
                                        "finalizado": "FINALIZADO", "cancelado": "CANCELADO"})
        st.write(f"{len(salida)} viajes entre {desde:%d/%m/%Y} y {hasta:%d/%m/%Y}")
        st.dataframe(salida, hide_index=True)
        st.download_button(
            "⬇️ Descargar CSV", salida.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"plan_carga_{desde}_a_{hasta}.csv", mime="text/csv", type="primary",
            on_click=bd.bitacora, args=(USR, "EXPORTAR_CSV", f"{desde} a {hasta}", f"{len(salida)} viajes"),
        )
    else:
        st.info("Elige la fecha final del rango.")

# ================================================================== USUARIOS Y BITÁCORA (solo MASTER)
if "seg" in T and es_master:        # doble candado: la pestaña solo existe para MASTER y se revisa otra vez aquí
  with T["seg"]:
    st.subheader("Usuarios y bitácora")
    sub_u, sub_b = st.tabs(["👥 Usuarios", "🧾 Bitácora (quién hizo qué)"])
    with sub_u:
        st.markdown("**Crear usuario nuevo**")
        st.caption("Perfiles: **ATC** captura pedidos · **OPS** asigna unidad y operador · **TC** registra tiempos y estatus · "
                   "**TODOS** solo consulta. Los usuarios MASTER no se crean desde aquí.")
        nu = st.session_state.get("usr_ver", 0)
        with st.form(f"form_usuario_{nu}"):
            c1, c2 = st.columns(2)
            f_user = c1.text_input("USUARIO", help="De 3 a 20 letras o números, sin espacios. Ej: JPEREZ")
            f_nombre = c2.text_input("NOMBRE")
            c3, c4 = st.columns(2)
            f_pwd = c3.text_input("CONTRASEÑA", type="password", help="Mínimo 8 caracteres.")
            f_perfil = c4.selectbox("PERFIL", bd.PERFILES_CREABLES)
            crear = st.form_submit_button("➕ Crear usuario", type="primary")
        if crear:
            ok, msg = bd.crear_usuario(USR, f_user, f_nombre, f_pwd, f_perfil)
            if ok:
                st.session_state.usr_ver = nu + 1
                st.session_state.mensaje = msg + " Entrégale su usuario y contraseña."
                st.rerun()
            st.error(msg)

        st.markdown("**Usuarios registrados**")
        usuarios = bd.listar_usuarios()
        vista_u = usuarios.assign(activo=usuarios["activo"].map({1: "✅ Activo", 0: "⛔ Desactivado"})).rename(columns={
            "usuario": "USUARIO", "nombre": "NOMBRE", "perfil": "PERFIL", "activo": "ESTADO", "creado": "CREADO",
            "creado_por": "CREADO POR", "ultimo_acceso": "ÚLTIMO ACCESO"})
        st.dataframe(vista_u, hide_index=True)

        normales = usuarios[usuarios["perfil"] != "MASTER"]
        if not normales.empty:
            with st.expander("Desactivar / activar usuario o restablecer su contraseña"):
                elegido = st.selectbox("Usuario", normales["usuario"].tolist(),
                                       format_func=lambda u: f"{u} · {normales.set_index('usuario').loc[u, 'nombre']}")
                esta_activo = bool(normales.set_index("usuario").loc[elegido, "activo"])
                b1, b2 = st.columns(2)
                if b1.button("⛔ Desactivar" if esta_activo else "✅ Activar", key="usr_toggle"):
                    ok, msg = bd.cambiar_usuario(USR, elegido, activo=not esta_activo)
                    st.session_state.mensaje = f"{elegido}: {'desactivado' if esta_activo else 'activado'}"
                    st.rerun()
                nueva = b2.text_input("Nueva contraseña (mínimo 8 caracteres)", type="password", key="usr_nueva")
                if b2.button("🔑 Restablecer contraseña", key="usr_reset"):
                    ok, msg = bd.cambiar_usuario(USR, elegido, nueva_pwd=nueva)
                    if ok:
                        st.session_state.mensaje = f"Contraseña de {elegido} restablecida."
                        st.rerun()
                    st.error(msg)

    with sub_b:
        st.caption("Aquí queda anotado todo lo que se hace en el TMS: inicios de sesión, cambios al plan, pedidos nuevos, "
                   "estatus del monitoreo, catálogos, usuarios y lo que el sistema hace solo.")
        rango_b = st.date_input("Días", value=(date.today() - timedelta(days=7), date.today()), format="DD/MM/YYYY", key="bit_rango")
        if isinstance(rango_b, (tuple, list)) and len(rango_b) == 2:
            bit = bd.leer_bitacora(rango_b[0], rango_b[1])
            f1, f2, f3 = st.columns(3)
            quienes = f1.multiselect("Usuario", sorted(bit["usuario"].dropna().unique()))
            acciones = f2.multiselect("Acción", sorted(bit["accion"].dropna().unique()))
            texto = f3.text_input("Buscar (pedido, unidad, nombre…)")
            if quienes:
                bit = bit[bit["usuario"].isin(quienes)]
            if acciones:
                bit = bit[bit["accion"].isin(acciones)]
            if texto.strip():
                t_ = texto.strip().lower()
                bit = bit[bit.apply(lambda f: t_ in " ".join(str(v) for v in f.values).lower(), axis=1)]
            st.write(f"{len(bit)} registros")
            vista_b = bit.rename(columns=str.upper)
            st.dataframe(vista_b, hide_index=True, height=480, column_config={
                "DETALLE": st.column_config.TextColumn("DETALLE", width="large")})
            st.download_button("⬇️ Descargar bitácora (Excel)", tabla_a_excel(vista_b),
                               file_name=f"bitacora_{rango_b[0]}_a_{rango_b[1]}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.info("Elige la fecha final del rango.")

estilo.pie()
