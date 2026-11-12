# ==========================================================
#  estilo.py - Identidad Estrella Roja (formato fijo, sin animaciones)
#  Colores oficiales: azul #005DB4 (dominante), rojo #C8002D (acento),
#  marino #08457C, cyan #39C0E0, gris #636663. Tipografia Poppins.
# ==========================================================
import base64
from pathlib import Path

import streamlit as st

CARPETA = Path(__file__).parent / "assets"
LOGO = CARPETA / "logo-principal-er-oficial.png"            # sobre blanco
LOGO_CONTORNO = CARPETA / "logo-principal-er-contorno-blanco.png"  # sobre color
ISOTIPO = CARPETA / "isotipo-estrella.png"

AZUL, ROJO, MARINO, CYAN, GRIS = "#005DB4", "#C8002D", "#08457C", "#39C0E0", "#636663"
EXITO, ERROR = [22, 163, 74, 230], [220, 38, 38, 230]       # colores funcionales para el mapa


def _b64(ruta):
    return base64.b64encode(Path(ruta).read_bytes()).decode()


# Tractocamion con caja seca (vista lateral) para el indicador "procesando"
_TRACTO = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 96 40'>"
    "<rect x='2' y='4' width='58' height='24' rx='2' fill='%23005DB4'/>"
    "<rect x='2' y='24' width='58' height='3' fill='%23C8002D'/>"
    "<path d='M62 12h16l10 10v8H62z' fill='%2308457C'/>"
    "<path d='M66 15h11l6 7H66z' fill='%23CCDFF0'/>"
    "<rect x='60' y='28' width='30' height='3' fill='%23636663'/>"
    "<g fill='%23333'><circle cx='14' cy='33' r='5'/><circle cx='26' cy='33' r='5'/>"
    "<circle cx='70' cy='33' r='5'/><circle cx='83' cy='33' r='5'/></g>"
    "</svg>"
)
TRACTO_URI = f'url("data:image/svg+xml;utf8,{_TRACTO}")'


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');

html, body, [class*="st-"], .stMarkdown, .stText, button, input, textarea, select {{
    font-family: 'Poppins', Arial, Helvetica, sans-serif;
}}
/* los iconos de Streamlit usan su propia fuente: no tocarla */
[data-testid="stIconMaterial"], .material-symbols-rounded, [data-testid="stExpanderIcon"] {{
    font-family: 'Material Symbols Rounded' !important;
}}
h1, h2, h3, h4 {{ color: {AZUL} !important; font-family: 'Poppins', Arial, sans-serif !important; font-weight: 700 !important; }}

/* barra superior de Streamlit en colores de marca */
[data-testid="stDecoration"] {{ background: {AZUL}; height: 4px; }}

/* ---------- encabezado fijo: panel azul con logo y linea roja de acento ---------- */
.er-banner {{
    background: {AZUL}; border-radius: 8px; padding: 14px 22px; margin-bottom: 14px;
    border-bottom: 4px solid {ROJO};
}}
.er-banner .fila {{ display: flex; align-items: center; gap: 18px; }}
.er-banner img {{ height: 52px; }}
.er-banner .titulo {{ color: #FFFFFF; font-weight: 700; font-size: 1.55rem; line-height: 1.2; }}
.er-banner .sub {{ color: #CCDFF0; font-weight: 500; font-size: .9rem; }}
/* ---------- metricas, pestanas, botones ---------- */
[data-testid="stMetricValue"] {{ color: {AZUL}; font-weight: 700; }}
[data-testid="stMetricLabel"] p {{ color: {GRIS}; font-weight: 500; }}
[data-testid="stMetric"] {{
    background: #FFFFFF; border: 1px solid #E0E0E0; border-left: 4px solid {AZUL};
    border-radius: 6px; padding: 8px 12px;
}}
.stTabs [data-baseweb="tab"] p {{ font-weight: 600; color: {MARINO}; }}
.stTabs [aria-selected="true"] p {{ color: {AZUL}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {ROJO}; }}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"],
.stDownloadButton button[kind="primary"] {{ background: {AZUL}; border-color: {AZUL}; }}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primary"]:hover {{
    background: {MARINO}; border-color: {MARINO};
}}
[data-testid="stSidebar"] {{ background: #F2F2F2; border-right: 3px solid {AZUL}; }}

/* ---------- indicador "procesando" de Streamlit (arriba a la derecha):
   en lugar del muñequito, un tractocamión avanzando sobre la carretera.
   Solo se ve mientras la app procesa; el resto de la pantalla queda fija. ---------- */
[data-testid="stStatusWidgetRunningManIcon"] {{ display: none !important; }}
[data-testid="stStatusWidgetRunningIcon"]::before {{
    content: ""; display: inline-block; width: 64px; height: 22px; vertical-align: middle;
    background: {TRACTO_URI} no-repeat 0 0 / 40px 17px,
                repeating-linear-gradient(90deg, {GRIS} 0 8px, transparent 8px 14px) bottom / 100% 2px no-repeat;
    animation: er-tracto 1.4s linear infinite;
}}
@keyframes er-tracto {{ from {{ background-position: -40px 0, 0 100%; }} to {{ background-position: 64px 0, -14px 100%; }} }}
@media (prefers-reduced-motion: reduce) {{
    [data-testid="stStatusWidgetRunningIcon"]::before {{
        animation: none !important; background-position: 12px 0, 0 100%;
    }}
}}

/* pie con barras de acento */
.er-pie {{ display: flex; height: 4px; margin-top: 28px; }}
.er-pie span:nth-child(1) {{ flex: 6; background: {AZUL}; }}
.er-pie span:nth-child(2) {{ flex: 1; background: {ROJO}; }}
.er-pie span:nth-child(3) {{ flex: 2; background: {GRIS}; }}
</style>
"""


def aplicar():
    """Inyecta estilos de marca y pone el logo en la barra lateral."""
    st.markdown(CSS, unsafe_allow_html=True)
    if LOGO.exists():
        st.logo(str(LOGO), size="large", icon_image=str(ISOTIPO) if ISOTIPO.exists() else None)


def encabezado(titulo, subtitulo=""):
    """Panel azul fijo con logo (contorno blanco), titulo y subtitulo."""
    logo = f"<img src='data:image/png;base64,{_b64(LOGO_CONTORNO)}' alt='Estrella Roja'>" \
        if LOGO_CONTORNO.exists() else ""
    st.markdown(
        f"""<div class="er-banner">
              <div class="fila">{logo}
                <div><div class="titulo">{titulo}</div><div class="sub">{subtitulo}</div></div>
              </div>
            </div>""",
        unsafe_allow_html=True,
    )


def pie():
    st.markdown('<div class="er-pie"><span></span><span></span><span></span></div>', unsafe_allow_html=True)


# ---------------------------------------------------------- FICHA DEL PEDIDO (pantalla para tomar captura)
FICHA_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stHeader"] { display: none !important; }
.block-container { padding-top: 1.2rem !important; max-width: 980px !important; }
/* pantallas para captura: SIEMPRE blancas con letra negra, aunque el celular o navegador este en modo oscuro */
:root, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stBottomBlockContainer"] {
    background: #FFFFFF !important; color-scheme: light !important; }
table.er-ficha { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 6px auto 0 auto; background: #FFFFFF; }
table.er-ficha td { border: 2px solid #000; padding: 5px 10px; font-size: 17px; color: #000; text-align: center;
                    word-wrap: break-word; vertical-align: middle; }
table.er-ficha td.et { width: 27%; font-weight: 700; background: #FFFFFF; }
table.er-ficha td.et.gris { background: #D9D9D9; }
table.er-ficha tr.cabecera td.et { background: #C00000; color: #FFFFFF; }
table.er-ficha td.vl { font-weight: 500; background: #FFFFFF; color: #000000 !important; }
table.er-ficha td.vl.vacio { color: #888888 !important; }
table.er-ficha td.et { color: #000000; }
.er-ficha-tip a { color: #005DB4 !important; }
table.er-ficha tr.grande td { height: 150px; }
.er-ficha-tip { color: #636663; font-size: 13px; margin: 8px 0 0 0; text-align: center; }
</style>
"""


def ficha(filas, destacado):
    """filas: lista de (etiqueta, valor). 'destacado' = etiqueta de la fila roja (PEDIDO).
    Las etiquetas CITA ... van en gris y INSTRUCCIONES ESPECIALES con la fila mas alta (como el formato en Excel)."""
    from html import escape
    st.markdown(FICHA_CSS, unsafe_allow_html=True)
    partes = ['<table class="er-ficha">']
    for etiqueta, valor in filas:
        vacio = valor in (None, "")
        texto = "—" if vacio else escape(str(valor)).replace("\n", "<br>")
        clases_tr = " ".join(x for x in ("cabecera" if etiqueta == destacado else "",
                                         "grande" if etiqueta == "INSTRUCCIONES ESPECIALES" else "") if x)
        gris = " gris" if etiqueta.startswith("CITA") else ""
        partes.append(f'<tr class="{clases_tr}"><td class="et{gris}">{escape(etiqueta)}</td>'
                      f'<td class="vl{" vacio" if vacio else ""}">{texto}</td></tr>')
    partes.append("</table>")
    st.markdown("".join(partes), unsafe_allow_html=True)
