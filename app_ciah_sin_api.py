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
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse
import pandas as pd
import shlex

# ==========================================
# CONFIGURACIÓN GENERAL STREAMLIT
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

# ==========================================
# BLINDAJE VISUAL: OCULTAR GITHUB Y MANAGE APP
# ==========================================
st.markdown("""
<style>
    #MainMenu, header, footer { visibility: hidden !important; display: none !important; height: 0px !important; }
    a.header-anchor, a[data-testid="header-anchor"] { display: none !important; visibility: hidden !important; }
    [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"], [data-testid="manage-app-button"],
    button[kind="manage-app"], .manage-app, div[class*="manage-app"], .viewerBadge_container__1QSob, div[class*="viewerBadge"],
    div[class*="profileContainer"], a[href*="github.com"] {
        visibility: hidden !important; display: none !important; opacity: 0 !important; pointer-events: none !important;
    }
</style>
""", unsafe_allow_html=True)

components.html("""
<script>
    setInterval(() => {
        try {
            [window.document, window.parent.document, window.top.document].forEach(doc => {
                if (doc) {
                    doc.querySelectorAll('[data-testid="manage-app-button"], button[kind="manage-app"], div[class*="manage-app"], div[class*="profileContainer"], .viewerBadge_container__1QSob, header, footer').forEach(el => el.remove());
                }
            });
        } catch(e) {}
    }, 400);
</script>
""", height=0, width=0)

# ==========================================
# CONTROL DE ACCESO SEGURO (SIN BYPASS EN URL)
# ==========================================
CLAVE_SISTEMA = st.secrets.get("GAD_PASSWORD", "GAD2026")

def gestionar_acceso():
    if st.session_state.get("autenticado", False):
        return True
    
    st.markdown("""
        <div style="max-width: 470px; margin: 40px auto 20px auto; padding: 26px; background: #0f172a; border-radius: 12px; border-left: 6px solid #dc2626; color: white;">
            <h2 style="margin-top: 0; color: #ffffff; font-size: 21px;">🔒 Acceso Restringido</h2>
            <p style="color: #94a3b8; font-size: 13.5px; margin-bottom: 0;">Sistema de Vigilancia de Fuentes Abiertas. Introduzca la clave autorizada.</p>
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
# CLIENTE HTTP ROBUSTO Y CACHÉ THREAD-SAFE
# ==========================================
SESION_HTTP = requests.Session()
adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=1)
SESION_HTTP.mount("https://", adapter)
SESION_HTTP.mount("http://", adapter)
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,pl;q=0.6,zh;q=0.5,ar;q=0.5"
})

CACHE_TITULARES_GLOBAL = {}
CACHE_LOCK = threading.Lock()

if "errores_fuentes" not in st.session_state:
    st.session_state.errores_fuentes = []

# ==========================================
# MATRICES Y PATRONES OPERATIVOS
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

PATRON_CHINA = re.compile(
    r'\b(china|chino|chinos|chinas|pekin|beijing|xi jinping|pcc|pla|taiwan|taipei|canton|hong kong|'
    r'apt41|volt typhoon|flax typhoon|mustang panda|salt typhoon|mss|ciberespionaje chino|injerencia china|'
    r'china digital times|xinhua|cgtn)\b', re.IGNORECASE
)

RAICES_CHINO = ["中国", "北京", "习近平", "解放军", "台湾", "华为", "国安部", "间谍"]
RAICES_CIRILICO = ["росси", "рф", "москв", "кремл", "путин", "гру", "фсб", "свр", "украин", "киев", "зсу", "сбу", "вагнер", "шахед", "геран", "диверси"]
RAICES_ARABE = ["روسيا", "أوكرانيا", "الصين", "بوتين", "تجسس", "طائرات مسيرة"]
RAICES_POLACO = ["rosja", "rosyjski", "ukraina", "putin", "sabotaż", "szpiegostwo", "dywersja", "abw"]

# ==========================================
# SANITIZACIÓN Y NORMALIZACIÓN
# ==========================================
def limpiar_html_texto(texto):
    if not texto: return ""
    t = re.sub(r'<[^<]+?>', ' ', str(texto))
    t = html_module.unescape(t)
    t = re.sub(r'&[a-zA-Z0-9#]+;', ' ', t)
    t = re.sub(r'(?i)(suscr[ií]bete a nuestro bolet[ií]n|lee m[aá]s en|copyright|todos los derechos reservados|pincha aqu[ií]|sigue leyendo).*', '', t)
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
    return html_module.escape(url) if es_url_segura(url) else "about:blank"

def id_determinista(url):
    return hashlib.sha256(str(url).encode('utf-8')).hexdigest()[:16]

# ==========================================
# FILTROS DE SEGURIDAD Y ANTI-RUIDO RIGUROSOS
# ==========================================
def cumple_ventana_temporal(fecha_dt, horas):
    if not fecha_dt: return False
    if fecha_dt.tzinfo is None: fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    diferencia = datetime.now(timezone.utc) - fecha_dt.astimezone(timezone.utc)
    return timedelta(seconds=0) <= diferencia <= timedelta(hours=horas)

def es_publicidad_o_spam(texto):
    t = normalizar_texto(texto)
    spam = [
        "cambio de hora", "horario de verano", "horario de invierno", "reloj", "master profesional",
        "lisa institute", "udima", "beca", "matriculate", "curso online", "descuento", "sorteo", "casino",
        "oferta de empleo", "horoscopo", "loteria", "quiniela", "liga de futbol", "champions league", "apuestas",
        "receta de cocina", "supermercado", "rebajas"
    ]
    return any(s in t for s in spam)

def tiene_vinculo_geopolitico(texto):
    if not texto or es_publicidad_o_spam(texto): return False
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    if re.search(r'\b(papa|vaticano|santa sede|obispo|pontifice)\b', t_norm) and not re.search(r'\b(ciberataque|sabotaje|espionaje)\b', t_norm):
        return False

    if any(c in t_raw for c in RAICES_CIRILICO + RAICES_CHINO + RAICES_ARABE + RAICES_POLACO):
        return True

    actores = [
        "rusia", "ruso", "rusa", "rusos", "moscu", "kremlin", "putin", "ucrania", "ucraniano", "zelensky", "zsu", "sbu", "gur", "hur", "fsb", "gru", "svr",
        "china", "chino", "pekin", "beijing", "xi jinping", "taiwan", "mss", "otan", "nato", "dsn", "cni", "incibe", "europol", "eurojust",
        "stratcom", "euvsdisinfo", "bellingcat", "insider", "occrp", "meduza", "mediazona", "isw", "deepstate"
    ]
    return any(a in t_norm for a in actores)

def tiene_vector_amenaza_global(texto):
    t_norm = normalizar_texto(texto)
    t_raw = str(texto).lower()

    if any(emp in t_norm for emp in ["destinus", "uav navigation", "oesia", "tecnobit", "indra", "escribano"]):
        return True

    if any(c in t_raw for c in ["диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид", "бпла", "дрон", "рэб", "санкц", "флот"]):
        return True

    # Se eliminaron términos genéricos aislados como 'guerra', 'ataque' o 'militar' para cortar falsos positivos
    vectores = [
        "hibrid", "hybrid", "sabotaj", "sabotage", "sabotaż", "espia", "espionaje", "spy", "espionage",
        "ciberataque", "ciberespionaje", "ransomware", "ddos", "malware", "apt28", "apt29", "apt41", "volt typhoon", "sandworm", "noname057", "killnet",
        "desinformacion", "fimi", "propaganda pro-kremlin", "injerencia extranjera", "doppelganger", "red de bots",
        "criptoactivo", "bitcoin", "usdt", "garantex", "elusion de sanciones", "evasion de sanciones", "flota fantasma", "shadow fleet",
        "cable submarino", "crimen de guerra", "corte penal internacional", "genocidio", "ejecucion de prisioneros", "fosa comun",
        "detenido por espionaje", "arrestado por sabotaje", "detencion de espia", "dron kamikaze", "uav", "guerra electronica",
        "paquete incendiario", "sicario", "asesinato selectivo", "envenenamiento", "terrorismo de estado", "atentado terrorista"
    ]
    return any(v in t_norm for v in vectores)

# ==========================================
# TRADUCTOR THREAD-SAFE ULTRA RÁPIDO
# ==========================================
def traducir_texto_rapido(texto_orig):
    if not texto_orig: return ""
    t_clean = limpiar_html_texto(texto_orig)
    if len(t_clean) < 4: return t_clean

    with CACHE_LOCK:
        if t_clean in CACHE_TITULARES_GLOBAL:
            return CACHE_TITULARES_GLOBAL[t_clean]

    medio_suffix = ""
    texto_a_traducir = t_clean
    m_medio = re.search(r'(\s*[-–|]\s*[^-–|]{2,40})$', t_clean)
    if m_medio:
        medio_suffix = m_medio.group(1)
        texto_a_traducir = t_clean[:m_medio.start()].strip()

    endpoints = [
        {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:350]},
        {"client": "dict-chrome-ex", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:350]}
    ]

    for params in endpoints:
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
                            CACHE_TITULARES_GLOBAL[t_clean] = res
                        return res
        except Exception:
            continue

    with CACHE_LOCK:
        CACHE_TITULARES_GLOBAL[t_clean] = t_clean
    return t_clean

def traducir_lote(lista_textos):
    with ThreadPoolExecutor(max_workers=25) as ex:
        return list(ex.map(traducir_texto_rapido, lista_textos))

# ==========================================
# CLASIFICACIÓN Y NER
# ==========================================
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
        "SBU": r'\bsbu\b', "GUR / HUR": r'\b(gur|hur)\b', "DSN": r'\bdsn\b', "CNI": r'\bcni\b', "Europol": r'\beuropol\b',
        "NATO StratCom": r'\bstratcom\b', "Bellingcat": r'\bbellingcat\b', "OCCRP": r'\boccrp\b', "The Insider": r'\bthe insider\b'
    }
    vectores = {
        "Sabotaje": r'\b(sabotaj|sabotaż|cable submarino|red ferroviaria|incendio provocado|arson)\b',
        "Ciberataque": r'\b(ciberataque|ransomware|ddos|malware|infiltracion cibernetica)\b',
        "Desinformación / FIMI": r'\b(desinformacion|fimi|propaganda|fake news|red de bots|doppelganger)\b',
        "Flota Fantasma": r'\b(flota fantasma|shadow fleet|petrolero clandestino)\b',
        "Sanciones / Doble Uso": r'\b(elusion|evasion de sanciones|doble uso|chips militares|triangulacion|ofac|opensanctions)\b',
        "Sicariato / Muerte": r'\b(sicario|asesinato selectivo|homicidio|envenenamiento|defenestrado)\b',
        "Terrorismo": r'\b(terrorismo|atentado|paquete incendiario|explosivo)\b'
    }
    empresas = {
        "Destinus": r'\bdestinus\b', "Indra": r'\bindra\b', "Oesía / Tecnobit": r'\b(oesia|tecnobit)\b',
        "Navantia": r'\bnavantia\b', "Escribano": r'\bescribano\b', "GDELS / Santa Bárbara": r'\b(gdels|santa barbara)\b'
    }

    for n, p in actores.items():
        if re.search(p, t_norm): etiquetas.append((n, "#2563eb"))
    for n, p in vectores.items():
        if re.search(p, t_norm): etiquetas.append((n, "#dc2626"))
    for n, p in empresas.items():
        if re.search(p, t_norm): etiquetas.append((n, "#059669"))
    return etiquetas[:4]

def categorizar_noticia(titular, cuerpo, plat, canal):
    if plat == "REDES": return "REDES"
    if plat == "TELEGRAM": return "TELEGRAM"
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    
    if any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP):
        return "EMPRESA"
    if re.search(r'\b(detenido|detenida|detenidos|detencion|arrestado|arresto|arrested|zatrzymany)\b', t_all):
        return "DETENCION"
    if re.search(r'\b(cripto|bitcoin|usdt|tether|garantex|blockchain)\b', t_all):
        return "CRIPTO"
    if re.search(r'\b(sancion|sanciones|elusion|flota fantasma|shadow fleet|tope al crudo|price cap|ofac|opensanctions)\b', t_all):
        return "SANCIONES"
    if re.search(r'\b(espana|espanol|espanola|madrid|barcelona|cartagena|rota|torrejon|cni|dsn|incibe|margarita robles)\b', t_all):
        return "ESPANA"
    if PATRON_CHINA.search(t_all) or any(c in str(f"{titular} {cuerpo}").lower() for c in RAICES_CHINO):
        return "CHINA"
    if re.search(r'\b(crimen de guerra|cpi|icc|genocidio|tortura|masacre|bucha|mariupol|terrorismo|atentado|sicario)\b', t_all):
        return "CRIMENES"
    return "GLOBAL"

def generar_analisis_contexto(categoria, canal=""):
    c = canal.lower()
    if "rybar" in c or "zvinchuk" in c: return "Análisis táctico y propaganda militar de Mijaíl Zvinchuk (@rybar), exoficial del Ministerio de Defensa ruso."
    if "sharij" in c: return "Contenido de Anatolii Sharij: seguimiento crítico y difusión en redes del frente ucraniano-ruso."
    if c.replace("@","") in CANALES_DESINFORMACION_ESP: return "Canal clasificado en la red de propaganda, desinformación e injerencia extranjera (FIMI) vinculada al ecosistema pro-Kremlin."
    if any(k in c for k in ["bellingcat", "insider", "occrp"]): return "Investigación OSINT / Periodismo de investigación sobre inteligencia, operaciones encubiertas y redes criminales."
    if any(k in c for k in ["stratcom", "euvsdisinfo", "dfrlab"]): return "Vigilancia institucional de manipulación de información e injerencia por actores extranjeros (FIMI)."
    if categoria == "SANCIONES": return "Vigilancia de elusión de sanciones: flota fantasma, intermediarios, doble uso (OFAC / OpenSanctions / KSE)."
    if categoria == "CHINA": return "Actividad estratégica de la República Popular China: ciberespionaje (Volt Typhoon, APT41), medios estatales (Xinhua, CGTN) e injerencia."
    if categoria == "EMPRESA": return "Seguimiento corporativo: empresas estratégicas españolas frente a amenazas híbridas, ciberataques o contratos de defensa."
    if categoria == "CRIMENES": return "Documentación de crímenes de guerra, terrorismo no convencional, ataques a civiles y homicidios orquestados."
    if categoria == "DETENCION": return "Operaciones policiales y contrainteligencia: neutralización y arresto de espías y saboteadores."
    if categoria == "ESPANA": return "Impacto directo en la seguridad nacional e infraestructuras críticas de España (DSN / CNI / Ministerio de Defensa)."
    if categoria == "CRIPTO": return "Inteligencia financiera (FININT): evasión de sanciones internacionales y uso de criptoactivos (USDT, BTC, Garantex)."
    return "Actividad vinculada a operaciones híbridas globales, conflicto militar y servicios de inteligencia."

# ==========================================
# RASTREADORES PARALELOS CON DIAGNÓSTICO
# ==========================================
def raspar_youtube_canal(canal_id, etiqueta):
    items = []
    try:
        r = SESION_HTTP.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}", timeout=2.5)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:15]:
                tit = limpiar_html_texto(entry.findtext("{http://www.w3.org/2005/Atom}title", ""))
                link = entry.find("{http://www.w3.org/2005/Atom}link").get("href", "")
                pub = entry.findtext("{http://www.w3.org/2005/Atom}published", "")
                desc = limpiar_html_texto(entry.findtext(".//{http://search.yahoo.com/mrss/}description", ""))

                texto_eval = f"{tit} {desc}"
                if not tiene_vinculo_geopolitico(texto_eval) or not tiene_vector_amenaza_global(texto_eval):
                    continue

                try: dt_obj = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(timezone.utc)
                except: dt_obj = datetime.now(timezone.utc)

                items.append({
                    "id": f"YT-{id_determinista(link)}", "plataforma": "REDES", "canal": etiqueta, "fecha_dt": dt_obj,
                    "titular_raw": tit, "cuerpo_raw": desc[:800], "multimedia": None, "tiene_video": True, "enlace": link
                })
        else:
            st.session_state.errores_fuentes.append(f"{etiqueta} (HTTP {r.status_code})")
    except Exception as e:
        st.session_state.errores_fuentes.append(f"{etiqueta} ({str(e)[:30]})")
    return items

def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=2.5)
        if r.status_code != 200:
            st.session_state.errores_fuentes.append(f"Telegram @{canal} (HTTP {r.status_code})")
            return []
        soup = BeautifulSoup(r.text, "html.parser")

        for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-35:]:
            time_tag = p.find("time")
            try: dt_obj = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc) if time_tag else datetime.now(timezone.utc)
            except: dt_obj = datetime.now(timezone.utc)

            if not cumple_ventana_temporal(dt_obj, 168): continue

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
                "fecha_dt": dt_obj, "titular_raw": tit_corto, "cuerpo_raw": texto_sin_urls[:1500],
                "multimedia": None, "tiene_video": False, "enlace": link
            })
    except Exception as e:
        st.session_state.errores_fuentes.append(f"Telegram @{canal} ({str(e)[:30]})")
    return mensajes

def decodificar_url_google_news_offline(url_rss):
    if not url_rss or "news.google.com" not in url_rss:
        return url_rss
    try:
        m = re.search(r'articles/([A-Za-z0-9_-]+)', url_rss)
        if m:
            token = m.group(1)
            pad = token + "=" * ((4 - len(token) % 4) % 4)
            decoded_bytes = base64.urlsafe_b64decode(pad)
            for u in re.findall(rb'https?://[^\s"\'<>\\\x00-\x1f\x7f-\xff]+', decoded_bytes):
                u_str = u.decode('utf-8', errors='ignore')
                if "google.com" not in u_str and len(u_str) > 16:
                    return u_str
    except: pass
    return url_rss

def procesar_un_feed_rss(datos_feed):
    url, etiqueta = datos_feed
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=2.5)
        if r.status_code != 200:
            st.session_state.errores_fuentes.append(f"{etiqueta} (HTTP {r.status_code})")
            return []
        root = ET.fromstring(r.content)
        for item in root.findall(".//item")[:30]:
            tit = limpiar_html_texto(item.findtext("title", ""))
            desc = item.findtext("description", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")

            cuerpo_crudo = limpiar_html_texto(desc)[:1000]
            texto_eval = f"{tit} {cuerpo_crudo}"

            if not tiene_vinculo_geopolitico(texto_eval) or not tiene_vector_amenaza_global(texto_eval):
                continue

            try: dt_obj = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except: dt_obj = datetime.now(timezone.utc)

            url_destino = decodificar_url_google_news_offline(link) if "news.google.com" in link else link

            items.append({
                "id": f"RSS-{id_determinista(url_destino)}", "plataforma": "PRENSA", "canal": etiqueta,
                "fecha_dt": dt_obj, "titular_raw": limpiar_firma_medio(tit), "cuerpo_raw": cuerpo_crudo,
                "multimedia": None, "tiene_video": False, "enlace": url_destino
            })
    except Exception as e:
        st.session_state.errores_fuentes.append(f"{etiqueta} ({str(e)[:30]})")
    return items

def raspar_externos(url, etiqueta, limite=25):
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=2.5)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:limite]:
                tit = limpiar_html_texto(item.findtext("title", ""))
                desc = limpiar_html_texto(item.findtext("description", ""))
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")

                texto_eval = f"{tit} {desc}"
                if not tiene_vinculo_geopolitico(texto_eval) or not tiene_vector_amenaza_global(texto_eval):
                    continue

                try: dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except: dt = datetime.now(timezone.utc)

                items.append({
                    "id": f"EXT-{id_determinista(link)}", "plataforma": "INVESTIGACIÓN", "canal": etiqueta,
                    "fecha_dt": dt, "titular_raw": tit, "cuerpo_raw": desc[:1000],
                    "multimedia": None, "tiene_video": False, "enlace": link
                })
        else:
            st.session_state.errores_fuentes.append(f"{etiqueta} (HTTP {r.status_code})")
    except Exception as e:
        st.session_state.errores_fuentes.append(f"{etiqueta} ({str(e)[:30]})")
    return items

# ==========================================
# DEDUPLICACIÓN CONTEXTUAL Y TEMPORAL
# ==========================================
def son_mismo_evento(item_a, item_b):
    # Condición 1: No juntar noticias si difieren en más de 36 horas
    dt_a = item_a["fecha_dt"]
    dt_b = item_b["fecha_dt"]
    if abs((dt_a - dt_b).total_seconds()) > (36 * 3600):
        return False

    # Condición 2: Alta similitud textual estricta
    t1 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', item_a["titular_raw"])))
    t2 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', item_b["titular_raw"])))
    if len(t1) < 20 or len(t2) < 20:
        return False
    return SequenceMatcher(None, t1[:160], t2[:160]).ratio() > 0.84

def ejecutar_actualizacion_total():
    st.session_state.errores_fuentes = []

    canales_yt = [
        ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
        ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
        ("UC6n-ikz_s3zP9v9H6a_3tKg", "YouTube: CSIS Strategic Studies"),
        ("UCvps_Xgqj0i2v4Puh2p-E2w", "YouTube: Kyiv Independent"),
        ("UC5q_h_4qjF04bJkU-Fq0e5A", "YouTube: BBC News Russian"),
        ("UCN3h_V5k0Z4P9WvE9j_Yg3A", "YouTube: DW News"),
        ("UC7_2eFfP_n5v3H_Z_L-v_4w", "YouTube: Bellingcat OSINT")
    ]

    canales_tg = [
        "rybar", "ASupersharij", "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "GeneralStaffZSU", "kpszsu", "operativnoZSU", "CenterCounteringDisinformation",
        "meduzalive", "astrapress", "istories_media", "vchkogpu", "mediazzzona", "bbcrussian",
        "novayagazeta_europe", "theinsider", "kyivindependent_official", "pravda_com_ua",
        "ukrainenowenglish", "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu"
    ]

    fuentes_rss = [
        ("https://news.google.com/rss/search?q=(DSN+OR+\"Seguridad+Nacional\"+OR+\"FIMI\"+OR+\"injerencia+extranjera\")+España&hl=es&gl=ES&ceid=ES:es", "DSN / Seguridad Nacional (ES)"),
        ("https://news.google.com/rss/search?q=(\"Consilium\"+OR+\"Consejo+de+la+UE\")+(\"amenazas+híbridas\"+OR+\"sanciones\"+OR+\"sabotaje\")&hl=es&gl=ES&ceid=ES:es", "UE Consilium (Híbridas)"),
        ("https://news.google.com/rss/search?q=(\"NATO+StratCom\"+OR+\"StratCom+COE\")+disinformation&hl=en-US&gl=US&ceid=US:en", "NATO StratCom COE"),
        ("https://news.google.com/rss/search?q=Europol+(\"hybrid+threats\"+OR+\"organized+crime\"+OR+\"sabotage\"+OR+\"arrested\")&hl=en-US&gl=US&ceid=US:en", "Europol Press & SOCTA"),
        ("https://news.google.com/rss/search?q=Eurojust+(\"cross-border\"+OR+\"investigation\"+OR+\"sanctions\")&hl=en-US&gl=US&ceid=US:en", "Eurojust Judicial Ops"),
        ("https://euvsdisinfo.eu/feed/", "EUvsDisinfo"),
        ("https://www.disinfo.eu/feed/", "EU DisinfoLab"),
        ("https://citizenlab.ca/feed/", "Citizen Lab Tech"),
        ("https://chinadigitaltimes.net/feed/", "China Digital Times"),
        ("https://news.google.com/rss/search?q=(China+OR+Pekín+OR+Xi+Jinping+OR+APT41+OR+\"Volt+Typhoon\")+(espionaje+OR+ciberataque+OR+injerencia)&hl=es&gl=ES&ceid=ES:es", "China Injerencia (ES)"),
        ("https://news.google.com/rss/search?q=(\"empresas+amenazadas+por+rusia\"+OR+(Tecnobit+OR+Oesia+OR+Destinus+OR+Indra+OR+Navantia+OR+Escribano)+AND+(Rusia+OR+Kremlin))&hl=es&gl=ES&ceid=ES:es", "Empresas Amenazadas (ES)"),
        ("https://news.google.com/rss/search?q=(Destinus+OR+Kokorich)+(Rusia+OR+Ucrania+OR+drones+OR+frente)&hl=es&gl=ES&ceid=ES:es", "Destinus / Drones (ES)"),
        ("https://www.bellingcat.com/feed/", "Bellingcat OSINT"),
        ("https://theins.ru/feed", "The Insider (RU/EN)"),
        ("https://www.occrp.org/en/feed", "OCCRP Investigations"),
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado+OR+detencion)+(espia+ruso+OR+espia+chino+OR+sabotaje)&hl=es&gl=ES&ceid=ES:es", "Detenciones (ES)"),
        ("https://news.google.com/rss/search?q=(Polska+OR+Poland)+(sabotaż+OR+szpiegostwo+OR+dywersja+OR+ABW+OR+Rosja)&hl=pl&gl=PL&ceid=PL:pl", "Polonia / ABW (PL)"),
        ("https://news.google.com/rss/search?q=(OFAC+OR+\"OpenSanctions\"+OR+\"KSE+Institute\"+OR+\"CREA\")+(Russia+OR+sanctions)&hl=en-US&gl=US&ceid=US:en", "OFAC / Sanciones Tracker"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+China+OR+Ucrania)+(\"elusion+de+sanciones\"+OR+\"flota+fantasma\"+OR+\"shadow+fleet\"+OR+\"doble+uso\")&hl=es&gl=ES&ceid=ES:es", "Sanciones / Elusión (ES)"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT+OR+Tether)+(Russia+OR+China+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)"),
        ("https://news.google.com/rss/search?q=(\"Institute+for+the+Study+of+War\"+OR+\"ISW\")+Russian+offensive&hl=en-US&gl=US&ceid=US:en", "ISW War Studies"),
        ("https://kyivindependent.com/feed/", "The Kyiv Independent"),
        ("https://www.pravda.com.ua/rss/", "Ukrainska Pravda"),
        ("https://meduza.io/rss/all", "Meduza (RU/EN)"),
        ("https://theobjective.com/feed/", "The Objective (General)"),
        ("https://news.google.com/rss/search?q=Россия+диверсия+OR+теракт+OR+шпионаж+OR+СБУ&hl=ru&gl=RU&ceid=RU:ru", "Rusia / Ecosistema RU"),
        ("https://news.google.com/rss/search?q=Україна+диверсія+OR+агент+OR+обстріл+OR+ГУР&hl=uk&gl=UA&ceid=UA:uk", "Ucrania / Frente UA"),
        ("https://news.google.com/rss/search?q=(روسيا+OR+أوكرانيا+OR+الصين)+(تجسس+OR+طائرات+مسيرة)&hl=ar&gl=EG&ceid=EG:ar", "Mundo Árabe / Intel (AR)")
    ]

    todos_brutos = []
    with ThreadPoolExecutor(max_workers=32) as ex:
        f_yt = [ex.submit(raspar_youtube_canal, cid, tag) for cid, tag in canales_yt]
        f_tg = [ex.submit(raspar_un_canal_tg, c) for c in canales_tg]
        f_rss = [ex.submit(procesar_un_feed_rss, f) for f in fuentes_rss]
        f_ext = [
            ex.submit(raspar_externos, "https://www.rumafia.net/en/rss/", "RuMafia"),
            ex.submit(raspar_externos, "https://antikor.com.ua/articles.rss", "Antikor")
        ]

        for f in as_completed(f_yt + f_tg + f_rss + f_ext):
            try: todos_brutos.extend(f.result())
            except: pass

    anteriores = st.session_state.get("alertas_monitor", [])
    for ant in anteriores:
        todos_brutos.append({
            "id": ant["id"], "plataforma": ant["plataforma"], "canal": ant["canal"], "fecha_dt": ant["fecha_dt"],
            "titular_raw": ant.get("titular_original", ant["titular"]), "cuerpo_raw": ant.get("cuerpo_original", ant["hecho_reportado"]),
            "multimedia": ant.get("multimedia"), "tiene_video": ant.get("tiene_video", False), "enlace": ant["enlace"],
            "canales_correlacionados": ant.get("canales_correlacionados", [ant["canal"]]),
            "fuentes_coincidentes": ant.get("fuentes_coincidentes", [])
        })

    todos_brutos.sort(key=lambda x: x["fecha_dt"], reverse=True)

    pre_unicos = []
    urls_vistas = set()
    for b in todos_brutos:
        u = limpiar_url(b["enlace"])
        if u in urls_vistas: continue

        coincidente = next((ex for ex in pre_unicos if son_mismo_evento(b, ex)), None)
        if coincidente:
            if b["canal"] not in coincidente.get("canales_correlacionados", []):
                coincidente.setdefault("canales_correlacionados", []).append(b["canal"])
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

    # Traducción en masa optimizada
    titulares_crudos = [item["titular_raw"] for item in pre_unicos]
    titulares_traducidos = traducir_lote(titulares_crudos)

    alertas_finales = []
    for item, tit_trad in zip(pre_unicos, titulares_traducidos):
        plat = item["plataforma"]
        tit_raw = item["titular_raw"]
        cuerpo_raw = item["cuerpo_raw"]

        cat_final = categorizar_noticia(tit_trad, cuerpo_raw, plat, item["canal"])

        alertas_finales.append({
            "id": item["id"], "plataforma": plat, "canal": item["canal"],
            "canales_correlacionados": item.get("canales_correlacionados", [item["canal"]]),
            "fuentes_coincidentes": item.get("fuentes_coincidentes", []), "fecha_dt": item["fecha_dt"],
            "titular_traducido": f"[{item['canal'].replace('@','').upper()}] {tit_trad}" if plat == "TELEGRAM" else tit_trad,
            "titular_original": f"[{item['canal'].replace('@','').upper()}] {tit_raw}" if plat == "TELEGRAM" else tit_raw,
            "cuerpo_original": cuerpo_raw,
            "titular": tit_trad,
            "hecho_reportado": cuerpo_raw,
            "categoria": cat_final,
            "etiquetas_ner": extraer_entidades_clave(f"{tit_trad} {cuerpo_raw}"),
            "evaluacion_contexto": generar_analisis_contexto(cat_final, canal=item["canal"]),
            "multimedia": item.get("multimedia"), "tiene_video": item.get("tiene_video", False), "enlace": item["enlace"]
        })

    st.session_state.alertas_monitor = sorted(alertas_finales, key=lambda x: x["fecha_dt"], reverse=True)
    st.session_state.ultima_actualizacion = obtener_fecha_madrid_str()
    st.session_state.seleccionadas_pdf = {a["id"] for a in alertas_finales}
    for a in alertas_finales: st.session_state[f"sel_{a['id']}"] = True

# ==========================================
# MOTOR BOOLEANO AVANZADO (PARÉNTESIS Y COMILLAS)
# ==========================================
def cumple_busqueda_booleana_avanzada(item, query_str):
    if not query_str or not query_str.strip(): return True
    
    corpus = f"{item.get('titular_traducido','')} {item.get('hecho_reportado','')} {item.get('titular_original','')}"
    t_norm = normalizar_texto(corpus)
    q_raw = query_str.strip()

    # Extracción de frases exactas entre comillas
    frases_exactas = re.findall(r'"([^"]+)"', q_raw)
    for frase in frases_exactas:
        if normalizar_texto(frase) not in t_norm:
            return False
    
    q_sin_frases = re.sub(r'"[^"]+"', ' ', q_raw)

    # Identificación estricta del operador NOT (palabra NOT o '-' precedido de espacio)
    terminos_not = re.findall(r'(?:NOT\s+|(?<=\s)-)(\w+)', q_sin_frases, flags=re.IGNORECASE)
    for t_not in terminos_not:
        if normalizar_texto(t_not) in t_norm:
            return False

    q_limpia = re.sub(r'(?:NOT\s+|(?<=\s)-)\w+', ' ', q_sin_frases, flags=re.IGNORECASE).strip()
    if not q_limpia:
        return True

    # Evaluación de bloques OR / AND
    bloques_or = [b.strip() for b in re.split(r'\bOR\b', q_limpia, flags=re.IGNORECASE) if b.strip()]
    for bloque in bloques_or:
        terminos_and = [t.strip() for t in re.split(r'\bAND\b|\s+', bloque, flags=re.IGNORECASE) if t.strip()]
        if all(normalizar_texto(t) in t_norm for t in terminos_and):
            return True

    return False

# ==========================================
# EXPORTACIONES E INFORMES
# ==========================================
def exportar_alertas_csv(alertas):
    if not alertas: return b""
    filas = [{
        "Fecha (Madrid)": obtener_fecha_madrid_str(a["fecha_dt"]),
        "Categoría": a["categoria"],
        "Fuente": a["canal"],
        "Titular": a["titular"],
        "Enlace": a["enlace"]
    } for a in alertas]
    return pd.DataFrame(filas).to_csv(index=False).encode('utf-8-sig')

def generar_informe_markdown(alertas):
    fecha_actual = obtener_fecha_madrid_str()
    md = f"# INFORME DE INTELIGENCIA // BUSCADOR DE AMENAZAS HÍBRIDAS\n**Emisión:** {fecha_actual} (Madrid)\n\n---\n\n"
    for cat in ["ESPANA", "EMPRESA", "GLOBAL", "CRIMENES", "CRIPTO", "SANCIONES", "DETENCION", "CHINA", "TELEGRAM", "REDES"]:
        items = [a for a in alertas if a["categoria"] == cat]
        if not items: continue
        md += f"## {cat} ({len(items)})\n\n"
        for a in items:
            md += f"### {a['titular']}\n- **Fuente:** {a['canal']} | **Fecha:** {obtener_fecha_madrid_str(a['fecha_dt'])}\n- **Evaluación:** {a['evaluacion_contexto']}\n- **Enlace:** {a['enlace']}\n\n"
        md += "---\n\n"
    return md.encode('utf-8')

def generar_html_ejecutivo(alertas, ventana_str="GENERAL"):
    fecha_actual = obtener_fecha_madrid_str()
    html = f"""<!DOCTYPE html><html lang='es'><head><meta charset='UTF-8'><title>Informe de Inteligencia</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 25px; color: #0f172a; line-height: 1.4; }}
        h1 {{ color: #0f172a; font-size: 20px; border-bottom: 2px solid #dc2626; padding-bottom: 8px; }}
        h2 {{ background: #0f172a; color: #fff; padding: 6px 12px; font-size: 13px; margin-top: 20px; }}
        .item {{ border: 1px solid #cbd5e1; border-left: 5px solid #0284c7; padding: 12px; margin-bottom: 10px; border-radius: 4px; }}
        .meta {{ font-size: 11px; color: #64748b; margin-bottom: 4px; font-weight: bold; }}
        .analisis {{ background: #f0f9ff; padding: 6px 10px; font-size: 11.5px; color: #0369a1; margin-top: 6px; }}
        a {{ color: #0284c7; text-decoration: none; font-size: 11.5px; font-weight: bold; }}
    </style></head><body>
    <h1>BUSCADOR DE AMENAZAS HÍBRIDAS // INFORME DE SITUACIÓN</h1>
    <p>Emisión: {fecha_actual} (Madrid) | Ventana: {ventana_str}</p>"""

    for cat in ["ESPANA", "EMPRESA", "GLOBAL", "CRIMENES", "CRIPTO", "SANCIONES", "DETENCION", "CHINA", "TELEGRAM", "REDES"]:
        items = [a for a in alertas if a["categoria"] == cat]
        if not items: continue
        html += f"<h2>{cat} ({len(items)})</h2>"
        for a in items:
            enlace_seguro = url_sanitizada(a.get("enlace", ""))
            html += f"""<div class='item'>
                <div class='meta'>📢 {html_module.escape(a['canal'])} | 📅 {obtener_fecha_madrid_str(a['fecha_dt'])}</div>
                <div style='font-size: 14px; font-weight: bold;'>{html_module.escape(a['titular'])}</div>
                <div class='analisis'>🧠 {html_module.escape(a['evaluacion_contexto'])}</div>
                <div style='margin-top: 6px;'><a href='{enlace_seguro}' target='_blank' rel='noopener noreferrer'>🔗 Abrir enlace contrastado</a></div>
            </div>"""
    html += "</body></html>"
    return html

# ==========================================
# INTERFAZ Y ESTILOS
# ==========================================
st.markdown("""
<style>
    .main { background-color: #0b0f19; } .block-container { padding-top: 1.2rem !important; }
    .tactical-header { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); border-left: 8px solid #dc2626; border-radius: 12px; padding: 18px 24px; margin-bottom: 16px; display: flex; align-items: center; gap: 20px; }
    .header-titles h1 { color: #ffffff; margin: 0; font-size: 24px !important; font-weight: 900; }
    .header-titles p { color: #94a3b8; font-size: 13.5px !important; margin: 4px 0 0 0; }
    div[data-baseweb="tab-list"] { display: flex !important; flex-wrap: wrap !important; gap: 6px !important; background: #0f172a !important; padding: 8px 10px !important; border-radius: 10px !important; }
    button[data-baseweb="tab"] { flex: 1 1 auto !important; height: 38px !important; font-size: 11.5px !important; font-weight: 800 !important; padding: 6px 12px !important; border-radius: 6px !important; color: #ffffff !important; border: 1px solid rgba(255,255,255,0.1) !important; }
    button[data-baseweb="tab"][aria-selected="true"] { border: 2px solid #38bdf8 !important; transform: scale(1.02) !important; }
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
    div[data-baseweb="tab-highlight"] { display: none !important; }
    .osint-card { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px 20px; margin-bottom: 14px; }
    .badge-item { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 4px; }
    .ner-pill { font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px; color: #fff; margin-right: 5px; }
    .btn-source { background-color: #0f172a; color: #ffffff !important; padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: 700; text-decoration: none !important; }
</style>
""", unsafe_allow_html=True)

logo_svg = """<svg width="70" height="70" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="50" cy="50" r="47" fill="#1e293b" stroke="#334155" stroke-width="2" />
    <circle cx="50" cy="50" r="40" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.6" />
    <path d="M50 14 L75 25 V52 C75 69 50 84 50 84 C50 84 25 69 25 52 V25 Z" fill="#0f172a" stroke="#38bdf8" stroke-width="2.5" />
    <polygon points="53,24 37,48 48,48 43,72 61,44 51,44" fill="#ef4444" />
</svg>"""

st.markdown(f'<div class="tactical-header"><div>{logo_svg}</div><div class="header-titles"><h1>BUSCADOR DE AMENAZAS HÍBRIDAS</h1><p>SISTEMA DE VIGILANCIA DE FUENTES ABIERTAS</p></div></div>', unsafe_allow_html=True)

if "arranque_inicial_ok" not in st.session_state:
    st.session_state.check_traducir = True
    with st.spinner("Sincronizando fuentes abiertas..."):
        ejecutar_actualizacion_total()
    st.session_state.arranque_inicial_ok = True
    st.rerun()

alertas_totales = st.session_state.get("alertas_monitor", [])

# ==========================================
# PANEL SUPERIOR DE FILTROS Y CONTROL
# ==========================================
with st.container():
    c_flt1, c_flt2, c_flt3, c_flt4 = st.columns([1.5, 1.2, 1.3, 1.0])
    with c_flt1:
        busqueda_txt = st.text_input("🔍 Búsqueda Booleana (comillas, NOT, OR):", placeholder='Ej: "flota fantasma" OR sabotaje -incendio')
    with c_flt2:
        rango_tiempo = st.selectbox("📅 Ventana Temporal:", ["Últimas 24 Horas", "Últimas 48 Horas", "Última Semana", "Histórico Completo"], index=2)
    with c_flt3:
        canales_sel = st.multiselect("Filtrar por Canal:", sorted(list({a["canal"] for a in alertas_totales})))
    with c_flt4:
        auto_traducir = st.checkbox("🌐 Auto-Traducir", value=st.session_state.get("check_traducir", True), key="check_traducir")

# Aplicación estricta de ventana temporal
if rango_tiempo == "Últimas 24 Horas": datos_filtrados = [x for x in alertas_totales if cumple_ventana_temporal(x["fecha_dt"], 24)]
elif rango_tiempo == "Últimas 48 Horas": datos_filtrados = [x for x in alertas_totales if cumple_ventana_temporal(x["fecha_dt"], 48)]
elif rango_tiempo == "Última Semana": datos_filtrados = [x for x in alertas_totales if cumple_ventana_temporal(x["fecha_dt"], 168)]
else: datos_filtrados = alertas_totales

if busqueda_txt: datos_filtrados = [x for x in datos_filtrados if cumple_busqueda_booleana_avanzada(x, busqueda_txt)]
if canales_sel: datos_filtrados = [x for x in datos_filtrados if x["canal"] in canales_sel]

# Barra de Herramientas
c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([1.1, 1.2, 1.2, 1.5])
with c_btn1:
    if st.button("🔄 ACTUALIZAR FUENTES", type="primary", use_container_width=True):
        with st.spinner("Sincronizando fuentes..."):
            ejecutar_actualizacion_total()
        st.rerun()

alertas_para_informe = [x for x in datos_filtrados if st.session_state.get(f"sel_{x['id']}", False)]

with c_btn2:
    st.download_button(
        f"📥 INFORME IMPRIMIBLE ({len(alertas_para_informe)})",
        data=generar_html_ejecutivo(alertas_para_informe, ventana_str=rango_tiempo),
        file_name=f"Informe_Amenazas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.html",
        mime="text/html",
        use_container_width=True
    )

with c_btn3:
    st.download_button(
        f"📝 INFORME WORD/MD ({len(alertas_para_informe)})",
        data=generar_informe_markdown(alertas_para_informe),
        file_name=f"Informe_Amenazas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.md",
        mime="text/markdown",
        use_container_width=True
    )

with c_btn4:
    if "ultima_actualizacion" in st.session_state:
        st.markdown(f'<div style="font-size:12px;color:#94a3b8;margin-top:6px;">🕒 <strong>Actualizado:</strong> {st.session_state.ultima_actualizacion}<br>Mostrando <strong>{len(datos_filtrados)}</strong> alertas activas.</div>', unsafe_allow_html=True)

# Visor de Diagnóstico de Fuentes Caídas
if st.session_state.errores_fuentes:
    with st.expander(f"⚠️ Diagnóstico de Conexión: {len(st.session_state.errores_fuentes)} fuentes no respondieron"):
        for err in set(st.session_state.errores_fuentes):
            st.caption(f"• {err}")

st.write("")

# ==========================================
# RENDERIZADO DE ALERTAS
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros.")
        return

    for item in lista_items:
        cat = item.get("categoria", "GLOBAL")
        b_color = {"ESPANA":"#dc2626", "EMPRESA":"#d97706", "GLOBAL":"#0f172a", "CRIMENES":"#991b1b", "CRIPTO":"#7c3aed", "SANCIONES":"#b45309", "DETENCION":"#be123c", "CHINA":"#1e3a8a", "TELEGRAM":"#0284c7", "REDES":"#059669"}.get(cat, "#0f172a")

        badges = ""
        if cumple_ventana_temporal(item["fecha_dt"], 24): badges += '<span class="badge-item" style="background:#fee2e2;color:#991b1b;">⚡ 24H</span> '
        elif cumple_ventana_temporal(item["fecha_dt"], 48): badges += '<span class="badge-item" style="background:#fef08a;color:#854d0e;">🔥 48H</span> '

        pildoras_ner = "".join([f'<span class="ner-pill" style="background-color:{c};">{html_module.escape(n)}</span>' for n, c in item.get("etiquetas_ner", [])])

        enlace_seguro = url_sanitizada(item.get("enlace", ""))
        titular_mostrar = item["titular_traducido"] if auto_traducir else item["titular_original"]
        item["titular"] = titular_mostrar

        texto_copia = f"🚨 ALERTA OSINT | {cat}\nTitular: {item['titular_traducido']}\nFuente: {item['canal']} | Fecha: {obtener_fecha_madrid_str(item['fecha_dt'])} (Madrid)\nEnlace: {item.get('enlace','')}"

        html_card = f"""
        <div class="osint-card" style="border-left: 8px solid {b_color};">
            <div style="font-size:12px;color:#64748b;margin-bottom:8px;">{badges} 📅 {obtener_fecha_madrid_str(item['fecha_dt'])} | 📢 {html_module.escape(item['canal'])}</div>
            <div style="font-size:17.5px;font-weight:850;color:#0f172a;margin-bottom:8px;">{html_module.escape(titular_mostrar)}</div>
            <div style="margin-bottom:8px;">{pildoras_ner}</div>
            <div style="background:#f0f9ff;border-left:4px solid #0284c7;padding:8px 12px;color:#0c4a6e;font-size:13.5px;margin-bottom:10px;"><strong>🧠 Análisis:</strong> {html_module.escape(item['evaluacion_contexto'])}</div>
            <a href="{enlace_seguro}" target="_blank" rel="noopener noreferrer" class="btn-source">🔗 ABRIR FUENTE ORIGINAL</a>
        </div>
        """

        c_chk, c_card = st.columns([0.05, 0.95])
        with c_chk:
            st.checkbox("PDF", key=f"sel_{item['id']}", label_visibility="collapsed")

        with c_card:
            st.markdown(html_card, unsafe_allow_html=True)
            with st.expander("📋 Formato de Alerta (Copiar)"):
                st.code(texto_copia, language="markdown")

            if item.get("fuentes_coincidentes"):
                with st.expander(f"📑 Coberturas coincidentes ({len(item['fuentes_coincidentes'])})"):
                    for f_sec in item["fuentes_coincidentes"]:
                        sec_link = url_sanitizada(f_sec.get('enlace', ''))
                        st.markdown(f"- **{html_module.escape(f_sec['canal'])}:** [{html_module.escape(f_sec['titular'])}]({sec_link})")

# ==========================================
# RENDERIZADO POR PESTAÑAS
# ==========================================
if "alertas_monitor" in st.session_state:
    l_esp = [x for x in datos_filtrados if x["categoria"] == "ESPANA"]
    l_emp = [x for x in datos_filtrados if x["categoria"] == "EMPRESA"]
    l_prensa = [x for x in datos_filtrados if x["categoria"] == "GLOBAL"]
    l_crim = [x for x in datos_filtrados if x["categoria"] == "CRIMENES"]
    l_cripto = [x for x in datos_filtrados if x["categoria"] == "CRIPTO"]
    l_sanc = [x for x in datos_filtrados if x["categoria"] == "SANCIONES"]
    l_det = [x for x in datos_filtrados if x["categoria"] == "DETENCION"]
    l_china = [x for x in datos_filtrados if x["categoria"] == "CHINA"]
    l_tg = [x for x in datos_filtrados if x["categoria"] == "TELEGRAM"]
    l_redes = [x for x in datos_filtrados if x["categoria"] == "REDES"]

    t1,t2,t3,t4,t5,t6,t7,t8,t9,t10 = st.tabs([
        f"🇪🇸 ESPAÑA ({len(l_esp)})", f"🏢 EMPRESAS ({len(l_emp)})", f"📰 GLOBAL ({len(l_prensa)})", f"⚖️ CRÍMENES ({len(l_crim)})",
        f"🟣 CRIPTO ({len(l_cripto)})", f"📦 SANCIONES ({len(l_sanc)})", f"🛑 DETENCIONES ({len(l_det)})", f"🇨🇳 CHINA ({len(l_china)})",
        f"✈️ TELEGRAM ({len(l_tg)})", f"🌐 REDES ({len(l_redes)})"
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
