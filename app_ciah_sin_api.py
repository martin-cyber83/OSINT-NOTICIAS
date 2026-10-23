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
import pandas as pd
import io

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
    # Elimina sufijos típicos de medios tipo "- MarketScreener España", "- Libertad Digital", etc.
    t = re.sub(r'\s*[-–|·]\s*[^-–|·]{2,55}$', '', str(texto)).strip()
    return t

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
    return timedelta(seconds=0) <= diferencia <= timedelta(hours=24)

def es_reciente_48h(fecha_dt):
    if not fecha_dt:
        return False
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    diferencia = ahora - fecha_dt.astimezone(timezone.utc)
    return timedelta(seconds=0) <= diferencia <= timedelta(hours=48)

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
        r = SESION_HTTP.get(url, params=params, timeout=3.0)
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
# DICCIONARIOS Y FILTROS ESTRICTOS
# ==========================================
EMPRESAS_ESTRATEGICAS_ESP = [
    "indra", "grupo oesia", "oesia", "uav navigation", "tecnobit", "navantia",
    "escribano", "em&e", "sener", "arquimea", "destinus", "santa barbara", "gdels",
    "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa", "itp aero",
    "airbus espana", "airbus getafe", "hisdesat", "hispasat", "sateliot", "pld space",
    "amadeus", "aena", "enaire", "renfe", "adif", "telefonica", "iberdrola",
    "repsol", "naturgy", "redeia", "red electrica", "enagas", "talgo"
]

CANALES_DESINFORMACION_ESP = {
    "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu", "ASupersharij"
}

PATRON_RUSIA_ESTRICTO = re.compile(
    r'\b('
    r'rusia|ruso|rusa|rusos|rusas|moscu|kremlin|putin|mod rf|fuerzas armadas rusas|'
    r'russia|russian|russians|moscow|russie|russe|russland|russisch|'
    r'gru|fsb|svr|apt28|apt29|fancy bear|cozy bear|sandworm|noname057|killnet|turla|gamaredon|cyberarmyofrussia|garantex|wagner|'
    r'bielorrusia|belarus|kaliningrado|kaliningrad|lukashenko|'
    r'ucrania|ucraniano|ucraniana|ucranianos|ukraine|ukrainian|zelensky|zelenski|zsu|vsu|sbu|gur|'
    r'kursk|belgorod|donbas|donbass|crimea|zaporiyia|zaporizhzhia|kherson|donetsk|lugansk|luhansk|jarkov|kharkiv|shariy|sharij|zvinchuk|rybar'
    r')\b', re.IGNORECASE
)

RAICES_CIRILICO_RUSIA = [
    "росси", "рф", "москв", "кремл", "путин", "гру", "фсб", "свр",
    "украин", "киев", "київ", "зсу", "всу", "сбу", "гур", "донбасс",
    "крым", "беларус", "вагнер", "шахед", "геран", "искандер", "шарий", "рыбарь", "звинчук"
]

PATRON_PAISES_AJENOS = re.compile(
    r'\b('
    r'cuba|cubano|cubana|cubanos|la habana|castro|diaz-canel|'
    r'venezuela|venezolano|venezolana|maduro|caracas|'
    r'nicaragua|ortega|managua|colombia|petro|bogota|'
    r'gaza|hamas|hezbollah|hezbola|netanyahu|tel aviv|cisjordania|palestina|palestino|israel|israeli|'
    r'yemen|huti|huties|iran|irani|teheran|siria|damasco'
    r')\b', re.IGNORECASE
)

def es_publicidad_o_spam(texto):
    t = normalizar_texto(texto)
    spam = ["master profesional", "lisa institute", "udima", "beca", "matriculate", "curso online", "descuento", "sorteo", "casino", "apuestas", "tarot", "horoscopo", "oferta de empleo"]
    return any(s in t for s in spam)

def tiene_vinculo_ruso_estricto(texto):
    if not texto or es_publicidad_o_spam(texto):
        return False
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    if any(c in t_raw for c in RAICES_CIRILICO_RUSIA):
        return True

    if PATRON_PAISES_AJENOS.search(t_norm):
        if not re.search(r'\b(kremlin|putin|tropas rusas|fuerzas armadas rusas|moscu|espionaje ruso|invasion rusa|shariy|sharij|rybar|zvinchuk)\b', t_norm):
            return False

    return bool(PATRON_RUSIA_ESTRICTO.search(t_norm))

def tiene_vector_amenaza_estricto(texto):
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)

    vectores_cirilico = [
        "диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид",
        "крипт", "биткоин", "usdt", "бпла", "дron", "дрон", "рэб", "арест", "задержан", "санкц", "кабел", "флот",
        "казн", "пленн", "военн", "преступлен", "поджог", "теракт", "удар", "взрыв", "зеленск", "фронт",
        "гражданск", "больниц", "депортац", "гааг", "мкс"
    ]
    if any(c in t_raw for c in vectores_cirilico):
        return True

    vectores_latinos = [
        "hibrid", "hybrid", "sabotaj", "sabotage", "saboteador", "espia", "espionaje", "spy", "espionage", "spying",
        "ciber", "cyber", "hack", "ddos", "malware", "ransomware", "apt28", "apt29", "sandworm", "noname057", "killnet",
        "desinformacion", "disinformation", "propaganda", "injerencia", "fimi",
        "cripto", "crypto", "bitcoin", "btc", "usdt", "tether", "garantex", "sancion", "sanction", "elusion",
        "flota fantasma", "shadow fleet", "cable submarino", "undersea cable", "petrolero",
        "crimen de guerra", "war crime", "corte penal internacional", "icc",
        "genocidio", "tortura", "pow execution", "ejecucion", "masacre", "fosa comun", "bucha", "mariupol",
        "ataque civil", "ataque a civiles", "hospital", "escuela", "deportacion", "ninos ucranianos",
        "detenido", "detencion", "arrestado", "arresto", "arrested", "detained", "dron", "uav", "guerra electronica",
        "jamming", "arson", "incendio provocado", "guerra", "ejercito", "militar", "otan", "nato", "frente", "misil",
        "ataque", "ofensiva", "bombardeo", "shariy", "sharij", "rybar", "zvinchuk"
    ]
    return any(v in t_norm for v in vectores_latinos)

# ==========================================
# CATEGORIZACIÓN (FILTROS DEPURADOS)
# ==========================================
def es_cripto(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(cripto|criptomoneda|criptomonedas|crypto|criptobolsa|usdt|tether|btc|bitcoin|monero|garantex|blockchain)\b'
    return bool(re.search(patron, t_all))

def es_crimen_guerra(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron = r'\b(' \
             r'crimen de guerra|crimenes de guerra|war crime|war crimes|' \
             r'corte penal internacional|tribunal penal|cpi|icc|la haya|the hague|' \
             r'lesa humanidad|genocidio|pow execution|ejecucion de prisioneros|' \
             r'tortura|torturas|fosa comun|fosas comunes|masacre|bucha|irpin|izium|mariupol|' \
             r'deportacion|menores deportados|ninos robados|' \
             r'ataque a civiles|ataque contra civiles|civiles muertos|muerte de civiles|' \
             r'bombardeo de hospital|bombardeo residencial|edificio de viviendas|edificio residencial|' \
             r'escuela bombardeada|hospital infantil|bloque de apartamentos' \
             r')\b'
    return bool(re.search(patron, t_all))

def es_detencion(titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    patron_det = r'\b(detenido|detenida|detenidos|detenidas|detienen|detiene|detencion|detenciones|arrestado|arrestada|arrestados|arrestadas|arrestan|arresta|arresto|arrestos|arrested|detained|festnahme|verhaftet|capturado|capturada|capturados|capturadas|capturan|desarticulad[oa]s?|prision preventiva|prision provisional)\b'
    return bool(re.search(patron_det, t_all))

def es_empresa_esp(titular, cuerpo):
    # Se evalúa sobre el texto sin firmas de medios
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    return any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP)

def es_espana(titular, cuerpo):
    # Se evalúa estrictamente sobre el contenido limpio (evitando 'MarketScreener España' o 'gobierno ruso')
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    
    # Expresiones positivas claras de España
    patron_esp = r'\b(' \
                 r'espana|espanol|espanola|espanoles|espanolas|spain|madrid|barcelona|cartagena|' \
                 r'cni|incibe|ccn-cert|audiencia nacional|guardia civil|policia nacional|base de rota|torrejon|' \
                 r'gobierno de espana|gobierno espanol|ministerio de defensa|margarita robles|' \
                 r'ejercito de tierra|armada espanola|ejercito del aire|militares espanoles|' \
                 r'defensa confirma|defensa envia|seguridad nacional espanola' \
                 r')\b'
    
    if not re.search(patron_esp, t_all):
        return False
        
    # Si la única mención a España proviene de una edición de prensa o dominio, no es de España
    if re.search(r'\b(marketscreener|edition|edicion espana)\b', t_all) and not re.search(r'\b(madrid|cni|defensa|militares espanoles|guardia civil)\b', t_all):
        # Si habla explícitamente de otro país sin afectación española real
        if re.search(r'\b(en ucrania|en rusia|en polonia|en alemania)\b', t_all) and not re.search(r'\b(militares espanoles|cni|espana)\b', t_all):
            return False

    return True

def calcular_puntuacion_severidad(categoria, titular, cuerpo):
    t_all = normalizar_texto(f"{titular} {cuerpo}")
    score = 2

    if categoria in ["ESPANA", "EMPRESA"]:
        score = 4
        if re.search(r'\b(sabotaje|ciberataque|infiltrado|infraestructura critica|red electrica|cni|defensa)\b', t_all):
            score = 5
    elif categoria in ["DETENCION", "CRIMENES"]:
        score = 4
    elif categoria == "CRIPTO":
        score = 3
    elif categoria in ["TELEGRAM", "REDES"]:
        if re.search(r'\b(ofensiva|misil|nuclear|sabotaje|bpla|shahed|dron|frente)\b', t_all):
            score = 3
        else:
            score = 2

    if re.search(r'\b(cable submarino|flota fantasma|nord stream|otan|baltico)\b', t_all):
        score = max(score, 4)

    return score

def generar_analisis_contexto(categoria, canal=""):
    canal_clean = canal.replace("@", "")
    if "rybar" in canal_clean.lower() or "zvinchuk" in canal_clean.lower():
        return "Análisis táctico y propaganda militar de Mijaíl Zvinchuk (@rybar), exoficial del Ministerio de Defensa ruso."
    if "sharij" in canal_clean.lower() or "shariy" in canal_clean.lower():
        return "Contenido de Anatolii Sharij: seguimiento crítico y difusión en redes del frente ucraniano-ruso."
    if canal_clean in CANALES_DESINFORMACION_ESP:
        return "Canal clasificado en la red de propaganda, desinformación e injerencia extranjera (FIMI) vinculada al ecosistema pro-Kremlin."
    if categoria == "CRIMENES":
        return "Documentación de crímenes de guerra, ataques a población o infraestructuras civiles e investigaciones judiciales internacionales (CPI/ONU)."
    if categoria == "DETENCION":
        return "Operaciones policiales y contrainteligencia: neutralización y arresto de espías, saboteadores o colaboradores vinculados a la inteligencia rusa."
    if categoria == "REDES":
        return "Contenido capturado en redes sociales, canales de análisis en YouTube y cuentas de seguimiento del conflicto ruso-ucraniano."
    if categoria == "EMPRESA":
        return "Seguimiento sobre empresas estratégicas de España (Defensa, Energía y Telecomunicaciones) ante ciberataques o sabotajes."
    if categoria == "ESPANA":
        return "Impacto directo en la seguridad nacional e infraestructuras críticas de España frente a operaciones hostiles."
    if categoria == "CRIPTO":
        return "Inteligencia financiera (FININT): evasión de sanciones internacionales y uso de criptoactivos (USDT, BTC)."
    if categoria == "TELEGRAM":
        return "Reporte capturado de canales tácticos en Telegram: inteligencia operativa de primera línea."
    return "Actividad vinculada a operaciones híbridas globales del Kremlin: ciberataques, sabotaje o desinformación."

# ==========================================
# RASTREADOR YOUTUBE DIRECTO
# ==========================================
def raspar_youtube_canal(canal_id, etiqueta):
    items = []
    try:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}"
        r = SESION_HTTP.get(url, timeout=5.0)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:10]:
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
# RASTREADOR TELEGRAM (ZVINCHUK Y SHARIJ EN REDES)
# ==========================================
def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=6.0)
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

            if not es_reciente_24h(dt_obj):
                continue

            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""

            if len(texto_raw) < 25 or es_publicidad_o_spam(texto_raw):
                continue

            texto_limpio_eval = re.sub(r'@\w+', '', texto_raw)
            texto_limpio_eval = re.sub(r'https?://\S+', '', texto_limpio_eval)

            if not tiene_vinculo_ruso_estricto(texto_limpio_eval):
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
        "rybar",
        "ASupersharij",
        "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua",
        "entre_guerras", "dva_majors", "vchkogpu",
        "meduzalive", "astrapress", "istories_media",
        "ostorozhno_novosti", "nvua_official", "operativnoZSU",
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
                if len(tit) > 10 and tiene_vinculo_ruso_estricto(texto_eval) and tiene_vector_amenaza_estricto(texto_eval):
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
        # Feeds nativos de The Objective
        ("https://theobjective.com/feed/", "The Objective (General)"),
        ("https://theobjective.com/espana/feed/", "The Objective (España)"),
        ("https://theobjective.com/internacional/feed/", "The Objective (Internacional)"),

        # Cobertura España y Seguridad
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin+OR+NoName057+OR+APT28)+(España+OR+Madrid+OR+CNI+OR+INCIBE)+(sabotaje+OR+ciberataque+OR+espionaje+OR+amenaza)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Russian+OR+Kremlin)+(Spain+OR+Madrid)+(sabotage+OR+cyberattack+OR+spy+OR+espionage)&hl=en-US&gl=US&ceid=US:en", "Spain Watch (EN)"),
        ("https://news.google.com/rss/search?q=(Indra+OR+Navantia+OR+Escribano+OR+Sener+OR+Telefónica+OR+Iberdrola)+(Rusia+OR+ciberataque+OR+sabotaje+OR+hackers)&hl=es&gl=ES&ceid=ES:es", "Empresas España (ES)"),

        # Detenciones
        ("https://news.google.com/rss/search?q=(detenido+OR+arrestado+OR+detencion)+(espia+ruso+OR+sabotaje+ruso+OR+GRU+OR+FSB+OR+SVR)&hl=es&gl=ES&ceid=ES:es", "Detenciones (ES)"),
        ("https://news.google.com/rss/search?q=(arrested+OR+detained)+(Russian+spy+OR+Russian+sabotage+OR+GRU+OR+FSB)&hl=en-US&gl=US&ceid=US:en", "Detenciones (EN)"),

        # Crímenes de Guerra y Ataques a Civiles
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+tropas+rusas)+(\"crimen+de+guerra\"+OR+\"crimenes+de+guerra\"+OR+\"Corte+Penal\"+OR+\"ataque+a+civiles\"+OR+\"bombardeo+residencial\"+OR+\"prisioneros\")&hl=es&gl=ES&ceid=ES:es", "Crímenes Guerra (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Russian+forces)+(\"war+crime\"+OR+\"war+crimes\"+OR+ICC+OR+\"civilian+casualties\"+OR+\"hospital+strike\"+OR+\"POW+execution\")&hl=en-US&gl=US&ceid=US:en", "War Crimes (EN)"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+Ucrania)+(\"ejecucion\"+OR+\"tortura\"+OR+\"fosa+comun\"+OR+\"deportacion\")+civiles&hl=es&gl=ES&ceid=ES:es", "DDHH Conflicto (ES)"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+Russian)+Ukraine+(\"strike+on+civilians\"+OR+\"residential+building\"+OR+\"massacre\")&hl=en-US&gl=US&ceid=US:en", "Civilian Strikes (EN)"),

        # Inteligencia global y FININT
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(sabotage+OR+cyberattack+OR+espionage+OR+\"hybrid+warfare\"+OR+partisan)&hl=en-US&gl=US&ceid=US:en", "Intel Global (EN)"),
        ("https://news.google.com/rss/search?q=(Rusia+OR+Ucrania)+(sabotaje+OR+ciberataque+OR+espionaje+OR+\"guerra+hibrida\")&hl=es&gl=ES&ceid=ES:es", "Frente Global (ES)"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT+OR+Tether)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)")
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
# ACTUALIZACIÓN CENTRAL Y CLASIFICACIÓN
# ==========================================
def son_mismo_evento(tit1, tit2):
    t1 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit1)))
    t2 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit2)))
    if len(t1) < 15 or len(t2) < 15:
        return False
    return SequenceMatcher(None, t1[:95], t2[:95]).ratio() > 0.68

def ejecutar_actualizacion_total():
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

    todos_brutos = brutos_tg + brutos_yt + brutos_prensa + brutos_rumafia + brutos_antikor
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
            continue

        urls_vistas.add(u)
        b["canales_correlacionados"] = [b["canal"]]
        pre_unicos.append(b)

    if not pre_unicos:
        st.session_state.alertas_monitor = []
        st.session_state.seleccionadas_pdf = set()
        return

    titulares_traducidos = []
    for item in pre_unicos:
        titulares_traducidos.append(traducir_solo_titular(item["titular_raw"]))

    alertas_finales = []
    for item, tit_es in zip(pre_unicos, titulares_traducidos):
        cuerpo_intacto = item["cuerpo_raw"]
        plat = item["plataforma"]

        if plat == "REDES":
            titular_final = tit_es
            cat_final = "REDES"
        elif plat == "TELEGRAM":
            canal_tag = item["canal"].replace("@", "").upper()
            titular_final = f"[{canal_tag}] {tit_es}"
            cat_final = "TELEGRAM"
        else:
            titular_final = tit_es
            # Se retira la firma del medio (ej. MarketScreener España) antes de clasificar
            tit_sin_medio = limpiar_firma_medio(titular_final)

            # Cascada lógica corregida:
            # 1. Cripto (sanciones a bolsas, usdt, etc.)
            # 2. Detenciones (arrestos de espías)
            # 3. Crímenes de Guerra (hospitales, civiles)
            # 4. Empresas Estratégicas españolas
            # 5. España (afectación real al territorio/gobierno español)
            # 6. Global
            if es_cripto(tit_sin_medio, cuerpo_intacto):
                cat_final = "CRIPTO"
            elif es_detencion(tit_sin_medio, cuerpo_intacto):
                cat_final = "DETENCION"
            elif es_crimen_guerra(tit_sin_medio, cuerpo_intacto):
                cat_final = "CRIMENES"
            elif es_empresa_esp(tit_sin_medio, cuerpo_intacto):
                cat_final = "EMPRESA"
            elif es_espana(tit_sin_medio, cuerpo_intacto):
                cat_final = "ESPANA"
            else:
                cat_final = "GLOBAL"

        severidad = calcular_puntuacion_severidad(cat_final, titular_final, cuerpo_intacto)

        nueva_alerta = {
            "id": item["id"],
            "plataforma": plat,
            "canal": item["canal"],
            "canales_correlacionados": item.get("canales_correlacionados", [item["canal"]]),
            "fecha_dt": item["fecha_dt"],
            "titular": titular_final,
            "titular_original": item["titular_raw"],
            "categoria": cat_final,
            "severidad": severidad,
            "hecho_reportado": cuerpo_intacto,
            "evaluacion_contexto": generar_analisis_contexto(cat_final, canal=item["canal"]),
            "multimedia": item.get("multimedia"),
            "tiene_video": item.get("tiene_video", False),
            "enlace": item["enlace"]
        }

        alertas_finales.append(nueva_alerta)

    alertas_finales.sort(key=lambda x: (x["severidad"], x["fecha_dt"]), reverse=True)

    st.session_state.alertas_monitor = alertas_finales
    st.session_state.ultima_actualizacion = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")
    st.session_state.seleccionadas_pdf = {a["id"] for a in alertas_finales}
    for a in alertas_finales:
        st.session_state[f"sel_{a['id']}"] = True

# ==========================================
# EXPORTACIÓN CSV
# ==========================================
def exportar_alertas_csv(alertas):
    if not alertas:
        return b""
    filas = []
    for a in alertas:
        filas.append({
            "Fecha (UTC)": a["fecha_dt"].strftime("%Y-%m-%d %H:%M"),
            "Severidad (1-5)": a["severidad"],
            "Categoría": a["categoria"],
            "Plataforma": a.get("plataforma", ""),
            "Canal/Fuente": a["canal"],
            "Difusión Cruzada": ", ".join(a.get("canales_correlacionados", [])),
            "Titular": a["titular"],
            "Texto/Reporte": a["hecho_reportado"],
            "Evaluación": a["evaluacion_contexto"],
            "Enlace": a["enlace"]
        })
    df = pd.DataFrame(filas)
    return df.to_csv(index=False).encode('utf-8-sig')

# ==========================================
# INFORME IMPRIMIBLE (HTML / PDF)
# ==========================================
SECCIONES_INFORME = [
    ("ESPANA", "SECCIÓN 1: ESPAÑA E INFRAESTRUCTURAS CRÍTICAS", "#dc2626"),
    ("EMPRESA", "SECCIÓN 2: EMPRESAS ESTRATÉGICAS DE ESPAÑA Y DEFENSA", "#d97706"),
    ("GLOBAL", "SECCIÓN 3: RUSIA, UCRANIA Y AMENAZAS GLOBALES", "#0f172a"),
    ("CRIMENES", "SECCIÓN 4: CRÍMENES DE GUERRA Y DERECHOS HUMANOS", "#991b1b"),
    ("CRIPTO", "SECCIÓN 5: CRIPTOACTIVOS Y FINANCIACIÓN ILÍCITA", "#7c3aed"),
    ("DETENCION", "SECCIÓN 6: DETENCIONES POR ESPIONAJE Y SABOTAJE", "#be123c"),
    ("TELEGRAM", "SECCIÓN 7: TELEGRAM OSINT Y CANALES TÁCTICOS", "#0284c7"),
    ("REDES", "SECCIÓN 8: REDES SOCIALES Y ANÁLISIS MULTIMEDIA", "#059669")
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
        .tag-sev {{ background: #0f172a; color: #ffffff; padding: 1px 5px; border-radius: 2px; font-weight: 800; }}
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
        <div><strong>Informe Inteligencia</strong> &nbsp;|&nbsp; Amenazas Clasificadas</div>
        <button class="btn-print" onclick="window.print()">🖨️ IMPRIMIR / GUARDAR PDF</button>
    </div>
    <div id="contenedor-reporte">
        <div class="header">
            <h1>ANALIZADOR DE AMENAZAS HÍBRIDAS</h1>
            <p>{subtitulo_filtro} | Emisión: {fecha_actual} | Fuentes Verificadas OSINT</p>
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
            fecha_str = a.get('fecha_dt').strftime('%d/%m/%Y %H:%M')
            canal = html_module.escape(str(a.get('canal', '')))
            enlace = html_module.escape(str(a.get('enlace', '#')))
            sev = a.get('severidad', 2)

            cuerpo_html = f'<div class="cuerpo-box"><strong>Reporte:</strong> {hecho}</div>' if a.get("categoria") in ["TELEGRAM", "REDES"] else ""

            html += f"""
            <div class="card" style="border-left-color: {cat_color};">
                <div class="meta"><span class="tag-sev">CRITICIDAD {sev}/5</span> | 📅 {fecha_str} UTC | 📢 {canal}</div>
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
# INTERFAZ STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Analizador de Amenazas Híbridas",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main { background-color: #0b0f19; }
    .header-box {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        padding: 22px 30px;
        border-radius: 10px;
        border-left: 10px solid #dc2626;
        border-bottom: 2px solid #334155;
        margin-bottom: 16px;
        display: flex;
        align-items: center;
        gap: 20px;
    }
    .header-text h1 { color: #ffffff; margin: 0; font-size: 28px !important; font-weight: 900; text-transform: uppercase; }
    .header-text p { color: #cbd5e1; font-size: 15px !important; margin: 4px 0 0 0; }

    button[data-baseweb="tab"] {
        font-size: 12px !important; font-weight: 800 !important;
        padding: 8px 10px !important; margin: 0 4px 10px 0 !important;
        border-radius: 8px !important; color: #ffffff !important;
        text-transform: uppercase;
    }
    button[data-baseweb="tab"]:nth-child(1) { background-color: #dc2626 !important; }
    button[data-baseweb="tab"]:nth-child(2) { background-color: #d97706 !important; }
    button[data-baseweb="tab"]:nth-child(3) { background-color: #1e293b !important; }
    button[data-baseweb="tab"]:nth-child(4) { background-color: #991b1b !important; }
    button[data-baseweb="tab"]:nth-child(5) { background-color: #7c3aed !important; }
    button[data-baseweb="tab"]:nth-child(6) { background-color: #be123c !important; }
    button[data-baseweb="tab"]:nth-child(7) { background-color: #0284c7 !important; }
    button[data-baseweb="tab"]:nth-child(8) { background-color: #059669 !important; }

    button[data-baseweb="tab"][aria-selected="true"] {
        border: 2px solid #ffffff !important;
        box-shadow: 0 0 12px rgba(255,255,255,0.4) !important;
    }

    .osint-card {
        background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px;
        padding: 16px 20px; margin-bottom: 14px; box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05);
    }
    .osint-flex { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; }
    .osint-media { flex: 0 0 280px; max-width: 280px; }
    .osint-media img { width: 100%; height: auto; max-height: 220px; object-fit: cover; border-radius: 6px; }
    .osint-body { flex: 1; min-width: 260px; }
    .badge-item { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; }
    .osint-title { color: #0f172a; font-size: 18px; font-weight: 850; margin: 4px 0 8px 0; line-height: 1.35; }
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
        border-radius: 4px; font-size: 12.5px; font-weight: 700; text-decoration: none !important;
    }
    .btn-video {
        background-color: #0284c7; color: #ffffff !important; padding: 6px 14px;
        border-radius: 4px; font-size: 12.5px; font-weight: 700; text-decoration: none !important;
    }
</style>
""", unsafe_allow_html=True)

logo_svg = """<svg width="64" height="64" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><defs><radialGradient id="grad" cx="50%" cy="50%" r="50%"><stop offset="0%" style="stop-color:#1e293b;stop-opacity:1" /><stop offset="100%" style="stop-color:#0b0f19;stop-opacity:1" /></radialGradient></defs><circle cx="50" cy="50" r="48" fill="url(#grad)" stroke="#dc2626" stroke-width="3.5" /><circle cx="50" cy="50" r="38" fill="none" stroke="#38bdf8" stroke-width="1.5" stroke-dasharray="4,3" /><path d="M50 20 L73 31 V54 C73 69 50 81 50 81 C50 81 27 69 27 54 V31 Z" fill="#0f172a" stroke="#ffffff" stroke-width="2" /><polygon points="51,29 41,48 49,48 46,68 59,45 51,45" fill="#dc2626" /></svg>"""
st.markdown(f'<div class="header-box"><div>{logo_svg}</div><div class="header-text"><h1>ANALIZADOR DE AMENAZAS HÍBRIDAS</h1><p>Monitor táctico de fuentes abiertas e inteligencia operativa</p></div></div>', unsafe_allow_html=True)

if "arranque_inicial_ok" not in st.session_state:
    with st.spinner("Buscando en fuentes..."):
        ejecutar_actualizacion_total()
    st.session_state.arranque_inicial_ok = True
    st.rerun()

if "seleccionadas_pdf" not in st.session_state:
    st.session_state.seleccionadas_pdf = set()

alertas_totales = st.session_state.get("alertas_monitor", [])

# ==========================================
# BARRA LATERAL (FILTROS Y BÚSQUEDA AVANZADA)
# ==========================================
with st.sidebar:
    st.subheader("🔍 Filtros de Búsqueda")
    busqueda_txt = st.text_input("Filtrar por texto:", placeholder="Ej: Indra, Zvinchuk, The Objective...")
    
    canales_disponibles = sorted(list({a["canal"] for a in alertas_totales}))
    canales_sel = st.multiselect("Filtrar por canal/fuente:", canales_disponibles, default=[])
    
    nivel_severidad_min = st.slider("Severidad mínima:", min_value=1, max_value=5, value=1)
    
    st.write("---")
    st.subheader("📊 Exportación")
    csv_bytes = exportar_alertas_csv(alertas_totales)
    st.download_button(
        "📊 DESCARGAR BASE DE DATOS (CSV)",
        data=csv_bytes,
        file_name=f"alertas_osint_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True
    )

datos_filtrados = alertas_totales

if busqueda_txt:
    b_norm = normalizar_texto(busqueda_txt)
    datos_filtrados = [
        x for x in datos_filtrados
        if b_norm in normalizar_texto(x["titular"]) or b_norm in normalizar_texto(x["hecho_reportado"])
    ]

if canales_sel:
    datos_filtrados = [x for x in datos_filtrados if x["canal"] in canales_sel]

if nivel_severidad_min > 1:
    datos_filtrados = [x for x in datos_filtrados if x.get("severidad", 1) >= nivel_severidad_min]

# ==========================================
# BOTONERA SUPERIOR
# ==========================================
col_btn1, col_btn2, col_filtro, col_info = st.columns([1.2, 1.4, 1.1, 1.5])

with col_btn1:
    if st.button("🔄 ACTUALIZAR FUENTES", type="primary", use_container_width=True):
        with st.spinner("Buscando en fuentes..."):
            ejecutar_actualizacion_total()
        st.rerun()

with col_filtro:
    solo_48h = st.checkbox("🔥 Solo Últimas 48h", value=False)

datos_activos = [x for x in datos_filtrados if es_reciente_48h(x.get("fecha_dt"))] if solo_48h else datos_filtrados
alertas_para_informe = [x for x in datos_activos if x.get("id") in st.session_state.seleccionadas_pdf]

with col_btn2:
    if "alertas_monitor" in st.session_state:
        st.download_button(
            f"📥 INFORME EJECUTIVO ({len(alertas_para_informe)}/{len(datos_activos)})",
            data=generar_html_ejecutivo(alertas_para_informe, solo_48h=solo_48h),
            file_name=f"Informe_Amenazas_{'48h_' if solo_48h else 'general_'}{datetime.now().strftime('%Y%m%d_%H%M')}.html",
            mime="text/html",
            use_container_width=True
        )

with col_info:
    if "ultima_actualizacion" in st.session_state:
        st.markdown(f'<div style="font-size:12.5px;color:#94a3b8;margin-top:6px;">🕒 <strong>Última sincronización:</strong> {st.session_state.ultima_actualizacion}<br>Mostrando <strong>{len(datos_activos)}</strong> alertas filtradas.</div>', unsafe_allow_html=True)

ids_visibles = [x["id"] for x in datos_activos]
c_sel1, c_sel2, _ = st.columns([1.2, 1.2, 4.0])

with c_sel1:
    st.button("✅ Seleccionar todas", use_container_width=True, on_click=marcar_todas_callback, args=(ids_visibles,))

with c_sel2:
    st.button("⬜ Desmarcar todas", use_container_width=True, on_click=desmarcar_todas_callback, args=(ids_visibles,))

st.write("")

# ==========================================
# RENDERIZADO DE ALERTAS
# ==========================================
def renderizar_tarjetas(lista_items):
    if not lista_items:
        st.info("No se registran alertas que cumplan los filtros seleccionados para esta sección.")
        return
    for item in lista_items:
        item_id = item.get("id")
        cat = item.get("categoria", "GLOBAL")
        canal_nombre = item.get("canal", "")
        sev = item.get("severidad", 2)
        es_desinfo = canal_nombre.replace("@", "") in CANALES_DESINFORMACION_ESP

        b_color = {
            "ESPANA": "#dc2626", "EMPRESA": "#d97706", "GLOBAL": "#0f172a",
            "CRIMENES": "#991b1b", "CRIPTO": "#7c3aed", "DETENCION": "#be123c",
            "TELEGRAM": "#0284c7", "REDES": "#059669"
        }.get(cat, "#0f172a")

        sev_color = "#dc2626" if sev >= 4 else ("#d97706" if sev == 3 else "#475569")

        badges = f'<span class="badge-item" style="background:{sev_color};color:#ffffff;">SEVERIDAD {sev}/5</span>'
        if es_reciente_48h(item.get("fecha_dt")):
            badges += '<span class="badge-item" style="background:#fef08a;color:#854d0e;border:1px solid #eab308;margin-left:6px;">🔥 RELEVANTE (48H)</span>'

        if es_desinfo:
            badges += '<span class="badge-item" style="background:#ffe4e6;color:#be123c;border:1px solid #fda4af;margin-left:6px;">📢 DESINFORMACIÓN / FIMI</span>'

        correlacionados = item.get("canales_correlacionados", [])
        if len(correlacionados) > 1:
            badges += f'<span class="badge-item" style="background:#e0e7ff;color:#3730a3;border:1px solid #c7d2fe;margin-left:6px;">🔄 DIFUNDIDO EN {len(correlacionados)} FUENTES</span>'

        fecha_str = item.get("fecha_dt").strftime("%d/%m/%Y %H:%M")
        t_safe = html_module.escape(str(item.get("titular", "")))
        h_safe = html_module.escape(str(item.get("hecho_reportado", "")))
        a_safe = html_module.escape(str(item.get("evaluacion_contexto", "")))
        canal_safe = html_module.escape(str(canal_nombre))
        enlace = item.get("enlace", "#")
        if not es_url_segura(enlace):
            enlace = "#"

        media_html = f'<div class="osint-media"><img src="{html_module.escape(item.get("multimedia"))}" alt="Evidencia" loading="lazy" /></div>' if item.get("multimedia") and es_url_segura(item.get("multimedia")) else ""
        vid_html = f'<a href="{html_module.escape(enlace)}" target="_blank" class="btn-video">▶️ VER CONTENIDO MULTIMEDIA</a>' if item.get("tiene_video") else ""

        bloque_reporte = f'<div class="osint-resumen"><strong>📄 Reporte/Post:</strong><br>{h_safe}</div>' if cat in ["TELEGRAM", "REDES"] else ""

        tarjeta_html = (
            f'<div class="osint-card" style="border-left: 8px solid {b_color};">'
            f'<div class="osint-flex">{media_html}'
            f'<div class="osint-body">'
            f'<div style="margin-bottom:8px;">{badges}<span style="font-size:12px;color:#64748b;margin-left:10px;">📅 {fecha_str} UTC | 📢 {canal_safe}</span></div>'
            f'<div class="osint-title">{t_safe}</div>'
            f'{bloque_reporte}'
            f'<div class="osint-analisis"><strong>🧠 Análisis:</strong> {a_safe}</div>'
            f'<div style="display:flex;gap:10px;align-items:center;">'
            f'<a href="{enlace}" target="_blank" class="btn-source">🔗 ABRIR FUENTE ORIGINAL</a>'
            f'{vid_html}'
            f'</div>'
            f'</div></div></div>'
        )

        col_check, col_card = st.columns([0.05, 0.95])
        with col_check:
            if f"sel_{item_id}" not in st.session_state:
                st.session_state[f"sel_{item_id}"] = (item_id in st.session_state.seleccionadas_pdf)

            st.checkbox(
                "Incluir en PDF",
                key=f"sel_{item_id}",
                on_change=toggle_checkbox_callback,
                args=(item_id,),
                label_visibility="collapsed"
            )

        with col_card:
            st.markdown(tarjeta_html, unsafe_allow_html=True)

# ==========================================
# PESTAÑAS (ORDEN ORIGINAL)
# ==========================================
if "alertas_monitor" in st.session_state:
    l_esp = [x for x in datos_activos if x.get("categoria") == "ESPANA"]
    l_emp = [x for x in datos_activos if x.get("categoria") == "EMPRESA"]
    l_prensa = [x for x in datos_activos if x.get("categoria") == "GLOBAL"]
    l_crim = [x for x in datos_activos if x.get("categoria") == "CRIMENES"]
    l_cripto = [x for x in datos_activos if x.get("categoria") == "CRIPTO"]
    l_det = [x for x in datos_activos if x.get("categoria") == "DETENCION"]
    l_tg = [x for x in datos_activos if x.get("categoria") == "TELEGRAM"]
    l_redes = [x for x in datos_activos if x.get("categoria") == "REDES"]

    t1, t2, t3, t4, t5, t6, t7, t8 = st.tabs([
        f"🇪🇸 ESPAÑA ({len(l_esp)})",
        f"🏢 EMPRESAS ({len(l_emp)})",
        f"📰 GLOBAL ({len(l_prensa)})",
        f"⚖️ CRÍMENES GUERRA ({len(l_crim)})",
        f"🟣 CRIPTO ({len(l_cripto)})",
        f"🛑 DETENCIONES ({len(l_det)})",
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
        renderizar_tarjetas(l_det)
    with t7:
        renderizar_tarjetas(l_tg)
    with t8:
        renderizar_tarjetas(l_redes)
