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
import html as html_module
import hashlib
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse
import pandas as pd

# ==========================================
# 1. CONFIGURACIÓN STREAMLIT & CONTROL ACCESO
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
    return dt.astimezone(ZONA_MADRID).strftime("%d/%m/%Y %H:%M:%S")

# Blindaje visual CSS: oculta branding de GitHub y gestión de Streamlit Cloud
# sin eliminar los controles de apertura del sidebar en dispositivos móviles
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
@st.cache_resource
def get_sesion_http():
    sesion = requests.Session()
    adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=1)
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
# 3. REGEX Y MATRICES OPERATIVAS (CON \b)
# ==========================================
CANALES_DESINFORMACION_ESP = {
    "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu", "ASupersharij"
}

EMPRESAS_ESTRATEGICAS_ESP = [
    "destinus", "mikhail kokorich", "kokorich", "uav navigation", "grupo oesia", "oesia", "tecnobit", "indra", "navantia", "escribano",
    "em&e", "sener", "arquimea", "santa barbara sistemas", "santa barbara", "gdels", "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa",
    "itp aero", "airbus espana", "airbus getafe", "hisdesat", "hispasat", "sateliot", "pld space", "amadeus", "aena", "enaire", "renfe", "adif",
    "telefonica", "iberdrola", "repsol", "naturgy", "redeia", "red electrica", "enagas", "talgo"
]

PATRON_ACTORES_GEOPOLITICOS = re.compile(
    r'\b('
    r'rusia|ruso|rusa|rusos|rusas|moscu|kremlin|putin|'
    r'russia|russian|russians|moscow|russie|russe|russland|'
    r'gru|fsb|svr|apt28|apt29|fancy bear|cozy bear|sandworm|noname057|killnet|turla|garantex|wagner|'
    r'bielorrusia|belarus|kaliningrado|lukashenko|'
    r'ucrania|ucraniano|ucraniana|ukraine|ukrainian|zelensky|zsu|sbu|gur|hur|'
    r'kursk|belgorod|donbas|crimea|zaporiyia|kherson|donetsk|lugansk|shariy|sharij|rybar|zvinchuk|'
    r'china|chino|chinos|chinas|pekin|beijing|xi jinping|pcc|pla|taiwan|taipei|hong kong|'
    r'apt41|volt typhoon|flax typhoon|mustang panda|salt typhoon|mss|'
    r'otan|nato|dsn|cni|incibe|europol|eurojust|stratcom|euvsdisinfo|bellingcat|the insider|occrp|meduza|mediazona|isw'
    r')\b', re.IGNORECASE
)

PATRON_CIRILICO = re.compile(
    r'\b(россия|российск\w*|рф|москва|кремль|путин|гру|фсб|свр|украин\w*|киев|київ|зсу|сбу|гур|вагнер|шахед|герань|диверси\w*|шпион\w*)\b',
    re.IGNORECASE
)

PATRON_CHINO = re.compile(r'(中国|北京|习近平|解放军|台湾|华为|国安部|间谍)')
PATRON_ARABE = re.compile(r'(روسيا|أوكرانيا|الصين|بوتين|تجسس|طائرات مسيرة)')
PATRON_POLACO = re.compile(r'\b(rosja|rosyjsk\w*|ukraina|putin|sabotaż\w*|szpiegostw\w*|dywersj\w*|abw)\b', re.IGNORECASE)

PATRON_VECTORES_AMENAZA = re.compile(
    r'\b('
    r'hibrid\w*|hybrid\w*|sabotaj\w*|sabotage\w*|saboteador\w*|espia\w*|espionaje|spy\w*|espionage|'
    r'ciberataque\w*|ciberespionaje|ransomware|ddos|malware|infiltracion cibernetica|'
    r'desinformacion|fimi|doppelganger|red de bots|'
    r'elusion de sanciones|evasion de sanciones|flota fantasma|shadow fleet|petrolero clandestino|'
    r'cable submarino|undersea cable|red ferroviaria sabot\w*|paquete incendiario|incendio provocado|'
    r'crimen de guerra|war crime\w*|corte penal internacional|cpi|icc|genocidio|fosa comun|'
    r'detenido por espionaje|arrestado por sabotaje|detencion de espia|'
    r'dron kamikaze|guerra electronica|jamming|sicariato|asesinato selectivo|envenenamiento|'
    r'garantex|usdt ilicito|financiacion terrorista'
    r')\b', re.IGNORECASE
)

# ==========================================
# 4. SANITIZACIÓN Y NORMALIZACIÓN
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
# 5. FILTROS Y CLASIFICACIÓN
# ==========================================
def cumple_ventana_temporal(fecha_dt, horas):
    if not fecha_dt: return False
    if fecha_dt.tzinfo is None: fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    dif = (datetime.now(timezone.utc) - fecha_dt.astimezone(timezone.utc)).total_seconds()
    # Margen de 5 minutos por posibles desajustes de reloj
    return -300 <= dif <= (horas * 3600)

def tiene_vinculo_geopolitico(texto):
    if not texto: return False
    t_norm = normalizar_texto(texto)
    t_raw = str(texto)

    # Exclusión religiosa estricta sin afectar noticias generales
    if re.search(r'\b(vaticano|santa sede|papa francisco|obispo diocesano)\b', t_norm):
        if not PATRON_VECTORES_AMENAZA.search(t_norm):
            return False

    if PATRON_ACTORES_GEOPOLITICOS.search(t_norm):
        return True
    if PATRON_CIRILICO.search(t_raw) or PATRON_CHINO.search(t_raw) or PATRON_ARABE.search(t_raw) or PATRON_POLACO.search(t_norm):
        return True

    return False

def tiene_vector_amenaza_global(texto):
    t_norm = normalizar_texto(texto)
    if any(emp in t_norm for emp in ["destinus", "uav navigation", "oesia", "tecnobit", "indra", "escribano"]):
        return True
    return bool(PATRON_VECTORES_AMENAZA.search(t_norm))

def categorizar_alerta(titular, cuerpo, plat, canal):
    if plat == "REDES": return "REDES"
    if plat == "TELEGRAM": return "TELEGRAM"
    
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    
    # 1. España e infraestructuras críticas prioritarias
    if re.search(r'\b(espana|espanol\w*|madrid|barcelona|cartagena|rota|torrejon|cni|dsn|incibe|ccn-cert|margarita robles)\b', t_all):
        return "ESPANA"
    
    # 2. Empresas de defensa estratégicas
    if any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP):
        return "EMPRESA"
        
    # 3. Detenciones policiales y contraespionaje
    if re.search(r'\b(detenido\w*|arrestado\w*|detencion\w*|arresto\w*|zatrzymany)\b', t_all):
        if not re.search(r'\b(prisionero de guerra|pow)\b', t_all):
            return "DETENCION"
            
    # 4. Criptomonedas / FININT
    if re.search(r'\b(cripto\w*|bitcoin|btc|usdt|tether|garantex|blockchain)\b', t_all):
        return "CRIPTO"
        
    # 5. Sanciones / Flota fantasma (CREA delimitado estrictamente)
    if re.search(r'\b(sancion\w*|elusion|flota fantasma|shadow fleet|tope al crudo|price cap|ofac|opensanctions|kse institute|\bcrea\b.*ingresos)\b', t_all):
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
    if "rybar" in c: return "Canal de análisis militar ruso vinculado a M. Zvinchuk. Información sujeta a narrativa oficial."
    if "sharij" in c: return "Canal de Anatolii Sharij: seguimiento y difusión del frente ucraniano-ruso."
    if c.replace("@","") in CANALES_DESINFORMACION_ESP: return "Fuente identificada en redes de difusión y manipulación informativa (FIMI)."
    if any(k in c for k in ["bellingcat", "insider", "occrp"]): return "Investigación OSINT / Periodismo de investigación sobre inteligencia y operaciones encubiertas."
    if any(k in c for k in ["stratcom", "euvsdisinfo", "dfrlab"]): return "Registro institucional de desinformación e injerencia extranjera."
    
    contextos = {
        "ESPANA": "Impacto directo en la seguridad nacional o infraestructuras críticas de España.",
        "EMPRESA": "Seguimiento corporativo: sector defensa y empresas estratégicas.",
        "DETENCION": "Operación policial o judicial contra redes de espionaje o sabotaje.",
        "SANCIONES": "Vigilancia de elusión de sanciones comerciales, financieras o flota fantasma.",
        "CRIPTO": "Inteligencia financiera (FININT): trazabilidad de activos y transferencias.",
        "CHINA": "Operaciones estratégicas, ciberespionaje o injerencia atribuidas a actores chinos.",
        "CRIMENES": "Documentación de incidentes con víctimas civiles, crímenes de guerra o terrorismo."
    }
    return contextos.get(categoria, "Información operativa de fuentes abiertas sobre el conflicto.")

# ==========================================
# 6. TRADUCCIÓN THREAD-SAFE CON CACHÉ
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

    params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:350]}
    try:
        r = SESION_HTTP.get("https://translate.googleapis.com/translate_a/single", params=params, timeout=2.0)
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

def traducir_lote(lista_textos):
    with ThreadPoolExecutor(max_workers=20) as ex:
        return list(ex.map(traducir_texto, lista_textos))

# ==========================================
# 7. PARSER BOOLEANO REAL (COMILLAS, AND, OR, NOT)
# ==========================================
def cumple_busqueda_booleana(item, query_str):
    if not query_str or not query_str.strip():
        return True

    corpus = f"{item.get('titular_traducido','')} {item.get('titular_original','')} {item.get('cuerpo_original','')}"
    t_norm = normalizar_texto(corpus)
    q_raw = query_str.strip()

    # 1. Comprobar frases exactas entre comillas
    frases_exactas = re.findall(r'"([^"]+)"', q_raw)
    for frase in frases_exactas:
        if normalizar_texto(frase) not in t_norm:
            return False

    q_sin_frases = re.sub(r'"[^"]+"', ' ', q_raw)

    # 2. Términos NOT (palabra NOT explícita o guion precedido de espacio/inicio de línea)
    terminos_not = re.findall(r'(?:NOT\s+|(?:^|\s)-)(\w+)', q_sin_frases, flags=re.IGNORECASE)
    for t_not in terminos_not:
        if re.search(r'\b' + re.escape(normalizar_texto(t_not)) + r'\b', t_norm):
            return False

    q_limpia = re.sub(r'(?:NOT\s+|(?:^|\s)-)\w+', ' ', q_sin_frases, flags=re.IGNORECASE)
    q_limpia = re.sub(r'[()]', ' ', q_limpia).strip()
    if not q_limpia:
        return True

    # 3. Evaluación de bloques OR / AND
    bloques_or = [b.strip() for b in re.split(r'\bOR\b', q_limpia, flags=re.IGNORECASE) if b.strip()]
    for bloque in bloques_or:
        terminos_and = [t.strip() for t in re.split(r'\bAND\b|\s+', bloque, flags=re.IGNORECASE) if t.strip()]
        if all(re.search(r'\b' + re.escape(normalizar_texto(t)) + r'\b', t_norm) for t in terminos_and):
            return True

    return False

# ==========================================
# 8. RASTREADORES EN PARALELO
# ==========================================
def procesar_feed_rss(url, etiqueta):
    items = []
    err = None
    try:
        r = SESION_HTTP.get(url, timeout=2.5)
        if r.status_code != 200:
            return [], f"{etiqueta} (HTTP {r.status_code})"
        root = ET.fromstring(r.content)
        for item in root.findall(".//item")[:25]:
            tit = limpiar_html_texto(item.findtext("title", ""))
            desc = limpiar_html_texto(item.findtext("description", ""))
            link = item.findtext("link", "").strip()
            pub = item.findtext("pubDate", "")

            if not tit or len(tit) < 10: continue
            texto_eval = f"{tit} {desc}"
            if not tiene_vinculo_geopolitico(texto_eval) or not tiene_vector_amenaza_global(texto_eval):
                continue

            try: dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception: dt = None

            items.append({
                "id": f"RSS-{id_determinista(link)}", "plataforma": "PRENSA", "canal": etiqueta,
                "fecha_dt": dt, "titular_raw": limpiar_firma_medio(tit), "cuerpo_raw": desc[:1000],
                "enlace": link, "multimedia": None, "tiene_video": False
            })
    except Exception as e:
        err = f"{etiqueta} ({type(e).__name__})"
    return items, err

def raspar_canal_tg(canal):
    mensajes = []
    err = None
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=2.5)
        if r.status_code != 200:
            return [], f"Telegram @{canal} (HTTP {r.status_code})"
        soup = BeautifulSoup(r.text, "html.parser")
        for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-30:]:
            time_tag = p.find("time")
            dt = None
            if time_tag and time_tag.get("datetime"):
                try: dt = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception: dt = None

            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""
            if len(texto_raw) < 15: continue

            texto_sin_urls = limpiar_html_texto(re.sub(r'http\S+', '', texto_raw))
            if not tiene_vinculo_geopolitico(texto_sin_urls): continue

            a_link = p.find("a", class_="tgme_widget_message_date")
            link = a_link["href"] if a_link else f"https://t.me/s/{canal}"
            tit_corto = re.split(r'(?<=[.!?])\s+', texto_sin_urls)[0][:140]

            mensajes.append({
                "id": f"TG-{id_determinista(link)}", "plataforma": "TELEGRAM", "canal": f"@{canal}",
                "fecha_dt": dt, "titular_raw": tit_corto, "cuerpo_raw": texto_sin_urls[:1500],
                "enlace": link, "multimedia": None, "tiene_video": False
            })
    except Exception as e:
        err = f"Telegram @{canal} ({type(e).__name__})"
    return mensajes, err

def raspar_youtube(canal_id, etiqueta):
    items = []
    err = None
    try:
        r = SESION_HTTP.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}", timeout=2.5)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:12]:
                tit = limpiar_html_texto(entry.findtext("{http://www.w3.org/2005/Atom}title", ""))
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_el.get("href", "") if link_el is not None else ""
                pub = entry.findtext("{http://www.w3.org/2005/Atom}published", "")
                desc = limpiar_html_texto(entry.findtext(".//{http://search.yahoo.com/mrss/}description", ""))

                if not tiene_vinculo_geopolitico(f"{tit} {desc}"): continue
                try: dt = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception: dt = None

                items.append({
                    "id": f"YT-{id_determinista(link)}", "plataforma": "REDES", "canal": etiqueta,
                    "fecha_dt": dt, "titular_raw": tit, "cuerpo_raw": desc[:800],
                    "enlace": link, "multimedia": None, "tiene_video": True
                })
        else:
            err = f"{etiqueta} (HTTP {r.status_code})"
    except Exception as e:
        err = f"{etiqueta} ({type(e).__name__})"
    return items, err

# ==========================================
# 9. GESTOR DE ACTUALIZACIÓN CON CACHÉ DATA
# ==========================================
@st.cache_data(ttl=600, show_spinner=False)
def cargar_datos_fuentes():
    canales_tg = [
        "rybar", "ASupersharij", "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "GeneralStaffZSU", "kpszsu", "operativnoZSU", "CenterCounteringDisinformation",
        "meduzalive", "astrapress", "istories_media", "vchkogpu", "mediazzzona", "bbcrussian",
        "novayagazeta_europe", "theinsider", "kyivindependent_official", "pravda_com_ua",
        "ukrainenowenglish", "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu"
    ]
    canales_yt = [
        ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
        ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
        ("UCvps_Xgqj0i2v4Puh2p-E2w", "YouTube: Kyiv Independent")
    ]
    fuentes_rss = [
        ("https://news.google.com/rss/search?q=(DSN+OR+\"Seguridad+Nacional\"+OR+\"FIMI\")+España&hl=es&gl=ES&ceid=ES:es", "DSN España"),
        ("https://news.google.com/rss/search?q=(\"Consilium\"+OR+\"Consejo+de+la+UE\")+(\"amenazas+híbridas\"+OR+\"sanciones\")&hl=es&gl=ES&ceid=ES:es", "UE Consilium"),
        ("https://news.google.com/rss/search?q=(\"NATO+StratCom\"+OR+\"StratCom+COE\")+disinformation&hl=en-US&gl=US&ceid=US:en", "NATO StratCom"),
        ("https://news.google.com/rss/search?q=Europol+(\"hybrid+threats\"+OR+\"organized+crime\"+OR+\"sabotage\")&hl=en-US&gl=US&ceid=US:en", "Europol"),
        ("https://news.google.com/rss/search?q=Eurojust+(\"cross-border\"+OR+\"investigation\"+OR+\"sanctions\")&hl=en-US&gl=US&ceid=US:en", "Eurojust"),
        ("https://euvsdisinfo.eu/feed/", "EUvsDisinfo"),
        ("https://www.disinfo.eu/feed/", "EU DisinfoLab"),
        ("https://citizenlab.ca/feed/", "Citizen Lab"),
        ("https://chinadigitaltimes.net/feed/", "China Digital Times"),
        ("https://www.bellingcat.com/feed/", "Bellingcat OSINT"),
        ("https://theins.ru/feed", "The Insider"),
        ("https://www.occrp.org/en/feed", "OCCRP"),
        ("https://kyivindependent.com/feed/", "The Kyiv Independent"),
        ("https://www.pravda.com.ua/rss/", "Ukrainska Pravda"),
        ("https://meduza.io/rss/all", "Meduza"),
        ("https://theobjective.com/feed/", "The Objective"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin)+(España+OR+Madrid+OR+CNI)&hl=es&gl=ES&ceid=ES:es", "España Seguridad"),
        ("https://news.google.com/rss/search?q=(China+OR+Pekín+OR+Xi+Jinping)+(España+OR+ciberespionaje)&hl=es&gl=ES&ceid=ES:es", "China Injerencia"),
        ("https://news.google.com/rss/search?q=(\"empresas+amenazadas+por+rusia\"+OR+Tecnobit+OR+Destinus+OR+Indra+OR+Navantia)&hl=es&gl=ES&ceid=ES:es", "Empresas Amenazadas"),
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado)+(espia+ruso+OR+espia+chino+OR+sabotaje)&hl=es&gl=ES&ceid=ES:es", "Detenciones"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+China)+(\"elusion+de+sanciones\"+OR+\"flota+fantasma\")&hl=es&gl=ES&ceid=ES:es", "Sanciones / Flota"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto")
    ]

    todos_brutos = []
    errores = []

    with ThreadPoolExecutor(max_workers=24) as ex:
        f_tg = [ex.submit(raspar_canal_tg, c) for c in canales_tg]
        f_yt = [ex.submit(raspar_youtube, cid, tag) for cid, tag in canales_yt]
        f_rss = [ex.submit(procesar_feed_rss, url, tag) for url, tag in fuentes_rss]

        for f in as_completed(f_tg + f_yt + f_rss):
            try:
                res, err = f.result()
                todos_brutos.extend(res)
                if err: errores.append(err)
            except Exception:
                pass

    # Deduplicación contextual y temporal estricta (máximo 36h de diferencia)
    unicos = []
    urls_vistas = set()
    for b in todos_brutos:
        u = limpiar_url(b["enlace"])
        if u in urls_vistas: continue

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

        urls_vistas.add(u)
        b.setdefault("fuentes_coincidentes", [])
        unicos.append(b)

    return unicos, errores

# ==========================================
# 10. INTERFAZ DE USUARIO Y CONTROL
# ==========================================
if "alertas_base" not in st.session_state:
    with st.spinner("Sincronizando fuentes abiertas..."):
        unicos, errores = cargar_datos_fuentes()
        # Enriquecer con categorías y análisis de contexto
        for item in unicos:
            cat = categorizar_alerta(item["titular_raw"], item["cuerpo_raw"], item["plataforma"], item["canal"])
            item["categoria"] = cat
            item["evaluacion_contexto"] = obtener_contexto_fuente(cat, item["canal"])
            item["titular_traducido"] = item["titular_raw"]
            item["cuerpo_traducido"] = item["cuerpo_raw"]
            item["traducido_ok"] = False
        st.session_state.alertas_base = unicos
        st.session_state.errores_fuentes = errores
        st.session_state.ultima_actualizacion = obtener_fecha_madrid_str()
        st.session_state.seleccionadas_pdf = {a["id"] for a in unicos}

# Cabecera táctica
st.markdown("""
<div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); border-left: 8px solid #dc2626; border-radius: 10px; padding: 16px 22px; margin-bottom: 14px;">
    <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 900; letter-spacing: 0.5px;">BUSCADOR DE AMENAZAS HÍBRIDAS</h1>
    <p style="color: #94a3b8; font-size: 13px; margin: 4px 0 0 0;">SISTEMA DE VIGILANCIA DE FUENTES ABIERTAS // OSINT</p>
</div>
""", unsafe_allow_html=True)

# Panel de filtros superior
with st.container():
    c_flt1, c_flt2, c_flt3, c_flt4 = st.columns([1.6, 1.2, 1.2, 0.9])
    with c_flt1:
        busqueda_txt = st.text_input("🔍 Búsqueda Booleana:", placeholder='Ej: "flota fantasma" OR sabotaje -incendio')
    with c_flt2:
        rango_tiempo = st.selectbox("📅 Ventana Temporal:", ["Últimas 24 Horas", "Últimas 48 Horas", "Última Semana", "Histórico Completo"], index=3)
    with c_flt3:
        canales_sel = st.multiselect("Filtrar por Canal:", sorted(list({a["canal"] for a in st.session_state.alertas_base})))
    with c_flt4:
        auto_traducir = st.checkbox("🌐 Traducir", value=False, help="Traduce automáticamente los titulares y despachos al español")

# Proceso de traducción bajo demanda
if auto_traducir:
    pendientes = [a for a in st.session_state.alertas_base if not a.get("traducido_ok")]
    if pendientes:
        with st.spinner(f"Traduciendo {len(pendientes)} alertas..."):
            tits = traducir_lote([a["titular_raw"] for a in pendientes])
            for a, t_trad in zip(pendientes, tits):
                a["titular_traducido"] = t_trad
                a["traducido_ok"] = True

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
        html_reporte += f"<div style='border-bottom:1px solid #ccc;padding:10px 0;'>"
        html_reporte += f"<h3>{html_module.escape(a['titular_traducido'] if auto_traducir else a['titular_raw'])}</h3>"
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
        tit = a['titular_traducido'] if auto_traducir else a['titular_raw']
        md_reporte += f"### {tit}\n- **Fuente:** {a['canal']}\n- **Contexto:** {a['evaluacion_contexto']}\n- **Enlace:** {a['enlace']}\n\n"

    st.download_button(
        f"📝 INFORME MD ({len(alertas_informe)})",
        data=md_reporte.encode('utf-8'),
        file_name=f"Informe_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
        use_container_width=True
    )

with c_btn4:
    st.markdown(f'<div style="font-size:12px;color:#94a3b8;margin-top:6px;">🕒 Sincronizado: {st.session_state.ultima_actualizacion}<br>Mostrando <strong>{len(alertas_visibles)}</strong> alertas.</div>', unsafe_allow_html=True)

# Diagnóstico de fuentes visible
if st.session_state.errores_fuentes:
    with st.expander(f"⚠️ Diagnóstico de Conexión ({len(st.session_state.errores_fuentes)} incidencias)"):
        for err in sorted(set(st.session_state.errores_fuentes)):
            st.caption(f"• {err}")

# Control de selección
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
# 11. RENDERIZADO POR PESTAÑAS
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros.")
        return

    for item in lista_items:
        cat = item.get("categoria", "GLOBAL")
        b_color = {
            "ESPANA": "#dc2626", "EMPRESA": "#d97706", "GLOBAL": "#0f172a",
            "CRIMENES": "#991b1b", "CRIPTO": "#7c3aed", "SANCIONES": "#b45309",
            "DETENCION": "#be123c", "CHINA": "#1e3a8a", "TELEGRAM": "#0284c7", "REDES": "#059669"
        }.get(cat, "#0f172a")

        titular_mostrar = item["titular_traducido"] if auto_traducir else item["titular_raw"]
        enlace_seguro = url_sanitizada(item.get("enlace", ""))
        fecha_str = obtener_fecha_madrid_str(item["fecha_dt"]) if item["fecha_dt"] else "Fecha no disponible"

        html_card = f"""
        <div style="background:#ffffff; border:1px solid #cbd5e1; border-left:7px solid {b_color}; border-radius:8px; padding:14px 18px; margin-bottom:12px;">
            <div style="font-size:11.5px; color:#64748b; margin-bottom:6px;">📅 {fecha_str} | 📢 {html_module.escape(item['canal'])}</div>
            <div style="font-size:16.5px; font-weight:800; color:#0f172a; margin-bottom:8px;">{html_module.escape(titular_mostrar)}</div>
            <div style="background:#f0f9ff; border-left:4px solid #0284c7; padding:8px 12px; color:#0c4a6e; font-size:13px; margin-bottom:10px;">
                <strong>Contexto de la fuente:</strong> {html_module.escape(item['evaluacion_contexto'])}
            </div>
            <a href="{enlace_seguro}" target="_blank" rel="noopener noreferrer" style="background:#0f172a; color:#fff; padding:5px 12px; border-radius:4px; font-size:11.5px; font-weight:700; text-decoration:none;">🔗 ABRIR FUENTE ORIGINAL</a>
        </div>
        """

        col_chk, col_content = st.columns([0.05, 0.95])
        with col_chk:
            st.checkbox("Sel", key=f"sel_{item['id']}", label_visibility="collapsed")
        with col_content:
            st.markdown(html_card, unsafe_allow_html=True)
            with st.expander("📋 Desplegar alerta para copiar"):
                texto_copia = f"🚨 ALERTA OSINT | {cat}\nTitular: {titular_mostrar}\nFuente: {item['canal']} | Fecha: {fecha_str}\nEnlace: {item.get('enlace','')}"
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
