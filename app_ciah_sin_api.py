import streamlit as st
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
from urllib.parse import urlparse, urlunparse

# ==========================================
# CONFIGURACIÓN DE SESIÓN HTTP Y CACHÉ
# ==========================================
SESION_HTTP = requests.Session()
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,de;q=0.6"
})

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

def es_url_segura(url):
    return str(url).startswith("http://") or str(url).startswith("https://")

def es_reciente_24h(fecha_dt):
    if not fecha_dt:
        return False
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    diferencia = ahora - fecha_dt.astimezone(timezone.utc)
    return timedelta(hours=-24) <= diferencia <= timedelta(hours=24)

def es_reciente_48h(fecha_dt):
    if not fecha_dt:
        return False
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    diferencia = ahora - fecha_dt.astimezone(timezone.utc)
    return timedelta(hours=-24) <= diferencia <= timedelta(hours=48)

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
# TRADUCTOR DE TITULARES
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

CANALES_DESINFORMACION_ESP = {
    "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu"
}

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
        "desinformacion", "disinformation", "propaganda", "injerencia", "fimi",
        "cripto", "crypto", "bitcoin", "btc", "usdt", "tether", "garantex", "sancion", "sanction", "elusion",
        "flota fantasma", "shadow fleet", "cable submarino", "undersea cable", "petrolero",
        "crimen de guerra", "war crime", "corte penal internacional", "icc", "genocidio", "tortura", "pow execution",
        "detenido", "arrestado", "arrested", "detained", "dron", "uav", "guerra electronica", "jamming", "arson", "incendio provocado",
        "guerra", "ejercito", "militar", "otan", "nato", "frente", "misil", "ataque", "ofensiva", "bombardeo"
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

def generar_analisis_contexto(categoria, canal=""):
    if canal.replace("@", "") in CANALES_DESINFORMACION_ESP:
        return "Canal clasificado en la red de propaganda, desinformación e injerencia extranjera (FIMI) vinculada al ecosistema pro-Kremlin en español."
    if categoria == "CRIMENES":
        return "Documentación de crímenes de guerra, ataques a población o infraestructuras civiles e investigaciones judiciales internacionales en el contexto de la agresión rusa."
    if categoria == "EMPRESA":
        return "Seguimiento sobre empresas estratégicas de España (Defensa, Aeroespacial, Energía y Telecomunicaciones) ante ciberataques, sabotajes o señalamiento hostil ruso."
    if categoria == "ESPANA":
        return "Impacto directo en la seguridad nacional, ciberespacio o infraestructuras críticas de España frente a operaciones híbridas y de desestabilización vinculadas al Kremlin."
    if categoria == "CRIPTO":
        return "Inteligencia financiera (FININT): evasión de sanciones internacionales y empleo de criptoactivos (USDT, BTC) para financiación encubierta o plataformas sin regulación."
    if categoria == "DETENCION":
        return "Operaciones de contrainteligencia y policiales frente a redes de espionaje del GRU/FSB/SVR, células de sabotaje o agentes encubiertos."
    if categoria == "TELEGRAM":
        return "Reporte capturado de canales tácticos y de difusión en Telegram: inteligencia operativa y sobre el terreno de la zona de conflicto."
    return "Actividad vinculada a operaciones híbridas globales del Kremlin: ciberataques, guerra electrónica, sabotaje, flota fantasma o desinformación."

# ==========================================
# RASTREADORES RUMAFIA Y ANTIKOR
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

                texto_eval = f"{tit} {desc}"
                if len(tit) > 10 and tiene_vector_amenaza_estricto(texto_eval):
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

                texto_eval = f"{tit} {desc}"
                if len(tit) > 10 and (tiene_vinculo_ruso_estricto(texto_eval) and tiene_vector_amenaza_estricto(texto_eval)):
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
# RECOLECTOR TELEGRAM (SOLO RUSIA Y 24 HORAS)
# ==========================================
def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=6.0)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.find_all("div", class_="tgme_widget_message_wrap")

        for p in posts[-35:]:
            # 1. Filtro estricto de fecha: solo últimas 24 horas
            time_tag = p.find("time")
            dt_obj = datetime.now(timezone.utc)
            if time_tag and time_tag.get("datetime"):
                try:
                    dt_obj = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass

            if not es_reciente_24h(dt_obj):
                continue

            # 2. Filtro estricto de texto: OBLIGATORIO vínculo ruso para TODOS los canales
            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""

            if len(texto_raw) < 25 or es_publicidad_o_spam(texto_raw):
                continue

            if not tiene_vinculo_ruso_estricto(texto_raw):
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

            mensajes.append({
                "id": f"TG-{hash(link)}",
                "plataforma": "TELEGRAM",
                "canal": f"@{canal}",
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
        # Canales militares y tácticos OSINT
        "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "entre_guerras", "rybar", "dva_majors", "vchkogpu",
        "meduzalive", "astrapress", "istories_media",
        "ostorozhno_novosti", "nvua_official", "operativnoZSU",
        # Red de difusión en español (filtrados a 24h y tema ruso)
        "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu"
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
# RECOLECTOR RSS PRENSA
# ==========================================
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
# ACTUALIZACIÓN CENTRAL
# ==========================================
def son_mismo_evento(tit1, tit2):
    t1 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit1)))
    t2 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit2)))
    if len(t1) < 15 or len(t2) < 15:
        return False
    return SequenceMatcher(None, t1[:95], t2[:95]).ratio() > 0.68

def ejecutar_actualizacion_total():
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
        st.session_state.alertas_monitor = []
        st.session_state.seleccionadas_pdf = set()
        return

    with ThreadPoolExecutor(max_workers=10) as ex:
        titulares_traducidos = list(ex.map(lambda it: traducir_solo_titular(it["titular_raw"]), pre_unicos))

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
            "id": item["id"],
            "plataforma": item["plataforma"],
            "canal": item["canal"],
            "fecha_dt": item["fecha_dt"],
            "titular": titular_final,
            "titular_original": item["titular_raw"],
            "categoria": cat_final,
            "hecho_reportado": cuerpo_intacto,
            "evaluacion_contexto": generar_analisis_contexto(cat_final, canal=item["canal"]),
            "multimedia": item.get("multimedia"),
            "tiene_video": item.get("tiene_video", False),
            "enlace": item["enlace"]
        }

        if not any(son_mismo_evento(nueva_alerta["titular"], ex["titular"]) for ex in alertas_finales):
            alertas_finales.append(nueva_alerta)

    st.session_state.alertas_monitor = alertas_finales
    st.session_state.ultima_actualizacion = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
    st.session_state.seleccionadas_pdf = {a["id"] for a in alertas_finales}

# ==========================================
# INFORME IMPRIMIBLE (MAQUETACIÓN COMPACTA DIN A4)
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

def generar_html_ejecutivo(alertas, solo_48h=False):
    fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M")
    subtitulo_filtro = "INFORME OPERATIVO EXCLUSIVO (ÚLTIMAS 48 HORAS)" if solo_48h else "INFORME GENERAL CONSOLIDADO"
    
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Informe Analizador de Amenazas Híbridas</title>
    <style>
        @page {{
            size: A4 portrait;
            margin: 10mm 10mm 10mm 10mm;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            background-color: #ffffff;
            color: #0f172a;
            margin: 0;
            padding: 0;
            line-height: 1.35;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
            top: 0px !important;
            position: static !important;
        }}

        iframe.goog-te-banner-frame,
        .goog-te-banner-frame,
        #goog-gt-tt,
        .goog-te-spinner-pos {{
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
        }}

        @media screen {{
            body {{
                background-color: #f1f5f9;
                padding: 20px;
            }}
            #contenedor-reporte {{
                max-width: 860px;
                margin: 0 auto;
                background: #ffffff;
                padding: 26px;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }}
            .toolbar {{
                max-width: 860px;
                margin: 0 auto 16px auto;
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: #ffffff;
                padding: 12px 18px;
                border-radius: 8px;
                border: 1px solid #cbd5e1;
            }}
            .translate-wrapper {{
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 13.5px;
                font-weight: 700;
                color: #0f172a;
            }}
            .goog-te-gadget {{
                color: #475569 !important;
                font-size: 12px !important;
            }}
            .goog-te-combo {{
                padding: 5px 8px;
                font-size: 13px;
                font-weight: 600;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                background-color: #f8fafc;
                color: #0f172a;
                cursor: pointer;
            }}
            .btn-print {{
                background: #dc2626;
                color: #ffffff;
                border: none;
                padding: 10px 20px;
                font-size: 13.5px;
                font-weight: 800;
                border-radius: 6px;
                cursor: pointer;
            }}
        }}

        @media print {{
            body {{
                background: #ffffff !important;
                padding: 0 !important;
                margin: 0 !important;
            }}
            .toolbar,
            .skiptranslate,
            #google_translate_element,
            .goog-te-gadget {{
                display: none !important;
                visibility: hidden !important;
            }}
            #contenedor-reporte {{
                border: none !important;
                padding: 0 !important;
                margin: 0 !important;
                max-width: 100% !important;
                width: 100% !important;
            }}
            .card {{
                page-break-inside: avoid !important;
                break-inside: avoid !important;
                margin-bottom: 6px !important;
                padding: 6px 10px !important;
            }}
            .seccion-bloque {{
                page-break-after: avoid !important;
                break-after: avoid !important;
                margin-top: 12px !important;
                margin-bottom: 6px !important;
            }}
        }}

        .header {{
            background-color: #0f172a;
            color: #ffffff;
            border-left: 6px solid #dc2626;
            padding: 12px 16px;
            margin-bottom: 12px;
            border-radius: 4px;
        }}
        .header h1 {{
            margin: 0 0 2px 0;
            font-size: 16px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}
        .header p {{
            margin: 0;
            color: #cbd5e1;
            font-size: 11px;
        }}
        .resumen-estadistico {{
            display: flex;
            gap: 12px;
            margin-bottom: 12px;
            background: #f8fafc;
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid #e2e8f0;
        }}
        .seccion-titulo {{
            font-size: 11.5px;
            font-weight: 900;
            color: #ffffff;
            padding: 5px 10px;
            border-radius: 3px;
            margin: 0;
            text-transform: uppercase;
        }}
        .card {{
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-left-width: 4px;
            border-left-style: solid;
            padding: 8px 12px;
            margin-bottom: 8px;
            border-radius: 3px;
        }}
        .card h3 {{
            margin: 0 0 4px 0;
            font-size: 13px;
            color: #0f172a;
            line-height: 1.25;
        }}
        .meta {{
            font-size: 10px;
            color: #64748b;
            font-weight: 700;
            margin-bottom: 4px;
        }}
        .tag-relevante {{
            background: #fef08a;
            color: #854d0e;
            padding: 1px 4px;
            border-radius: 2px;
            border: 1px solid #eab308;
            font-weight: 800;
            margin-right: 4px;
        }}
        .cuerpo-box {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            padding: 6px 8px;
            font-size: 11px;
            color: #1e293b;
            margin-bottom: 4px;
            text-align: justify;
            white-space: pre-line;
            border-radius: 3px;
            line-height: 1.4;
        }}
        .analisis-box {{
            background: #f0f9ff;
            border-left: 3px solid #0284c7;
            padding: 5px 8px;
            font-size: 10.5px;
            color: #0c4a6e;
            margin-bottom: 4px;
            border-radius: 2px;
        }}
        .link-button {{
            color: #0284c7 !important;
            font-size: 10.5px;
            font-weight: 700;
            text-decoration: underline !important;
        }}
    </style>
    <script>
        function googleTranslateElementInit() {{
            new google.translate.TranslateElement({{
                pageLanguage: 'auto',
                includedLanguages: 'es,en,ru,uk,de,fr',
                layout: google.translate.TranslateElement.InlineLayout.SIMPLE
            }}, 'google_translate_element');
        }}
    </script>
    <script type="text/javascript" src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"></script>
</head>
<body>
    <div class="toolbar">
        <div class="translate-wrapper">
            <span>🌐 Idioma del informe:</span>
            <div id="google_translate_element"></div>
        </div>
        <button class="btn-print" onclick="window.print()">🖨️ GUARDAR COMO PDF (IMPRIMIR)</button>
    </div>
    <div id="contenedor-reporte">
        <div class="header">
            <h1>ANALIZADOR DE AMENAZAS HÍBRIDAS</h1>
            <p>{subtitulo_filtro} | Emisión: {fecha_actual} | Fuentes Verificadas OSINT</p>
        </div>
        <div class="resumen-estadistico">
            <span>Alertas incluidas en el informe: <strong>{len(alertas)}</strong></span>
            <span>&nbsp;|&nbsp;</span>
            <span>Filtro activo: <strong>{subtitulo_filtro}</strong></span>
        </div>"""

    for cat_cod, cat_nombre, cat_color in SECCIONES_INFORME:
        items_seccion = [a for a in alertas if a.get("categoria") == cat_cod]
        if not items_seccion:
            continue
        html += f"""
        <div class="seccion-bloque">
            <div class="seccion-titulo" style="background-color: {cat_color};">{cat_nombre} ({len(items_seccion)})</div>
        </div>"""
        for a in items_seccion:
            tit = html_module.escape(str(a.get('titular', '')))
            hecho = html_module.escape(str(a.get('hecho_reportado', '')))
            ana = html_module.escape(str(a.get('evaluacion_contexto', '')))
            fecha_str = a.get('fecha_dt').strftime('%d/%m/%Y %H:%M')
            canal = html_module.escape(str(a.get('canal', '')))
            enlace = html_module.escape(str(a.get('enlace', '#')))
            badge_relevante = '<span class="tag-relevante">🔥 RELEVANTE (48H)</span>' if es_reciente_48h(a.get("fecha_dt")) else ""
            vid_txt = " | 🎥 Vídeo" if a.get("tiene_video") else ""
            
            cuerpo_html = f'<div class="cuerpo-box"><strong>📄 Reporte Telegram:</strong> {hecho}</div>' if a.get("categoria") == "TELEGRAM" else ""

            html += f"""
            <div class="card" style="border-left-color: {cat_color};">
                <div class="meta">{badge_relevante}📅 {fecha_str} UTC | 📢 {canal}{vid_txt}</div>
                <h3>{tit}</h3>
                {cuerpo_html}
                <div class="analisis-box"><strong>🧠 Evaluación:</strong> {ana}</div>
                <div><a href="{enlace}" target="_blank" class="link-button">🔗 Noticia original en {canal} &rarr;</a></div>
            </div>"""
    html += """
    </div>
</body>
</html>"""
    return html

# ==========================================
# INTERFAZ PRINCIPAL DE STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Analizador de Amenazas Híbridas",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .main { background-color: #0b0f19; }
    .header-box {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        padding: 26px 34px;
        border-radius: 10px;
        border-left: 12px solid #dc2626;
        border-bottom: 2px solid #334155;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        gap: 24px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.35);
    }
    .header-text h1 { color: #ffffff; margin: 0; font-size: 31px !important; font-weight: 900; text-transform: uppercase; }
    .header-text p { color: #cbd5e1; font-size: 16px !important; margin: 6px 0 0 0; }

    button[data-baseweb="tab"] {
        font-size: 13px !important;
        font-weight: 800 !important;
        padding: 10px 14px !important;
        margin: 0 4px 10px 0 !important;
        border-radius: 8px !important;
        color: #ffffff !important;
        text-transform: uppercase;
        cursor: pointer !important;
    }
    button[data-baseweb="tab"]:nth-child(1) { background-color: #dc2626 !important; }
    button[data-baseweb="tab"]:nth-child(2) { background-color: #d97706 !important; }
    button[data-baseweb="tab"]:nth-child(3) { background-color: #1e293b !important; }
    button[data-baseweb="tab"]:nth-child(4) { background-color: #991b1b !important; }
    button[data-baseweb="tab"]:nth-child(5) { background-color: #7c3aed !important; }
    button[data-baseweb="tab"]:nth-child(6) { background-color: #be123c !important; }
    button[data-baseweb="tab"]:nth-child(7) { background-color: #0284c7 !important; }

    button[data-baseweb="tab"][aria-selected="true"] {
        border: 2px solid #ffffff !important;
        box-shadow: 0 0 15px rgba(255,255,255,0.45) !important;
        filter: brightness(1.15);
    }

    .osint-card {
        background-color: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 10px;
        padding: 18px 24px;
        margin-bottom: 18px;
        box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
    }
    .osint-flex { display: flex; gap: 24px; align-items: flex-start; flex-wrap: wrap; }
    .osint-media { flex: 0 0 310px; max-width: 310px; }
    .osint-media img {
        width: 100%; height: auto; max-height: 260px; object-fit: cover;
        border-radius: 8px; border: 1px solid #cbd5e1;
    }
    .osint-body { flex: 1; min-width: 280px; }
    .osint-badges { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
    .badge-item { font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 4px; text-transform: uppercase; }
    .osint-title { color: #0f172a; font-size: 20px; font-weight: 850; margin: 4px 0 10px 0; line-height: 1.38; }
    .osint-resumen {
        background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
        padding: 12px 16px; color: #1e293b; font-size: 14.5px; line-height: 1.65;
        text-align: justify; margin-bottom: 10px;
    }
    .osint-analisis {
        background-color: #f0f9ff; border-left: 4px solid #0284c7; padding: 10px 14px;
        color: #0c4a6e; font-size: 14px; line-height: 1.55; text-align: justify;
        margin-bottom: 14px; border-radius: 0 6px 6px 0;
    }
    .osint-footer {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
    }
    .btn-source {
        display: inline-block; background-color: #0f172a; color: #ffffff !important;
        padding: 8px 18px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none !important;
        transition: background-color 0.2s ease;
    }
    .btn-source:hover {
        background-color: #1e293b;
    }
    .btn-video {
        display: inline-block; background-color: #0284c7; color: #ffffff !important;
        padding: 8px 18px; border-radius: 6px; font-size: 13px; font-weight: 700; text-decoration: none !important;
        transition: background-color 0.2s ease;
    }
    .btn-video:hover {
        background-color: #0369a1;
    }
    .update-timestamp { font-size: 13px; color: #94a3b8; font-style: italic; margin-top: 8px; }
</style>
""", unsafe_allow_html=True)

logo_svg = """<svg width="78" height="78" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><defs><radialGradient id="grad" cx="50%" cy="50%" r="50%"><stop offset="0%" style="stop-color:#1e293b;stop-opacity:1" /><stop offset="100%" style="stop-color:#0b0f19;stop-opacity:1" /></radialGradient></defs><circle cx="50" cy="50" r="48" fill="url(#grad)" stroke="#dc2626" stroke-width="3.5" /><circle cx="50" cy="50" r="38" fill="none" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="4,3" /><path d="M50 20 L73 31 V54 C73 69 50 81 50 81 C50 81 27 69 27 54 V31 Z" fill="#0f172a" stroke="#ffffff" stroke-width="2" /><polygon points="51,29 41,48 49,48 46,68 59,45 51,45" fill="#dc2626" /></svg>"""
st.markdown(f'<div class="header-box"><div>{logo_svg}</div><div class="header-text"><h1>ANALIZADOR DE AMENAZAS HÍBRIDAS</h1><p>Buscador de fuentes abiertas de amenazas desestabilizadoras</p></div></div>', unsafe_allow_html=True)

if "arranque_inicial_ok" not in st.session_state:
    with st.spinner("Buscando en fuentes..."):
        ejecutar_actualizacion_total()
    st.session_state.arranque_inicial_ok = True
    st.rerun()

if "seleccionadas_pdf" not in st.session_state:
    st.session_state.seleccionadas_pdf = set()

col_btn1, col_btn2, col_filtro, col_info = st.columns([1.2, 1.4, 1.1, 1.5])

with col_btn1:
    if st.button("🔄 ACTUALIZAR BÚSQUEDA", type="primary", use_container_width=True):
        with st.spinner("Buscando en fuentes..."):
            ejecutar_actualizacion_total()
        st.rerun()

with col_filtro:
    solo_48h = st.checkbox("🔥 Solo Relevantes (Últimas 48h)", value=False)

alertas_totales = st.session_state.get("alertas_monitor", [])
datos_activos = [x for x in alertas_totales if es_reciente_48h(x.get("fecha_dt"))] if solo_48h else alertas_totales

# Noticias seleccionadas para el informe
alertas_para_informe = [x for x in datos_activos if x.get("id") in st.session_state.seleccionadas_pdf]

with col_btn2:
    if "alertas_monitor" in st.session_state:
        st.download_button(
            f"📥 DESCARGAR INFORME ({len(alertas_para_informe)}/{len(datos_activos)})",
            data=generar_html_ejecutivo(alertas_para_informe, solo_48h=solo_48h),
            file_name=f"Informe_Amenazas_{'48h_' if solo_48h else 'general_'}{datetime.now().strftime('%Y%m%d_%H%M')}.html",
            mime="text/html",
            use_container_width=True
        )

with col_info:
    if "ultima_actualizacion" in st.session_state:
        total_48h = sum(1 for x in alertas_totales if es_reciente_48h(x.get("fecha_dt")))
        total_total = len(alertas_totales)
        st.markdown(f'<div class="update-timestamp">🕒 <strong>Actualizado:</strong> {st.session_state.ultima_actualizacion} &nbsp;|&nbsp; 🔥 <strong>Últimas 48h:</strong> {total_48h} de {total_total}</div>', unsafe_allow_html=True)

# Botones rápidos de control
c_sel1, c_sel2, _ = st.columns([1.2, 1.2, 4.0])
with c_sel1:
    if st.button("✅ Seleccionar todas", use_container_width=True):
        st.session_state.seleccionadas_pdf = {x["id"] for x in alertas_totales}
        st.rerun()
with c_sel2:
    if st.button("⬜ Desmarcar todas", use_container_width=True):
        st.session_state.seleccionadas_pdf = set()
        st.rerun()

st.write("")

def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas verificadas en este intervalo para esta sección.")
        return
    for item in lista_items:
        item_id = item.get("id")
        cat = item.get("categoria", "GLOBAL")
        canal_nombre = item.get("canal", "")
        es_desinfo = canal_nombre.replace("@", "") in CANALES_DESINFORMACION_ESP

        b_color = {
            "ESPANA": "#dc2626",
            "EMPRESA": "#d97706",
            "GLOBAL": "#0f172a",
            "CRIMENES": "#991b1b",
            "CRIPTO": "#7c3aed",
            "DETENCION": "#be123c",
            "TELEGRAM": "#0284c7"
        }.get(cat, "#0f172a")

        badges = ""
        if es_reciente_48h(item.get("fecha_dt")):
            badges += '<span class="badge-item" style="background:#fef08a;color:#854d0e;border:1px solid #eab308;">🔥 RELEVANTE (ÚLTIMAS 48H)</span>'

        bg_c, txt_c, label_c = {
            "ESPANA": ('#fee2e2', '#991b1b', '🇪🇸 ESPAÑA'),
            "EMPRESA": ('#fef3c7', '#92400e', '🏢 EMPRESA ESTRATÉGICA'),
            "GLOBAL": ('#e2e8f0', '#1e293b', '🌐 GLOBAL / INTERNACIONAL'),
            "CRIMENES": ('#fecaca', '#7f1d1d', '⚖️ CRÍMENES DE GUERRA'),
            "CRIPTO": ('#f3e8ff', '#6b21a8', '🟣 CRIPTO / FININT'),
            "DETENCION": ('#ffe4e6', '#9f1239', '🛑 DETENCIÓN / ESPIONAJE'),
            "TELEGRAM": ('#e0f2fe', '#0369a1', '✈️ TELEGRAM OSINT')
        }.get(cat, ('#e2e8f0', '#1e293b', '🌐 GLOBAL'))
        
        badges += f'<span class="badge-item" style="background:{bg_c};color:{txt_c};">{label_c}</span>'
        
        if es_desinfo:
            badges += '<span class="badge-item" style="background:#ffe4e6;color:#be123c;border:1px solid #fda4af;">📢 DESINFORMACIÓN / FIMI</span>'

        if item.get("tiene_video"):
            badges += '<span class="badge-item" style="background:#fef3c7;color:#92400e;border:1px solid #f59e0b;">🎥 VÍDEO</span>'

        fecha_str = item.get("fecha_dt").strftime("%d/%m/%Y %H:%M")
        t_safe = html_module.escape(str(item.get("titular", "")))
        h_safe = html_module.escape(str(item.get("hecho_reportado", "")))
        a_safe = html_module.escape(str(item.get("evaluacion_contexto", "")))
        canal_safe = html_module.escape(str(canal_nombre))
        enlace = item.get("enlace", "#")
        if not es_url_segura(enlace):
            enlace = "#"

        media_html = f'<div class="osint-media"><img src="{html_module.escape(item.get("multimedia"))}" alt="Evidencia" loading="lazy" /></div>' if item.get("multimedia") and es_url_segura(item.get("multimedia")) else ""
        vid_html = f'<a href="{html_module.escape(enlace)}" target="_blank" class="btn-video">▶️ VER VÍDEO EN TELEGRAM</a>' if item.get("tiene_video") else ""

        bloque_reporte = f'<div class="osint-resumen" translate="yes"><strong>📄 Mensaje Original Telegram:</strong><br>{h_safe}</div>' if cat == "TELEGRAM" else ""

        tarjeta_html = (
            f'<div class="osint-card" style="border-left: 9px solid {b_color};" translate="yes">'
            f'<div class="osint-flex">{media_html}'
            f'<div class="osint-body">'
            f'<div class="osint-badges">{badges}<span style="font-size:12px;color:#64748b;margin-left:8px;">📅 {fecha_str} UTC | 📢 {canal_safe}</span></div>'
            f'<div class="osint-title" translate="yes">{t_safe}</div>'
            f'{bloque_reporte}'
            f'<div class="osint-analisis"><strong>🧠 Evaluación Analítica:</strong> {a_safe}</div>'
            f'<div class="osint-footer">'
            f'<a href="{enlace}" target="_blank" class="btn-source">🔗 ABRIR FUENTE ORIGINAL COMPLETA</a>'
            f'{vid_html}'
            f'</div>'
            f'</div></div></div>'
        )

        col_check, col_card = st.columns([0.05, 0.95])
        with col_check:
            esta_activa = item_id in st.session_state.seleccionadas_pdf
            marcado = st.checkbox("Incluir en PDF", value=esta_activa, key=f"sel_{item_id}", label_visibility="collapsed")
            if marcado:
                st.session_state.seleccionadas_pdf.add(item_id)
            else:
                st.session_state.seleccionadas_pdf.discard(item_id)

        with col_card:
            st.markdown(tarjeta_html, unsafe_allow_html=True)

# ==========================================
# PESTAÑAS (7 ORIGINALES)
# ==========================================
if "alertas_monitor" in st.session_state:
    l_esp = [x for x in datos_activos if x.get("categoria") == "ESPANA"]
    l_emp = [x for x in datos_activos if x.get("categoria") == "EMPRESA"]
    l_prensa = [x for x in datos_activos if x.get("categoria") == "GLOBAL"]
    l_crim = [x for x in datos_activos if x.get("categoria") == "CRIMENES"]
    l_cripto = [x for x in datos_activos if x.get("categoria") == "CRIPTO"]
    l_det = [x for x in datos_activos if x.get("categoria") == "DETENCION"]
    l_tg = [x for x in datos_activos if x.get("categoria") == "TELEGRAM"]

    t1, t2, t3, t4, t5, t6, t7 = st.tabs([
        f"🇪🇸 ESPAÑA ({len(l_esp)})",
        f"🏢 EMPRESAS ({len(l_emp)})",
        f"📰 GLOBAL ({len(l_prensa)})",
        f"⚖️ CRÍMENES GUERRA ({len(l_crim)})",
        f"🟣 CRIPTO ({len(l_cripto)})",
        f"🛑 DETENCIONES ({len(l_det)})",
        f"✈️ TELEGRAM ({len(l_tg)})"
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
        renderizar_tarjetas(l_det)
    with t7:
        renderizar_tarjetas(l_tg)
