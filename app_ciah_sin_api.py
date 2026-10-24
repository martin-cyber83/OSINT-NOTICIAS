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
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import random
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

# Zona horaria de referencia (Madrid, España)
ZONA_MADRID = ZoneInfo("Europe/Madrid")

def obtener_fecha_madrid_str(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_madrid = dt.astimezone(ZONA_MADRID)
    return dt_madrid.strftime("%d/%m/%Y %H:%M:%S")

# ==========================================
# BLINDAJE VISUAL DEFINITIVO (ANTI-GITHUB / MANAGE APP / ANCLAS)
# ==========================================
st.markdown("""
<style>
    /* Ocultar barra superior, menú y pie de página */
    #MainMenu, header, footer {
        visibility: hidden !important;
        display: none !important;
        height: 0px !important;
    }
    /* Ocultar iconos de ancla/enlace en cabeceras de Streamlit */
    a.header-anchor, a[data-testid="header-anchor"] {
        display: none !important;
        visibility: hidden !important;
    }
    /* Ocultar barras y herramientas de Streamlit Cloud */
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
</style>
""", unsafe_allow_html=True)

components.html("""
<script>
    function eliminarElementosGestion() {
        try {
            const targets = [window.document, window.parent.document, window.top.document];
            const css = `
                [data-testid="manage-app-button"],
                button[kind="manage-app"],
                .manage-app,
                div[class*="manage-app"],
                div[class*="profileContainer"],
                div[class*="profilePreview"],
                div[class*="viewerBadge"],
                .viewerBadge_container__1QSob,
                div[class*="stAppViewerAppBottomBar"],
                div[class*="Toolbar"],
                a.header-anchor,
                header, footer {
                    display: none !important;
                    visibility: hidden !important;
                    opacity: 0 !important;
                    pointer-events: none !important;
                    height: 0 !important;
                    width: 0 !important;
                }
            `;
            targets.forEach(doc => {
                if (doc && !doc.getElementById('blindaje-estilo-gad')) {
                    const style = doc.createElement('style');
                    style.id = 'blindaje-estilo-gad';
                    style.innerHTML = css;
                    doc.head.appendChild(style);
                }
                if (doc) {
                    doc.querySelectorAll('[data-testid="manage-app-button"], button[kind="manage-app"], div[class*="manage-app"], div[class*="profileContainer"]').forEach(el => el.remove());
                }
            });
        } catch(e) {}
    }
    eliminarElementosGestion();
    setInterval(eliminarElementosGestion, 400);

    window.copiarAlerta = function(textoBase64) {
        try {
            const texto = decodeURIComponent(escape(window.atob(textoBase64)));
            navigator.clipboard.writeText(texto).then(() => {
                alert("📋 Alerta copiada en formato táctico.");
            }).catch(() => {
                const ta = document.createElement('textarea');
                ta.value = texto;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                alert("📋 Alerta copiada.");
            });
        } catch (err) {}
    };
</script>
""", height=0, width=0)

# ==========================================
# CONSTANTES Y LISTAS (DEFINICIÓN INICIAL SEGURA)
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

PATRON_PAISES_AJENOS = re.compile(
    r'\b('
    r'cuba|cubano|cubana|cubanos|la habana|castro|diaz-canel|'
    r'venezuela|venezolano|venezolana|maduro|caracas|'
    r'nicaragua|ortega|managua|colombia|petro|bogota|'
    r'gaza|hamas|hezbollah|hezbola|netanyahu|tel aviv|cisjordania|palestina|palestino|israel|israeli|'
    r'yemen|huti|huties|iran|irani|teheran|siria|damasco'
    r')\b', re.IGNORECASE
)

# ==========================================
# CONTROL DE ACCESO (CLAVE: GAD2026)
# ==========================================
CLAVE_SISTEMA = "GAD2026"
TOKEN_PERSISTENTE = "gad_auth_ok_2026"

def gestionar_acceso():
    if st.session_state.get("autenticado", False):
        return True
    
    if st.query_params.get("auth") == TOKEN_PERSISTENTE:
        st.session_state.autenticado = True
        return True

    st.markdown("""
        <div style="max-width: 470px; margin: 40px auto 20px auto; padding: 26px; background: #0f172a; border-radius: 12px; border-left: 6px solid #dc2626; color: white; box-shadow: 0 16px 36px rgba(0,0,0,0.6);">
            <h2 style="margin-top: 0; color: #ffffff; font-size: 21px; letter-spacing: 0.5px;">🔒 Acceso Restringido</h2>
            <p style="color: #94a3b8; font-size: 13.5px; margin-bottom: 0;">Sistema de Vigilancia de Fuentes Abiertas. Introduzca la credencial autorizada para continuar.</p>
        </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("form_login"):
            clave_introducida = st.text_input("Clave de acceso:", type="password", placeholder="Introduce la contraseña...")
            recordar_equipo = st.checkbox("Recordar acceso en este equipo", value=True)
            boton_acceso = st.form_submit_button("INGRESAR AL SISTEMA", type="primary", use_container_width=True)

            if boton_acceso:
                if clave_introducida == CLAVE_SISTEMA:
                    st.session_state.autenticado = True
                    if recordar_equipo:
                        st.query_params["auth"] = TOKEN_PERSISTENTE
                    st.rerun()
                else:
                    st.error("Credencial no autorizada.")
        
    return False

if not gestionar_acceso():
    st.stop()

# ==========================================
# SESIÓN HTTP ROBUSTA
# ==========================================
SESION_HTTP = requests.Session()
retries = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=retries)
SESION_HTTP.mount("https://", adapter)
SESION_HTTP.mount("http://", adapter)
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,pl;q=0.6,zh;q=0.5,ar;q=0.5"
})

CACHE_TITULARES_GLOBAL = {}
CACHE_LOCK = threading.Lock()

# ==========================================
# LIMPIEZA DE CADENAS Y TEXTO
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
        r'europa\s*press', r'agencia\s*efe', r'rtve', r'investigative\s*journalism\s*foundation',
        r'24horas', r'responsible\s*statecraft', r'aleteia(\.org)?', r'vatican\s*news', r'cmmedia'
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
    return str(url).startswith("http://") or str(url).startswith("https://")

# ==========================================
# MOTOR DE BÚSQUEDA BOOLEANA REAL (AND, OR, NOT)
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
# EXTRACCIÓN DE ENTIDADES CLAVE (NER LIGERA)
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
# DECODIFICACIÓN OFFLINE DE GOOGLE NEWS
# ==========================================
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
    except Exception:
        pass
    return url_rss

# ==========================================
# TRADUCTOR ANTI-BLOQUEO
# ==========================================
def traducir_un_titular_individual(titular_orig):
    if not titular_orig:
        return ""
    t_clean = limpiar_html_texto(titular_orig)
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

    time.sleep(random.uniform(0.02, 0.05))

    for _ in range(2):
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:380]}
            r = SESION_HTTP.get(url, params=params, timeout=3.0)
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
            elif r.status_code == 429:
                time.sleep(0.3)
        except Exception:
            time.sleep(0.2)

    with CACHE_LOCK:
        CACHE_TITULARES_GLOBAL[t_clean] = t_clean
    return t_clean

def traducir_lote_titulares(lista_titulares):
    with ThreadPoolExecutor(max_workers=6) as ex:
        resultados = list(ex.map(traducir_un_titular_individual, lista_titulares))
    return resultados

def extraer_resumen_rss(tit_orig, desc_rss):
    if not desc_rss:
        return limpiar_firma_medio(tit_orig)
    try:
        soup = BeautifulSoup(desc_rss, "html.parser")
        textos = []
        for tag in soup.find_all(["li", "p", "a", "font", "div"]):
            t = limpiar_html_texto(tag.get_text(" ", strip=True))
            if len(t) > 20 and t not in textos:
                textos.append(t)
        combinado = " — ".join(textos) if textos else limpiar_html_texto(soup.get_text(" ", strip=True))
        if len(combinado) > 25:
            return combinado[:1800]
    except Exception:
        pass
    limpio = limpiar_html_texto(desc_rss)
    return limpio[:1800] if len(limpio) > 25 else limpiar_firma_medio(tit_orig)

# ==========================================
# FILTRADO Y CATEGORIZACIÓN
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

    if re.search(r'\b(papa|vaticano|santa sede|iglesia|obispo|pontifice|francisco|leon xiv)\b', t_norm) and not re.search(r'\b(ciberataque|sabotaje|espionaje|hack|ddos)\b', t_norm):
        return False

    if any(c in t_raw for c in RAICES_CIRILICO) or any(c in t_raw for c in RAICES_CHINO) or any(c in t_raw for c in RAICES_ARABE) or any(c in t_raw for c in RAICES_POLACO):
        return True

    if PATRON_CHINA_ESTRICTO.search(t_norm) or PATRON_RUSIA_ESTRICTO.search(t_norm):
        return True

    if PATRON_PAISES_AJENOS.search(t_norm):
        if not re.search(r'\b(kremlin|putin|tropas rusas|fuerzas armadas rusas|moscu|espionaje ruso|invasion rusa|shariy|sharij|rybar|zvinchuk|destinus|beijing|xi jinping|mss)\b', t_norm):
            return False

    return False

def tiene_vector_amenaza_global(texto):
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    if re.search(r'\b(papa|vaticano|santa sede|iglesia|obispo|pontifice|francisco|leon xiv)\b', t_norm) and not re.search(r'\b(ciberataque|sabotaje|espionaje|hack|ddos)\b', t_norm):
        return False

    if any(emp in t_norm for emp in ["destinus", "uav navigation", "oesia", "tecnobit", "indra", "escribano"]):
        if re.search(r'\b(dron|drones|uav|hornet|antidron|defensa|militar|armada|contrato|ciberataque|amenaza|amenazadas|amenazado)\b', t_norm):
            return True

    vectores_cirilico = [
        "диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид",
        "крипт", "биткоин", "usdt", "бпла", "дron", "дрон", "рэб", "арест", "задержан", "санкц", "кабел", "флот",
        "казн", "пленн", "военн", "преступлен", "поджог", "теракт", "удар", "взрыв", "зеленск", "фронт",
        "гражданск", "больниц", "депортац", "гааг", "мкс", "убийств", "ликвидац"
    ]
    if any(c in t_raw for c in vectores_cirilico):
        return True

    vectores_latinos = [
        "hibrid", "hybrid", "sabotaj", "sabotage", "saboteador", "espia", "espionaje", "spy", "espionage", "spying",
        "ciber", "cyber", "hack", "ddos", "malware", "ransomware", "apt28", "apt29", "apt41", "volt typhoon", "sandworm", "noname057", "killnet",
        "desinformacion", "disinformation", "propaganda", "injerencia", "fimi",
        "cripto", "crypto", "bitcoin", "btc", "usdt", "tether", "garantex", "sancion", "sanction", "elusion",
        "flota fantasma", "shadow fleet", "cable submarino", "undersea cable", "petrolero",
        "crimen de guerra", "war crime", "corte penal internacional", "icc",
        "genocidio", "tortura", "pow execution", "ejecucion", "masacre", "fosa comun", "bucha", "mariupol",
        "ataque civil", "ataque a civiles", "hospital", "escuela", "deportacion", "ninos ucranianos",
        "detenido", "detencion", "arrestado", "arresto", "arrested", "detained", "dron", "uav", "guerra electronica",
        "jamming", "arson", "incendio provocado", "guerra", "ejercito", "militar", "otan", "nato", "frente", "misil",
        "ataque", "ofensiva", "bombardeo", "shariy", "sharij", "rybar", "zvinchuk", "destinus", "hornet", "interceptor",
        "amenaza", "amenazadas", "amenazado", "lista negra", "represalia",
        "crimen organizado", "mafia", "narcotrafico", "sicario", "asesinato", "homicidio", "muerte", "envenenamiento", "defenestrado",
        "atentado", "terrorismo", "terrorista", "no convencional", "paquete incendiario", "servicios de inteligencia", "fsb", "gru", "mss"
    ]
    return any(v in t_norm for v in vectores_latinos)

def es_empresa_esp(titular, cuerpo):
    t_all = normalizar_texto(desparasitar_nombres_medios(f"{titular} {cuerpo}"))
    menciona_empresa_concreta = any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP)
    menciona_empresa_generica = bool(re.search(r'\b(empresa espanola|empresas espanolas|filial espanola|sede en valdepe[nñ]as|industria de defensa espanola)\b', t_all))
    
    if not (menciona_empresa_concreta or menciona_empresa_generica):
        return False
        
    patron_vinculo = r'\b(' \
                     r'rusia|ruso|rusa|rusos|moscu|kremlin|putin|' \
                     r'ucrania|ucraniano|ucraniana|frente|zelensky|' \
                     r'noname057|killnet|apt28|apt29|sandworm|' \
                     r'amenazada por rusia|amenazadas por rusia|amenaza rusa|' \
                     r'ciberataque ruso|sabotaje ruso|espionaje ruso|sanciones rusas' \
                     r')\b'
    return bool(re.search(patron_vinculo, t_all))

def es_china(titular, cuerpo):
    t_raw = str(f"{titular} {cuerpo}").lower()
    t_norm = normalizar_texto(f"{titular} {cuerpo}")
    if any(c in t_raw for c in RAICES_CHINO):
        return True
    return bool(PATRON_CHINA_ESTRICTO.search(t_norm))

def es_sancion(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(' \
             r'sancion|sanciones|sanctions?|elusion|eludir|evasion de sanciones|' \
             r'flota fantasma|shadow fleet|petrolero ruso|petroleros fantasma|' \
             r'tope al crudo|price cap|contrabando de chips|tecnologia de doble uso|' \
             r'triangulacion|comercio clandestino|lista negra de la ue|ofac' \
             r')\b'
    return bool(re.search(patron, t_all))

def es_cripto(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(cripto|criptomoneda|criptomonedas|crypto|criptobolsa|usdt|tether|btc|bitcoin|monero|garantex|blockchain)\b'
    return bool(re.search(patron, t_all))

def es_crimen_guerra(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(crimen de guerra|crimenes de guerra|war crime|war crimes|corte penal internacional|tribunal penal|cpi|icc|la haya|the hague|lesa humanidad|genocidio|pow execution|ejecucion de prisioneros|tortura|torturas|fosa comun|fosas comunes|masacre|bucha|irpin|izium|mariupol|deportacion|menores deportados|ninos robados|ataque a civiles|ataque contra civiles|civiles muertos|muerte de civiles|bombardeo de hospital|bombardeo residencial|edificio de viviendas|edificio residencial|escuela bombardeada|hospital infantil|bloque de apartamentos|terrorismo|terrorista|atentado|paquete incendiario|asesinato selectivo|homicidio selectivo)\b'
    return bool(re.search(patron, t_all))

def es_detencion(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron_det = r'\b(detenido|detenida|detenidos|detenidas|detienen|detiene|detencion|detenciones|arrestado|arrestada|arrestados|arrestadas|arrestan|arresta|arresto|arrestos|arrested|detained|festnahme|verhaftet|capturado|capturada|capturados|capturadas|capturan|desarticulad[oa]s?|prision preventiva|prision provisional|aresztowany|zatrzymany)\b'
    return bool(re.search(patron_det, t_all))

def es_espana(titular, cuerpo):
    texto_limpio = desparasitar_nombres_medios(f"{titular} {cuerpo}")
    t_norm = normalizar_texto(texto_limpio)

    if re.search(r'\b(leon xiv|vaticano|santa sede|papa leon|francisco|obispo|pontifice)\b', t_norm) and not re.search(r'\b(cni|ministerio de defensa|guardia civil)\b', t_norm):
        return False

    patron_esp = r'\b(espana|espanol|espanola|espanoles|espanolas|spain|madrid|barcelona|cartagena|rota|torrejon|cni|incibe|ccn-cert|audiencia nacional|guardia civil|policia nacional|gobierno de espana|gobierno espanol|ministerio de defensa|margarita robles|ejercito de tierra|armada espanola|ejercito del aire|militares espanoles|seguridad nacional)\b'
    
    if not re.search(patron_esp, t_norm):
        return False

    if re.search(r'\b(en ucrania|en rusia|en polonia|en alemania|en dinamarca|eeuu|estados unidos|en pekin|en china)\b', t_norm):
        if not re.search(r'\b(militares espanoles|cni|gobierno de espana|ministerio de defensa|incibe|guardia civil)\b', t_norm):
            return False

    return True

def generar_analisis_contexto(categoria, canal=""):
    canal_clean = canal.replace("@", "")
    if "rybar" in canal_clean.lower() or "zvinchuk" in canal_clean.lower():
        return "Análisis táctico y propaganda militar de Mijaíl Zvinchuk (@rybar), exoficial del Ministerio de Defensa ruso."
    if "sharij" in canal_clean.lower() or "shariy" in canal_clean.lower():
        return "Contenido de Anatolii Sharij: seguimiento crítico y difusión en redes del frente ucraniano-ruso."
    if canal_clean in CANALES_DESINFORMACION_ESP:
        return "Canal clasificado en la red de propaganda, desinformación e injerencia extranjera (FIMI) vinculada al ecosistema pro-Kremlin."
    if categoria == "SANCIONES":
        return "Vigilancia de elusión de sanciones: movimientos de la flota fantasma, redes de contrabando de doble uso o triangulación logística."
    if categoria == "CHINA":
        return "Actividad estratégica de la República Popular China: ciberespionaje (Volt Typhoon, APT41), injerencia en Occidente y alineación con Moscú."
    if categoria == "EMPRESA":
        return "Seguimiento corporativo: empresas estratégicas españolas frente a amenazas híbridas, ciberataques o contratos de defensa."
    if categoria == "CRIMENES":
        return "Documentación de crímenes de guerra, terrorismo no convencional, ataques a civiles y homicidios orquestados por servicios extranjeros."
    if categoria == "DETENCION":
        return "Operaciones policiales y contrainteligencia: neutralización y arresto de espías, saboteadores o redes de inteligencia extranjera."
    if categoria == "REDES":
        return "Contenido capturado en redes sociales, canales de análisis en YouTube y cuentas de seguimiento del conflicto."
    if categoria == "ESPANA":
        return "Impacto directo en la seguridad nacional e infraestructuras críticas de España frente a operaciones hostiles."
    if categoria == "CRIPTO":
        return "Inteligencia financiera (FININT): evasión de sanciones internacionales y uso de criptoactivos (USDT, BTC)."
    if categoria == "TELEGRAM":
        return "Reporte capturado de canales tácticos en Telegram: inteligencia operativa de primera línea."
    return "Actividad vinculada a operaciones híbridas globales, crimen organizado y servicios de inteligencia."

# ==========================================
# RASTREADOR YOUTUBE DIRECTO
# ==========================================
def raspar_youtube_canal(canal_id, etiqueta):
    items = []
    try:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}"
        r = SESION_HTTP.get(url, timeout=3.0)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:15]:
                tit_tag = entry.find("{http://www.w3.org/2005/Atom}title")
                tit_orig = limpiar_html_texto(tit_tag.text if tit_tag is not None else "")

                link_tag = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_tag.get("href", "") if link_tag is not None else ""

                pub_tag = entry.find("{http://www.w3.org/2005/Atom}published")
                pub_str = pub_tag.text if pub_tag is not None else ""
                try:
                    dt_obj = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    dt_obj = datetime.now(timezone.utc)

                desc_tag = entry.find(".//{http://search.yahoo.com/mrss/}description")
                cuerpo = limpiar_html_texto(desc_tag.text if desc_tag is not None else "")

                items.append({
                    "id": f"YT-{hash(link)}",
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

def barrido_canales_youtube():
    canales = [
        ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
        ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
        ("UC6n-ikz_s3zP9v9H6a_3tKg", "YouTube: CSIS Strategic Studies")
    ]
    todas = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futuros = [ex.submit(raspar_youtube_canal, cid, tag) for cid, tag in canales]
        for f in as_completed(futuros):
            try:
                todas.extend(f.result())
            except Exception:
                pass
    return todas

# ==========================================
# RASTREADOR TELEGRAM
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

            texto_limpio_eval = re.sub(r'@\w+', '', texto_raw)
            texto_limpio_eval = re.sub(r'https?://\S+', '', texto_limpio_eval)

            if not tiene_vinculo_geopolitico(texto_limpio_eval):
                continue

            texto_sin_urls = limpiar_html_texto(re.sub(r'http\S+', '', texto_raw))
            if len(texto_sin_urls) < 20:
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
                "id": f"TG-{hash(link)}",
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

def raspar_telegram_masivo():
    canales = [
        "rybar", "ASupersharij", "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "entre_guerras", "dva_majors", "vchkogpu", "meduzalive", "astrapress", "istories_media",
        "ostorozhno_novosti", "nvua_official", "operativnoZSU", "musicarusaesp", "Irinamar_Z",
        "eurekapress", "elOJOen", "ophiucu"
    ]
    todos = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futuros = [ex.submit(raspar_un_canal_tg, c) for c in canales]
        for f in as_completed(futuros):
            try:
                todos.extend(f.result())
            except Exception:
                pass
    return todos

# ==========================================
# RASTREADORES RUMAFIA Y ANTIKOR
# ==========================================
def raspar_rumafia():
    items = []
    try:
        url = "https://www.rumafia.net/en/rss/"
        r = SESION_HTTP.get(url, timeout=3.5)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:30]:
                tit = limpiar_html_texto(item.findtext("title", ""))
                desc = limpiar_html_texto(item.findtext("description", ""))
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")
                try:
                    dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)

                texto_eval = f"{tit} {desc}"
                if len(tit) > 10 and tiene_vinculo_geopolitico(texto_eval) and tiene_vector_amenaza_global(texto_eval):
                    items.append({
                        "id": f"RUMAFIA-{hash(link)}",
                        "plataforma": "INVESTIGACIÓN",
                        "canal": "RuMafia.net",
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

def raspar_antikor():
    items = []
    try:
        url = "https://antikor.com.ua/articles.rss"
        r = SESION_HTTP.get(url, timeout=3.5)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:30]:
                tit = limpiar_html_texto(item.findtext("title", ""))
                desc = limpiar_html_texto(item.findtext("description", ""))
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")
                try:
                    dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)

                texto_eval = f"{tit} {desc}"
                if len(tit) > 10 and (tiene_vinculo_geopolitico(texto_eval) and tiene_vector_amenaza_global(texto_eval)):
                    items.append({
                        "id": f"ANTIKOR-{hash(link)}",
                        "plataforma": "INVESTIGACIÓN",
                        "canal": "Antikor Portal",
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
# RECOLECTOR RSS PRENSA MULTILINGÜE
# ==========================================
def procesar_un_feed_rss(datos_feed):
    url, etiqueta = datos_feed
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=3.5)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        for item in root.findall(".//item")[:45]:
            tit_orig = limpiar_html_texto(item.findtext("title", ""))
            desc_orig = item.findtext("description", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")

            if len(tit_orig) < 15 or es_publicidad_o_spam(tit_orig + " " + desc_orig):
                continue

            tit_sin_medio = limpiar_firma_medio(tit_orig)
            cuerpo_crudo = extraer_resumen_rss(tit_orig, desc_orig)
            texto_eval = f"{tit_sin_medio} {cuerpo_crudo}"

            if not tiene_vinculo_geopolitico(texto_eval) or not tiene_vector_amenaza_global(texto_eval):
                continue

            try:
                dt_obj = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception:
                dt_obj = datetime.now(timezone.utc)

            url_destino = decodificar_url_google_news_offline(link)

            items.append({
                "id": f"RSS-{hash(link)}",
                "plataforma": "PRENSA",
                "canal": etiqueta,
                "fecha_dt": dt_obj,
                "titular_raw": tit_orig,
                "cuerpo_raw": cuerpo_crudo,
                "multimedia": None,
                "tiene_video": False,
                "enlace": url_destino
            })
    except Exception:
        pass
    return items

def barrido_prensa_multilingue():
    fuentes_rss = [
        ("https://theobjective.com/feed/", "The Objective (General)"),
        ("https://theobjective.com/espana/feed/", "The Objective (España)"),
        ("https://theobjective.com/internacional/feed/", "The Objective (Internacional)"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin+OR+NoName057+OR+APT28)+(España+OR+Madrid+OR+CNI+OR+INCIBE)+(sabotaje+OR+ciberataque+OR+espionaje+OR+amenaza)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Russian+OR+Kremlin)+(Spain+OR+Madrid)+(sabotage+OR+cyberattack+OR+spy+OR+espionage)&hl=en-US&gl=US&ceid=US:en", "Spain Watch (EN)"),
        
        # CHINA Y CIBERESPIONAJE
        ("https://news.google.com/rss/search?q=(China+OR+Pekín+OR+Xi+Jinping+OR+APT41+OR+\"Volt+Typhoon\")+(España+OR+Occidente+OR+espionaje+OR+ciberataque+OR+injerencia)&hl=es&gl=ES&ceid=ES:es", "China Injerencia (ES)"),
        ("https://news.google.com/rss/search?q=(China+OR+Beijing)+(espionage+OR+sabotage+OR+cyberattack+OR+\"hybrid+warfare\")+West&hl=en-US&gl=US&ceid=US:en", "China Intel (EN)"),
        ("https://news.google.com/rss/search?q=China+Taiwan+(military+OR+sabotage+OR+cyber+OR+espionage)&hl=en-US&gl=US&ceid=US:en", "Asia Pacific Watch (EN)"),

        # EMPRESAS E INDUSTRIA DE DEFENSA
        ("https://news.google.com/rss/search?q=(\"empresas+amenazadas+por+rusia\"+OR+\"empresa+amenazada+por+rusia\"+OR+(Tecnobit+OR+Oesia+OR+Destinus+OR+Indra+OR+Navantia+OR+Escribano)+AND+(Rusia+OR+Kremlin+OR+Putin+OR+amenaza))&hl=es&gl=ES&ceid=ES:es", "Empresas Amenazadas (ES)"),
        ("https://news.google.com/rss/search?q=(Destinus+OR+Kokorich)+(Rusia+OR+Ucrania+OR+drones+OR+frente)&hl=es&gl=ES&ceid=ES:es", "Destinus / Drones (ES)"),

        # SANCIONES, ELUSIÓN Y FLOTA FANTASMA
        ("https://news.google.com/rss/search?q=(Rusia+OR+China+OR+Ucrania)+(\"elusion+de+sanciones\"+OR+\"flota+fantasma\"+OR+\"petrolero\"+OR+\"doble+uso\"+OR+\"triangulacion\")&hl=es&gl=ES&ceid=ES:es", "Sanciones / Elusión (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+China+OR+Iran)+(\"sanctions+evasion\"+OR+\"shadow+fleet\"+OR+\"illicit+oil\"+OR+\"dual-use\")&hl=en-US&gl=US&ceid=US:en", "Sanctions Evasion (EN)"),

        # DETENCIONES Y CONTRAESPIONAJE
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado+OR+detencion)+(espia+ruso+OR+espia+chino+OR+sabotaje+OR+GRU+OR+MSS)&hl=es&gl=ES&ceid=ES:es", "Detenciones (ES)"),
        ("https://news.google.com/rss/search?q=(arrested+OR+detained)+(Russian+spy+OR+Chinese+spy+OR+sabotage)&hl=en-US&gl=US&ceid=US:en", "Detenciones (EN)"),

        # POLONIA Y EUROPA DEL ESTE
        ("https://news.google.com/rss/search?q=(Polska+OR+Poland)+(sabotaż+OR+szpiegostwo+OR+dywersja+OR+ABW+OR+Rosja)&hl=pl&gl=PL&ceid=PL:pl", "Polonia / ABW (PL)"),
        ("https://news.google.com/rss/search?q=Poland+sabotage+Russian+agents+OR+arson&hl=en-US&gl=US&ceid=US:en", "Poland Sabotage (EN)"),

        # CRIMEN ORGANIZADO, ASESINATOS Y TERRORISMO
        ("https://news.google.com/rss/search?q=(Rusia+OR+Ucrania+OR+China)+(\"crimen+organizado\"+OR+\"asesinato\"+OR+\"sicario\"+OR+\"atentado\"+OR+\"terrorismo\"+OR+\"servicios+de+inteligencia\")&hl=es&gl=ES&ceid=ES:es", "Crimen y Terrorismo (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine+OR+China)+(\"organized+crime\"+OR+\"assassination\"+OR+\"targeted+killing\"+OR+\"intelligence+service\")&hl=en-US&gl=US&ceid=US:en", "Intel Killings & Terror (EN)"),

        # FUENTES CIRÍLICAS
        ("https://news.google.com/rss/search?q=Россия+диверсия+OR+теракт+OR+шпионаж+OR+СБУ&hl=ru&gl=RU&ceid=RU:ru", "Rusia / Ecosistema RU"),
        ("https://news.google.com/rss/search?q=Україна+диверсія+OR+агент+OR+обстріл+OR+ГУР&hl=uk&gl=UA&ceid=UA:uk", "Ucrania / Frente UA"),

        # CRIPTO Y FINANZAS CLANDESTINAS
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT+OR+Tether)+(Russia+OR+China+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)")
    ]
    todas = []
    with ThreadPoolExecutor(max_workers=18) as ex:
        futuros = [ex.submit(procesar_un_feed_rss, f) for f in fuentes_rss]
        for f in as_completed(futuros):
            try:
                todas.extend(f.result())
            except Exception:
                pass
    return todas

# ==========================================
# ACTUALIZACIÓN CENTRAL
# ==========================================
def son_mismo_evento(tit1, tit2):
    t1 = re.sub(r'^(última hora|exclusiva|urgente|video)[:\-]?\s*', '', normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit1))))
    t2 = re.sub(r'^(última hora|exclusiva|urgente|video)[:\-]?\s*', '', normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit2))))
    if len(t1) < 15 or len(t2) < 15:
        return False
    return SequenceMatcher(None, t1[:95], t2[:95]).ratio() > 0.68

def ejecutar_actualizacion_total(activar_traduccion=True):
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_prensa = ex.submit(barrido_prensa_multilingue)
        f_tg = ex.submit(raspar_telegram_masivo)
        f_yt = ex.submit(barrido_canales_youtube)
        f_rumafia = ex.submit(raspar_rumafia)
        f_antikor = ex.submit(raspar_antikor)

        brutos_prensa = f_prensa.result()
        brutos_tg = f_tg.result()
        brutos_yt = f_yt.result()
        brutos_rumafia = f_rumafia.result()
        brutos_antikor = f_antikor.result()

    anteriores = st.session_state.get("alertas_monitor", [])
    todos_brutos = brutos_tg + brutos_yt + brutos_prensa + brutos_rumafia + brutos_antikor
    
    for ant in anteriores:
        todos_brutos.append({
            "id": ant["id"],
            "plataforma": ant["plataforma"],
            "canal": ant["canal"],
            "fecha_dt": ant["fecha_dt"],
            "titular_raw": ant.get("titular_original", ant["titular"]),
            "cuerpo_raw": ant["hecho_reportado"],
            "multimedia": ant.get("multimedia"),
            "tiene_video": ant.get("tiene_video", False),
            "enlace": ant["enlace"],
            "canales_correlacionados": ant.get("canales_correlacionados", [ant["canal"]]),
            "fuentes_coincidentes": ant.get("fuentes_coincidentes", [])
        })

    todos_brutos.sort(key=lambda x: x["fecha_dt"], reverse=True)

    pre_unicos = []
    urls_vistas = set()
    for b in todos_brutos:
        u = limpiar_url(b["enlace"])
        if u in urls_vistas:
            continue

        coincidente = next((ex for ex in pre_unicos if son_mismo_evento(b["titular_raw"], ex["titular_raw"])), None)
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
        st.session_state.seleccionadas_pdf = set()
        return

    titulares_crudos = [item["titular_raw"] for item in pre_unicos]
    if activar_traduccion:
        titulares_traducidos = traducir_lote_titulares(titulares_crudos)
    else:
        titulares_traducidos = titulares_crudos

    alertas_finales = []
    for item, tit_es in zip(pre_unicos, titulares_traducidos):
        cuerpo_intacto = item["cuerpo_raw"]
        plat = item["plataforma"]
        titular_limpio = limpiar_firma_medio(tit_es)

        if plat == "REDES":
            titular_final = titular_limpio
            cat_final = "REDES"
        elif plat == "TELEGRAM":
            canal_tag = item["canal"].replace("@", "").upper()
            titular_final = f"[{canal_tag}] {titular_limpio}"
            cat_final = "TELEGRAM"
        else:
            titular_final = titular_limpio
            
            if es_empresa_esp(titular_limpio, cuerpo_intacto):
                cat_final = "EMPRESA"
            elif es_detencion(titular_limpio, cuerpo_intacto):
                cat_final = "DETENCION"
            elif es_cripto(titular_limpio, cuerpo_intacto):
                cat_final = "CRIPTO"
            elif es_sancion(titular_limpio, cuerpo_intacto):
                cat_final = "SANCIONES"
            elif es_espana(titular_limpio, cuerpo_intacto):
                cat_final = "ESPANA"
            elif es_china(titular_limpio, cuerpo_intacto):
                cat_final = "CHINA"
            elif es_crimen_guerra(titular_limpio, cuerpo_intacto):
                cat_final = "CRIMENES"
            else:
                cat_final = "GLOBAL"

        etiquetas_ner = extraer_entidades_clave(f"{titular_final} {cuerpo_intacto}")

        nueva_alerta = {
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
            "hecho_reportado": cuerpo_intacto,
            "evaluacion_contexto": generar_analisis_contexto(cat_final, canal=item["canal"]),
            "multimedia": item.get("multimedia"),
            "tiene_video": item.get("tiene_video", False),
            "enlace": item["enlace"]
        }

        alertas_finales.append(nueva_alerta)

    alertas_finales.sort(key=lambda x: x["fecha_dt"], reverse=True)

    st.session_state.alertas_monitor = alertas_finales
    st.session_state.ultima_actualizacion = obtener_fecha_madrid_str()
    st.session_state.seleccionadas_pdf = {a["id"] for a in alertas_finales}
    for a in alertas_finales:
        st.session_state[f"sel_{a['id']}"] = True

# ==========================================
# EXPORTACIÓN CSV Y MARKDOWN (.MD / WORD)
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

def generar_informe_markdown(alertas):
    fecha_actual = obtener_fecha_madrid_str()
    md = f"# INFORME DE INTELIGENCIA // BUSCADOR DE AMENAZAS HÍBRIDAS\n"
    md += f"**Fecha de Emisión:** {fecha_actual} (Hora Peninsular)\n"
    md += f"**Total de Alertas Consolidadas:** {len(alertas)}\n\n---\n\n"
    
    secciones_nombres = {
        "ESPANA": "🇪🇸 ESPAÑA E INFRAESTRUCTURAS CRÍTICAS",
        "EMPRESA": "🏢 EMPRESAS ESTRATÉGICAS Y DEFENSA",
        "GLOBAL": "📰 AMENAZAS GLOBALES (RUSIA/UCRANIA)",
        "CRIMENES": "⚖️ CRÍMENES DE GUERRA Y TERRORISMO",
        "CRIPTO": "🟣 FININT Y CRIPTOACTIVOS",
        "SANCIONES": "📦 SANCIONES Y FLOTA FANTASMA",
        "DETENCION": "🛑 DETENCIONES Y CONTRAESPIONAJE",
        "CHINA": "🇨🇳 CHINA Y OPERACIONES DE INJERENCIA",
        "TELEGRAM": "✈️ CANALES TÁCTICOS TELEGRAM",
        "REDES": "🌐 REDES SOCIALES Y MONITORIZACIÓN"
    }

    for cat_cod, cat_nombre in secciones_nombres.items():
        items_cat = [a for a in alertas if a.get("categoria") == cat_cod]
        if not items_cat:
            continue
        md += f"## {cat_nombre} ({len(items_cat)})\n\n"
        for a in items_cat:
            fecha_str = obtener_fecha_madrid_str(a["fecha_dt"])
            md += f"### {a['titular']}\n"
            md += f"- **Fecha:** {fecha_str} | **Fuente:** {a['canal']}\n"
            if a.get("etiquetas_ner"):
                md += f"- **Entidades:** {', '.join([e[0] for e in a['etiquetas_ner']])}\n"
            md += f"- **Reporte:** {a['hecho_reportado'][:400]}...\n"
            md += f"- **Evaluación:** {a['evaluacion_contexto']}\n"
            md += f"- **Enlace Fuente:** [Abrir Fuente Externa]({a['enlace']})\n\n"
        md += "---\n\n"
    return md.encode('utf-8')

# ==========================================
# INFORME IMPRIMIBLE (HTML / PDF)
# ==========================================
SECCIONES_INFORME = [
    ("ESPANA", "SECCIÓN 1: ESPAÑA E INFRAESTRUCTURAS CRÍTICAS", "#dc2626"),
    ("EMPRESA", "SECCIÓN 2: EMPRESAS ESTRATÉGICAS Y DEFENSA", "#d97706"),
    ("GLOBAL", "SECCIÓN 3: RUSIA, UCRANIA Y AMENAZAS GLOBALES", "#0f172a"),
    ("CRIMENES", "SECCIÓN 4: CRÍMENES DE GUERRA Y TERRORISMO", "#991b1b"),
    ("CRIPTO", "SECCIÓN 5: CRIPTOACTIVOS Y FINANCIACIÓN ILÍCITA", "#7c3aed"),
    ("SANCIONES", "SECCIÓN 6: SANCIONES, ELUSIÓN Y FLOTA FANTASMA", "#b45309"),
    ("DETENCION", "SECCIÓN 7: DETENCIONES Y CONTRAESPIONAJE", "#be123c"),
    ("CHINA", "SECCIÓN 8: CHINA Y AMENAZAS ESTRATÉGICAS A OCCIDENTE", "#1e3a8a"),
    ("TELEGRAM", "SECCIÓN 9: TELEGRAM OSINT Y CANALES TÁCTICOS", "#0284c7"),
    ("REDES", "SECCIÓN 10: REDES SOCIALES Y ANÁLISIS MULTIMEDIA", "#059669")
]

def generar_html_ejecutivo(alertas, ventana_str="GENERAL"):
    fecha_actual = obtener_fecha_madrid_str()
    subtitulo_filtro = f"INFORME OPERATIVO ({ventana_str})"
    
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
            background-color: #ffffff; color: #0f172a; margin: 0; padding: 0; line-height: 1.35;
            -webkit-print-color-adjust: exact; print-color-adjust: exact;
        }}
        @media screen {{
            body {{ background-color: #f1f5f9; padding: 20px; }}
            #contenedor-reporte {{
                max-width: 860px; margin: 0 auto; background: #ffffff; padding: 26px;
                border: 1px solid #cbd5e1; border-radius: 8px;
            }}
            .toolbar {{
                max-width: 860px; margin: 0 auto 16px auto; display: flex;
                justify-content: space-between; align-items: center; background: #ffffff;
                padding: 12px 18px; border-radius: 8px; border: 1px solid #cbd5e1;
            }}
            .btn-print {{
                background: #dc2626; color: #ffffff; border: none; padding: 10px 20px;
                font-size: 13.5px; font-weight: 800; border-radius: 6px; cursor: pointer;
            }}
        }}
        @media print {{
            body {{ background: #ffffff !important; padding: 0 !important; margin: 0 !important; }}
            .toolbar {{ display: none !important; }}
            #contenedor-reporte {{ border: none !important; padding: 0 !important; width: 100% !important; }}
            .card {{ page-break-inside: avoid !important; margin-bottom: 6px !important; }}
        }}
        .header {{
            background-color: #0f172a; color: #ffffff; border-left: 6px solid #dc2626;
            padding: 12px 16px; margin-bottom: 12px; border-radius: 4px;
        }}
        .header h1 {{ margin: 0 0 2px 0; font-size: 16px; letter-spacing: 0.5px; text-transform: uppercase; }}
        .header p {{ margin: 0; color: #cbd5e1; font-size: 11px; }}
        .card {{
            background: #ffffff; border: 1px solid #cbd5e1; border-left-width: 4px;
            border-left-style: solid; padding: 8px 12px; margin-bottom: 8px; border-radius: 3px;
        }}
        .card h3 {{ margin: 0 0 4px 0; font-size: 13px; color: #0f172a; }}
        .meta {{ font-size: 10px; color: #64748b; font-weight: 700; margin-bottom: 4px; }}
        .cuerpo-box {{
            background: #f8fafc; border: 1px solid #e2e8f0; padding: 6px 8px;
            font-size: 11px; color: #1e293b; margin-bottom: 4px; text-align: justify;
        }}
        .analisis-box {{
            background: #f0f9ff; border-left: 3px solid #0284c7; padding: 5px 8px;
            font-size: 10.5px; color: #0c4a6e; margin-bottom: 4px;
        }}
        .link-button {{ color: #0284c7 !important; font-size: 10.5px; font-weight: 700; text-decoration: underline !important; }}
    </style>
</head>
<body>
    <div class="toolbar">
        <div><strong>Informe de Inteligencia</strong> &nbsp;|&nbsp; Fuentes Verificadas</div>
        <button class="btn-print" onclick="window.print()">🖨️ IMPRIMIR / GUARDAR PDF</button>
    </div>
    <div id="contenedor-reporte">
        <div class="header">
            <h1>BUSCADOR DE AMENAZAS HÍBRIDAS</h1>
            <p>{subtitulo_filtro} | Emisión: {fecha_actual} (Hora Peninsular) | Sistema de Vigilancia de Fuentes Abiertas</p>
        </div>"""

    for cat_cod, cat_nombre, cat_color in SECCIONES_INFORME:
        items_seccion = [a for a in alertas if a.get("categoria") == cat_cod]
        if not items_seccion:
            continue
        html += f"""
        <div style="background-color: {cat_color}; color:#fff; font-size:11.5px; font-weight:900; padding:5px 10px; margin:10px 0 6px 0; text-transform:uppercase;">
            {cat_nombre} ({len(items_seccion)})
        </div>"""
        for a in items_seccion:
            tit = html_module.escape(str(a.get('titular', '')))
            hecho = html_module.escape(str(a.get('hecho_reportado', '')))
            ana = html_module.escape(str(a.get('evaluacion_contexto', '')))
            fecha_str = obtener_fecha_madrid_str(a.get('fecha_dt'))
            canal = html_module.escape(str(a.get('canal', '')))
            enlace = html_module.escape(str(a.get('enlace', '#')))

            cuerpo_html = f'<div class="cuerpo-box"><strong>Reporte:</strong> {hecho}</div>' if a.get("categoria") in ["TELEGRAM", "REDES"] else ""

            html += f"""
            <div class="card" style="border-left-color: {cat_color};">
                <div class="meta">📅 {fecha_str} | 📢 {canal}</div>
                <h3>{tit}</h3>
                {cuerpo_html}
                <div class="analisis-box"><strong>Evaluación:</strong> {ana}</div>
                <div><a href="{enlace}" target="_blank" class="link-button">🔗 Fuente original en {canal} &rarr;</a></div>
            </div>"""
    html += "</div></body></html>"
    return html

# ==========================================
# CALLBACKS DE CONTROL
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
# ESTILOS VISUALES Y CABECERA LIMPIA
# ==========================================
st.markdown("""
<style>
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
    .osint-title { color: #0f172a; font-size: 17.5px; font-weight: 850; margin: 4px 0 8px 0; line-height: 1.35; }
    .osint-resumen {
        background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
        padding: 10px 14px; color: #1e293b; font-size: 14px; line-height: 1.55;
        text-align: justify; margin-bottom: 8px;
    }
    .osint-analisis {
        background-color: #f0f9ff; border-left: 4px solid #0284c7; padding: 8px 12px;
        color: #0c4a6e; font-size: 13.5px; line-height: 1.5; margin-bottom: 10px; border-radius: 0 4px 4px 0;
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
    .btn-video {
        background-color: #0284c7; color: #ffffff !important; padding: 6px 14px;
        border-radius: 4px; font-size: 12px; font-weight: 700; text-decoration: none !important;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# LOGO TÁCTICO VECTORIAL DE ALTA DEFINICIÓN
# ==========================================
logo_svg = """<svg width="68" height="68" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
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
    <!-- Base Circular Táctica -->
    <circle cx="50" cy="50" r="47" fill="url(#cyberBg)" stroke="#334155" stroke-width="2" />
    <circle cx="50" cy="50" r="40" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.6" />
    <circle cx="50" cy="50" r="30" stroke="#0ea5e9" stroke-width="0.8" opacity="0.4" />
    <!-- Retícula Polar -->
    <line x1="50" y1="3" x2="50" y2="97" stroke="#38bdf8" stroke-width="0.8" opacity="0.35" />
    <line x1="3" y1="50" x2="97" y2="50" stroke="#38bdf8" stroke-width="0.8" opacity="0.35" />
    <!-- Escudo Defensivo Angulado -->
    <path d="M50 14 L75 25 V52 C75 69 50 84 50 84 C50 84 25 69 25 52 V25 Z" fill="#0f172a" stroke="url(#neonGlow)" stroke-width="2.5" filter="url(#glow)" />
    <!-- Rayo de Intervención Rápida -->
    <polygon points="53,24 37,48 48,48 43,72 61,44 51,44" fill="url(#coreBolt)" filter="url(#glow)" />
</svg>"""

cabecera_html = f"""
<div class="tactical-header">
    <div>{logo_svg}</div>
    <div class="header-titles">
        <h1>BUSCADOR DE AMENAZAS HÍBRIDAS</h1>
        <p>SISTEMA DE VIGILANCIA DE FUENTES ABIERTAS</p>
    </div>
</div>
"""
st.markdown(cabecera_html, unsafe_allow_html=True)

# Arranque inicial con traducción activa
if "arranque_inicial_ok" not in st.session_state:
    with st.spinner("Sincronizando fuentes abiertas en tiempo real..."):
        ejecutar_actualizacion_total(activar_traduccion=True)
    st.session_state.arranque_inicial_ok = True
    st.rerun()

if "seleccionadas_pdf" not in st.session_state:
    st.session_state.seleccionadas_pdf = set()

alertas_totales = st.session_state.get("alertas_monitor", [])

# ==========================================
# BARRA LATERAL (FILTROS BOOLEANOS Y RANGO TEMPORAL)
# ==========================================
with st.sidebar:
    st.subheader("🔍 Filtros de Búsqueda")
    busqueda_txt = st.text_input(
        "Búsqueda Booleana (AND, OR, NOT, -):",
        placeholder="Ej: Destinus AND drones / Indra OR Navantia / sabotaje -incendio"
    )

    rango_tiempo = st.selectbox(
        "📅 Ventana Temporal de Vigilancia:",
        ["Últimas 24 Horas", "Últimas 48 Horas", "Últimos 5 Días", "Histórico Completo"],
        index=2
    )

    canales_disponibles = sorted(list({a["canal"] for a in alertas_totales}))
    canales_sel = st.multiselect("Filtrar por canal/fuente:", canales_disponibles, default=[])

    st.write("---")
    st.subheader("📊 Exportación")
    csv_bytes = exportar_alertas_csv(alertas_totales)
    st.download_button(
        "📊 BASE DE DATOS COMPLETA (CSV)",
        data=csv_bytes,
        file_name=f"alertas_osint_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True
    )

# Filtrado por Rango Temporal Flexible
ahora_utc = datetime.now(timezone.utc)
if rango_tiempo == "Últimas 24 Horas":
    datos_temporales = [x for x in alertas_totales if (ahora_utc - x["fecha_dt"].astimezone(timezone.utc)) <= timedelta(hours=24)]
elif rango_tiempo == "Últimas 48 Horas":
    datos_temporales = [x for x in alertas_totales if (ahora_utc - x["fecha_dt"].astimezone(timezone.utc)) <= timedelta(hours=48)]
elif rango_tiempo == "Últimos 5 Días":
    datos_temporales = [x for x in alertas_totales if (ahora_utc - x["fecha_dt"].astimezone(timezone.utc)) <= timedelta(days=5)]
else:
    datos_temporales = alertas_totales

datos_filtrados = datos_temporales

# Filtrado Booleano Real
if busqueda_txt:
    datos_filtrados = [
        x for x in datos_filtrados
        if cumple_busqueda_booleana(f"{x['titular']} {x['hecho_reportado']}", busqueda_txt)
    ]

# Filtrado por canal
if canales_sel:
    datos_filtrados = [x for x in datos_filtrados if x["canal"] in canales_sel]

# ==========================================
# BOTONERA SUPERIOR
# ==========================================
col_btn1, col_trad, col_btn2, col_btn_md, col_info = st.columns([1.1, 1.0, 1.2, 1.2, 1.6])

with col_btn1:
    if st.button("🔄 ACTUALIZAR FUENTES", type="primary", use_container_width=True):
        with st.spinner("Sincronizando fuentes abiertas en tiempo real..."):
            ejecutar_actualizacion_total(activar_traduccion=st.session_state.get("check_traducir", True))
        st.rerun()

with col_trad:
    st.checkbox("🌐 Auto-Traducir", value=True, key="check_traducir", help="Desactívalo si deseas una sincronización ultra-veloz en idioma original.")

alertas_para_informe = [x for x in datos_filtrados if x.get("id") in st.session_state.seleccionadas_pdf]

with col_btn2:
    if "alertas_monitor" in st.session_state:
        st.download_button(
            f"📥 INFORME PDF ({len(alertas_para_informe)})",
            data=generar_html_ejecutivo(alertas_para_informe, ventana_str=rango_tiempo.upper()),
            file_name=f"Informe_Amenazas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.html",
            mime="text/html",
            use_container_width=True
        )

with col_btn_md:
    if "alertas_monitor" in st.session_state:
        st.download_button(
            f"📝 INFORME WORD/MD ({len(alertas_para_informe)})",
            data=generar_informe_markdown(alertas_para_informe),
            file_name=f"Informe_Amenazas_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
            use_container_width=True
        )

with col_info:
    if "ultima_actualizacion" in st.session_state:
        st.markdown(f'<div style="font-size:12.5px;color:#94a3b8;margin-top:4px;">🕒 <strong>Última sincronización:</strong> {st.session_state.ultima_actualizacion} (Madrid)<br>Mostrando <strong>{len(datos_filtrados)}</strong> alertas consolidadas ({rango_tiempo}).</div>', unsafe_allow_html=True)

ids_visibles = [x["id"] for x in datos_filtrados]
c_sel1, c_sel2, _ = st.columns([1.2, 1.2, 4.0])

with c_sel1:
    st.button("✅ Seleccionar todas", use_container_width=True, on_click=marcar_todas_callback, args=(ids_visibles,))

with c_sel2:
    st.button("⬜ Desmarcar todas", use_container_width=True, on_click=desmarcar_todas_callback, args=(ids_visibles,))

st.write("")

# ==========================================
# RENDERIZADO DE TARJETAS AVANZADAS
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros seleccionados para esta sección.")
        return
    for item in lista_items:
        item_id = item.get("id")
        cat = item.get("categoria", "GLOBAL")
        canal_nombre = item.get("canal", "")
        es_desinfo = canal_nombre.replace("@", "") in CANALES_DESINFORMACION_ESP

        b_color = {
            "ESPANA": "#dc2626", "EMPRESA": "#d97706", "GLOBAL": "#0f172a",
            "CRIMENES": "#991b1b", "CRIPTO": "#7c3aed", "SANCIONES": "#b45309",
            "DETENCION": "#be123c", "CHINA": "#1e3a8a", "TELEGRAM": "#0284c7", "REDES": "#059669"
        }.get(cat, "#0f172a")

        badges = ""
        if (ahora_utc - item.get("fecha_dt").astimezone(timezone.utc)) <= timedelta(hours=24):
            badges += '<span class="badge-item" style="background:#fee2e2;color:#991b1b;border:1px solid #f87171;">⚡ ÚLTIMAS 24H</span>'
        elif (ahora_utc - item.get("fecha_dt").astimezone(timezone.utc)) <= timedelta(hours=48):
            badges += '<span class="badge-item" style="background:#fef08a;color:#854d0e;border:1px solid #eab308;">🔥 48H</span>'

        if es_desinfo:
            sep = 'margin-left:6px;' if badges else ''
            badges += f'<span class="badge-item" style="background:#ffe4e6;color:#be123c;border:1px solid #fda4af;{sep}">📢 DESINFORMACIÓN / FIMI</span>'

        correlacionados = item.get("canales_correlacionados", [])
        if len(correlacionados) > 1:
            sep = 'margin-left:6px;' if badges else ''
            badges += f'<span class="badge-item" style="background:#e0e7ff;color:#3730a3;border:1px solid #c7d2fe;{sep}">🔄 DIFUNDIDO EN {len(correlacionados)} MEDIOS</span>'

        pildoras_ner = ""
        for nombre_ent, color_ent in item.get("etiquetas_ner", []):
            pildoras_ner += f'<span class="ner-pill" style="background-color:{color_ent};">{html_module.escape(nombre_ent)}</span>'

        fecha_str = obtener_fecha_madrid_str(item.get("fecha_dt"))
        t_safe = html_module.escape(str(item.get("titular", "")))
        h_safe = html_module.escape(str(item.get("hecho_reportado", "")))
        a_safe = html_module.escape(str(item.get("evaluacion_contexto", "")))
        canal_safe = html_module.escape(str(canal_nombre))
        enlace = item.get("enlace", "#")
        if not es_url_segura(enlace):
            enlace = "#"

        media_html = f'<div class="osint-media"><img src="{html_module.escape(item.get("multimedia"))}" alt="Evidencia" loading="lazy" /></div>' if item.get("multimedia") and es_url_segura(item.get("multimedia")) else ""
        vid_html = f'<a href="{html_module.escape(enlace)}" target="_blank" class="btn-video">▶️ VER MULTIMEDIA</a>' if item.get("tiene_video") else ""

        bloque_reporte = f'<div class="osint-resumen"><strong>📄 Reporte/Post:</strong><br>{h_safe}</div>' if cat in ["TELEGRAM", "REDES"] else ""

        badge_header = f'<div style="margin-bottom:8px;">{badges}<span style="font-size:12px;color:#64748b;margin-left:8px;">📅 {fecha_str} | 📢 {canal_safe}</span></div>' if badges else f'<div style="margin-bottom:8px;"><span style="font-size:12px;color:#64748b;">📅 {fecha_str} | 📢 {canal_safe}</span></div>'

        texto_copia = f"🚨 ALERTA OSINT | {cat}\nTitular: {item.get('titular', '')}\nFuente: {canal_nombre} | Fecha: {fecha_str} (Madrid)\nEnlace: {enlace}"
        b64_copia = base64.b64encode(texto_copia.encode('utf-8')).decode('utf-8')

        tarjeta_html = (
            f'<div class="osint-card" style="border-left: 8px solid {b_color};">'
            f'<div class="osint-flex">{media_html}'
            f'<div class="osint-body">'
            f'{badge_header}'
            f'<div class="osint-title">{t_safe}</div>'
            f'<div style="margin-bottom:8px;">{pildoras_ner}</div>'
            f'{bloque_reporte}'
            f'<div class="osint-analisis"><strong>🧠 Análisis:</strong> {a_safe}</div>'
            f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">'
            f'<a href="{enlace}" target="_blank" class="btn-source">🔗 ABRIR FUENTE ORIGINAL</a>'
            f'<button class="btn-copy" onclick="window.parent.copiarAlerta(\'{b64_copia}\')">📋 COPIAR ALERTA</button>'
            f'{vid_html}'
            f'</div>'
            f'</div></div></div>'
        )

        col_check, col_card = st.columns([0.05, 0.95])
        with col_check:
            if f"sel_{item_id}" not in st.session_state:
                st.session_state[f"sel_{item_id}"] = (item_id in st.session_state.seleccionadas_pdf)

            st.checkbox(
                "Incluir en informe",
                key=f"sel_{item_id}",
                on_change=toggle_checkbox_callback,
                args=(item_id,),
                label_visibility="collapsed"
            )

        with col_card:
            st.markdown(tarjeta_html, unsafe_allow_html=True)
            
            fuentes_sec = item.get("fuentes_coincidentes", [])
            if fuentes_sec:
                with st.expander(f"📑 Ver {len(fuentes_sec)} cobertura(s) adicional(es) de este mismo evento"):
                    for f_sec in fuentes_sec:
                        st.markdown(f"- **{f_sec['canal']}:** [{f_sec['titular']}]({f_sec['enlace']})")

# ==========================================
# PESTAÑAS (ORDEN OPERATIVO)
# ==========================================
if "alertas_monitor" in st.session_state:
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
        f"⚖️ CRÍMENES DE GUERRA Y TERRORISMO ({len(l_crim)})",
        f"🟣 CRIPTO ({len(l_cripto)})",
        f"📦 SANCIONES ({len(l_sanc)})",
        f"🛑 DETENCIONES ({len(l_det)})",
        f"🇨🇳 CHINA ({len(l_china)})",
        f"✈️ TELEGRAM ({len(l_tg)})",
        f"🌐 REDES SOCIALES ({len(l_redes)})"
    ])
    with t1:
        renderizar_tarjetas(l_esp)
    with t2:
        renderizar_tarjetas(l_emp)
    with t3:
        renderizar_tarjetas(l_prensa)
    with t4:
        renderizar_tarjetas(l_crim)
    with t5:
        renderizar_tarjetas(l_cripto)
    with t6:
        renderizar_tarjetas(l_sanc)
    with t7:
        renderizar_tarjetas(l_det)
    with t8:
        renderizar_tarjetas(l_china)
    with t9:
        renderizar_tarjetas(l_tg)
    with t10:
        renderizar_tarjetas(l_redes)
