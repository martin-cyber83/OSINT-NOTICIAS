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
    page_title="Monitor Táctico de Amenazas Híbridas",
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
# CONTROL DE ACCESO ROBUSTO
# ==========================================
CLAVE_SISTEMA = st.secrets.get("AUTH_KEY", os.environ.get("GAD_AUTH_KEY", "GAD2026"))

def gestionar_acceso():
    if st.session_state.get("autenticado", False):
        return True

    st.markdown("""
        <div style="max-width: 480px; margin: 50px auto 20px auto; padding: 26px; background: #0f172a; border-radius: 12px; border-left: 6px solid #dc2626; color: white; box-shadow: 0 16px 36px rgba(0,0,0,0.6);">
            <h2 style="margin-top: 0; color: #ffffff; font-size: 20px; letter-spacing: 0.5px;">🔒 Acceso Restringido // Monitor OSINT</h2>
            <p style="color: #94a3b8; font-size: 13px;">Sistema de Vigilancia de Amenazas Híbridas e Injerencia. Introduzca la credencial para autorizar la sesión.</p>
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
# BLINDAJE VISUAL STREAMLIT
# ==========================================
st.markdown("""
<style>
    #MainMenu, header, footer { visibility: hidden !important; display: none !important; }
    a.header-anchor, a[data-testid="header-anchor"] { display: none !important; }
    [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
    [data-testid="manage-app-button"], button[kind="manage-app"], .viewerBadge_container__1QSob {
        visibility: hidden !important; display: none !important; opacity: 0 !important;
    }
</style>
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
# REGLAS, ENTIDADES Y VECTORES DE INTELIGENCIA
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

PATRON_RUSIA_ESTRICTO = re.compile(
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

PATRON_CHINA_ESTRICTO = re.compile(
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

PATRON_PAISES_DESCARTE = re.compile(
    r'\b('
    r'cuba|cubano|cubana|venezuela|venezolano|maduro|caracas|'
    r'nicaragua|colombia|petro|gaza|hamas|hezbollah|hezbola|'
    r'netanyahu|tel aviv|cisjordania|palestina|palestino|israel|israeli|'
    r'yemen|huti|huties'
    r')\b', re.IGNORECASE
)

PATRON_VECTORES_AMENAZA = re.compile(
    r'\b('
    r'sabotaj|sabotage|saboteador|arson|incendio provocado|cable submarino|undersea cable|red ferroviaria|'
    r'desinformacion|disinformation|fimi|propaganda|guerra cognitiva|red de bots|fake news|troll|'
    r'espia|espionaje|spy|espionage|agente encubierto|infiltracion|'
    r'ciber|cyber|hack|ddos|ransomware|malware|injerencia|interferencia|'
    r'sancion|sanciones|sanctions?|elusion|evasion de sanciones|flota fantasma|shadow fleet|petrolero clandestino|'
    r'doble uso|dual-use|contrabando de chips|triangulacion|garantex|'
    r'sicariato|sicario|asesinato selectivo|targeted killing|envenenamiento|defenestrado|homicidio|muerte sospechosa|'
    r'crimen organizado|crimen transnacional|mafia|bandas instrumentadas|'
    r'terrorismo|terrorista|atentado|paquete incendiario|explosivo|amenaza no convencional|'
    r'crimen de guerra|war crime|corte penal internacional|ejecucion de prisioneros|fosa comun|ataque a civiles'
    r')\b', re.IGNORECASE
)

# ==========================================
# UTILIDADES DE TEXTO Y LIMPIEZA
# ==========================================
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

def desparasitar_nombres_medios(texto):
    t = str(texto)
    patrones_medios = [
        r'marketscreener\s*espa[nñ]a', r'libertad\s*digital', r'el\s*confidencial',
        r'el\s*periodico', r'el\s*pais', r'el\s*mundo', r'la\s*razon', r'the\s*objective',
        r'europa\s*press', r'agencia\s*efe', r'rtve', r'responsible\s*statecraft'
    ]
    for p in patrones_medios:
        t = re.sub(p, ' ', t, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', t).strip()

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
# FILTROS DE INTELIGENCIA
# ==========================================
def es_publicidad_o_spam(texto):
    t = normalizar_texto(texto)
    spam = ["master profesional", "lisa institute", "udima", "beca", "matriculate", "curso online", "descuento", "sorteo", "casino", "apuestas", "tarot", "horoscopo", "oferta de empleo"]
    return any(s in t for s in spam)

def tiene_vinculo_geopolitico(texto):
    if not texto or es_publicidad_o_spam(texto):
        return False
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    if re.search(r'\b(papa|vaticano|santa sede|iglesia|obispo|pontifice|francisco)\b', t_norm) and not re.search(r'\b(ciberataque|sabotaje|espionaje|hack|ddos)\b', t_norm):
        return False

    if any(c in t_raw for c in RAICES_CIRILICO) or any(c in t_raw for c in RAICES_CHINO) or any(c in t_raw for c in RAICES_ARABE) or any(c in t_raw for c in RAICES_POLACO):
        return True

    if PATRON_CHINA_ESTRICTO.search(t_norm) or PATRON_RUSIA_ESTRICTO.search(t_norm):
        return True

    if PATRON_PAISES_DESCARTE.search(t_norm):
        if not re.search(r'\b(kremlin|putin|fuerzas armadas rusas|moscu|espionaje ruso|invasion rusa|shariy|sharij|rybar|destinus|beijing|xi jinping|mss)\b', t_norm):
            return False

    return False

def tiene_vector_amenaza_global(texto):
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    if any(emp in t_norm for emp in ["destinus", "uav navigation", "oesia", "tecnobit", "indra", "escribano"]):
        return True

    vectores_cirilico = [
        "диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид",
        "крипт", "бпла", "дрон", "рэб", "арест", "задержан", "санкц", "кабел", "флот",
        "казн", "пленн", "военн", "преступлен", "поджог", "теракт", "убийств", "ликвидац"
    ]
    if any(c in t_raw for c in vectores_cirilico):
        return True

    return bool(PATRON_VECTORES_AMENAZA.search(t_norm))

def es_empresa_esp(titular, cuerpo):
    t_all = normalizar_texto(desparasitar_nombres_medios(f"{titular} {cuerpo}"))
    menciona_empresa = any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP)
    menciona_defensa = bool(re.search(r'\b(empresa espanola|empresas espanolas|industria de defensa espanola)\b', t_all))
    
    if not (menciona_empresa or menciona_defensa):
        return False
        
    return bool(re.search(r'\b(rusia|ruso|rusa|moscu|kremlin|putin|ucrania|ciberataque|sabotaje|espionaje|amenaza)\b', t_all))

def es_china(titular, cuerpo):
    t_raw = str(f"{titular} {cuerpo}").lower()
    t_norm = normalizar_texto(f"{titular} {cuerpo}")
    return any(c in t_raw for c in RAICES_CHINO) or bool(PATRON_CHINA_ESTRICTO.search(t_norm))

def es_sancion(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(sancion|sanciones|sanctions?|elusion|evasion de sanciones|flota fantasma|shadow fleet|petrolero ruso|tope al crudo|price cap|contrabando de chips|doble uso|triangulacion|ofac)\b'
    return bool(re.search(patron, t_all))

def es_cripto(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    return bool(re.search(r'\b(cripto|criptomoneda|crypto|usdt|tether|btc|bitcoin|garantex|blockchain)\b', t_all))

def es_crimen_guerra_o_terror(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(crimen de guerra|war crime|cpi|icc|la haya|genocidio|ejecucion de prisioneros|tortura|masacre|bucha|mariupol|deportacion|ataque a civiles|hospital infantil|bloque de apartamentos|terrorismo|terrorista|atentado|paquete incendiario|asesinato selectivo|homicidio selectivo|sicariato|sicario)\b'
    return bool(re.search(patron, t_all))

def es_detencion(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(detenido|detenida|detenidos|detencion|arrestado|arrestada|arrestados|arresto|arrested|detained|desarticulad[oa]s?|prision preventiva|zatrzymany)\b'
    return bool(re.search(patron, t_all))

def es_espana(titular, cuerpo):
    t_norm = normalizar_texto(desparasitar_nombres_medios(f"{titular} {cuerpo}"))
    patron_esp = r'\b(espana|espanol|espanola|espanoles|spain|madrid|cartagena|rota|torrejon|cni|incibe|ccn-cert|audiencia nacional|guardia civil|policia nacional|ministerio de defensa|margarita robles|armada espanola|seguridad nacional)\b'
    return bool(re.search(patron_esp, t_norm))

def generar_analisis_contexto(categoria, canal=""):
    canal_clean = canal.replace("@", "").lower()
    if "rybar" in canal_clean or "zvinchuk" in canal_clean:
        return "Análisis táctico y propaganda militar de Mijaíl Zvinchuk (@rybar), exoficial del Ministerio de Defensa ruso."
    if "sharij" in canal_clean or "shariy" in canal_clean:
        return "Contenido de Anatolii Sharij: seguimiento y difusión crítica del conflicto ruso-ucraniano."
    if canal_clean in CANALES_DESINFORMACION_ESP:
        return "Canal catalogado dentro de la red de desinformación e injerencia extranjera (FIMI) vinculada al entorno pro-Kremlin."
    if categoria == "SANCIONES":
        return "Operaciones de elusión de sanciones: movimientos de la flota fantasma, tráfico de bienes de doble uso y triangulación comercial."
    if categoria == "CHINA":
        return "Actividad estratégica de la República Popular China: ciberespionaje (Volt Typhoon, Salt Typhoon, APT41), coerción y amenazas híbridas a Occidente."
    if categoria == "EMPRESA":
        return "Seguimiento corporativo: sector de defensa y empresas estratégicas españolas frente a amenazas hostiles y ciberataques."
    if categoria == "CRIMENES":
        return "Documentación de crímenes de guerra, terrorismo no convencional, sicariato orquestado y agresiones directas contra población e infraestructuras civiles."
    if categoria == "DETENCION":
        return "Contrainteligencia activa: detención y desarticulación de agentes, espías, enlaces logísticos y saboteadores a sueldo de servicios extranjeros."
    if categoria == "CRIPTO":
        return "Inteligencia financiera (FININT): transferencias en criptoactivos (USDT, BTC) para la financiación de sabotajes y evasión de bloqueos."
    if categoria == "ESPANA":
        return "Impacto directo en la seguridad nacional e infraestructuras críticas del Reino de España."
    return "Actividad vinculada a operaciones híbridas internacionales, crimen organizado e injerencia estatal."

# ==========================================
# NER LIGERO
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
        "Sicariato / Homicidio": r'\b(sicario|asesinato selectivo|homicidio|envenenamiento|defenestrado)\b',
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
# TRADUCTOR DIRECTO Y CACHEADO
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
    with ThreadPoolExecutor(max_workers=12) as ex:
        resultados = list(ex.map(traducir_texto, lista_textos))
    return resultados

def on_cambio_traduccion():
    activar = st.session_state.get("check_traducir", True)
    alertas = st.session_state.get("alertas_monitor", [])
    if not alertas:
        return
    if activar:
        textos_originales = [a.get("titular_original", a["titular"]) for a in alertas]
        traducidos = traducir_lote(textos_originales)
        for a, trad in zip(alertas, traducidos):
            a["titular"] = trad
    else:
        for a in alertas:
            a["titular"] = a.get("titular_original", a["titular"])

# ==========================================
# RASTREADORES FILTRADOS
# ==========================================
def decodificar_url_google_news(url_rss):
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
    except Exception:
        pass
    return url_rss

def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=3.5)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.find_all("div", class_="tgme_widget_message_wrap")

        es_red_social = canal in ["ASupersharij", "rybar"]

        for p in posts[-40:]:
            time_tag = p.find("time")
            dt_obj = datetime.now(timezone.utc)
            if time_tag and time_tag.get("datetime"):
                try:
                    dt_obj = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass

            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""

            if len(texto_raw) < 25 or es_publicidad_o_spam(texto_raw):
                continue

            texto_sin_urls = limpiar_html_texto(re.sub(r'http\S+', '', texto_raw))
            if len(texto_sin_urls) < 20:
                continue

            # Filtro de pertinencia
            if not (tiene_vinculo_geopolitico(texto_sin_urls) and tiene_vector_amenaza_global(texto_sin_urls)):
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
                nombre_canal_visual = "Mijaíl Zvinchuk (@rybar)"
            elif canal == "ASupersharij":
                nombre_canal_visual = "Telegram: Anatolii Sharij"
            else:
                nombre_canal_visual = f"@{canal}"

            mensajes.append({
                "id": generar_id_estable("TG", link),
                "plataforma": "REDES" if es_red_social else "TELEGRAM",
                "canal": nombre_canal_visual,
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

            if len(tit_orig) < 12 or es_publicidad_o_spam(tit_orig + " " + desc_orig):
                continue

            texto_completo = f"{tit_orig} {desc_orig}"

            # Filtro estricto: actor hostil + vector híbrido
            if not (tiene_vinculo_geopolitico(texto_completo) and tiene_vector_amenaza_global(texto_completo)):
                continue

            try:
                dt_obj = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception:
                dt_obj = datetime.now(timezone.utc)

            url_destino = decodificar_url_google_news(link)

            items.append({
                "id": generar_id_estable("RSS", link),
                "plataforma": "PRENSA",
                "canal": etiqueta,
                "fecha_dt": dt_obj,
                "titular_raw": tit_orig,
                "cuerpo_raw": desc_orig[:1800] if len(desc_orig) > 20 else tit_orig,
                "multimedia": None,
                "tiene_video": False,
                "enlace": url_destino
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

                if not (tiene_vinculo_geopolitico(tit_orig + " " + cuerpo) and tiene_vector_amenaza_global(tit_orig + " " + cuerpo)):
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

def raspar_rumafia_y_antikor():
    portales = [
        ("https://www.rumafia.net/en/rss/", "RuMafia.net", "RUMAFIA"),
        ("https://antikor.com.ua/articles.rss", "Antikor Portal", "ANTIKOR")
    ]
    items = []
    for url, etiqueta, prefijo in portales:
        try:
            r = SESION_HTTP.get(url, timeout=3.5)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:25]:
                    tit = limpiar_html_texto(item.findtext("title", ""))
                    desc = limpiar_html_texto(item.findtext("description", ""))
                    link = item.findtext("link", "")
                    pub = item.findtext("pubDate", "")

                    if not (tiene_vinculo_geopolitico(tit + " " + desc) and tiene_vector_amenaza_global(tit + " " + desc)):
                        continue

                    try:
                        dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                    except Exception:
                        dt = datetime.now(timezone.utc)

                    items.append({
                        "id": generar_id_estable(prefijo, link),
                        "plataforma": "INVESTIGACIÓN",
                        "canal": etiqueta,
                        "fecha_dt": dt,
                        "titular_raw": tit,
                        "cuerpo_raw": desc if len(desc) > 20 else tit,
                        "multimedia": None,
                        "tiene_video": False,
                        "enlace": link
                    })
        except Exception:
            pass
    return items

# ==========================================
# AGREGACIÓN Y DEDUPLICACIÓN
# ==========================================
def son_mismo_evento(tit1, tit2):
    t1 = re.sub(r'^(última hora|exclusiva|urgente|video)[:\-]?\s*', '', normalizar_texto(limpiar_firma_medio(tit1)))
    t2 = re.sub(r'^(última hora|exclusiva|urgente|video)[:\-]?\s*', '', normalizar_texto(limpiar_firma_medio(tit2)))
    if len(t1) < 16 or len(t2) < 16:
        return False
    # Comparación de longitud completa con umbral estricto
    return SequenceMatcher(None, t1, t2).ratio() > 0.82

def ejecutar_actualizacion_total(activar_traduccion=True):
    canales_tg = [
        "rybar", "ASupersharij", "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "entre_guerras", "dva_majors", "vchkogpu", "meduzalive", "astrapress", "istories_media",
        "ostorozhno_novosti", "nvua_official", "operativnoZSU", "musicarusaesp", "Irinamar_Z",
        "eurekapress", "elOJOen", "ophiucu"
    ]

    fuentes_rss = [
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin+OR+NoName057+OR+APT28)+(sabotaje+OR+injerencia+OR+espionaje+OR+desinformacion)+(España+OR+Madrid+OR+CNI)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(China+OR+Pekín+OR+Xi+Jinping+OR+APT41+OR+\"Volt+Typhoon\")+(espionaje+OR+sabotaje+OR+injerencia)+(España+OR+Occidente)&hl=es&gl=ES&ceid=ES:es", "China Injerencia (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+China)+(sabotage+OR+espionage+OR+\"shadow+fleet\"+OR+\"hybrid+threat\")&hl=en-US&gl=US&ceid=US:en", "Global Hybrid Watch (EN)"),
        ("https://news.google.com/rss/search?q=(Destinus+OR+Indra+OR+Navantia+OR+Oesia+OR+Tecnobit)+(Rusia+OR+drones+OR+ciberataque+OR+sabotaje)&hl=es&gl=ES&ceid=ES:es", "Defensa y Empresas (ES)"),
        ("https://news.google.com/rss/search?q=(\"elusion+de+sanciones\"+OR+\"flota+fantasma\"+OR+\"contrabando+de+chips\"+OR+\"doble+uso\")+(Rusia+OR+China)&hl=es&gl=ES&ceid=ES:es", "Sanciones / Evasión (ES)"),
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado)+(espia+ruso+OR+sabotaje+ruso+OR+injerencia+china)&hl=es&gl=ES&ceid=ES:es", "Detenciones / Contraespionaje (ES)"),
        ("https://news.google.com/rss/search?q=(Polska+OR+Poland)+(sabotaż+OR+szpiegostwo+OR+dywersja+OR+ABW+OR+Rosja)&hl=pl&gl=PL&ceid=PL:pl", "Polonia / ABW (PL)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(\"organized+crime\"+OR+\"targeted+killing\"+OR+\"assassination\")&hl=en-US&gl=US&ceid=US:en", "Crimen Instrumentalizado (EN)"),
        ("https://news.google.com/rss/search?q=Россия+диверсия+OR+шпионаж+OR+поджог+OR+СБУ&hl=ru&gl=RU&ceid=RU:ru", "Rusia / Despachos (RU)"),
        ("https://news.google.com/rss/search?q=Україна+диверсія+OR+агент+OR+ГУР&hl=uk&gl=UA&ceid=UA:uk", "Ucrania / Frente (UA)"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)")
    ]

    canales_yt = [
        ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
        ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
        ("UC6n-ikz_s3zP9v9H6a_3tKg", "YouTube: CSIS Strategic Studies")
    ]

    todos_brutos = []
    with ThreadPoolExecutor(max_workers=14) as ex:
        f_tg = [ex.submit(raspar_un_canal_tg, c) for c in canales_tg]
        f_rss = [ex.submit(procesar_un_feed_rss, f) for f in fuentes_rss]
        f_yt = [ex.submit(raspar_youtube_canal, cid, tag) for cid, tag in canales_yt]
        f_rumafia = ex.submit(raspar_rumafia_y_antikor)

        for f in as_completed(f_tg + f_rss + f_yt + [f_rumafia]):
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
        st.session_state.seleccionadas_pdf = set()
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

        if plat == "REDES":
            cat_final = "REDES"
            titular_final = titular_limpio
        elif plat == "TELEGRAM":
            cat_final = "TELEGRAM"
            canal_tag = item["canal"].replace("@", "").upper()
            titular_final = f"[{canal_tag}] {titular_limpio}"
        else:
            titular_final = titular_limpio
            if es_empresa_esp(titular_limpio, cuerpo):
                cat_final = "EMPRESA"
            elif es_detencion(titular_limpio, cuerpo):
                cat_final = "DETENCION"
            elif es_cripto(titular_limpio, cuerpo):
                cat_final = "CRIPTO"
            elif es_sancion(titular_limpio, cuerpo):
                cat_final = "SANCIONES"
            elif es_espana(titular_limpio, cuerpo):
                cat_final = "ESPANA"
            elif es_china(titular_limpio, cuerpo):
                cat_final = "CHINA"
            elif es_crimen_guerra_o_terror(titular_limpio, cuerpo):
                cat_final = "CRIMENES"
            else:
                cat_final = "GLOBAL"

        etiquetas_ner = extraer_entidades_clave(f"{titular_final} {cuerpo}")

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
            "etiquetas_ner": etiquetas_ner,
            "hecho_reportado": cuerpo,
            "evaluacion_contexto": generar_analisis_contexto(cat_final, canal=item["canal"]),
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
# EXPORTACIONES E INFORMES
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
            "Entidades Clave": ", ".join([e[0] for e in a.get("etiquetas_ner", [])]),
            "Difusión Cruzada": ", ".join(a.get("canales_correlacionados", [])),
            "Titular": a["titular"],
            "Texto/Reporte": a["hecho_reportado"],
            "Evaluación": a["evaluacion_contexto"],
            "Enlace": a["enlace"]
        })
    df = pd.DataFrame(filas)
    return df.to_csv(index=False).encode('utf-8-sig')

def generar_html_ejecutivo(alertas, ventana_str="GENERAL"):
    fecha_actual = obtener_fecha_madrid_str()
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Informe Buscador de Amenazas Híbridas</title>
    <style>
        @page {{ size: A4 portrait; margin: 10mm; }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            background-color: #ffffff; color: #0f172a; margin: 0; padding: 20px; line-height: 1.35;
        }}
        .header {{
            background-color: #0f172a; color: #ffffff; border-left: 6px solid #dc2626;
            padding: 14px 18px; margin-bottom: 14px; border-radius: 4px;
        }}
        .header h1 {{ margin: 0 0 4px 0; font-size: 16px; text-transform: uppercase; }}
        .header p {{ margin: 0; color: #cbd5e1; font-size: 11px; }}
        .card {{
            background: #ffffff; border: 1px solid #cbd5e1; border-left-width: 4px;
            border-left-style: solid; padding: 10px 14px; margin-bottom: 10px; border-radius: 4px;
            page-break-inside: avoid;
        }}
        .card h3 {{ margin: 0 0 4px 0; font-size: 13.5px; color: #0f172a; }}
        .meta {{ font-size: 10px; color: #64748b; font-weight: 700; margin-bottom: 4px; }}
        .cuerpo-box {{
            background: #f8fafc; border: 1px solid #e2e8f0; padding: 6px 8px;
            font-size: 11px; color: #1e293b; margin-bottom: 4px; text-align: justify;
        }}
        .analisis-box {{
            background: #f0f9ff; border-left: 3px solid #0284c7; padding: 5px 8px;
            font-size: 10.5px; color: #0c4a6e; margin-bottom: 4px;
        }}
        .link-button {{ color: #0284c7 !important; font-size: 10.5px; font-weight: 700; text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>INFORME DE INTELIGENCIA // AMENAZAS HÍBRIDAS E INJERENCIA</h1>
        <p>VENTANA: {ventana_str} | EMISIÓN: {fecha_actual} (Madrid) | TOTAL ALERTAS: {len(alertas)}</p>
    </div>"""

    secciones = [
        ("ESPANA", "ESPAÑA E INFRAESTRUCTURAS CRÍTICAS", "#dc2626"),
        ("EMPRESA", "EMPRESAS ESTRATÉGICAS Y DEFENSA", "#d97706"),
        ("GLOBAL", "SITUACIÓN DEL CONFLICTO Y AMENAZAS GLOBALES", "#0f172a"),
        ("CRIMENES", "CRÍMENES DE GUERRA, SICARIATO Y ATENTADOS", "#991b1b"),
        ("CRIPTO", "FININT Y CRIPTOACTIVOS", "#7c3aed"),
        ("SANCIONES", "SANCIONES, ELUSIÓN Y FLOTA FANTASMA", "#b45309"),
        ("DETENCION", "DETENCIONES Y CONTRAESPIONAJE", "#be123c"),
        ("CHINA", "CHINA Y OPERACIONES ESTRATÉGICAS", "#1e3a8a"),
        ("TELEGRAM", "TELEGRAM OSINT Y CANALES TÁCTICOS", "#0284c7"),
        ("REDES", "REDES SOCIALES Y MONITORIZACIÓN", "#059669")
    ]

    for cat_cod, cat_nombre, cat_color in secciones:
        items_sec = [a for a in alertas if a.get("categoria") == cat_cod]
        if not items_sec:
            continue
        html += f"""
        <div style="background-color: {cat_color}; color:#fff; font-size:11.5px; font-weight:900; padding:5px 10px; margin:12px 0 6px 0; text-transform:uppercase;">
            {cat_nombre} ({len(items_sec)})
        </div>"""
        for a in items_sec:
            tit = html_module.escape(str(a.get('titular', '')))
            hecho = html_module.escape(str(a.get('hecho_reportado', '')))
            ana = html_module.escape(str(a.get('evaluacion_contexto', '')))
            fecha_str = obtener_fecha_madrid_str(a.get('fecha_dt'))
            canal = html_module.escape(str(a.get('canal', '')))
            enlace = html_module.escape(str(a.get('enlace', '#')), quote=True)

            html += f"""
            <div class="card" style="border-left-color: {cat_color};">
                <div class="meta">📅 {fecha_str} | 📢 {canal}</div>
                <h3>{tit}</h3>
                <div class="cuerpo-box"><strong>Hecho:</strong> {hecho}</div>
                <div class="analisis-box"><strong>Evaluación:</strong> {ana}</div>
                <div><a href="{enlace}" target="_blank" class="link-button">🔗 Abrir fuente original &rarr;</a></div>
            </div>"""

    html += "</body></html>"
    return html

# ==========================================
# MOTOR BOOLEANO DE BÚSQUEDA
# ==========================================
def cumple_busqueda_booleana(texto_evaluar, query_str):
    if not query_str or not query_str.strip():
        return True
    
    t_norm = normalizar_texto(texto_evaluar)
    q_raw = query_str.strip()

    if re.search(r'\b(AND|OR|NOT)\b', q_raw) or "-" in q_raw:
        partes_not = re.findall(r'(?:NOT\s+|-)(\w+)', q_raw, flags=re.IGNORECASE)
        for term_not in partes_not:
            if normalizar_texto(term_not) in t_norm:
                return False
        
        q_clean = re.sub(r'(?:NOT\s+|-)\w+', '', q_raw, flags=re.IGNORECASE).strip()
        if not q_clean:
            return True

        bloques_or = [b.strip() for b in re.split(r'\bOR\b', q_clean, flags=re.IGNORECASE) if b.strip()]
        for bloque in bloques_or:
            terminos_and = [t.strip() for t in re.split(r'\bAND\b|\s+', bloque, flags=re.IGNORECASE) if t.strip()]
            if all(normalizar_texto(t) in t_norm for t in terminos_and):
                return True
        return False

    palabras = [normalizar_texto(p) for p in q_raw.split() if p]
    return all(p in t_norm for p in palabras)

# ==========================================
# GESTIÓN DE CALLBACKS
# ==========================================
def marcar_todas_callback(lista_ids):
    st.session_state.seleccionadas_pdf = set(lista_ids)
    for _id in lista_ids:
        st.session_state[f"sel_{_id}"] = True

def desmarcar_todas_callback(lista_ids):
    st.session_state.seleccionadas_pdf = set()
    for _id in lista_ids:
        st.session_state[f"sel_{_id}"] = False

def toggle_checkbox_callback(item_id):
    nuevo_estado = st.session_state.get(f"sel_{item_id}", False)
    if nuevo_estado:
        st.session_state.seleccionadas_pdf.add(item_id)
    else:
        st.session_state.seleccionadas_pdf.discard(item_id)

# ==========================================
# INICIALIZACIÓN
# ==========================================
if "arranque_inicial_ok" not in st.session_state:
    with st.spinner("Sincronizando fuentes tácticas abiertas..."):
        ejecutar_actualizacion_total(activar_traduccion=True)
    st.session_state.arranque_inicial_ok = True
    st.rerun()

if "seleccionadas_pdf" not in st.session_state:
    st.session_state.seleccionadas_pdf = set()

alertas_totales = st.session_state.get("alertas_monitor", [])

# ==========================================
# BARRA LATERAL (FILTROS)
# ==========================================
with st.sidebar:
    st.subheader("🔍 Filtros de Inteligencia")
    busqueda_txt = st.text_input("Búsqueda Booleana:", placeholder="Destinus AND drones / sabotaje -incendio")
    
    rango_tiempo = st.selectbox(
        "📅 Ventana Temporal:",
        ["Últimas 24 Horas", "Últimas 48 Horas", "Última Semana", "Todo el Histórico"],
        index=3
    )

    canales_disponibles = sorted(list({a["canal"] for a in alertas_totales}))
    canales_sel = st.multiselect("Filtrar por Canal / Medio:", canales_disponibles, default=[])

    categorias_disponibles = ["ESPANA", "EMPRESA", "GLOBAL", "CRIMENES", "CRIPTO", "SANCIONES", "DETENCION", "CHINA", "TELEGRAM", "REDES"]
    cat_sel = st.multiselect("Filtrar por Categoría:", categorias_disponibles, default=[])

    st.write("---")
    st.subheader("📊 Exportación")
    csv_bytes = exportar_alertas_csv(alertas_totales)
    st.download_button(
        "📊 DESCARGAR BASE DE DATOS (CSV)",
        data=csv_bytes,
        file_name=f"alertas_hibridas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True
    )

# Filtrado temporal estricto (0 <= diff <= ventana)
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
    datos_filtrados = [x for x in datos_filtrados if cumple_busqueda_booleana(f"{x['titular']} {x['hecho_reportado']}", busqueda_txt)]

if canales_sel:
    datos_filtrados = [x for x in datos_filtrados if x["canal"] in canales_sel]

if cat_sel:
    datos_filtrados = [x for x in datos_filtrados if x["categoria"] in cat_sel]

# ==========================================
# PANEL SUPERIOR DE CONTROL
# ==========================================
col_btn1, col_trad, col_btn2, col_info = st.columns([1.2, 1.0, 1.4, 2.0])

with col_btn1:
    if st.button("🔄 ACTUALIZAR FUENTES", type="primary", use_container_width=True):
        with st.spinner("Sincronizando fuentes tácticas en tiempo real..."):
            ejecutar_actualizacion_total(activar_traduccion=st.session_state.get("check_traducir", True))
        st.rerun()

with col_trad:
    st.checkbox("🌐 Auto-Traducir", value=True, key="check_traducir", on_change=on_cambio_traduccion)

alertas_para_informe = [x for x in datos_filtrados if x.get("id") in st.session_state.seleccionadas_pdf]

with col_btn2:
    if "alertas_monitor" in st.session_state:
        st.download_button(
            f"📥 INFORME IMPRIMIBLE (HTML/PDF) ({len(alertas_para_informe)})",
            data=generar_html_ejecutivo(alertas_para_informe, ventana_str=rango_tiempo.upper()),
            file_name=f"Informe_Amenazas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.html",
            mime="text/html",
            use_container_width=True
        )

with col_info:
    if "ultima_actualizacion" in st.session_state:
        st.markdown(
            f'<div style="font-size:12.5px;color:#94a3b8;margin-top:6px;">🕒 <strong>Última sincronización:</strong> {st.session_state.ultima_actualizacion} (Madrid) | <strong>{len(datos_filtrados)}</strong> alertas activas ({rango_tiempo}).</div>',
            unsafe_allow_html=True
        )

ids_visibles = [x["id"] for x in datos_filtrados]
c_sel1, c_sel2, _ = st.columns([1.2, 1.2, 4.0])
with c_sel1:
    st.button("✅ Seleccionar todas", use_container_width=True, on_click=marcar_todas_callback, args=(ids_visibles,))
with c_sel2:
    st.button("⬜ Desmarcar todas", use_container_width=True, on_click=desmarcar_todas_callback, args=(ids_visibles,))

# ==========================================
# RENDERIZADOR DE TARJETAS
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros seleccionados.")
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
        a_safe = html_module.escape(str(item.get("evaluacion_contexto", "")))
        canal_safe = html_module.escape(str(canal_nombre))
        
        enlace_raw = item.get("enlace", "#")
        enlace_seguro = enlace_raw if es_url_segura(enlace_raw) else "#"
        enlace_attr = html_module.escape(enlace_seguro, quote=True)

        pildoras_ner = "".join([f'<span style="background-color:{c};color:#fff;font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:4px;margin-right:5px;">{html_module.escape(n)}</span>' for n, c in item.get("etiquetas_ner", [])])

        texto_copia = f"🚨 ALERTA OSINT | {cat}\nTitular: {item.get('titular', '')}\nFuente: {canal_nombre} | Fecha: {fecha_str} (Madrid)\nEnlace: {enlace_seguro}"
        b64_copia = base64.b64encode(texto_copia.encode('utf-8')).decode('utf-8')

        media_html = ""
        if item.get("multimedia") and es_url_segura(item["multimedia"]):
            media_html = f'<div style="flex:0 0 260px;max-width:260px;"><img src="{html_module.escape(item["multimedia"], quote=True)}" style="width:100%;height:auto;border-radius:6px;" loading="lazy"/></div>'

        tarjeta_html = f"""
        <div style="background:#ffffff;border:1px solid #cbd5e1;border-left:8px solid {b_color};border-radius:8px;padding:16px 20px;margin-bottom:12px;box-shadow:0 4px 10px rgba(0,0,0,0.04);">
            <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;">
                {media_html}
                <div style="flex:1;min-width:260px;">
                    <div style="font-size:12px;color:#64748b;margin-bottom:6px;">📅 {fecha_str} | 📢 {canal_safe}</div>
                    <div style="color:#0f172a;font-size:16.5px;font-weight:800;margin:0 0 8px 0;line-height:1.35;">{t_safe}</div>
                    <div style="margin-bottom:8px;">{pildoras_ner}</div>
                    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 12px;color:#1e293b;font-size:13.5px;line-height:1.5;margin-bottom:8px;text-align:justify;">
                        <strong>📄 Hecho:</strong> {h_safe}
                    </div>
                    <div style="background:#f0f9ff;border-left:4px solid #0284c7;padding:6px 10px;color:#0c4a6e;font-size:13px;line-height:1.45;margin-bottom:10px;">
                        <strong>🧠 Evaluación:</strong> {a_safe}
                    </div>
                    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                        <a href="{enlace_attr}" target="_blank" style="background:#0f172a;color:#ffffff;padding:6px 12px;border-radius:4px;font-size:12px;font-weight:700;text-decoration:none;">🔗 ABRIR FUENTE ORIGINAL</a>
                        <button style="background:#334155;color:#ffffff;padding:6px 12px;border-radius:4px;font-size:12px;font-weight:700;border:none;cursor:pointer;" onclick="navigator.clipboard.writeText(decodeURIComponent(escape(window.atob('{b64_copia}')))).then(()=>alert('📋 Alerta copiada')).catch(()=>prompt('Copiar alerta:', decodeURIComponent(escape(window.atob('{b64_copia}')))))">📋 COPIAR ALERTA</button>
                    </div>
                </div>
            </div>
        </div>
        """

        col_check, col_card = st.columns([0.05, 0.95])
        with col_check:
            if f"sel_{item_id}" not in st.session_state:
                st.session_state[f"sel_{item_id}"] = (item_id in st.session_state.seleccionadas_pdf)

            st.checkbox(
                "Incluir",
                key=f"sel_{item_id}",
                on_change=toggle_checkbox_callback,
                args=(item_id,),
                label_visibility="collapsed"
            )

        with col_card:
            st.markdown(tarjeta_html, unsafe_allow_html=True)
            if item.get("fuentes_coincidentes"):
                with st.expander(f"📑 Ver {len(item['fuentes_coincidentes'])} cobertura(s) adicional(es) de este mismo evento"):
                    for f_sec in item["fuentes_coincidentes"]:
                        st.markdown(f"- **{f_sec['canal']}:** [{f_sec['titular']}]({f_sec['enlace']})")

# ==========================================
# PESTAÑAS OPERATIVAS
# ==========================================
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
