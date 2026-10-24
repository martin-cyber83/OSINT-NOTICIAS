import streamlit as st

# ==========================================
# 1. CONFIGURACIÓN STREAMLIT & CONTROL ACCESO
# ==========================================
st.set_page_config(
    page_title="Buscador de Amenazas Híbridas",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Blindaje visual CSS
st.markdown("""
<style>
    #MainMenu, footer { visibility: hidden !important; display: none !important; height: 0px !important; }
    a.header-anchor, a[data-testid="header-anchor"] { display: none !important; visibility: hidden !important; }
    [data-testid="stDecoration"], [data-testid="stStatusWidget"], [data-testid="manage-app-button"],
    button[kind="manage-app"], .manage-app, div[class*="manage-app"], .viewerBadge_container__1QSob,
    div[class*="viewerBadge"], div[class*="profileContainer"], .stAppDeployButton {
        visibility: hidden !important; display: none !important; opacity: 0 !important; pointer-events: none !important;
    }
    .main { background-color: #0b0f19; }
</style>
""", unsafe_allow_html=True)

try:
    CLAVE_SISTEMA = st.secrets.get("GAD_PASSWORD", "GAD2026")
except Exception:
    CLAVE_SISTEMA = "GAD2026"

def gestionar_acceso():
    if st.session_state.get("autenticado", False):
        return True

    st.markdown("""
        <div style="max-width: 460px; margin: 50px auto 20px auto; padding: 26px; background: #0f172a; border-radius: 12px; border-left: 6px solid #dc2626; color: white; box-shadow: 0 12px 28px rgba(0,0,0,0.5);">
            <h2 style="margin-top: 0; color: #ffffff; font-size: 21px;">🔒 Acceso Restringido</h2>
            <p style="color: #94a3b8; font-size: 13.5px; margin-bottom: 0;">Sistema de Vigilancia de Fuentes Abiertas. Introduzca la credencial autorizada.</p>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("form_login"):
            clave_input = st.text_input("Clave de acceso:", type="password", placeholder="Introduce la contraseña...")
            if st.form_submit_button("INGRESAR AL SISTEMA", type="primary", use_container_width=True):
                if clave_input == CLAVE_SISTEMA:
                    st.session_state.autenticado = True
                    st.rerun()
                else:
                    st.error("Credencial incorrecta.")
    return False

if not gestionar_acceso():
    st.stop()

# ==========================================
# 2. CLIENTE HTTP Y CACHÉ PERSISTENTE
# ==========================================
import streamlit.components.v1 as components
import requests
from requests.adapters import HTTPAdapter
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import re
import html as html_module
import hashlib
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse
import pandas as pd

ZONA_MADRID = ZoneInfo("Europe/Madrid")

def obtener_fecha_madrid_str(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONA_MADRID).strftime("%d/%m/%Y %H:%M:%S")

@st.cache_resource
def get_sesion_http():
    sesion = requests.Session()
    adapter = HTTPAdapter(pool_connections=60, pool_maxsize=60, max_retries=1)
    sesion.mount("https://", adapter)
    sesion.mount("http://", adapter)
    sesion.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,pl;q=0.6,zh;q=0.5,ar;q=0.5"
    })
    return sesion

@st.cache_resource
def get_traducciones_cache():
    return {}, threading.Lock()

SESION_HTTP = get_sesion_http()
CACHE_TRADUCCIONES, CACHE_LOCK = get_traducciones_cache()

# ==========================================
# 3. REGEX Y MATRICES OPERATIVAS
# ==========================================
CANALES_DESINFORMACION_ESP = {
    "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu", "ASupersharij"
}

EMPRESAS_ESTRATEGICAS_ESP = [
    "destinus", "kokorich", "uav navigation", "grupo oesia", "oesia", "tecnobit", "indra", "navantia", "escribano",
    "em&e", "sener", "arquimea", "santa barbara sistemas", "santa barbara", "gdels", "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa",
    "itp aero", "airbus espana", "airbus getafe", "hisdesat", "hispasat", "sateliot", "pld space", "amadeus", "aena", "enaire", "renfe", "adif",
    "telefonica", "iberdrola", "repsol", "naturgy", "redeia", "red electrica", "enagas", "talgo"
]

PATRON_ACTORES = re.compile(
    r'\b('
    r'rusia|ruso|rusa|rusos|rusas|moscu|kremlin|putin|'
    r'russia|russian|russians|moscow|russie|russe|russland|'
    r'gru|fsb|svr|apt28|apt29|fancy bear|cozy bear|sandworm|noname057|killnet|turla|garantex|wagner|'
    r'bielorrusia|belarus|kaliningrado|lukashenko|'
    r'ucrania|ucraniano|ucraniana|ukraine|ukrainian|zelensky|zelenski|zsu|vsu|sbu|gur|hur|'
    r'kursk|belgorod|donbas|donbass|crimea|zaporiyia|zaporizhzhia|kherson|donetsk|lugansk|luhansk|jarkov|kharkiv|shariy|sharij|rybar|zvinchuk|'
    r'china|chino|chinos|chinas|pekin|beijing|xi jinping|pcc|pla|taiwan|taipei|hong kong|'
    r'apt41|volt typhoon|flax typhoon|mustang panda|salt typhoon|mss|'
    r'otan|nato|dsn|cni|incibe|ccn-cert|europol|eurojust|stratcom|euvsdisinfo|bellingcat|the insider|occrp|meduza|mediazona|isw'
    r')\b', re.IGNORECASE
)

PATRON_VECTORES = re.compile(
    r'\b('
    r'hibrid\w*|hybrid\w*|sabotaj\w*|sabotage\w*|saboteador\w*|espia\w*|espionaje|spy\w*|espionage|'
    r'ciber\w*|cyber\w*|hack\w*|ransomware|ddos|malware|infiltracion|'
    r'desinformacion|disinformation|fimi|doppelganger|propaganda|injerencia|'
    r'sancion\w*|sanction\w*|elusion|evasion|flota fantasma|shadow fleet|petrolero clandestino|'
    r'cable submarino|undersea cable|red ferroviaria|paquete incendiario|incendio provocado|arson|'
    r'crimen de guerra|war crime\w*|cpi|icc|genocidio|fosa comun|tortura\w*|'
    r'detenido\w*|arrestado\w*|detencion\w*|arresto\w*|zatrzymany|festnahme|'
    r'dron\w*|uav|jamming|guerra electronica|misil\w*|bombardeo|ofensiva|'
    r'sicario\w*|asesinato\w*|envenenamiento|terrorismo|atentado|finint|cripto\w*|bitcoin|usdt'
    r')\b', re.IGNORECASE
)

PATRON_CIRILICO = re.compile(r'[\u0400-\u04FF]')
PATRON_CHINO = re.compile(r'[\u4e00-\u9fff]')
PATRON_ARABE = re.compile(r'[\u0600-\u06FF]')

# ==========================================
# 4. UTILIDADES Y SANITIZACIÓN
# ==========================================
def limpiar_html_texto(texto):
    if not texto: return ""
    t = re.sub(r'<[^<]+?>', ' ', str(texto))
    t = html_module.unescape(t)
    t = re.sub(r'&[a-zA-Z0-9#]+;', ' ', t)
    t = re.sub(r'(?i)(suscr[ií]bete a nuestro bolet[ií]n|lee m[aá]s en|copyright|todos los derechos reservados).*', '', t)
    return re.sub(r'\s+', ' ', t).strip()

def normalizar_texto(texto):
    if not texto: return ""
    return "".join([c for c in unicodedata.normalize('NFKD', str(texto)) if not unicodedata.combining(c)]).lower()

def limpiar_firma_medio(texto):
    return re.sub(r'\s*[-–|·]\s*[^-–|·]{2,60}$', '', str(texto)).strip() if texto else ""

def limpiar_url(url):
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))
    except Exception: return url

def es_url_segura(url):
    try:
        p = urlparse(str(url).strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def url_sanitizada(url):
    return html_module.escape(url.strip()) if es_url_segura(url) else "about:blank"

def id_determinista(cadena):
    return hashlib.sha256(str(cadena).encode('utf-8')).hexdigest()[:16]

# ==========================================
# 5. FILTRADO TEMÁTICO Y CLASIFICACIÓN
# ==========================================
def cumple_ventana_temporal(fecha_dt, horas):
    if not fecha_dt: return True
    if fecha_dt.tzinfo is None: fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    dif = (datetime.now(timezone.utc) - fecha_dt.astimezone(timezone.utc)).total_seconds()
    return -600 <= dif <= (horas * 3600)

def es_relevante(texto, canal):
    if not texto: return False
    canal_l = canal.lower()
    if any(k in canal_l for k in ["sbu", "gur", "rybar", "sharij", "informnapalm", "bellingcat", "insider", "occrp", "euvsdisinfo", "generalstaff"]):
        return True

    t_norm = normalizar_texto(texto)
    t_raw = str(texto)

    if re.search(r'\b(vaticano|santa sede|papa francisco|obispo diocesano)\b', t_norm):
        if not re.search(r'\b(ciber\w*|sabotaj\w*|espia\w*|espionaje)\b', t_norm):
            return False

    if PATRON_CIRILICO.search(t_raw) or PATRON_CHINO.search(t_raw) or PATRON_ARABE.search(t_raw):
        return True

    tiene_actor = bool(PATRON_ACTORES.search(t_norm))
    tiene_vector = bool(PATRON_VECTORES.search(t_norm)) or any(emp in t_norm for emp in ["destinus", "oesia", "tecnobit", "indra", "navantia"])

    return tiene_actor and tiene_vector

def categorizar_alerta(titular, cuerpo, plat, canal):
    if plat == "REDES": return "REDES"
    if plat == "TELEGRAM": return "TELEGRAM"
    
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    
    # 1. España e infraestructuras críticas
    if re.search(r'\b(espana|espanol\w*|madrid|barcelona|cartagena|\brota\b|torrejon|cni|dsn|incibe|ccn-cert|margarita robles)\b', t_all):
        return "ESPANA"
    
    # 2. Empresas de defensa estratégicas
    if any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP):
        return "EMPRESA"
        
    # 3. Detenciones por espionaje/sabotaje
    if re.search(r'\b(detenido\w*|arrestado\w*|detencion\w*|arresto\w*|zatrzymany)\b', t_all):
        if not re.search(r'\b(prisionero de guerra|pow)\b', t_all):
            return "DETENCION"
            
    # 4. Criptomonedas / FININT
    if re.search(r'\b(cripto\w*|bitcoin|btc|usdt|tether|garantex|blockchain)\b', t_all):
        return "CRIPTO"
        
    # 5. Sanciones y flota fantasma
    if re.search(r'\b(sancion\w*|elusion|flota fantasma|shadow fleet|tope al crudo|price cap|ofac|opensanctions|kse institute)\b', t_all):
        return "SANCIONES"
        
    # 6. China e injerencia
    if re.search(r'\b(china|chino\w*|pekin|beijing|xi jinping|taiwan|mss|apt41|volt typhoon)\b', t_all) or PATRON_CHINO.search(titular + cuerpo):
        return "CHINA"
        
    # 7. Crímenes de guerra y terrorismo
    if re.search(r'\b(crimen de guerra|cpi|icc|la haya|genocidio|tortura\w*|masacre|bucha|mariupol|terrorismo|atentado)\b', t_all):
        return "CRIMENES"
        
    return "GLOBAL"

def obtener_contexto_fuente(categoria, canal=""):
    c = canal.lower()
    if "rybar" in c: return "Canal militar ruso (M. Zvinchuk). Información sujeta a narrativa oficial."
    if "sharij" in c: return "Canal de Anatolii Sharij: seguimiento operativo del frente ucraniano-ruso."
    if c.replace("@","") in CANALES_DESINFORMACION_ESP: return "Fuente identificada en redes de difusión y manipulación informativa (FIMI)."
    if any(k in c for k in ["bellingcat", "insider", "occrp"]): return "Periodismo de investigación OSINT sobre inteligencia y crimen transnacional."
    if any(k in c for k in ["stratcom", "euvsdisinfo", "dfrlab"]): return "Registro institucional de desinformación e injerencia extranjera."
    
    contextos = {
        "ESPANA": "Impacto directo en la seguridad nacional o infraestructuras de España.",
        "EMPRESA": "Seguimiento corporativo: sector defensa y contratos estratégicos.",
        "DETENCION": "Operación policial o judicial contra redes de espionaje o sabotaje.",
        "SANCIONES": "Vigilancia de elusión de sanciones comerciales, financieras o flota fantasma.",
        "CRIPTO": "Inteligencia financiera (FININT): trazabilidad de transferencias ilícitas.",
        "CHINA": "Operaciones estratégicas, ciberespionaje o injerencia atribuidas a actores chinos.",
        "CRIMENES": "Documentación de crímenes de guerra, ataques a civiles o terrorismo."
    }
    return contextos.get(categoria, "Información operativa de fuentes abiertas sobre el conflicto.")

# ==========================================
# 6. TRADUCCIÓN THREAD-SAFE CON PERSISTENCIA
# ==========================================
def traducir_texto(texto_orig):
    if not texto_orig: return ""
    t_clean = limpiar_html_texto(texto_orig)
    if len(t_clean) < 4: return t_clean

    with CACHE_LOCK:
        if t_clean in CACHE_TRADUCCIONES:
            return CACHE_TRADUCCIONES[t_clean]

    medio_suffix = ""
    texto_a_traducir = t_clean
    m_medio = re.search(r'(\s*[-–|]\s*[^-–|]{2,40})$', t_clean)
    if m_medio:
        medio_suffix = m_medio.group(1)
        texto_a_traducir = t_clean[:m_medio.start()].strip()

    params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:500]}
    try:
        r = SESION_HTTP.get("https://translate.googleapis.com/translate_a/single", params=params, timeout=2.5)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                partes = [seg[0] for seg in data[0] if isinstance(seg, list) and len(seg) > 0 and seg[0]]
                trad = limpiar_html_texto("".join(partes))
                if len(trad) > 3:
                    res = f"{trad}{medio_suffix}" if medio_suffix else trad
                    with CACHE_LOCK:
                        CACHE_TRADUCCIONES[t_clean] = res
                    return res
    except Exception:
        pass

    return t_clean

# ==========================================
# 7. PARSER BOOLEANO MULTILINGÜE
# ==========================================
def cumple_busqueda_booleana(item, query_str):
    if not query_str or not query_str.strip():
        return True

    corpus = f"{item.get('titular_traducido','')} {item.get('titular_raw','')} {item.get('cuerpo_traducido','')} {item.get('cuerpo_raw','')}"
    t_norm = normalizar_texto(corpus)
    q_raw = query_str.strip()

    for frase in re.findall(r'"([^"]+)"', q_raw):
        if normalizar_texto(frase) not in t_norm:
            return False

    q_sin_frases = re.sub(r'"[^"]+"', ' ', q_raw)

    for t_not in re.findall(r'(?:NOT\s+|(?:^|\s)-)(\w+)', q_sin_frases, flags=re.IGNORECASE):
        if re.search(r'\b' + re.escape(normalizar_texto(t_not)) + r'\b', t_norm):
            return False

    q_limpia = re.sub(r'(?:NOT\s+|(?:^|\s)-)\w+', ' ', q_sin_frases, flags=re.IGNORECASE)
    q_limpia = re.sub(r'[()]', ' ', q_limpia).strip()
    if not q_limpia:
        return True

    for bloque in [b.strip() for b in re.split(r'\bOR\b', q_limpia, flags=re.IGNORECASE) if b.strip()]:
        terminos_and = [t.strip() for t in re.split(r'\bAND\b|\s+', bloque, flags=re.IGNORECASE) if t.strip()]
        if all(re.search(r'\b' + re.escape(normalizar_texto(t)) + r'\b', t_norm) for t in terminos_and):
            return True

    return False

# ==========================================
# 8. RASTREADORES EN PARALELO
# ==========================================
def procesar_feed_rss(url, etiqueta):
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=3.0)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        entradas = root.findall(".//item")
        es_atom = False
        if not entradas:
            entradas = root.findall(".//{http://www.w3.org/2005/Atom}entry")
            es_atom = True

        for item in entradas[:35]:
            if es_atom:
                tit = limpiar_html_texto(item.findtext("{http://www.w3.org/2005/Atom}title", ""))
                desc = limpiar_html_texto(item.findtext("{http://www.w3.org/2005/Atom}summary", "") or item.findtext("{http://www.w3.org/2005/Atom}content", ""))
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "").strip() if link_el is not None else ""
                pub = item.findtext("{http://www.w3.org/2005/Atom}published", "") or item.findtext("{http://www.w3.org/2005/Atom}updated", "")
            else:
                tit = limpiar_html_texto(item.findtext("title", ""))
                desc = limpiar_html_texto(item.findtext("description", ""))
                link = item.findtext("link", "").strip()
                pub = item.findtext("pubDate", "")

            if not tit or len(tit) < 8: continue
            if not es_relevante(f"{tit} {desc}", etiqueta): continue

            dt = None
            if pub:
                try: dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    try: dt = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(timezone.utc)
                    except Exception: dt = None
            if dt is None:
                dt = datetime.now(timezone.utc)

            items.append({
                "id": f"RSS-{id_determinista(link or tit)}", "plataforma": "PRENSA", "canal": etiqueta,
                "fecha_dt": dt, "titular_raw": limpiar_firma_medio(tit), "cuerpo_raw": desc[:1500],
                "enlace": link, "multimedia": None, "tiene_video": False
            })
    except Exception:
        pass
    return items

def raspar_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=3.0)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-40:]:
            time_tag = p.find("time")
            dt = None
            if time_tag and time_tag.get("datetime"):
                try: dt = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception: dt = None
            if dt is None:
                dt = datetime.now(timezone.utc)

            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""
            if len(texto_raw) < 15: continue

            texto_sin_urls = limpiar_html_texto(re.sub(r'http\S+', '', texto_raw))
            if not es_relevante(texto_sin_urls, canal): continue

            foto_url = None
            tiene_video = False

            # Extracción de fotos de Telegram
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
            link = a_link["href"] if a_link else f"https://t.me/s/{canal}"
            tit_corto = re.split(r'(?<=[.!?])\s+', texto_sin_urls)[0][:140]

            mensajes.append({
                "id": f"TG-{id_determinista(link)}", "plataforma": "TELEGRAM", "canal": f"@{canal}",
                "fecha_dt": dt, "titular_raw": tit_corto, "cuerpo_raw": texto_sin_urls[:2200],
                "enlace": link, "multimedia": foto_url, "tiene_video": tiene_video
            })
    except Exception:
        pass
    return mensajes

def raspar_youtube(canal_id, etiqueta):
    items = []
    try:
        r = SESION_HTTP.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}", timeout=2.5)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:15]:
                tit = limpiar_html_texto(entry.findtext("{http://www.w3.org/2005/Atom}title", ""))
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "") if link_el is not None else ""
                pub = entry.findtext("{http://www.w3.org/2005/Atom}published", "")
                desc = limpiar_html_texto(entry.findtext(".//{http://search.yahoo.com/mrss/}description", ""))

                if not es_relevante(f"{tit} {desc}", etiqueta): continue
                try: dt = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception: dt = datetime.now(timezone.utc)

                items.append({
                    "id": f"YT-{id_determinista(link)}", "plataforma": "REDES", "canal": etiqueta,
                    "fecha_dt": dt, "titular_raw": tit, "cuerpo_raw": desc[:1000],
                    "enlace": link, "multimedia": None, "tiene_video": True
                })
    except Exception:
        pass
    return items

# ==========================================
# 9. BANCO COMPLETO DE FUENTES OSINT
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def cargar_todas_las_fuentes():
    canales_tg = [
        "GeneralStaffZSU", "kpszsu", "SBUkr", "DIUkraine", "operativnoZSU",
        "CenterCounteringDisinformation", "mvs_ua", "dsns_telegram", "suspilnenews", "ukrainenowenglish",
        "pravda_com_ua", "kyivindependent_official", "informnapalm", "cyberresistanceua",
        "meduzalive", "astrapress", "istories_media", "vchkogpu", "mediazzzona", "bbcrussian", "novayagazeta_europe", "theinsider",
        "rybar", "dva_majors", "ASupersharij", "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu"
    ]

    canales_yt = [
        ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
        ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
        ("UCvps_Xgqj0i2v4Puh2p-E2w", "YouTube: Kyiv Independent"),
        ("UC5q_h_4qjF04bJkU-Fq0e5A", "YouTube: BBC Russian"),
        ("UCN3h_V5k0Z4P9WvE9j_Yg3A", "YouTube: DW News"),
        ("UC7_2eFfP_n5v3H_Z_L-v_4w", "YouTube: Bellingcat OSINT")
    ]

    fuentes_rss = [
        ("https://news.google.com/rss/search?q=(DSN+OR+\"Seguridad+Nacional\")+España&hl=es&gl=ES&ceid=ES:es", "DSN Seguridad Nacional"),
        ("https://news.google.com/rss/search?q=(\"Consilium\"+OR+\"Consejo+de+la+UE\")+(\"híbridas\"+OR+\"sabotaje\"+OR+\"sanciones\")&hl=es&gl=ES&ceid=ES:es", "Consilium UE"),
        ("https://news.google.com/rss/search?q=(\"NATO+StratCom\"+OR+\"StratCom+COE\")+disinformation&hl=en-US&gl=US&ceid=US:en", "NATO StratCom"),
        ("https://news.google.com/rss/search?q=Europol+(\"hybrid\"+OR+\"sabotage\"+OR+\"organized+crime\")&hl=en-US&gl=US&ceid=US:en", "Europol"),
        ("https://news.google.com/rss/search?q=Eurojust+(\"cross-border\"+OR+\"sanctions\"+OR+\"investigation\")&hl=en-US&gl=US&ceid=US:en", "Eurojust"),
        ("https://euvsdisinfo.eu/feed/", "EUvsDisinfo"),
        ("https://www.disinfo.eu/feed/", "EU DisinfoLab"),
        ("https://citizenlab.ca/feed/", "Citizen Lab"),
        ("https://chinadigitaltimes.net/feed/", "China Digital Times"),
        ("https://news.google.com/rss/search?q=(\"EEAS\"+OR+\"FIMI\"+OR+\"Doppelganger\")+disinformation&hl=en-US&gl=US&ceid=US:en", "EEAS FIMI"),
        ("https://www.bellingcat.com/feed/", "Bellingcat OSINT"),
        ("https://theins.ru/feed", "The Insider"),
        ("https://www.occrp.org/en/feed", "OCCRP"),
        ("https://news.google.com/rss/search?q=(\"RUSI\"+OR+\"CSIS\")+sabotage+Europe&hl=en-US&gl=US&ceid=US:en", "RUSI / CSIS"),
        ("https://www.rumafia.net/en/rss/", "RuMafia"),
        ("https://antikor.com.ua/articles.rss", "Antikor Portal"),
        ("https://news.google.com/rss/search?q=(\"empresas+amenazadas+por+rusia\"+OR+Tecnobit+OR+Destinus+OR+Indra+OR+Navantia+OR+Escribano)&hl=es&gl=ES&ceid=ES:es", "Empresas Defensa ES"),
        ("https://news.google.com/rss/search?q=(CNI+OR+\"Guardia+Civil\"+OR+\"Policia+Nacional\")+(sabotaje+OR+espia+OR+ciberataque)&hl=es&gl=ES&ceid=ES:es", "España Seguridad"),
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado)+(espia+ruso+OR+espia+chino+OR+sabotaje)&hl=es&gl=ES&ceid=ES:es", "Detenciones"),
        ("https://theobjective.com/feed/", "The Objective"),
        ("https://news.google.com/rss/search?q=(\"Institute+for+the+Study+of+War\"+OR+\"ISW\")+Russian+offensive&hl=en-US&gl=US&ceid=US:en", "ISW War Studies"),
        ("https://kyivindependent.com/feed/", "The Kyiv Independent"),
        ("https://www.pravda.com.ua/rss/", "Ukrainska Pravda"),
        ("https://meduza.io/rss/all", "Meduza"),
        ("https://news.google.com/rss/search?q=Россия+(диверсия+OR+шпионаж+OR+СБУ)&hl=ru&gl=RU&ceid=RU:ru", "Rusia Frente (RU)"),
        ("https://news.google.com/rss/search?q=Україна+(диверсія+OR+агент+OR+ГУР)&hl=uk&gl=UA&ceid=UA:uk", "Ucrania Frente (UA)"),
        ("https://news.google.com/rss/search?q=(OFAC+OR+\"OpenSanctions\"+OR+\"KSE+Institute\")+(Russia+OR+sanctions)&hl=en-US&gl=US&ceid=US:en", "OFAC Sanciones"),
        ("https://news.google.com/rss/search?q=(\"flota+fantasma\"+OR+\"shadow+fleet\"+OR+\"elusion+de+sanciones\")&hl=es&gl=ES&ceid=ES:es", "Flota Fantasma"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto"),
        ("https://news.google.com/rss/search?q=(China+OR+Pekín)+(espionaje+OR+ciberataque+OR+injerencia)&hl=es&gl=ES&ceid=ES:es", "China Injerencia"),
        ("https://news.google.com/rss/search?q=(روسيا+OR+أوكرانيا+OR+الصين)+(تجسس+OR+طائرات+مسيرة)&hl=ar&gl=EG&ceid=EG:ar", "Intel Árabe (AR)")
    ]

    todos_brutos = []

    with ThreadPoolExecutor(max_workers=32) as ex:
        f_tg = [ex.submit(raspar_canal_tg, c) for c in canales_tg]
        f_yt = [ex.submit(raspar_youtube, cid, tag) for cid, tag in canales_yt]
        f_rss = [ex.submit(procesar_feed_rss, url, tag) for url, tag in fuentes_rss]

        for f in as_completed(f_tg + f_yt + f_rss):
            try:
                res = f.result()
                todos_brutos.extend(res)
            except Exception:
                pass

    unicos = []
    urls_vistas = set()
    for b in todos_brutos:
        u = limpiar_url(b["enlace"])
        if u and u in urls_vistas: continue

        coincidente = None
        for u_item in unicos:
            if b["fecha_dt"] and u_item["fecha_dt"]:
                if abs((b["fecha_dt"] - u_item["fecha_dt"]).total_seconds()) > (36 * 3600):
                    continue
            t1 = normalizar_texto(b["titular_raw"])
            t2 = normalizar_texto(u_item["titular_raw"])
            if len(t1) >= 15 and len(t2) >= 15 and SequenceMatcher(None, t1[:140], t2[:140]).ratio() > 0.84:
                coincidente = u_item
                break

        if coincidente:
            sec = coincidente.setdefault("fuentes_coincidentes", [])
            if not any(f["enlace"] == b["enlace"] for f in sec) and b["enlace"] != coincidente["enlace"]:
                sec.append({"canal": b["canal"], "enlace": b["enlace"], "titular": b["titular_raw"]})
            continue

        if u: urls_vistas.add(u)
        b.setdefault("fuentes_coincidentes", [])
        unicos.append(b)

    # Ordenación cronológica estricta: de más reciente a más antigua
    unicos.sort(key=lambda x: x["fecha_dt"] if x["fecha_dt"] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return unicos

# ==========================================
# 10. INTERFAZ Y RENDERIZADO
# ==========================================
if "alertas_base" not in st.session_state:
    with st.spinner("Sincronizando fuentes abiertas..."):
        unicos = cargar_todas_las_fuentes()
        for item in unicos:
            cat = categorizar_alerta(item["titular_raw"], item["cuerpo_raw"], item["plataforma"], item["canal"])
            item["categoria"] = cat
            item["evaluacion_contexto"] = obtener_contexto_fuente(cat, item["canal"])
            item["titular_traducido"] = ""
            item["cuerpo_traducido"] = ""
            item["traducido_ok"] = False
        st.session_state.alertas_base = unicos
        st.session_state.ultima_actualizacion = obtener_fecha_madrid_str()
        st.session_state.seleccionadas_pdf = {a["id"] for a in unicos}

# Cabecera Táctica con Logo Hexagonal HD y Widget de Traducción de Google
logo_svg = """<svg width="65" height="65" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="50" cy="50" r="47" fill="#1e293b" stroke="#334155" stroke-width="2" />
    <circle cx="50" cy="50" r="40" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.6" />
    <path d="M50 14 L75 25 V52 C75 69 50 84 50 84 C50 84 25 69 25 52 V25 Z" fill="#0f172a" stroke="#38bdf8" stroke-width="2.5" />
    <polygon points="53,24 37,48 48,48 43,72 61,44 51,44" fill="#ef4444" />
</svg>"""

col_head1, col_head2 = st.columns([0.75, 0.25])
with col_head1:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); border-left: 8px solid #dc2626; border-radius: 10px; padding: 14px 20px; display: flex; align-items: center; gap: 18px;">
        <div>{logo_svg}</div>
        <div>
            <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 900; letter-spacing: 0.5px;">BUSCADOR DE AMENAZAS HÍBRIDAS</h1>
            <p style="color: #94a3b8; font-size: 13px; margin: 4px 0 0 0;">SISTEMA DE VIGILANCIA DE FUENTES ABIERTAS // OSINT</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_head2:
    # Widget web de Google Translate integrado arriba a la derecha (equivalente al botón derecho)
    components.html("""
    <div style="text-align: right; padding-top: 10px;">
        <span style="font-size: 12px; color: #cbd5e1; font-family: sans-serif; font-weight: bold;">Traducir página: </span>
        <div id="google_translate_element" style="display: inline-block;"></div>
    </div>
    <script type="text/javascript">
    function googleTranslateElementInit() {
      new google.translate.TranslateElement({pageLanguage: 'auto', includedLanguages: 'es', layout: google.translate.TranslateElement.InlineLayout.SIMPLE}, 'google_translate_element');
    }
    </script>
    <script type="text/javascript" src="//translate.google.com/translate.a/element.js?cb=googleTranslateElementInit"></script>
    """, height=50)

# Panel de Filtros
with st.container():
    c_flt1, c_flt2, c_flt3 = st.columns([1.8, 1.2, 1.2])
    with c_flt1:
        busqueda_txt = st.text_input("🔍 Búsqueda Booleana (multilingüe):", placeholder='Ej: "flota fantasma" OR sabotaje -incendio')
    with c_flt2:
        rango_tiempo = st.selectbox("📅 Ventana Temporal:", ["Últimas 24 Horas", "Últimas 48 Horas", "Última Semana", "Histórico Completo"], index=3)
    with c_flt3:
        canales_sel = st.multiselect("Filtrar por Canal:", sorted(list({a["canal"] for a in st.session_state.alertas_base})))

# Filtrado reactivo en memoria
alertas_visibles = st.session_state.alertas_base

if rango_tiempo == "Últimas 24 Horas":
    alertas_visibles = [x for x in alertas_visibles if x["fecha_dt"] and cumple_ventana_temporal(x["fecha_dt"], 24)]
elif rango_tiempo == "Últimas 48 Horas":
    alertas_visibles = [x for x in alertas_visibles if x["fecha_dt"] and cumple_ventana_temporal(x["fecha_dt"], 48)]
elif rango_tiempo == "Última Semana":
    alertas_visibles = [x for x in alertas_visibles if x["fecha_dt"] and cumple_ventana_temporal(x["fecha_dt"], 168)]

if busqueda_txt:
    alertas_visibles = [x for x in alertas_visibles if cumple_busqueda_booleana(x, busqueda_txt)]
if canales_sel:
    alertas_visibles = [x for x in alertas_visibles if x["canal"] in canales_sel]

# Asegurar orden cronológico de más recientes a más antiguas
alertas_visibles.sort(key=lambda x: x["fecha_dt"] if x["fecha_dt"] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

# Barra de herramientas y exportación
c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([1.1, 1.2, 1.2, 1.5])
with c_btn1:
    if st.button("🔄 ACTUALIZAR", type="primary", use_container_width=True):
        st.cache_data.clear()
        del st.session_state.alertas_base
        st.rerun()

alertas_informe = [x for x in alertas_visibles if st.session_state.get(f"sel_{x['id']}", False)]

with c_btn2:
    html_reporte = f"""<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Informe</title></head>
    <body style='font-family:sans-serif;padding:20px;'><h1>INFORME DE SITUACIÓN // AMENAZAS HÍBRIDAS</h1>
    <p>Emisión: {obtener_fecha_madrid_str()} | Ventana: {rango_tiempo}</p><hr>"""
    for a in alertas_informe:
        tit_exp = a['titular_traducido'] if a.get('traducido_ok') else a['titular_raw']
        html_reporte += f"<div style='border-bottom:1px solid #ccc;padding:10px 0;'>"
        html_reporte += f"<h3>{html_module.escape(tit_exp)}</h3>"
        html_reporte += f"<p><strong>Fuente:</strong> {html_module.escape(a['canal'])} | <strong>Contexto:</strong> {html_module.escape(a['evaluacion_contexto'])}</p>"
        html_reporte += f"<p><a href='{url_sanitizada(a['enlace'])}' target='_blank'>Enlace a la fuente</a></p></div>"
    html_reporte += "</body></html>"

    st.download_button(
        f"📥 INFORME HTML ({len(alertas_informe)})",
        data=html_reporte,
        file_name=f"Informe_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.html",
        mime="text/html",
        use_container_width=True
    )

with c_btn3:
    md_reporte = f"# INFORME DE INTELIGENCIA\n**Emisión:** {obtener_fecha_madrid_str()}\n\n---\n\n"
    for a in alertas_informe:
        tit_exp = a['titular_traducido'] if a.get('traducido_ok') else a['titular_raw']
        md_reporte += f"### {tit_exp}\n- **Fuente:** {a['canal']}\n- **Contexto:** {a['evaluacion_contexto']}\n- **Enlace:** {a['enlace']}\n\n"

    st.download_button(
        f"📝 INFORME MD ({len(alertas_informe)})",
        data=md_reporte.encode('utf-8'),
        file_name=f"Informe_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
        use_container_width=True
    )

with c_btn4:
    st.markdown(f'<div style="font-size:12px;color:#94a3b8;margin-top:6px;">🕒 Sincronizado: {st.session_state.ultima_actualizacion}<br>Mostrando <strong>{len(alertas_visibles)}</strong> alertas.</div>', unsafe_allow_html=True)

col_s1, col_s2, _ = st.columns([1.2, 1.2, 4.0])
with col_s1:
    if st.button("✅ Seleccionar visibles", use_container_width=True):
        for a in alertas_visibles: st.session_state[f"sel_{a['id']}"] = True
        st.rerun()
with col_s2:
    if st.button("⬜ Desmarcar visibles", use_container_width=True):
        for a in alertas_visibles: st.session_state[f"sel_{a['id']}"] = False
        st.rerun()

st.write("")

# ==========================================
# 11. RENDERIZADO CON TRADUCCIÓN INDIVIDUAL
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros.")
        return

    # Orden cronológico estricto garantizado dentro de la pestaña
    items_ordenados = sorted(lista_items, key=lambda x: x["fecha_dt"] if x["fecha_dt"] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    for item in items_ordenados:
        cat = item.get("categoria", "GLOBAL")
        b_color = {
            "ESPANA": "#dc2626", "EMPRESA": "#d97706", "GLOBAL": "#0f172a",
            "CRIMENES": "#991b1b", "CRIPTO": "#7c3aed", "SANCIONES": "#b45309",
            "DETENCION": "#be123c", "CHINA": "#1e3a8a", "TELEGRAM": "#0284c7", "REDES": "#059669"
        }.get(cat, "#0f172a")

        esta_traducido = item.get("traducido_ok", False)
        titular_mostrar = item["titular_traducido"] if esta_traducido else item["titular_raw"]
        cuerpo_mostrar = item["cuerpo_traducido"] if esta_traducido else item["cuerpo_raw"]

        enlace_seguro = url_sanitizada(item.get("enlace", ""))
        fecha_str = obtener_fecha_madrid_str(item["fecha_dt"]) if item["fecha_dt"] else "Fecha reciente"

        # Foto lateral
        media_html = ""
        if item.get("multimedia") and es_url_segura(item["multimedia"]):
            media_html = f'<div style="flex: 0 0 260px; max-width: 260px; margin-right: 18px;"><img src="{html_module.escape(item["multimedia"])}" style="width: 100%; border-radius: 6px; max-height: 220px; object-fit: cover;" /></div>'

        # Bloque Reporte / Post
        bloque_reporte = ""
        if cat in ["TELEGRAM", "REDES"]:
            bloque_reporte = f'<div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 14px; color: #1e293b; font-size: 13.5px; line-height: 1.55; text-align: justify; margin-bottom: 8px;"><strong>📄 Reporte / Post:</strong><br>{html_module.escape(cuerpo_mostrar)}</div>'

        # HTML plano sin sangrías al inicio de línea para evitar que Streamlit lo confunda con código
        html_card = (
            f'<div style="background:#ffffff; border:1px solid #cbd5e1; border-left:7px solid {b_color}; border-radius:8px; padding:14px 18px; margin-bottom:8px;">'
            f'<div style="display: flex; flex-wrap: wrap; align-items: flex-start;">'
            f'{media_html}'
            f'<div style="flex: 1; min-width: 260px;">'
            f'<div style="font-size:11.5px; color:#64748b; margin-bottom:6px;">📅 {fecha_str} | 📢 {html_module.escape(item["canal"])}</div>'
            f'<div style="font-size:16.5px; font-weight:800; color:#0f172a; margin-bottom:8px;">{html_module.escape(titular_mostrar)}</div>'
            f'{bloque_reporte}'
            f'<div style="background:#f0f9ff; border-left:4px solid #0284c7; padding:8px 12px; color:#0c4a6e; font-size:13px; margin-bottom:10px;">'
            f'<strong>Contexto de la fuente:</strong> {html_module.escape(item["evaluacion_contexto"])}'
            f'</div>'
            f'<a href="{enlace_seguro}" target="_blank" rel="noopener noreferrer" style="background:#0f172a; color:#fff; padding:6px 14px; border-radius:4px; font-size:11.5px; font-weight:700; text-decoration:none;">🔗 ABRIR FUENTE ORIGINAL</a>'
            f'</div>'
            f'</div>'
            f'</div>'
        )

        col_chk, col_content = st.columns([0.04, 0.96])
        with col_chk:
            st.checkbox("Sel", key=f"sel_{item['id']}", label_visibility="collapsed")
        with col_content:
            st.markdown(html_card, unsafe_allow_html=True)
            
            # Botón de traducción bajo demanda
            c_tr, c_cp = st.columns([0.22, 0.78])
            with c_tr:
                if not esta_traducido:
                    if st.button("🌐 TRADUCIR ALERTA", key=f"btn_tr_{item['id']}", use_container_width=True):
                        with st.spinner("Traduciendo..."):
                            item["titular_traducido"] = traducir_texto(item["titular_raw"])
                            item["cuerpo_traducido"] = traducir_texto(item["cuerpo_raw"][:800])
                            item["traducido_ok"] = True
                        st.rerun()
                else:
                    st.caption("✅ Traducida al español")

            # Formato de copia de la alerta
            with st.expander("📋 Formato de alerta para copiar"):
                texto_copia = f"🚨 ALERTA OSINT | {cat}\nTitular: {titular_mostrar}\nReporte: {cuerpo_mostrar[:300]}...\nFuente: {item['canal']} | Fecha: {fecha_str}\nEnlace: {item.get('enlace','')}"
                st.code(texto_copia, language="markdown")

            if item.get("fuentes_coincidentes"):
                with st.expander(f"📑 Coberturas coincidentes ({len(item['fuentes_coincidentes'])})"):
                    for f_sec in item["fuentes_coincidentes"]:
                        st.markdown(f"- **{html_module.escape(f_sec['canal'])}:** [{html_module.escape(f_sec['titular'])}]({url_sanitizada(f_sec['enlace'])})")

categorias_orden = [
    ("ESPANA", "🇪🇸 ESPAÑA"), ("EMPRESA", "🏢 EMPRESAS"), ("GLOBAL", "📰 GLOBAL"),
    ("CRIMENES", "⚖️ CRÍMENES"), ("CRIPTO", "🟣 CRIPTO"), ("SANCIONES", "📦 SANCIONES"),
    ("DETENCION", "🛑 DETENCIONES"), ("CHINA", "🇨🇳 CHINA"), ("TELEGRAM", "✈️ TELEGRAM"), ("REDES", "🌐 REDES")
]

tabs = st.tabs([f"{nombre} ({len([x for x in alertas_visibles if x['categoria'] == cod])})" for cod, nombre in categorias_orden])
for tab, (cod, _) in zip(tabs, categorias_orden):
    with tab:
        renderizar_tarjetas([x for x in alertas_visibles if x["categoria"] == cod])
