import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import re
import base64
import html as html_module
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse, quote
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ==========================================
# CONFIGURACIÓN Y CREDENCIALES
# ==========================================
SESION_HTTP = requests.Session()
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,de;q=0.6"
})

EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR")
PASSWORD_EMISOR = os.environ.get("PASSWORD_EMISOR")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO")

CACHE_TITULARES_GLOBAL = {}

# ==========================================
# FUNCIONES DE LIMPIEZA Y FECHAS
# ==========================================
def limpiar_html_texto(texto):
    if not texto:
        return ""
    t = re.sub(r'<[^<]+?>', ' ', str(texto))
    t = html_module.unescape(t)
    t = re.sub(r'&[a-zA-Z0-9#]+;', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

def normalizar_texto(texto):
    if not texto:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', str(texto))
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()

def limpiar_firma_medio(texto):
    return re.sub(r'\s*[-–|·]\s*[^-–|·]{2,45}$', '', str(texto)).strip()

def limpiar_url(url):
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
    except Exception:
        return url

def es_reciente_48h(fecha_dt):
    if not fecha_dt:
        return False
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    diferencia = ahora - fecha_dt.astimezone(timezone.utc)
    return timedelta(hours=-24) <= diferencia <= timedelta(hours=48)

def contiene_cirilico(texto):
    return bool(re.search(r'[\u0400-\u04FF]', str(texto)))

# ==========================================
# DESCODIFICACIÓN OFFLINE DE GOOGLE NEWS
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
# TRADUCTOR MULTISOURCE
# ==========================================
def traducir_solo_titular(titular_orig):
    if not titular_orig:
        return ""
    t_clean = limpiar_html_texto(titular_orig)
    if len(t_clean) < 4:
        return t_clean

    if t_clean in CACHE_TITULARES_GLOBAL:
        return CACHE_TITULARES_GLOBAL[t_clean]

    medio_suffix = ""
    texto_a_traducir = t_clean
    m_medio = re.search(r'(\s*[-–|]\s*[^-–|]{2,40})$', t_clean)
    if m_medio:
        medio_suffix = m_medio.group(1)
        texto_a_traducir = t_clean[:m_medio.start()].strip()

    # Intento 1: API MyMemory
    try:
        url_my = f"https://api.mymemory.translated.net/get?q={quote(texto_a_traducir[:350])}&langpair=autodetect|es"
        r = SESION_HTTP.get(url_my, timeout=3.5)
        if r.status_code == 200:
            datos = r.json()
            if "responseData" in datos and datos["responseData"].get("translatedText"):
                trad = datos["responseData"]["translatedText"].strip()
                if trad and "MYMEMORY WARNING" not in trad.upper():
                    res = f"{trad}{medio_suffix}" if medio_suffix else trad
                    CACHE_TITULARES_GLOBAL[t_clean] = res
                    return res
    except Exception:
        pass

    # Intento 2: Google endpoint directo
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_a_traducir[:350]}
        r = SESION_HTTP.get(url, params=params, timeout=2.5)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                partes = [seg[0] for seg in data[0] if isinstance(seg, list) and len(seg) > 0 and seg[0]]
                trad = limpiar_html_texto("".join(partes))
                if len(trad) > 3:
                    resultado = f"{trad}{medio_suffix}" if medio_suffix else trad
                    CACHE_TITULARES_GLOBAL[t_clean] = resultado
                    return resultado
    except Exception:
        pass

    CACHE_TITULARES_GLOBAL[t_clean] = t_clean
    return t_clean

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
# FILTROS Y DICCIONARIOS DE INTELIGENCIA
# ==========================================
EMPRESAS_ESTRATEGICAS_ESP = [
    "indra", "grupo oesia", "oesia", "uav navigation", "tecnobit", "navantia",
    "escribano", "em&e", "sener", "arquimea", "destinus", "santa barbara", "gdels",
    "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa", "itp aero",
    "airbus espana", "airbus getafe", "hisdesat", "hispasat", "sateliot", "pld space",
    "amadeus", "aena", "enaire", "renfe", "adif", "telefonica", "iberdrola",
    "repsol", "naturgy", "redeia", "red electrica", "enagas", "talgo"
]

ACTORES_RUSIA_ESTRICTOS = [
    "rusia", "ruso", "rusa", "rusos", "rusas", "moscu", "kremlin", "putin",
    "russia", "russian", "moscow", "russie", "russe", "moscou", "russland", "russisch", "moskau",
    "gru", "fsb", "svr", "apt28", "apt29", "fancy bear", "cozy bear", "sandworm",
    "noname057", "killnet", "turla", "gamaredon", "cyberarmyofrussia", "garantex", "wagner",
    "bielorrusia", "belarus", "kaliningrad", "lukashenko",
    "ucrania", "ukraine", "ukrainian", "kiev", "kyiv", "donbas", "crimea", "bucha", "mariupol",
    "kursk", "belgorod", "zaporiyia", "zaporizhzhia", "kherson", "donetsk", "jarkov", "kharkiv", "odesa"
]

def es_publicidad_o_spam(texto):
    t = normalizar_texto(texto)
    spam = ["master profesional", "lisa institute", "udima", "beca", "matriculate", "curso online", "descuento", "sorteo", "casino", "apuestas", "tarot", "horoscopo", "oferta de empleo"]
    return any(s in t for s in spam)

def tiene_vinculo_ruso_estricto(texto):
    if not texto or es_publicidad_o_spam(texto):
        return False
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    raices_cirilico = [
        "росси", "рф", "москв", "кремл", "путин", "гру", "фсб", "свр",
        "украин", "киев", "київ", "зсу", "всу", "сбу", "гур", "донбасс",
        "крым", "беларус", "вагнер", "шахед", "геран", "искандер"
    ]
    if any(c in t_raw for c in raices_cirilico):
        return True

    return any(r in t_norm for r in ACTORES_RUSIA_ESTRICTOS)

def tiene_vector_amenaza_estricto(texto):
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    vectores_cirilico = [
        "диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид",
        "крипт", "биткоин", "usdt", "бпла", "дron", "дрон", "рэб", "арест", "задержан", "санкц", "кабел", "флот",
        "казн", "пленн", "военн", "преступлен", "поджог", "теракт"
    ]
    if any(c in t_raw for c in vectores_cirilico):
        return True

    vectores_latinos = [
        "hibrid", "hybrid", "sabotaj", "sabotage", "saboteador", "espia", "espionaje", "spy", "espionage", "spying",
        "ciber", "cyber", "hack", "ddos", "malware", "ransomware", "apt28", "apt29", "sandworm", "noname057", "killnet",
        "desinformacion", "disinformation", "propaganda", "injerencia",
        "cripto", "crypto", "bitcoin", "btc", "usdt", "tether", "garantex", "sancion", "sanction", "elusion",
        "flota fantasma", "shadow fleet", "cable submarino", "undersea cable", "petrolero",
        "crimen de guerra", "war crime", "corte penal internacional", "icc", "genocidio", "tortura", "pow execution",
        "detenido", "arrestado", "arrested", "detained", "dron", "uav", "guerra electronica", "jamming", "arson", "incendio provocado"
    ]
    return any(v in t_norm for v in vectores_latinos)

def es_crimen_guerra(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(crimen de guerra|crimenes de guerra|war crime|war crimes|corte penal internacional|tribunal penal|lesa humanidad|genocidio|pow execution|tortura|bucha|mariupol|fosa comun|ejecucion de prisioneros)\b'
    return bool(re.search(patron, t_all))

def es_detencion(titular, cuerpo):
    t_tit = normalizar_texto(titular)
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron_det = r'\b(detenido|detenidos|arrestado|arrestados|arrested|detained|festnahme|capturado)\b'
    patron_motivo = r'\b(espia|spy|espionage|espionaje|sabotaje|sabotage|gru|fsb|svr|ciber|hacker|infiltrado|traicion|treason)\b'
    return bool(re.search(patron_det, t_tit)) and bool(re.search(patron_motivo, t_all))

def es_cripto(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(cripto|criptomoneda|crypto|usdt|tether|btc|bitcoin|monero|garantex|blockchain|sancion|sanction|finint)\b'
    return bool(re.search(patron, t_all))

def es_empresa_esp(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    return any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP)

def es_espana(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(espana|spain|madrid|barcelona|cni|incibe|ccn-cert|audiencia nacional|guardia civil|policia nacional|rota|torrejon|enaire|cartagena|gobierno espanol|ministerio de defensa|puertos espanoles)\b'
    return bool(re.search(patron, t_all))

def generar_analisis_contexto(categoria):
    if categoria == "CRIMENES":
        return "Documentación de crímenes de guerra, ataques a infraestructuras civiles e investigaciones judiciales internacionales."
    if categoria == "EMPRESA":
        return "Seguimiento sobre empresas estratégicas de España (Defensa, Aeroespacial, Energía y Telecomunicaciones)."
    if categoria == "ESPANA":
        return "Impacto directo en la seguridad nacional e infraestructuras críticas de España frente a operaciones hostiles."
    if categoria == "CRIPTO":
        return "Inteligencia financiera (FININT): evasión de sanciones internacionales y uso de criptoactivos no regulados."
    if categoria == "DETENCION":
        return "Operaciones de contrainteligencia y policiales frente a redes de espionaje o sabotaje."
    if categoria == "TELEGRAM":
        return "Reporte táctico de canales primarios de Telegram: inteligencia operativa de la zona de conflicto."
    return "Actividad vinculada a operaciones híbridas globales del Kremlin: ciberataques, sabotajes o desinformación."

# ==========================================
# RASTREADORES
# ==========================================
def raspar_rumafia():
    items = []
    try:
        url = "https://www.rumafia.net/en/rss/"
        r = SESION_HTTP.get(url, timeout=6.0)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:20]:
                tit = limpiar_html_texto(item.findtext("title", ""))
                desc = limpiar_html_texto(item.findtext("description", ""))
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")
                try:
                    dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)

                if not es_reciente_48h(dt):
                    continue

                texto_eval = f"{tit} {desc}"
                if len(tit) > 10 and tiene_vector_amenaza_estricto(texto_eval):
                    items.append({
                        "plataforma": "INVESTIGACIÓN",
                        "canal": "RuMafia.net",
                        "fecha_dt": dt,
                        "titular_raw": tit,
                        "cuerpo_raw": desc if len(desc) > 20 else tit,
                        "enlace": link
                    })
    except Exception:
        pass
    return items

def raspar_antikor():
    items = []
    try:
        url = "https://antikor.com.ua/articles.rss"
        r = SESION_HTTP.get(url, timeout=6.0)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:20]:
                tit = limpiar_html_texto(item.findtext("title", ""))
                desc = limpiar_html_texto(item.findtext("description", ""))
                link = item.findtext("link", "")
                pub = item.findtext("pubDate", "")
                try:
                    dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)

                if not es_reciente_48h(dt):
                    continue

                texto_eval = f"{tit} {desc}"
                if len(tit) > 10 and (tiene_vinculo_ruso_estricto(texto_eval) and tiene_vector_amenaza_estricto(texto_eval)):
                    items.append({
                        "plataforma": "INVESTIGACIÓN",
                        "canal": "Antikor Portal",
                        "fecha_dt": dt,
                        "titular_raw": tit,
                        "cuerpo_raw": desc if len(desc) > 20 else tit,
                        "enlace": link
                    })
    except Exception:
        pass
    return items

def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=6.0)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.find_all("div", class_="tgme_widget_message_wrap")

        for p in posts[-30:]:
            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""

            if len(texto_raw) < 25 or es_publicidad_o_spam(texto_raw):
                continue

            if not (tiene_vinculo_ruso_estricto(texto_raw) and tiene_vector_amenaza_estricto(texto_raw)):
                continue

            texto_sin_urls = limpiar_html_texto(re.sub(r'http\S+', '', texto_raw))
            if len(texto_sin_urls) < 20:
                continue

            time_tag = p.find("time")
            dt_obj = datetime.now(timezone.utc)
            if time_tag and time_tag.get("datetime"):
                try:
                    dt_obj = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass

            if not es_reciente_48h(dt_obj):
                continue

            a_link = p.find("a", class_="tgme_widget_message_date")
            link = a_link["href"] if a_link and a_link.get("href") else f"https://t.me/s/{canal}"

            # Exactamente como en tu Streamlit: primera frase entre 20 y 160 caracteres
            primera_frase = re.split(r'(?<=[.!?])\s+', texto_sin_urls)[0]
            tit_corto = primera_frase if 20 <= len(primera_frase) <= 160 else texto_sin_urls[:140]

            mensajes.append({
                "plataforma": "TELEGRAM",
                "canal": f"@{canal}",
                "fecha_dt": dt_obj,
                "titular_raw": tit_corto,
                "cuerpo_raw": texto_sin_urls[:350],
                "enlace": link
            })
    except Exception:
        pass
    return mensajes

def raspar_telegram_masivo():
    canales = [
        "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "entre_guerras", "rybar", "dva_majors", "vchkogpu",
        "meduzalive", "astrapress", "istories_media",
        "ostorozhno_novosti", "nvua_official", "operativnoZSU"
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

def procesar_un_feed_rss(datos_feed):
    url, etiqueta = datos_feed
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=6.0)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        for item in root.findall(".//item")[:25]:
            tit_orig = limpiar_html_texto(item.findtext("title", ""))
            desc_orig = item.findtext("description", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")

            if len(tit_orig) < 15 or es_publicidad_o_spam(tit_orig + " " + desc_orig):
                continue

            tit_sin_medio = limpiar_firma_medio(tit_orig)
            cuerpo_crudo = extraer_resumen_rss(tit_orig, desc_orig)
            texto_eval = f"{tit_sin_medio} {cuerpo_crudo}"

            if not tiene_vinculo_ruso_estricto(texto_eval) or not tiene_vector_amenaza_estricto(texto_eval):
                continue

            try:
                dt_obj = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception:
                dt_obj = datetime.now(timezone.utc)

            if not es_reciente_48h(dt_obj):
                continue

            url_destino = decodificar_url_google_news_offline(link)

            items.append({
                "plataforma": "PRENSA",
                "canal": etiqueta,
                "fecha_dt": dt_obj,
                "titular_raw": tit_orig,
                "cuerpo_raw": cuerpo_crudo,
                "enlace": url_destino
            })
    except Exception:
        pass
    return items

def barrido_prensa_multilingue():
    fuentes_rss = [
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin+OR+NoName057+OR+APT28)+(España+OR+Madrid+OR+CNI+OR+INCIBE)+(sabotaje+OR+ciberataque+OR+espionaje+OR+amenaza)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Russian+OR+Kremlin)+(Spain+OR+Madrid)+(sabotage+OR+cyberattack+OR+spy+OR+espionage)&hl=en-US&gl=US&ceid=US:en", "Spain Watch (EN)"),
        ("https://news.google.com/rss/search?q=(Indra+OR+Navantia+OR+Escribano+OR+Sener+OR+Telefónica+OR+Iberdrola)+(Rusia+OR+ciberataque+OR+sabotaje+OR+hackers)&hl=es&gl=ES&ceid=ES:es", "Empresas España (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(sabotage+OR+cyberattack+OR+espionage+OR+\"hybrid+warfare\"+OR+partisan)&hl=en-US&gl=US&ceid=US:en", "Intel Global (EN)"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+Ucrania)+(sabotaje+OR+ciberataque+OR+espionaje+OR+\"guerra+hibrida\")&hl=es&gl=ES&ceid=ES:es", "Frente Global (ES)"),
        ("https://news.google.com/rss/search?q=(Russland+OR+Ukraine)+(Sabotage+OR+Spionage+OR+Cyberangriff)&hl=de&gl=DE&ceid=DE:de", "Intel Europa Central (DE)"),
        ("https://news.google.com/rss/search?q=(Russie+OR+Ukraine)+(sabotage+OR+espionnage+OR+cyberattaque)&hl=fr&gl=FR&ceid=FR:fr", "Intel Europa (FR)"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT+OR+Tether)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)"),
        ("https://news.google.com/rss/search?q=(criptomonedas+OR+Bitcoin+OR+USDT)+(Rusia+OR+sanciones+OR+Garantex)&hl=es&gl=ES&ceid=ES:es", "FININT Cripto (ES)"),
        ("https://news.google.com/rss/search?q=(arrested+OR+detained)+(Russian+spy+OR+Russian+sabotage+OR+GRU+OR+FSB)&hl=en-US&gl=US&ceid=US:en", "Detenciones (EN)"),
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado)+(espia+ruso+OR+sabotaje+ruso+OR+GRU+OR+FSB)&hl=es&gl=ES&ceid=ES:es", "Detenciones (ES)"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+Putin+OR+Ucrania)+(\"crimenes+de+guerra\"+OR+\"crimen+de+guerra\"+OR+\"Corte+Penal+Internacional\")&hl=es&gl=ES&ceid=ES:es", "Crímenes Guerra (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(\"war+crimes\"+OR+\"war+crime\"+OR+ICC)&hl=en-US&gl=US&ceid=US:en", "War Crimes (EN)")
    ]
    todas = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futuros = [ex.submit(procesar_un_feed_rss, f) for f in fuentes_rss]
        for f in as_completed(futuros):
            try:
                todas.extend(f.result())
            except Exception:
                pass
    return todas

# ==========================================
# COMPARADOR ANTI-DUPLICADOS (IDÉNTICO A STREAMLIT)
# ==========================================
def son_mismo_evento(tit1, tit2):
    t1 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit1)))
    t2 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit2)))
    if len(t1) < 15 or len(t2) < 15:
        return False
    # Umbral exacto de tu web para consolidar noticias de diferentes periódicos
    return SequenceMatcher(None, t1[:95], t2[:95]).ratio() > 0.68

# ==========================================
# GENERACIÓN DE HTML Y ENVÍO POR CORREO
# ==========================================
SECCIONES_INFORME = [
    ("ESPANA", "SECCIÓN 1: ESPAÑA E INFRAESTRUCTURAS CRÍTICAS", "#dc2626"),
    ("EMPRESA", "SECCIÓN 2: EMPRESAS ESTRATÉGICAS DE ESPAÑA Y DEFENSA", "#d97706"),
    ("GLOBAL", "SECCIÓN 3: RUSIA, UCRANIA Y AMENAZAS GLOBALES", "#0f172a"),
    ("CRIMENES", "SECCIÓN 4: CRÍMENES DE GUERRA Y DERECHOS HUMANOS", "#991b1b"),
    ("CRIPTO", "SECCIÓN 5: CRIPTOACTIVOS Y FINANCIACIÓN ILÍCITA", "#7c3aed"),
    ("DETENCION", "SECCIÓN 6: DETENCIONES POR ESPIONAJE Y SABOTAJE", "#be123c"),
    ("TELEGRAM", "SECCIÓN 7: TELEGRAM OSINT Y CANALES TÁCTICOS", "#0284c7")
]

def generar_html_email(alertas):
    fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M")
    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f8fafc; padding:16px; color:#0f172a;">
        <div style="max-width:760px; margin:0 auto; background:#ffffff; padding:20px; border-radius:6px; border:1px solid #cbd5e1;">
            <div style="background:#0f172a; color:#ffffff; padding:14px; border-left:6px solid #dc2626; border-radius:4px; margin-bottom:16px;">
                <h2 style="margin:0 0 4px 0; font-size:17px; text-transform:uppercase;">ANALIZADOR DE AMENAZAS HÍBRIDAS</h2>
                <p style="margin:0; font-size:12px; color:#cbd5e1;">INFORME OPERATIVO EXCLUSIVO (ÚLTIMAS 48 HORAS) | Emisión: {fecha_actual} | Alertas consolidadas: {len(alertas)}</p>
            </div>
    """
    for cat_cod, cat_nombre, cat_color in SECCIONES_INFORME:
        items_seccion = [a for a in alertas if a.get("categoria") == cat_cod]
        if not items_seccion:
            continue
        html += f"""
        <div style="background:{cat_color}; color:#ffffff; padding:6px 10px; font-size:12px; font-weight:800; border-radius:3px; margin:14px 0 8px 0; text-transform:uppercase;">
            {cat_nombre} ({len(items_seccion)})
        </div>
        """
        for a in items_seccion:
            tit = html_module.escape(str(a.get('titular', '')))
            ana = html_module.escape(str(a.get('evaluacion_contexto', '')))
            fecha_str = a.get('fecha_dt').strftime('%d/%m/%Y %H:%M')
            canal = html_module.escape(str(a.get('canal', '')))
            enlace = html_module.escape(str(a.get('enlace', '#')))
            
            # Botón adicional para abrir traducción directa en Google Translate con un clic
            texto_traducir_url = quote(str(a.get('titular_original', '')))
            btn_traducir = f"""&nbsp;|&nbsp;<a href="https://translate.google.com/?sl=auto&tl=es&text={texto_traducir_url}&op=translate" target="_blank" style="color:#0369a1; font-size:11px; font-weight:700; text-decoration:none;">🌐 Abrir en Traductor</a>""" if a.get("categoria") == "TELEGRAM" else ""

            html += f"""
            <div style="border:1px solid #e2e8f0; border-left:4px solid {cat_color}; padding:8px 12px; margin-bottom:8px; border-radius:3px; background:#ffffff;">
                <div style="font-size:10.5px; color:#64748b; font-weight:700; margin-bottom:4px;">
                    <span style="background:#fef08a; color:#854d0e; padding:1px 4px; border-radius:2px; border:1px solid #eab308; margin-right:4px;">🔥 48H</span>
                    📅 {fecha_str} UTC | 📢 {canal}
                </div>
                <div style="font-size:13.5px; font-weight:700; color:#0f172a; margin-bottom:6px; line-height:1.4;">
                    {tit}
                </div>
                <div style="font-size:11px; color:#0369a1; background:#f0f9ff; padding:5px 8px; border-radius:2px; margin-bottom:6px; border-left:3px solid #0284c7;">
                    <strong>🧠 Evaluación:</strong> {ana}
                </div>
                <div>
                    <a href="{enlace}" target="_blank" style="color:#0284c7; font-size:11px; font-weight:700;">🔗 Fuente / Noticia en {canal} &rarr;</a>
                    {btn_traducir}
                </div>
            </div>
            """
    html += "</div></body></html>"
    return html

def enviar_correo(html_content, total_alertas):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🛡️ Informe Amenazas Híbridas (48h): {total_alertas} Alertas - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    msg["From"] = EMAIL_EMISOR
    msg["To"] = EMAIL_DESTINO
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_EMISOR, PASSWORD_EMISOR)
        server.sendmail(EMAIL_EMISOR, EMAIL_DESTINO, msg.as_string())

def main():
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_prensa = ex.submit(barrido_prensa_multilingue)
        f_tg = ex.submit(raspar_telegram_masivo)
        f_rumafia = ex.submit(raspar_rumafia)
        f_antikor = ex.submit(raspar_antikor)

        brutos_prensa = f_prensa.result()
        brutos_tg = f_tg.result()
        brutos_rumafia = f_rumafia.result()
        brutos_antikor = f_antikor.result()

    todos_brutos = brutos_tg + brutos_prensa + brutos_rumafia + brutos_antikor
    todos_brutos.sort(key=lambda x: x["fecha_dt"], reverse=True)

    # FASE 1: Deduplicación por enlace y titular crudo (idéntica a Streamlit)
    pre_unicos = []
    urls_vistas = set()
    for b in todos_brutos:
        u = limpiar_url(b["enlace"])
        if u in urls_vistas:
            continue
        if any(son_mismo_evento(b["titular_raw"], ex["titular_raw"]) for ex in pre_unicos):
            continue
        urls_vistas.add(u)
        pre_unicos.append(b)

    if not pre_unicos:
        print("No se encontraron alertas en las últimas 48 horas.")
        return

    # FASE 2: Traducción con hilos
    with ThreadPoolExecutor(max_workers=8) as ex:
        titulares_traducidos = list(ex.map(lambda it: traducir_solo_titular(it["titular_raw"]), pre_unicos))

    # FASE 3: Clasificación y SEGUNDA deduplicación (evita duplicados Cripto y multilingües)
    alertas_finales = []
    for item, tit_es in zip(pre_unicos, titulares_traducidos):
        cuerpo_intacto = item["cuerpo_raw"]

        if item["plataforma"] == "TELEGRAM":
            canal_tag = item["canal"].replace("@", "").upper()
            titular_final = f"[{canal_tag}] {tit_es}"
            cat_final = "TELEGRAM"
        else:
            titular_final = tit_es
            tit_sin_medio = limpiar_firma_medio(titular_final)

            if es_crimen_guerra(tit_sin_medio, cuerpo_intacto):
                cat_final = "CRIMENES"
            elif es_detencion(tit_sin_medio, cuerpo_intacto):
                cat_final = "DETENCION"
            elif es_cripto(tit_sin_medio, cuerpo_intacto):
                cat_final = "CRIPTO"
            elif es_empresa_esp(tit_sin_medio, cuerpo_intacto):
                cat_final = "EMPRESA"
            elif es_espana(tit_sin_medio, cuerpo_intacto):
                cat_final = "ESPANA"
            else:
                cat_final = "GLOBAL"

        nueva_alerta = {
            "plataforma": item["plataforma"],
            "canal": item["canal"],
            "fecha_dt": item["fecha_dt"],
            "titular": titular_final,
            "titular_original": item["titular_raw"],
            "categoria": cat_final,
            "evaluacion_contexto": generar_analisis_contexto(cat_final),
            "enlace": item["enlace"]
        }

        # Segunda pasada de deduplicación sobre el titular ya traducido
        if not any(son_mismo_evento(nueva_alerta["titular"], ex["titular"]) for ex in alertas_finales):
            alertas_finales.append(nueva_alerta)

    if alertas_finales and EMAIL_EMISOR and PASSWORD_EMISOR and EMAIL_DESTINO:
        html = generar_html_email(alertas_finales)
        enviar_correo(html, len(alertas_finales))
        print(f"Correo enviado exitosamente con {len(alertas_finales)} alertas consolidadas.")
    else:
        print("No hay alertas recientes verificadas o faltan credenciales.")

if __name__ == "__main__":
    main()
