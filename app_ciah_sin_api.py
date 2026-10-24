import streamlit as st
import streamlit.components.v1 as components
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import re
import base64
import html as html_module
import hashlib
import os
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse
import pandas as pd
import io

# ==========================================
# CONFIGURACIÓN DE PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Buscador de Amenazas Híbridas",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

ZONA_MADRID = ZoneInfo("Europe/Madrid")

def obtener_fecha_madrid_str(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_madrid = dt.astimezone(ZONA_MADRID)
    return dt_madrid.strftime("%d/%m/%Y %H:%M:%S")

def generar_id_estable(prefijo, texto):
    digest = hashlib.sha256(str(texto).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefijo}-{digest}"

# ==========================================
# BLINDAJE VISUAL DEFINITIVO
# ==========================================
st.markdown("""
<style>
    #MainMenu, header, footer {
        visibility: hidden !important;
        display: none !important;
        height: 0px !important;
    }
    a.header-anchor, a[data-testid="header-anchor"] {
        display: none !important;
        visibility: hidden !important;
    }
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    [data-testid="manage-app-button"],
    button[kind="manage-app"],
    .manage-app,
    div[class*="manage-app"],
    .viewerBadge_container__1QSob,
    div[class*="viewerBadge"],
    div[class*="profileContainer"],
    a[href*="github.com"] {
        visibility: hidden !important;
        display: none !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    .main { background-color: #0b0f19; }
    .block-container { padding-top: 1.2rem !important; }

    .tactical-header {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.98) 0%, rgba(30, 41, 59, 0.95) 100%);
        border: 1px solid rgba(56, 189, 248, 0.2);
        border-left: 8px solid #dc2626;
        border-radius: 12px;
        padding: 18px 24px;
        margin-bottom: 16px;
        display: flex;
        align-items: center;
        gap: 20px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
    }
    .header-titles h1 {
        color: #ffffff;
        margin: 0;
        font-size: 24px !important;
        font-weight: 900;
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }
    .header-titles p {
        color: #94a3b8;
        font-size: 13.5px !important;
        margin: 4px 0 0 0;
        letter-spacing: 0.3px;
        text-transform: uppercase;
    }

    div[data-baseweb="tab-list"] {
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 6px !important;
        background: #0f172a !important;
        padding: 8px 10px !important;
        border-radius: 10px !important;
        border: 1px solid #1e293b !important;
        margin-bottom: 14px !important;
    }

    button[data-baseweb="tab"] {
        flex: 1 1 auto !important;
        min-width: fit-content !important;
        height: 38px !important;
        font-size: 11.5px !important;
        font-weight: 800 !important;
        letter-spacing: 0.4px !important;
        padding: 6px 12px !important;
        margin: 0 !important;
        border-radius: 6px !important;
        color: #ffffff !important;
        text-transform: uppercase;
        border: 1px solid rgba(255,255,255,0.1) !important;
        transition: all 0.2s ease !important;
        justify-content: center !important;
    }

    button[data-baseweb="tab"]:hover {
        transform: translateY(-1px) !important;
        filter: brightness(1.2) !important;
    }

    button[data-baseweb="tab"]:nth-child(1) { background: linear-gradient(135deg, #dc2626, #991b1b) !important; }
    button[data-baseweb="tab"]:nth-child(2) { background: linear-gradient(135deg, #d97706, #92400e) !important; }
    button[data-baseweb="tab"]:nth-child(3) { background: linear-gradient(135deg, #334155, #1e293b) !important; }
    button[data-baseweb="tab"]:nth-child(4) { background: linear-gradient(135deg, #991b1b, #7f1d1d) !important; }
    button[data-baseweb="tab"]:nth-child(5) { background: linear-gradient(135deg, #7c3aed, #5b21b6) !important; }
    button[data-baseweb="tab"]:nth-child(6) { background: linear-gradient(135deg, #b45309, #78350f) !important; }
    button[data-baseweb="tab"]:nth-child(7) { background: linear-gradient(135deg, #be123c, #881337) !important; }
    button[data-baseweb="tab"]:nth-child(8) { background: linear-gradient(135deg, #1e3a8a, #1e293b) !important; }
    button[data-baseweb="tab"]:nth-child(9) { background: linear-gradient(135deg, #0284c7, #075985) !important; }
    button[data-baseweb="tab"]:nth-child(10) { background: linear-gradient(135deg, #059669, #065f46) !important; }

    button[data-baseweb="tab"][aria-selected="true"] {
        border: 2px solid #38bdf8 !important;
        box-shadow: 0 0 14px rgba(56, 189, 248, 0.5) !important;
        transform: scale(1.02) !important;
    }

    div[data-baseweb="tab-highlight"] { display: none !important; }

    .osint-card {
        background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px;
        padding: 16px 20px; margin-bottom: 14px; box-shadow: 0 4px 12px rgba(15, 23, 42, 0.06);
    }
    .osint-flex { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; }
    .osint-media { flex: 0 0 280px; max-width: 280px; }
    .osint-media img { width: 100%; height: auto; max-height: 220px; object-fit: cover; border-radius: 6px; }
    .osint-body { flex: 1; min-width: 260px; }
    .badge-item { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; }
    .ner-pill { font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px; color: #fff; margin-right: 5px; }
    .osint-title { color: #0f172a; font-size: 17px; font-weight: 850; margin: 4px 0 8px 0; line-height: 1.35; }
    .osint-resumen {
        background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
        padding: 10px 14px; color: #1e293b; font-size: 13.5px; line-height: 1.55;
        text-align: justify; margin-bottom: 8px;
    }
    .osint-analisis {
        background-color: #f0f9ff; border-left: 4px solid #0284c7; padding: 8px 12px;
        color: #0c4a6e; font-size: 13px; line-height: 1.5; margin-bottom: 10px; border-radius: 0 4px 4px 0;
    }
    .btn-source {
        background-color: #0f172a; color: #ffffff !important; padding: 6px 14px;
        border-radius: 4px; font-size: 12px; font-weight: 700; text-decoration: none !important;
    }
    .btn-copy {
        background-color: #334155; color: #ffffff !important; padding: 6px 12px;
        border-radius: 4px; font-size: 12px; font-weight: 700; border: none; cursor: pointer;
    }
    .btn-copy:hover { background-color: #475569; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# CONTROL DE ACCESO
# ==========================================
CLAVE_SISTEMA = st.secrets.get("AUTH_KEY", os.environ.get("GAD_AUTH_KEY", "GAD2026"))

def gestionar_acceso():
    if st.session_state.get("autenticado", False):
        return True

    st.markdown("""
        <div style="max-width: 480px; margin: 50px auto 20px auto; padding: 26px; background: #0f172a; border-radius: 12px; border-left: 6px solid #dc2626; color: white; box-shadow: 0 16px 36px rgba(0,0,0,0.6);">
            <h2 style="margin-top: 0; color: #ffffff; font-size: 20px; letter-spacing: 0.5px;">🔒 Acceso Restringido // Monitor OSINT</h2>
            <p style="color: #94a3b8; font-size: 13px;">Sistema de Vigilancia de Amenazas Híbridas e Injerencia. Introduzca la credencial autorizada para continuar.</p>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("form_login"):
            clave_introducida = st.text_input("Clave de acceso:", type="password", placeholder="Introduce la contraseña...")
            boton_acceso = st.form_submit_button("INGRESAR AL SISTEMA", type="primary", use_container_width=True)

            if boton_acceso:
                if clave_introducida == CLAVE_SISTEMA:
                    st.session_state.autenticado = True
                    st.rerun()
                else:
                    st.error("Credencial no autorizada.")
        
    return False

if not gestionar_acceso():
    st.stop()

# ==========================================
# PORTADA Y CABECERA TÁCTICA
# ==========================================
logo_svg = """<svg width="70" height="70" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <radialGradient id="cyberBg" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stop-color="#1e293b" />
            <stop offset="100%" stop-color="#020617" />
        </radialGradient>
        <linearGradient id="neonGlow" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#38bdf8" />
            <stop offset="50%" stop-color="#0284c7" />
            <stop offset="100%" stop-color="#0369a1" />
        </linearGradient>
        <linearGradient id="coreBolt" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#f87171" />
            <stop offset="100%" stop-color="#dc2626" />
        </linearGradient>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
    </defs>
    <circle cx="50" cy="50" r="47" fill="url(#cyberBg)" stroke="#334155" stroke-width="2" />
    <circle cx="50" cy="50" r="40" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.6" />
    <circle cx="50" cy="50" r="30" stroke="#0ea5e9" stroke-width="0.8" opacity="0.4" />
    <line x1="50" y1="3" x2="50" y2="97" stroke="#38bdf8" stroke-width="0.8" opacity="0.35" />
    <line x1="3" y1="50" x2="97" y2="50" stroke="#38bdf8" stroke-width="0.8" opacity="0.35" />
    <path d="M50 14 L75 25 V52 C75 69 50 84 50 84 C50 84 25 69 25 52 V25 Z" fill="#0f172a" stroke="url(#neonGlow)" stroke-width="2.5" filter="url(#glow)" />
    <polygon points="53,24 37,48 48,48 43,72 61,44 51,44" fill="url(#coreBolt)" filter="url(#glow)" />
</svg>"""

st.markdown(f"""
<div class="tactical-header">
    <div>{logo_svg}</div>
    <div class="header-titles">
        <h1>BUSCADOR DE AMENAZAS HÍBRIDAS</h1>
        <p>SISTEMA DE VIGILANCIA DE FUENTES ABIERTAS // OSINT</p>
    </div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# SESIÓN HTTP ROBUSTA
# ==========================================
SESION_HTTP = requests.Session()
retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(pool_connections=60, pool_maxsize=60, max_retries=retries)
SESION_HTTP.mount("https://", adapter)
SESION_HTTP.mount("http://", adapter)
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,pl;q=0.6,zh;q=0.5,ar;q=0.5"
})

CACHE_TITULARES_GLOBAL = {}
CACHE_LOCK = threading.Lock()

# ==========================================
# DICCIONARIOS Y EXPRESIONES REGULARES
# ==========================================
CANALES_DESINFORMACION_ESP = {
    "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu", "ASupersharij"
}

EMPRESAS_ESTRATEGICAS_ESP = [
    "destinus", "mikhail kokorich", "kokorich", "uav navigation",
    "grupo oesia", "oesia", "tecnobit", "indra", "navantia", "escribano",
    "em&e", "sener", "arquimea", "santa barbara sistemas", "santa barbara",
    "gdels", "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa",
    "itp aero", "airbus espana", "airbus getafe", "hisdesat", "hispasat",
    "sateliot", "pld space", "amadeus", "aena", "enaire", "renfe", "adif",
    "telefonica", "iberdrola", "repsol", "naturgy", "redeia", "red electrica",
    "enagas", "talgo"
]

PATRON_RUSIA = re.compile(
    r'\b('
    r'rusia|ruso|rusa|rusos|rusas|moscu|kremlin|putin|mod rf|fuerzas armadas rusas|'
    r'russia|russian|russians|moscow|russie|russe|russland|russisch|'
    r'gru|fsb|svr|apt28|apt29|fancy bear|cozy bear|sandworm|noname057|killnet|turla|gamaredon|cyberarmyofrussia|garantex|wagner|'
    r'bielorrusia|belarus|kaliningrado|kaliningrad|lukashenko|'
    r'ucrania|ucraniano|ucraniana|ucranianos|ukraine|ukrainian|zelensky|zelenski|zsu|vsu|sbu|gur|'
    r'kursk|belgorod|donbas|donbass|crimea|zaporiyia|zaporizhzhia|kherson|donetsk|lugansk|luhansk|jarkov|kharkiv|shariy|sharij|zvinchuk|rybar|'
    r'chechenia|kadyrov|transnistria|moldavia|georgia|abjasia|osetia|kazajistan|uzbekistan|tayikistan'
    r')\b', re.IGNORECASE
)

RAICES_CIRILICO = [
    "росси", "рф", "москв", "кремл", "путин", "гру", "фсб", "свр",
    "украин", "киев", "київ", "зсу", "всу", "сбу", "гур", "донбасс",
    "крым", "беларус", "вагнер", "шахед", "геран", "искандер", "шарий", "рыбарь", "звинчук"
]

PATRON_CHINA = re.compile(
    r'\b('
    r'china|chino|chinos|chinas|pekin|beijing|xi jinping|partido comunista chino|pcc|'
    r'pla|ejercito popular de liberacion|taiwan|taipei|canton|hong kong|'
    r'apt41|volt typhoon|flax typhoon|mustang panda|salt typhoon|mss|ministerio de seguridad del estado|'
    r'ciberespionaje chino|injerencia china|injerencia de pekin|espionaje chino|comisarias chinas|estaciones de policia chinas'
    r')\b', re.IGNORECASE
)

RAICES_CHINO = ["中国", "北京", "习近平", "解放军", "台湾", "华为", "国安部", "间谍"]
RAICES_ARABE = ["روسيا", "أوكرانيا", "الصين", "بوتين", "تجسس", "طائرات مسيرة"]
RAICES_POLACO = ["rosja", "rosyjski", "ukraina", "putin", "sabotaż", "szpiegostwo", "dywersja", "abw", "służby"]

PATRON_VECTORES = re.compile(
    r'\b('
    r'sabotaj|sabotage|saboteador|arson|incendio provocado|cable submarino|undersea cable|red ferroviaria|'
    r'desinformacion|disinformation|fimi|propaganda|guerra cognitiva|red de bots|fake news|'
    r'espia|espionaje|spy|espionage|infiltracion|injerencia|interferencia|'
    r'ciber|cyber|hack|ddos|ransomware|malware|'
    r'sancion|sanciones|sanctions?|elusion|evasion de sanciones|flota fantasma|shadow fleet|petrolero clandestino|'
    r'doble uso|dual-use|contrabando de chips|triangulacion|garantex|'
    r'sicariato|sicario|asesinato selectivo|targeted killing|envenenamiento|defenestrado|homicidio|muerte|'
    r'crimen organizado|mafia|bandas|'
    r'terrorismo|terrorista|atentado|paquete incendiario|explosivo|no convencional|'
    r'crimen de guerra|war crime|cpi|icc|ejecucion|fosa comun|ataque a civiles|misil|dron|bombardeo'
    r')\b', re.IGNORECASE
)

def limpiar_html_texto(texto):
    if not texto:
        return ""
    t = re.sub(r'<[^<]+?>', ' ', str(texto))
    t = html_module.unescape(t)
    t = re.sub(r'&[a-zA-Z0-9#]+;', ' ', t)
    t = re.sub(r'(?i)(suscr[ií]bete a nuestro bolet[ií]n|lee m[aá]s en|copyright|todos los derechos reservados|pincha aqu[ií]|sigue leyendo).*', '', t)
    return re.sub(r'\s+', ' ', t).strip()

def normalizar_texto(texto):
    if not texto:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', str(texto))
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

def limpiar_firma_medio(texto):
    if not texto:
        return ""
    return re.sub(r'\s*[-–|·]\s*[^-–|·]{2,60}$', '', str(texto)).strip()

def limpiar_url(url):
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
    except Exception:
        return url

def es_url_segura(url):
    u = str(url).strip()
    return u.startswith("http://") or u.startswith("https://")

# ==========================================
# FILTRADO INTELIGENTE
# ==========================================
def es_publicidad_o_spam(texto):
    t = normalizar_texto(texto)
    spam = ["master profesional", "lisa institute", "udima", "beca", "matriculate", "curso online", "descuento", "sorteo", "casino", "apuestas", "horoscopo", "oferta de empleo"]
    return any(s in t for s in spam)

def cumple_criterio_inteligencia(texto):
    if not texto or es_publicidad_o_spam(texto):
        return False
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    # Coincidencia con actores clave
    actor_ok = (
        bool(PATRON_RUSIA.search(t_norm)) or
        bool(PATRON_CHINA.search(t_norm)) or
        any(c in t_raw for c in RAICES_CIRILICO) or
        any(c in t_raw for c in RAICES_CHINO) or
        any(c in t_raw for c in RAICES_ARABE) or
        any(c in t_raw for c in RAICES_POLACO) or
        any(emp in t_norm for emp in EMPRESAS_ESTRATEGICAS_ESP)
    )

    # Coincidencia con vectores hostiles o situación operativa
    vector_ok = bool(PATRON_VECTORES.search(t_norm)) or any(c in t_raw for c in ["диверс", "шпион", "кибер", "саботаж", "санкц", "арест", "казн", "поджог", "теракт", "убийств"])

    return actor_ok and vector_ok

def extraer_entidades_clave(texto_completo):
    t_norm = normalizar_texto(texto_completo)
    etiquetas = []

    actores = {
        "GRU": r'\bgru\b', "FSB": r'\bfsb\b', "SVR": r'\bsvr\b',
        "APT41": r'\bapt41\b', "Volt Typhoon": r'\bvolt typhoon\b',
        "Mustang Panda": r'\bmustang panda\b', "Salt Typhoon": r'\bsalt typhoon\b',
        "NoName057": r'\bnoname057\b', "Sandworm": r'\bsandworm\b',
        "APT28 (Fancy Bear)": r'\b(apt28|fancy bear)\b', "APT29 (Cozy Bear)": r'\b(apt29|cozy bear)\b',
        "Killnet": r'\bkillnet\b', "Wagner": r'\bwagner\b', "MSS (China)": r'\bmss\b|ministerio de seguridad del estado',
        "SBU": r'\bsbu\b', "GUR": r'\bgur\b'
    }
    for nombre, patron in actores.items():
        if re.search(patron, t_norm):
            etiquetas.append((nombre, "#2563eb"))

    vectores = {
        "Sabotaje": r'\b(sabotaj|cable submarino|red ferroviaria|incendio provocado|arson)\b',
        "Ciberataque": r'\b(ciberataque|ransomware|ddos|malware|infiltracion cibernetica)\b',
        "Desinformación": r'\b(desinformacion|fimi|propaganda|fake news|red de bots)\b',
        "Flota Fantasma": r'\b(flota fantasma|shadow fleet|petrolero clandestino)\b',
        "Doble Uso / Sanciones": r'\b(elusion|evasion de sanciones|doble uso|chips militares|triangulacion)\b',
        "Sicariato / Muerte": r'\b(sicario|asesinato selectivo|homicidio|envenenamiento|defenestrado)\b',
        "Terrorismo": r'\b(terrorismo|atentado|paquete incendiario|explosivo)\b'
    }
    for nombre, patron in vectores.items():
        if re.search(patron, t_norm):
            etiquetas.append((nombre, "#dc2626"))

    empresas = {
        "Destinus": r'\bdestinus\b', "Indra": r'\bindra\b', "Oesía / Tecnobit": r'\b(oesia|tecnobit)\b',
        "Navantia": r'\bnavantia\b', "Escribano": r'\bescribano\b', "GDELS / Santa Bárbara": r'\b(gdels|santa barbara)\b'
    }
    for nombre, patron in empresas.items():
        if re.search(patron, t_norm):
            etiquetas.append((nombre, "#059669"))

    return etiquetas[:5]

# ==========================================
# TRADUCTOR REACTIVO
# ==========================================
def traducir_texto(texto_orig):
    if not texto_orig:
        return ""
    t_clean = limpiar_html_texto(texto_orig)
    if len(t_clean) < 4:
        return t_clean

    with CACHE_LOCK:
        if t_clean in CACHE_TITULARES_GLOBAL:
            return CACHE_TITULARES_GLOBAL[t_clean]

    medio_suffix = ""
    texto_a_traducir = t_clean
    m_medio = re.search(r'(\s*[-–|]\s*[^-–|]{2,40})$', t_clean)
    if m_medio:
        medio_suffix = m_medio.group(1)
        texto_a_traducir = t_clean[:m_medio.start()].strip()

    for _ in range(2):
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:450]}
            r = SESION_HTTP.get(url, params=params, timeout=2.5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    partes = [seg[0] for seg in data[0] if isinstance(seg, list) and len(seg) > 0 and seg[0]]
                    trad = limpiar_html_texto("".join(partes))
                    if len(trad) > 3:
                        res = f"{trad}{medio_suffix}" if medio_suffix else trad
                        with CACHE_LOCK:
                            CACHE_TITULARES_GLOBAL[t_clean] = res
                        return res
        except Exception:
            pass

    with CACHE_LOCK:
        CACHE_TITULARES_GLOBAL[t_clean] = t_clean
    return t_clean

def traducir_lote(lista_textos):
    with ThreadPoolExecutor(max_workers=10) as ex:
        resultados = list(ex.map(traducir_texto, lista_textos))
    return resultados

def on_cambio_traduccion():
    activar = st.session_state.get("check_traducir", True)
    alertas = st.session_state.get("alertas_monitor", [])
    if not alertas:
        return
    if activar:
        textos = [a.get("titular_original", a["titular"]) for a in alertas]
        traducidos = traducir_lote(textos)
        for a, trad in zip(alertas, traducidos):
            a["titular"] = trad
    else:
        for a in alertas:
            a["titular"] = a.get("titular_original", a["titular"])

# ==========================================
# RASTREO TÁCTICO
# ==========================================
def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=3.5)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.find_all("div", class_="tgme_widget_message_wrap")
        es_red_social = canal in ["ASupersharij", "rybar"]

        for p in posts[-35:]:
            time_tag = p.find("time")
            dt_obj = datetime.now(timezone.utc)
            if time_tag and time_tag.get("datetime"):
                try:
                    dt_obj = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass

            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""
            texto_sin_urls = limpiar_html_texto(re.sub(r'http\S+', '', texto_raw))

            if len(texto_sin_urls) < 20 or not cumple_criterio_inteligencia(texto_sin_urls):
                continue

            foto_url = None
            tiene_video = False

            for tag_foto in p.find_all("a", class_="tgme_widget_message_photo_wrap"):
                m = re.search(r"url\(['\"]?(https?://[^'\")]+)['\"]?\)", tag_foto.get("style", ""))
                if m:
                    foto_url = m.group(1)
                    break

            for elem in p.find_all(["i", "div", "a", "video"]):
                clases = " ".join(elem.get("class", [])) if elem.get("class") else ""
                if elem.name == "video" or any(v in clases for v in ["video", "roundvideo"]):
                    tiene_video = True
                    estilo = elem.get("style", "")
                    if estilo and not foto_url:
                        m = re.search(r"url\(['\"]?(https?://[^'\")]+)['\"]?\)", estilo)
                        if m:
                            foto_url = m.group(1)

            a_link = p.find("a", class_="tgme_widget_message_date")
            link = a_link["href"] if a_link and a_link.get("href") else f"https://t.me/s/{canal}"

            primera_frase = re.split(r'(?<=[.!?])\s+', texto_sin_urls)[0]
            tit_corto = primera_frase if 20 <= len(primera_frase) <= 160 else texto_sin_urls[:140]

            if canal == "rybar":
                nombre_canal = "Mijaíl Zvinchuk (@rybar)"
            elif canal == "ASupersharij":
                nombre_canal = "Telegram: Anatolii Sharij"
            else:
                nombre_canal = f"@{canal}"

            mensajes.append({
                "id": generar_id_estable("TG", link),
                "plataforma": "REDES" if es_red_social else "TELEGRAM",
                "canal": nombre_canal,
                "fecha_dt": dt_obj,
                "titular_raw": tit_corto,
                "cuerpo_raw": texto_sin_urls[:2200],
                "multimedia": foto_url,
                "tiene_video": bool(tiene_video),
                "enlace": link
            })
    except Exception:
        pass
    return mensajes

def procesar_un_feed_rss(datos_feed):
    url, etiqueta = datos_feed
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=3.5)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        for item in root.findall(".//item")[:50]:
            tit_orig = limpiar_html_texto(item.findtext("title", ""))
            desc_orig = limpiar_html_texto(item.findtext("description", ""))
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")

            texto_completo = f"{tit_orig} {desc_orig}"
            if len(tit_orig) < 12 or not cumple_criterio_inteligencia(texto_completo):
                continue

            try:
                dt_obj = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception:
                dt_obj = datetime.now(timezone.utc)

            items.append({
                "id": generar_id_estable("RSS", link),
                "plataforma": "PRENSA",
                "canal": etiqueta,
                "fecha_dt": dt_obj,
                "titular_raw": tit_orig,
                "cuerpo_raw": desc_orig[:1800] if len(desc_orig) > 20 else tit_orig,
                "multimedia": None,
                "tiene_video": False,
                "enlace": link
            })
    except Exception:
        pass
    return items

def raspar_youtube_canal(canal_id, etiqueta):
    items = []
    try:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}"
        r = SESION_HTTP.get(url, timeout=3.0)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:15]:
                tit_orig = limpiar_html_texto(entry.findtext("{http://www.w3.org/2005/Atom}title", ""))
                link_tag = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_tag.get("href", "") if link_tag is not None else ""
                pub_str = entry.findtext("{http://www.w3.org/2005/Atom}published", "")

                try:
                    dt_obj = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    dt_obj = datetime.now(timezone.utc)

                desc_tag = entry.find(".//{http://search.yahoo.com/mrss/}description")
                cuerpo = limpiar_html_texto(desc_tag.text if desc_tag is not None else "")

                if not cumple_criterio_inteligencia(tit_orig + " " + cuerpo):
                    continue

                items.append({
                    "id": generar_id_estable("YT", link),
                    "plataforma": "REDES",
                    "canal": etiqueta,
                    "fecha_dt": dt_obj,
                    "titular_raw": tit_orig,
                    "cuerpo_raw": cuerpo[:1200] if len(cuerpo) > 20 else tit_orig,
                    "multimedia": None,
                    "tiene_video": True,
                    "enlace": link
                })
    except Exception:
        pass
    return items

# ==========================================
# ACTUALIZACIÓN CENTRAL
# ==========================================
def son_mismo_evento(tit1, tit2):
    t1 = re.sub(r'^(última hora|exclusiva|urgente|video)[:\-]?\s*', '', normalizar_texto(limpiar_firma_medio(tit1)))
    t2 = re.sub(r'^(última hora|exclusiva|urgente|video)[:\-]?\s*', '', normalizar_texto(limpiar_firma_medio(tit2)))
    if len(t1) < 16 or len(t2) < 16:
        return False
    return SequenceMatcher(None, t1, t2).ratio() > 0.82

def categorizar_alerta(titular, cuerpo, plat, canal):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    
    if plat == "REDES":
        return "REDES"
    if plat == "TELEGRAM":
        return "TELEGRAM"

    if any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP):
        return "EMPRESA"
    if re.search(r'\b(detenido|detencion|arrestado|arresto|arrested|detained|desarticulad[oa]s?)\b', t_all):
        return "DETENCION"
    if re.search(r'\b(cripto|crypto|usdt|tether|garantex|bitcoin)\b', t_all):
        return "CRIPTO"
    if re.search(r'\b(sancion|sanciones|sanctions?|flota fantasma|shadow fleet|tope al crudo|doble uso|elusion)\b', t_all):
        return "SANCIONES"
    if re.search(r'\b(espana|espanol|espanola|cni|incibe|audiencia nacional|guardia civil|policia nacional|ministerio de defensa)\b', t_all):
        return "ESPANA"
    if PATRON_CHINA.search(t_all) or any(c in t_all for c in RAICES_CHINO):
        return "CHINA"
    if re.search(r'\b(crimen de guerra|war crime|ejecucion|fosa comun|terrorismo|terrorista|atentado|sicario|asesinato selectivo|homicidio)\b', t_all):
        return "CRIMENES"
    
    return "GLOBAL"

def ejecutar_actualizacion_total(activar_traduccion=True):
    canales_tg = [
        "rybar", "ASupersharij", "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "entre_guerras", "dva_majors", "vchkogpu", "meduzalive", "astrapress", "istories_media",
        "ostorozhno_novosti", "nvua_official", "operativnoZSU", "musicarusaesp", "Irinamar_Z",
        "eurekapress", "elOJOen", "ophiucu"
    ]

    fuentes_rss = [
        ("https://news.google.com/rss/search?q=(Rusia+OR+Kremlin+OR+NoName057+OR+APT28)+(sabotaje+OR+injerencia+OR+espionaje+OR+desinformacion)+(España+OR+CNI)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(China+OR+Pekín+OR+APT41+OR+\"Volt+Typhoon\")+(espionaje+OR+sabotaje+OR+injerencia)&hl=es&gl=ES&ceid=ES:es", "China Injerencia (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+China)+(sabotage+OR+espionage+OR+\"shadow+fleet\"+OR+\"hybrid+threat\")&hl=en-US&gl=US&ceid=US:en", "Global Hybrid Watch (EN)"),
        ("https://news.google.com/rss/search?q=(Destinus+OR+Indra+OR+Navantia+OR+Oesia+OR+Tecnobit)+(Rusia+OR+drones+OR+ciberataque)&hl=es&gl=ES&ceid=ES:es", "Defensa y Empresas (ES)"),
        ("https://news.google.com/rss/search?q=(\"elusion+de+sanciones\"+OR+\"flota+fantasma\"+OR+\"doble+uso\")+(Rusia+OR+China)&hl=es&gl=ES&ceid=ES:es", "Sanciones / Evasión (ES)"),
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado)+(espia+ruso+OR+sabotaje+ruso)&hl=es&gl=ES&ceid=ES:es", "Detenciones / Contraespionaje (ES)"),
        ("https://news.google.com/rss/search?q=(Polska+OR+Poland)+(sabotaż+OR+szpiegostwo+OR+ABW+OR+Rosja)&hl=pl&gl=PL&ceid=PL:pl", "Polonia / ABW (PL)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(\"organized+crime\"+OR+\"targeted+killing\"+OR+\"assassination\")&hl=en-US&gl=US&ceid=US:en", "Crimen Instrumentalizado (EN)"),
        ("https://news.google.com/rss/search?q=Россия+диверсия+OR+шпионаж+OR+поджог&hl=ru&gl=RU&ceid=RU:ru", "Rusia / Despachos (RU)"),
        ("https://news.google.com/rss/search?q=Україна+диверсія+OR+агент+OR+ГУР&hl=uk&gl=UA&ceid=UA:uk", "Ucrania / Frente (UA)")
    ]

    canales_yt = [
        ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
        ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
        ("UC6n-ikz_s3zP9v9H6a_3tKg", "YouTube: CSIS Strategic Studies")
    ]

    todos_brutos = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        f_tg = [ex.submit(raspar_un_canal_tg, c) for c in canales_tg]
        f_rss = [ex.submit(procesar_un_feed_rss, f) for f in fuentes_rss]
        f_yt = [ex.submit(raspar_youtube_canal, cid, tag) for cid, tag in canales_yt]

        for f in as_completed(f_tg + f_rss + f_yt):
            try:
                todos_brutos.extend(f.result())
            except Exception:
                pass

    todos_brutos.sort(key=lambda x: x["fecha_dt"], reverse=True)

    pre_unicos = []
    urls_vistas = set()
    for b in todos_brutos:
        u = limpiar_url(b["enlace"])
        if u in urls_vistas:
            continue

        coincidente = next((ex for ex in pre_unicos if son_mismo_evento(b["titular_raw"], ex["titular_raw"])), None)
        if coincidente:
            if b["canal"] not in coincidente.setdefault("canales_correlacionados", []):
                coincidente["canales_correlacionados"].append(b["canal"])
            fuentes_sec = coincidente.setdefault("fuentes_coincidentes", [])
            if not any(f["enlace"] == b["enlace"] for f in fuentes_sec) and b["enlace"] != coincidente["enlace"]:
                fuentes_sec.append({"canal": b["canal"], "enlace": b["enlace"], "titular": b["titular_raw"]})
            continue

        urls_vistas.add(u)
        b.setdefault("canales_correlacionados", [b["canal"]])
        b.setdefault("fuentes_coincidentes", [])
        pre_unicos.append(b)

    if not pre_unicos:
        st.session_state.alertas_monitor = []
        return

    titulares_crudos = [item["titular_raw"] for item in pre_unicos]
    if activar_traduccion:
        titulares_traducidos = traducir_lote(titulares_crudos)
    else:
        titulares_traducidos = titulares_crudos

    alertas_finales = []
    for item, tit_es in zip(pre_unicos, titulares_traducidos):
        cuerpo = item["cuerpo_raw"]
        plat = item["plataforma"]
        titular_limpio = limpiar_firma_medio(tit_es)
        cat_final = categorizar_alerta(titular_limpio, cuerpo, plat, item["canal"])

        if plat == "TELEGRAM":
            canal_tag = item["canal"].replace("@", "").upper()
            titular_final = f"[{canal_tag}] {titular_limpio}"
        else:
            titular_final = titular_limpio

        alertas_finales.append({
            "id": item["id"],
            "plataforma": plat,
            "canal": item["canal"],
            "canales_correlacionados": item.get("canales_correlacionados", [item["canal"]]),
            "fuentes_coincidentes": item.get("fuentes_coincidentes", []),
            "fecha_dt": item["fecha_dt"],
            "titular": titular_final,
            "titular_original": item["titular_raw"],
            "categoria": cat_final,
            "etiquetas_ner": extraer_entidades_clave(f"{titular_final} {cuerpo}"),
            "hecho_reportado": cuerpo,
            "evaluacion_contexto": f"Actividad registrada en la categoría {cat_final}: seguimiento táctico y fuentes abiertas.",
            "multimedia": item.get("multimedia"),
            "tiene_video": item.get("tiene_video", False),
            "enlace": item["enlace"]
        })

    alertas_finales.sort(key=lambda x: x["fecha_dt"], reverse=True)
    st.session_state.alertas_monitor = alertas_finales
    st.session_state.ultima_actualizacion = obtener_fecha_madrid_str()
    st.session_state.seleccionadas_pdf = {a["id"] for a in alertas_finales}
    for a in alertas_finales:
        st.session_state[f"sel_{a['id']}"] = True

# ==========================================
# EXPORTACIÓN E INFORMES
# ==========================================
def exportar_alertas_csv(alertas):
    if not alertas:
        return b""
    filas = []
    for a in alertas:
        filas.append({
            "Fecha (Madrid)": obtener_fecha_madrid_str(a["fecha_dt"]),
            "Categoría": a["categoria"],
            "Plataforma": a.get("plataforma", ""),
            "Canal/Fuente": a["canal"],
            "Entidades": ", ".join([e[0] for e in a.get("etiquetas_ner", [])]),
            "Titular": a["titular"],
            "Texto": a["hecho_reportado"],
            "Enlace": a["enlace"]
        })
    df = pd.DataFrame(filas)
    return df.to_csv(index=False).encode('utf-8-sig')

def generar_html_ejecutivo(alertas, ventana_str="GENERAL"):
    fecha_actual = obtener_fecha_madrid_str()
    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Informe Amenazas Híbridas</title>
<style>
    body {{ font-family: sans-serif; padding: 20px; color: #0f172a; line-height: 1.4; }}
    .hdr {{ background: #0f172a; color: #fff; padding: 14px; border-left: 6px solid #dc2626; border-radius: 4px; margin-bottom: 15px; }}
    .card {{ background: #fff; border: 1px solid #cbd5e1; border-left: 4px solid #0284c7; padding: 10px; margin-bottom: 10px; border-radius: 4px; }}
</style></head><body>
<div class="hdr"><h2>INFORME DE INTELIGENCIA // AMENAZAS HÍBRIDAS</h2><p>Ventana: {ventana_str} | Fecha: {fecha_actual}</p></div>"""
    for a in alertas:
        html += f"""<div class="card"><div>📅 {obtener_fecha_madrid_str(a['fecha_dt'])} | 📢 {html_module.escape(a['canal'])}</div>
        <h3>{html_module.escape(a['titular'])}</h3><p>{html_module.escape(a['hecho_reportado'][:400])}...</p>
        <a href="{html_module.escape(a['enlace'], quote=True)}" target="_blank">🔗 Enlace Fuente</a></div>"""
    html += "</body></html>"
    return html

# ==========================================
# GESTIÓN DE ACCIONES Y FILTROS
# ==========================================
if "alertas_monitor" not in st.session_state:
    st.session_state.alertas_monitor = []
    st.session_state.seleccionadas_pdf = set()

# Si está vacío en el primer inicio, sincroniza
if not st.session_state.alertas_monitor and "primera_carga" not in st.session_state:
    st.session_state.primera_carga = True
    with st.spinner("Sincronizando fuentes OSINT..."):
        ejecutar_actualizacion_total(activar_traduccion=True)
    st.rerun()

alertas_totales = st.session_state.get("alertas_monitor", [])

# Barra lateral
with st.sidebar:
    st.subheader("🔍 Filtros de Búsqueda")
    busqueda_txt = st.text_input("Búsqueda por palabras:", placeholder="Ej: sabotaje, Destinus, drones...")
    rango_tiempo = st.selectbox("📅 Ventana Temporal:", ["Últimas 24 Horas", "Últimas 48 Horas", "Última Semana", "Todo el Histórico"], index=3)
    canales_disponibles = sorted(list({a["canal"] for a in alertas_totales}))
    canales_sel = st.multiselect("Filtrar por Canal / Medio:", canales_disponibles, default=[])
    categorias_disponibles = ["ESPANA", "EMPRESA", "GLOBAL", "CRIMENES", "CRIPTO", "SANCIONES", "DETENCION", "CHINA", "TELEGRAM", "REDES"]
    cat_sel = st.multiselect("Filtrar por Categoría:", categorias_disponibles, default=[])
    
    st.write("---")
    csv_bytes = exportar_alertas_csv(alertas_totales)
    st.download_button("📊 DESCARGAR CSV", data=csv_bytes, file_name=f"alertas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.csv", mime="text/csv", use_container_width=True)

# Filtro temporal estricto (sin aceptar fechas futuras)
ahora_utc = datetime.now(timezone.utc)
def en_rango_horas(dt_item, horas):
    diff = ahora_utc - dt_item.astimezone(timezone.utc)
    return timedelta(seconds=0) <= diff <= timedelta(hours=horas)

if rango_tiempo == "Últimas 24 Horas":
    datos_temporales = [x for x in alertas_totales if en_rango_horas(x["fecha_dt"], 24)]
elif rango_tiempo == "Últimas 48 Horas":
    datos_temporales = [x for x in alertas_totales if en_rango_horas(x["fecha_dt"], 48)]
elif rango_tiempo == "Última Semana":
    datos_temporales = [x for x in alertas_totales if en_rango_horas(x["fecha_dt"], 24 * 7)]
else:
    datos_temporales = alertas_totales

datos_filtrados = datos_temporales

if busqueda_txt:
    t_q = normalizar_texto(busqueda_txt)
    datos_filtrados = [x for x in datos_filtrados if t_q in normalizar_texto(f"{x['titular']} {x['hecho_reportado']}")]

if canales_sel:
    datos_filtrados = [x for x in datos_filtrados if x["canal"] in canales_sel]

if cat_sel:
    datos_filtrados = [x for x in datos_filtrados if x["categoria"] in cat_sel]

# Botones de control
col_btn1, col_trad, col_btn2, col_info = st.columns([1.2, 1.0, 1.4, 2.0])

with col_btn1:
    if st.button("🔄 ACTUALIZAR FUENTES", type="primary", use_container_width=True):
        with st.spinner("Sincronizando fuentes tácticas..."):
            ejecutar_actualizacion_total(activar_traduccion=st.session_state.get("check_traducir", True))
        st.rerun()

with col_trad:
    st.checkbox("🌐 Auto-Traducir", value=True, key="check_traducir", on_change=on_cambio_traduccion)

alertas_para_informe = [x for x in datos_filtrados if x.get("id") in st.session_state.seleccionadas_pdf]

with col_btn2:
    st.download_button(
        f"📥 INFORME ({len(alertas_para_informe)})",
        data=generar_html_ejecutivo(alertas_para_informe, ventana_str=rango_tiempo.upper()),
        file_name=f"Informe_Amenazas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.html",
        mime="text/html",
        use_container_width=True
    )

with col_info:
    ult = st.session_state.get("ultima_actualizacion", "No registrada")
    st.markdown(f'<div style="font-size:12.5px;color:#94a3b8;margin-top:6px;">🕒 <strong>Última sincronización:</strong> {ult} | <strong>{len(datos_filtrados)}</strong> alertas visibles.</div>', unsafe_allow_html=True)

# Callbacks de selección
def marcar_todas_callback(lista_ids):
    st.session_state.seleccionadas_pdf = set(lista_ids)
    for _id in lista_ids:
        st.session_state[f"sel_{_id}"] = True

def desmarcar_todas_callback(lista_ids):
    st.session_state.seleccionadas_pdf = set()
    for _id in lista_ids:
        st.session_state[f"sel_{_id}"] = False

def toggle_cb(item_id):
    if st.session_state.get(f"sel_{item_id}", False):
        st.session_state.seleccionadas_pdf.add(item_id)
    else:
        st.session_state.seleccionadas_pdf.discard(item_id)

ids_visibles = [x["id"] for x in datos_filtrados]
c_sel1, c_sel2, _ = st.columns([1.2, 1.2, 4.0])
with c_sel1:
    st.button("✅ Seleccionar todas", use_container_width=True, on_click=marcar_todas_callback, args=(ids_visibles,))
with c_sel2:
    st.button("⬜ Desmarcar todas", use_container_width=True, on_click=desmarcar_todas_callback, args=(ids_visibles,))

# ==========================================
# RENDERIZADO DE ALERTAS
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros en esta sección.")
        return

    for item in lista_items:
        item_id = item["id"]
        cat = item.get("categoria", "GLOBAL")
        canal_nombre = item.get("canal", "")
        fecha_str = obtener_fecha_madrid_str(item["fecha_dt"])

        b_color = {
            "ESPANA": "#dc2626", "EMPRESA": "#d97706", "GLOBAL": "#0f172a",
            "CRIMENES": "#991b1b", "CRIPTO": "#7c3aed", "SANCIONES": "#b45309",
            "DETENCION": "#be123c", "CHINA": "#1e3a8a", "TELEGRAM": "#0284c7", "REDES": "#059669"
        }.get(cat, "#0f172a")

        t_safe = html_module.escape(str(item.get("titular", "")))
        h_safe = html_module.escape(str(item.get("hecho_reportado", "")))
        canal_safe = html_module.escape(str(canal_nombre))
        enlace_attr = html_module.escape(item.get("enlace", "#") if es_url_segura(item.get("enlace")) else "#", quote=True)

        pildoras_ner = "".join([f'<span class="ner-pill" style="background-color:{c};">{html_module.escape(n)}</span>' for n, c in item.get("etiquetas_ner", [])])

        media_html = ""
        if item.get("multimedia") and es_url_segura(item["multimedia"]):
            media_html = f'<div class="osint-media"><img src="{html_module.escape(item["multimedia"], quote=True)}" loading="lazy"/></div>'

        texto_copia = f"🚨 ALERTA OSINT | {cat}\nTitular: {item.get('titular', '')}\nFuente: {canal_nombre} | Fecha: {fecha_str}\nEnlace: {item.get('enlace', '#')}"
        b64_copia = base64.b64encode(texto_copia.encode('utf-8')).decode('utf-8')

        tarjeta_html = f"""
        <div class="osint-card" style="border-left: 8px solid {b_color};">
            <div class="osint-flex">
                {media_html}
                <div class="osint-body">
                    <div style="font-size:12px;color:#64748b;margin-bottom:6px;">📅 {fecha_str} | 📢 {canal_safe}</div>
                    <div class="osint-title">{t_safe}</div>
                    <div style="margin-bottom:8px;">{pildoras_ner}</div>
                    <div class="osint-resumen"><strong>📄 Hecho:</strong> {h_safe}</div>
                    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                        <a href="{enlace_attr}" target="_blank" class="btn-source">🔗 ABRIR FUENTE ORIGINAL</a>
                        <button class="btn-copy" onclick="navigator.clipboard.writeText(decodeURIComponent(escape(window.atob('{b64_copia}')))).then(()=>alert('📋 Alerta copiada'))">📋 COPIAR ALERTA</button>
                    </div>
                </div>
            </div>
        </div>
        """

        col_check, col_card = st.columns([0.05, 0.95])
        with col_check:
            if f"sel_{item_id}" not in st.session_state:
                st.session_state[f"sel_{item_id}"] = (item_id in st.session_state.seleccionadas_pdf)
            st.checkbox("Sel", key=f"sel_{item_id}", on_change=toggle_cb, args=(item_id,), label_visibility="collapsed")

        with col_card:
            st.markdown(tarjeta_html, unsafe_allow_html=True)

# Pestañas operativas
l_esp = [x for x in datos_filtrados if x.get("categoria") == "ESPANA"]
l_emp = [x for x in datos_filtrados if x.get("categoria") == "EMPRESA"]
l_prensa = [x for x in datos_filtrados if x.get("categoria") == "GLOBAL"]
l_crim = [x for x in datos_filtrados if x.get("categoria") == "CRIMENES"]
l_cripto = [x for x in datos_filtrados if x.get("categoria") == "CRIPTO"]
l_sanc = [x for x in datos_filtrados if x.get("categoria") == "SANCIONES"]
l_det = [x for x in datos_filtrados if x.get("categoria") == "DETENCION"]
l_china = [x for x in datos_filtrados if x.get("categoria") == "CHINA"]
l_tg = [x for x in datos_filtrados if x.get("categoria") == "TELEGRAM"]
l_redes = [x for x in datos_filtrados if x.get("categoria") == "REDES"]

t1, t2, t3, t4, t5, t6, t7, t8, t9, t10 = st.tabs([
    f"🇪🇸 ESPAÑA ({len(l_esp)})",
    f"🏢 EMPRESAS ({len(l_emp)})",
    f"📰 GLOBAL ({len(l_prensa)})",
    f"⚖️ CRÍMENES / ATENTADOS ({len(l_crim)})",
    f"🟣 CRIPTO ({len(l_cripto)})",
    f"📦 SANCIONES ({len(l_sanc)})",
    f"🛑 DETENCIONES ({len(l_det)})",
    f"🇨🇳 CHINA ({len(l_china)})",
    f"✈️ TELEGRAM ({len(l_tg)})",
    f"🌐 REDES SOCIALES ({len(l_redes)})"
])

with t1: renderizar_tarjetas(l_esp)
with t2: renderizar_tarjetas(l_emp)
with t3: renderizar_tarjetas(l_prensa)
with t4: renderizar_tarjetas(l_crim)
with t5: renderizar_tarjetas(l_cripto)
with t6: renderizar_tarjetas(l_sanc)
with t7: renderizar_tarjetas(l_det)
with t8: renderizar_tarjetas(l_china)
with t9: renderizar_tarjetas(l_tg)
with t10: renderizar_tarjetas(l_redes)
