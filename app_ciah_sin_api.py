# -*- coding: utf-8 -*-
"""
BUSCADOR DE AMENAZAS HÍBRIDAS - Sistema de vigilancia de fuentes abiertas
Versión corregida: traducción robusta, filtros con límites de palabra,
búsqueda booleana real, caché compartida y alertas con caducidad.
"""
import streamlit as st
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
import hmac
import time
import random
import threading
import unicodedata
from functools import lru_cache
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse
import pandas as pd

ZONA_MADRID = ZoneInfo("Europe/Madrid")
RETENCION_HORAS = 240      # las alertas caducan a los 10 días
VENTANA_TG_HORAS = 168     # Telegram: solo la última semana
LIMITE_TARJETAS = 60       # tarjetas por pestaña antes de "Mostrar más"
REFRESCO_SEG = 300         # el rastreo de fuentes se repite solo cada 5 min (segundo plano)

# ==========================================
# RECURSOS COMPARTIDOS (sobreviven a los reruns de Streamlit)
# ==========================================
@st.cache_resource(show_spinner=False)
def obtener_recursos():
    sesion = requests.Session()
    retries = Retry(total=1, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=60, pool_maxsize=60, max_retries=retries)
    sesion.mount("https://", adapter)
    sesion.mount("http://", adapter)
    sesion.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,pl;q=0.6,zh;q=0.5,ar;q=0.5",
    })
    return {
        "sesion": sesion,
        "cache_trad": {},
        "lock": threading.Lock(),
        "sem_trad": threading.Semaphore(6),
        "primera_vista": {},
        "despertar": threading.Event(), "listo": threading.Event(), "snapshot": None,
        "ocupado": False, "traduciendo": 0, "fallos": {}, "pausadas": {}, "hilo": None,
        "deepl_key": "", "deepl_pausa": 0,
    }

RECURSOS = obtener_recursos()
SESION_HTTP = RECURSOS["sesion"]

# ==========================================
# UTILIDADES DE TEXTO
# ==========================================
def obtener_fecha_madrid_str(dt=None):
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONA_MADRID).strftime("%d/%m/%Y %H:%M:%S")

def normalizar_texto(texto):
    if not texto:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

def limpiar_html_texto(texto):
    if not texto:
        return ""
    t = re.sub(r"<[^<]+?>", " ", str(texto))
    t = html_module.unescape(t)
    t = re.sub(r"&[a-zA-Z0-9#]+;", " ", t)
    t = re.sub(r"(?i)(suscr[ií]bete a nuestro bolet[ií]n|lee m[aá]s en|copyright|todos los derechos reservados|pincha aqu[ií]|sigue leyendo).*", "", t)
    return re.sub(r"\s+", " ", t).strip()

def quitar_firma(titulo, medio):
    """Quita ' - Medio' del final SOLO si coincide con el medio declarado por el feed."""
    if not titulo or not medio:
        return titulo
    m = re.search(r"\s*[-–—|·]\s*" + re.escape(medio) + r"\s*$", titulo, re.IGNORECASE)
    return titulo[:m.start()].strip() if m else titulo

def limpiar_url(url):
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:
        return url

def es_url_segura(url):
    try:
        p = urlparse(str(url).strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def url_sanitizada(url):
    return html_module.escape(str(url).strip(), quote=True) if es_url_segura(url) else "about:blank"

def esc(x):
    return html_module.escape(str(x if x is not None else ""))

def id_determinista(url):
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16]

# ==========================================
# PATRONES CON LÍMITES DE PALABRA
# ==========================================
def rx_mixto(exactos=(), prefijos=()):
    """Regex sobre texto normalizado. exactos: palabra completa. prefijos: inicio de palabra."""
    partes = []
    if exactos:
        alt = "|".join(sorted((re.escape(normalizar_texto(t)) for t in exactos), key=len, reverse=True))
        partes.append(rf"(?:{alt})(?!\w)")
    if prefijos:
        alt = "|".join(sorted((re.escape(normalizar_texto(t)) for t in prefijos), key=len, reverse=True))
        partes.append(rf"(?:{alt})")
    return re.compile(r"(?<!\w)(?:" + "|".join(partes) + ")")

def subcadenas(lista):
    return [normalizar_texto(x) for x in lista]

ACTORES_FUERTES = rx_mixto(
    exactos=[
        "rusia", "ruso", "rusa", "rusos", "rusas", "moscu", "kremlin", "putin", "ucrania", "ucraniano",
        "ucraniana", "ucranianos", "ucranianas", "zelensky", "zelenski", "bielorrusia", "kaliningrado",
        "lukashenko", "transnistria", "moldavia", "chechenia", "kadyrov",
        "russia", "russian", "russians", "moscow", "ukraine", "ukrainian", "ukrainians", "belarus",
        "china", "chino", "chinos", "chinas", "chinese", "pekin", "beijing", "xi jinping", "taiwan", "taipei",
        "hong kong", "partido comunista chino", "pcc",
        "kursk", "belgorod", "donbas", "donbass", "crimea", "zaporiyia", "zaporizhzhia", "jerson", "kherson",
        "donetsk", "lugansk", "luhansk", "jarkov", "kharkiv", "kyiv", "kiev", "wagner",
        "gru", "fsb", "svr", "sbu", "gur", "hur", "zsu", "vsu", "mss", "abw",
        "apt28", "apt29", "apt41", "fancy bear", "cozy bear", "sandworm", "turla", "gamaredon", "noname057",
        "killnet", "garantex", "volt typhoon", "flax typhoon", "mustang panda", "salt typhoon",
        "shariy", "sharij", "zvinchuk", "rybar",
        "рф", "гру", "фсб", "свр", "зсу", "всу", "сбу", "гур",
    ],
    prefijos=[
        "росси", "москв", "кремл", "путин", "украин", "киев", "київ", "донбас", "крым", "беларус",
        "вагнер", "шахед", "геран", "искандер", "шарий", "рыбар", "звинчук",
        "rosj", "rosyjsk", "ukrain",
    ],
)
SUB_NOLATINAS_ACTORES = subcadenas(["中国", "北京", "习近平", "解放军", "台湾", "华为", "国安部", "俄罗斯", "乌克兰",
                                    "روسيا", "أوكرانيا", "الصين", "بوتين"])
ACTORES_DEBILES = rx_mixto(
    exactos=["otan", "nato", "dsn", "cni", "incibe", "ccn-cert", "europol", "eurojust", "stratcom", "euvsdisinfo",
             "bellingcat", "occrp", "meduza", "mediazona", "isw", "deepstate", "the insider", "kse institute"],
)
PAISES_AJENOS = rx_mixto(
    exactos=["cuba", "cubano", "cubana", "la habana", "venezuela", "venezolano", "maduro", "caracas", "nicaragua",
             "managua", "colombia", "bogota", "gaza", "hamas", "hezbollah", "hezbola", "netanyahu", "tel aviv",
             "cisjordania", "palestina", "palestino", "israel", "israeli", "yemen", "huti", "huties", "iran",
             "irani", "teheran", "siria", "damasco"],
)

VECTORES = rx_mixto(
    exactos=["dron", "drones", "drone", "uav", "ddos", "btc", "bitcoin", "usdt", "tether", "garantex", "monero",
             "blockchain", "crypto", "icc", "ofac", "mafia", "spy", "spies", "apt28", "apt29", "apt41", "sandworm",
             "noname057", "killnet", "volt typhoon", "fimi", "doppelganger"],
    prefijos=[
        "hibrid", "hybrid", "sabotaj", "sabotag", "saboteador", "saboteur", "sabotaz", "espia", "espionaje",
        "espionage", "ciberataq", "ciberespion", "cyberattack", "cyber-attack", "cyberespionage",
        "cyber espionage", "ciberoperac", "hackeo", "hacker", "ransomware", "malware", "desinformacion",
        "disinformation", "propaganda", "injerencia", "foreign interference", "red de bots", "cripto",
        "sancion", "sanction", "elusion", "evasion de sancion", "flota fantasma", "shadow fleet",
        "cable submarino", "undersea cable", "subsea cable", "crimen de guerra", "crimenes de guerra",
        "war crime", "corte penal internacional", "tribunal penal internacional", "genocidio", "tortura",
        "fosa comun", "masacre", "detenid", "detencion", "detienen", "detiene", "arrest", "detained",
        "guerra electronica", "jamming", "misil", "missile", "incendio provocado", "arson", "incendiari",
        "atentado", "terroris", "sicario", "asesinato", "homicidio", "envenenamiento", "crimen organizado",
        "narcotrafic", "lista negra", "bombardeo", "ofensiva", "invasion", "ejecucion de prisioneros",
        "pow execution", "doble uso", "dual-use", "dual use",
        # cirílico
        "диверс", "шпион", "хакер", "кибер", "ддос", "саботаж", "гибрид", "бпла", "дрон", "рэб", "арест",
        "задержан", "санкц", "кабел", "теракт", "поджог", "взрыв", "обстрел", "убийств", "ликвидац",
        "ракет", "теневой флот", "тіньовий флот",
        # polaco
        "szpieg", "dywersj", "cyberatak", "podpalen",
    ],
)
VECTOR_AGENTE = re.compile(r"(?<!\w)агент(?!ств)")
SUB_NOLATINAS_VECTORES = subcadenas(["间谍", "تجسس", "طائرات مسيرة"])
EMPRESAS_VECTOR = rx_mixto(exactos=["destinus", "uav navigation", "oesia", "tecnobit", "indra", "escribano"])

PAPA = rx_mixto(exactos=["papa", "vaticano", "santa sede", "pontifice", "leon xiv"])
CIBER_ESP = rx_mixto(prefijos=["ciberataque", "sabotaje", "espionaje", "hack", "ddos"])
SPAM = rx_mixto(exactos=[
    "cambio de hora", "horario de verano", "horario de invierno", "reloj", "master profesional", "lisa institute",
    "udima", "beca", "becas", "matriculate", "curso online", "descuento", "sorteo", "casino", "apuestas", "tarot",
    "horoscopo", "oferta de empleo", "loteria", "quiniela", "champions league", "liga de futbol",
    "receta de cocina", "supermercado", "rebajas"])

CANALES_DESINFORMACION_ESP = {"musicarusaesp", "irinamar_z", "eurekapress", "elojoen", "ophiucu", "asupersharij"}

EMPRESAS_ESTRATEGICAS_ESP = [
    "destinus", "mikhail kokorich", "kokorich", "uav navigation", "grupo oesia", "oesia", "tecnobit", "indra",
    "navantia", "escribano", "em&e", "sener", "arquimea", "santa barbara sistemas", "santa barbara", "gdels",
    "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa", "itp aero", "airbus espana", "airbus getafe",
    "hisdesat", "hispasat", "sateliot", "pld space", "amadeus", "aena", "enaire", "renfe", "adif", "telefonica",
    "iberdrola", "repsol", "naturgy", "redeia", "red electrica", "enagas", "talgo"]
RX_EMPRESAS = rx_mixto(exactos=EMPRESAS_ESTRATEGICAS_ESP)
RX_EMPRESA_GENERICA = rx_mixto(exactos=["empresa espanola", "empresas espanolas", "filial espanola",
                                        "sede en valdepenas", "industria de defensa espanola"])
RX_VINCULO_EMPRESA = rx_mixto(
    exactos=["rusia", "ruso", "rusa", "rusos", "moscu", "kremlin", "putin", "ucrania", "ucraniano", "ucraniana",
             "zelensky", "noname057", "killnet", "apt28", "apt29", "sandworm", "russia", "russian"],
    prefijos=["росси", "кремл", "путин", "украин"])

RX_DETENCION = rx_mixto(prefijos=["detenid", "detencion", "detienen", "detiene", "arrestad", "arrestan", "arresto",
                                  "arrest", "detained", "festnahme", "verhaftet", "desarticulad",
                                  "prision preventiva", "prision provisional", "aresztowan", "zatrzyman",
                                  "задержан", "арестован"])
RX_CRIPTO = rx_mixto(exactos=["usdt", "tether", "btc", "bitcoin", "monero", "garantex", "blockchain", "crypto"],
                     prefijos=["cripto"])
RX_SANCIONES = rx_mixto(prefijos=["sancion", "sanction", "elusion", "evasion de sancion", "flota fantasma",
                                  "shadow fleet", "petroleros fantasma", "tope al crudo", "price cap", "doble uso",
                                  "dual-use", "dual use", "triangulacion", "lista negra de la ue", "ofac",
                                  "opensanctions", "kse institute", "centre for research on energy",
                                  "contrabando de chips", "теневой флот", "санкц"])
RX_ESPANA = rx_mixto(exactos=[
    "espana", "espanol", "espanola", "espanoles", "espanolas", "spain", "spanish", "madrid", "barcelona",
    "cartagena", "torrejon", "cni", "dsn", "incibe", "ccn-cert", "audiencia nacional", "guardia civil",
    "policia nacional", "gobierno de espana", "gobierno espanol", "ministerio de defensa", "margarita robles",
    "ejercito de tierra", "armada espanola", "ejercito del aire", "militares espanoles", "seguridad nacional",
    "base naval de rota", "base de rota", "base de moron"])
RX_ESP_FUERA = rx_mixto(exactos=["en ucrania", "en rusia", "en polonia", "en alemania", "en dinamarca", "en pekin",
                                 "en china", "eeuu", "estados unidos"])
RX_ESP_FUERTE = rx_mixto(exactos=["militares espanoles", "cni", "dsn", "gobierno de espana", "ministerio de defensa",
                                  "incibe", "guardia civil"])
RX_CHINA = rx_mixto(
    exactos=["china", "chino", "chinos", "chinas", "chinese", "pekin", "beijing", "xi jinping",
             "partido comunista chino", "pcc", "ejercito popular de liberacion", "taiwan", "taipei", "hong kong",
             "apt41", "volt typhoon", "flax typhoon", "mustang panda", "salt typhoon", "mss",
             "ministerio de seguridad del estado", "comisarias chinas", "estaciones de policia chinas"])
SUB_CHINO = subcadenas(["中国", "北京", "习近平", "解放军", "台湾", "华为", "国安部", "间谍"])
RX_CRIMENES = rx_mixto(exactos=["icc", "bucha", "irpin", "izium", "mariupol"],
                       prefijos=["crimen de guerra", "crimenes de guerra", "war crime", "corte penal internacional",
                                 "tribunal penal internacional", "lesa humanidad", "genocidio",
                                 "ejecucion de prisioneros", "pow execution", "tortura", "fosa comun",
                                 "fosas comunes", "masacre", "deportacion", "menores deportados", "ninos robados",
                                 "ataque a civiles", "ataque contra civiles", "civiles muertos",
                                 "bombardeo de hospital", "bombardeo residencial", "edificio residencial",
                                 "escuela bombardeada", "hospital infantil", "terroris", "atentado",
                                 "paquete incendiario", "asesinato selectivo", "homicidio selectivo", "sicario"])
RX_DESPARASITAR = re.compile(
    r"marketscreener\s*espa[nñ]a|libertad\s*digital|el\s*confidencial|el\s*periodico|el\s*pais|el\s*mundo|la\s*razon|"
    r"the\s*objective|europa\s*press|agencia\s*efe|rtve|investigative\s*journalism\s*foundation|24horas|"
    r"responsible\s*statecraft|aleteia(\.org)?|vatican\s*news|cmmedia", re.IGNORECASE)

# ==========================================
# FILTROS GEOPOLÍTICOS
# ==========================================
def es_publicidad_o_spam(texto):
    return bool(SPAM.search(normalizar_texto(texto)))

def tiene_vinculo_geopolitico(texto):
    if not texto or es_publicidad_o_spam(texto):
        return False
    t = normalizar_texto(texto)
    if PAPA.search(t) and not CIBER_ESP.search(t):
        return False
    if ACTORES_FUERTES.search(t) or any(s in t for s in SUB_NOLATINAS_ACTORES):
        return True
    if ACTORES_DEBILES.search(t):
        return not PAISES_AJENOS.search(t)
    return False

def tiene_vector_amenaza_global(texto):
    t = normalizar_texto(texto)
    if PAPA.search(t) and not CIBER_ESP.search(t):
        return False
    return bool(EMPRESAS_VECTOR.search(t) or VECTORES.search(t) or VECTOR_AGENTE.search(t)
                or any(s in t for s in SUB_NOLATINAS_VECTORES))

def cumple_ventana_temporal(fecha_dt, horas):
    if not fecha_dt:
        return False
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.replace(tzinfo=timezone.utc)
    dif = datetime.now(timezone.utc) - fecha_dt.astimezone(timezone.utc)
    return timedelta(minutes=-10) <= dif <= timedelta(hours=horas)

# ==========================================
# TRADUCCIÓN ROBUSTA
# ==========================================
URL_TRADUCTOR = "https://translate.googleapis.com/translate_a/single"
STOP_ES = {"de", "la", "el", "en", "y", "que", "los", "las", "del", "por", "con", "una", "para", "se", "un", "su",
           "al", "es", "lo", "como", "mas", "pero", "sus", "este", "esta", "ha", "han"}
ESPECIFICAS_ES = {"los", "las", "del", "una", "para", "pero", "sus", "como", "han", "esta", "mas"}

def proporcion_no_latina(t):
    letras = [c for c in t if c.isalpha()]
    if not letras:
        return 0.0
    return sum(1 for c in letras if ord(c) > 0x024F) / len(letras)

def parece_espanol(t):
    if proporcion_no_latina(t) > 0.10:
        return False
    palabras = re.findall(r"[a-záéíóúñü]+", t.lower())
    if len(palabras) < 5:
        return False
    palabras = [normalizar_texto(p) for p in palabras]
    stops = sum(1 for p in palabras if p in STOP_ES)
    especificas = len({p for p in palabras if p in ESPECIFICAS_ES})
    return stops / len(palabras) >= 0.25 and especificas >= 2

def trocear_texto(texto, limite=900):
    frases = re.split(r"(?<=[.!?。؟])\s+", texto)
    chunks, actual = [], ""
    for f in frases:
        while len(f) > limite:
            corte = f.rfind(" ", 0, limite)
            corte = corte if corte > 200 else limite
            if actual:
                chunks.append(actual)
                actual = ""
            chunks.append(f[:corte])
            f = f[corte:].lstrip()
        if len(actual) + len(f) + 1 <= limite:
            actual = f"{actual} {f}".strip()
        else:
            if actual:
                chunks.append(actual)
            actual = f
    if actual:
        chunks.append(actual)
    return chunks

def _preparar_para_traducir(texto, max_chars=1500):
    t = limpiar_html_texto(texto)
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0]
    return t

def _clave_trad(t):
    return hashlib.sha1(t.encode("utf-8")).hexdigest()

def _llamar_deepl(texto):
    """DeepL (opcional): solo si hay DEEPL_API_KEY en los secretos. Cuota gratuita: 500.000 caracteres/mes."""
    clave = RECURSOS.get("deepl_key", "")
    if not clave or time.time() < RECURSOS.get("deepl_pausa", 0):
        return None, ""
    host = "api-free.deepl.com" if clave.endswith(":fx") else "api.deepl.com"
    try:
        with RECURSOS["sem_trad"]:
            r = SESION_HTTP.post(f"https://{host}/v2/translate",
                                 headers={"Authorization": f"DeepL-Auth-Key {clave}"},
                                 data={"text": texto, "target_lang": "ES"}, timeout=(3, 10))
        if r.status_code == 200:
            tr = r.json()["translations"][0]
            return tr["text"], str(tr.get("detected_source_language", "")).lower()
        if r.status_code == 456:      # cuota mensual agotada
            RECURSOS["deepl_pausa"] = time.time() + 6 * 3600
        elif r.status_code in (401, 403):
            RECURSOS["deepl_pausa"] = time.time() + 3600
    except Exception:
        pass
    return None, ""

def _llamar_google(texto):
    for intento, client in enumerate(("gtx", "dict-chrome-ex", "gtx")):
        espera = 0
        try:
            params = {"client": client, "sl": "auto", "tl": "es", "dt": "t"}
            with RECURSOS["sem_trad"]:
                r = SESION_HTTP.post(URL_TRADUCTOR, params=params, data={"q": texto}, timeout=(3, 8))
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data and isinstance(data[0], list):
                    partes = [seg[0] for seg in data[0] if isinstance(seg, list) and seg and seg[0]]
                    idioma = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
                    if partes:
                        return "".join(partes), idioma
            elif r.status_code in (429, 503):
                espera = 0.8 * (2 ** intento) + random.random() * 0.4
        except Exception:
            espera = 0.3
        if espera:
            time.sleep(espera)
    return None, ""

def _llamar_traductor(texto):
    """DeepL si hay clave y cuota; si no, el endpoint gratuito. Devuelve (traducción, idioma) o (None, '')."""
    res, idioma = _llamar_deepl(texto)
    if res is not None:
        return res, idioma
    return _llamar_google(texto)

def traduccion_en_cache(texto, max_chars=1500):
    """Solo consulta la caché (sin red). Devuelve (texto, True) o (None, False)."""
    t = _preparar_para_traducir(texto, max_chars)
    if len(t) < 4:
        return t, True
    with RECURSOS["lock"]:
        hit = RECURSOS["cache_trad"].get(_clave_trad(t))
    return (hit, True) if hit else (None, False)

def traducir_texto(texto, lang_hint="", max_chars=1500):
    """Devuelve (texto_en_español, ok). Si ok es False NO se cachea y se reintentará."""
    t = _preparar_para_traducir(texto, max_chars)
    if len(t) < 4:
        return t, True
    if lang_hint == "es" or parece_espanol(t):
        return t, True
    clave = _clave_trad(t)
    with RECURSOS["lock"]:
        hit = RECURSOS["cache_trad"].get(clave)
    if hit:
        return hit, True
    salida, idiomas = [], []
    for chunk in trocear_texto(t):
        res, idioma = _llamar_traductor(chunk)
        if res is None:
            return t, False
        salida.append(res.strip())
        idiomas.append(idioma)
    if idiomas and all(idiomas) and all(i.startswith("es") for i in idiomas):
        with RECURSOS["lock"]:
            RECURSOS["cache_trad"][clave] = t       # ya estaba en español
        return t, True
    trad = limpiar_html_texto(" ".join(salida))
    if len(trad) < 2 or proporcion_no_latina(trad) > 0.30:
        return t, False
    with RECURSOS["lock"]:
        RECURSOS["cache_trad"][clave] = trad
    return trad, True

def traducir_lote(tareas):
    """tareas: lista de (texto, lang_hint) -> lista de (texto_es, ok)."""
    if not tareas:
        return []
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(lambda p: traducir_texto(p[0], p[1]), tareas))

def aplicar_cache_traducciones(alertas):
    """Recoge de la caché los titulares que el hilo de fondo ya tradujo. Instantáneo, sin red."""
    cambiadas = []
    for a in alertas:
        if a.get("traducido_ok"):
            continue
        txt, ok = traduccion_en_cache(a["titular_raw"])
        if ok:
            a["titular_es"], a["traducido_ok"] = txt, True
            cambiadas.append(a)
    return cambiadas

def traducir_titulares_pendientes(alertas, limite=60):
    """Botón 'Traducir ahora': traduce los titulares pendientes más recientes."""
    pend = sorted((a for a in alertas if a.get("extranjero") and not a["traducido_ok"]),
                  key=lambda a: a["fecha_dt"], reverse=True)[:limite]
    if not pend:
        return []
    res = traducir_lote([(a["titular_raw"], a.get("lang", "")) for a in pend])
    hechas = []
    for a, (t, ok) in zip(pend, res):
        if ok:
            a["titular_es"], a["traducido_ok"] = t, True
            hechas.append(a)
    return hechas

def titular_visible(a, traducir):
    t = a["titular_es"] if (traducir and a.get("traducido_ok") and a.get("titular_es")) else a["titular_raw"]
    if a["plataforma"] == "TELEGRAM":
        return f"[{a['canal'].replace('@', '').upper()}] {t}"
    return t

def cuerpo_visible(a, traducir):
    return a["cuerpo_es"] if (traducir and a.get("cuerpo_ok") and a.get("cuerpo_es")) else a["cuerpo_raw"]

# ==========================================
# CLASIFICACIÓN, NER Y CONTEXTO
# ==========================================
def _rx(p):
    return re.compile(p)

NER_ACTORES = {
    "GRU": _rx(r"(?<!\w)gru(?!\w)"), "FSB": _rx(r"(?<!\w)fsb(?!\w)"), "SVR": _rx(r"(?<!\w)svr(?!\w)"),
    "APT41": _rx(r"(?<!\w)apt41(?!\w)"), "Volt Typhoon": _rx(r"volt typhoon"),
    "Mustang Panda": _rx(r"mustang panda"), "Salt Typhoon": _rx(r"salt typhoon"),
    "NoName057": _rx(r"noname057"), "Sandworm": _rx(r"sandworm"),
    "APT28 (Fancy Bear)": _rx(r"(?<!\w)(apt28|fancy bear)(?!\w)"), "APT29 (Cozy Bear)": _rx(r"(?<!\w)(apt29|cozy bear)(?!\w)"),
    "Killnet": _rx(r"killnet"), "Wagner": _rx(r"(?<!\w)wagner(?!\w)"),
    "MSS (China)": _rx(r"(?<!\w)mss(?!\w)|ministerio de seguridad del estado"),
    "SBU": _rx(r"(?<!\w)sbu(?!\w)"), "GUR": _rx(r"(?<!\w)(gur|hur)(?!\w)"), "DSN": _rx(r"(?<!\w)dsn(?!\w)"),
    "CNI": _rx(r"(?<!\w)cni(?!\w)"), "NATO StratCom": _rx(r"stratcom"), "Europol": _rx(r"europol"),
    "Eurojust": _rx(r"eurojust"), "Bellingcat": _rx(r"bellingcat"), "OCCRP": _rx(r"(?<!\w)occrp(?!\w)"),
}
NER_VECTORES = {
    "Sabotaje": _rx(r"(?<!\w)(sabotaj|sabotag|sabotaz|cable submarino|red ferroviaria|incendio provocado|arson)"),
    "Ciberataque": _rx(r"(?<!\w)(ciberataq|ransomware|ddos|malware|cyberattack)"),
    "Desinformación / FIMI": _rx(r"(?<!\w)(desinformacion|disinformation|fimi|propaganda|fake news|red de bots|doppelganger)"),
    "Flota Fantasma": _rx(r"(flota fantasma|shadow fleet|petrolero clandestino)"),
    "Doble Uso / Sanciones": _rx(r"(?<!\w)(elusion|evasion de sancion|doble uso|dual.use|chips militares|triangulacion|ofac|opensanctions)"),
    "Sicariato / Muerte": _rx(r"(?<!\w)(sicario|asesinato selectivo|homicidio|envenenamiento|defenestrado)"),
    "Terrorismo": _rx(r"(?<!\w)(terroris|atentado|paquete incendiario|explosivo)"),
}
NER_EMPRESAS = {
    "Destinus": _rx(r"(?<!\w)destinus(?!\w)"), "Indra": _rx(r"(?<!\w)indra(?!\w)"),
    "Oesía / Tecnobit": _rx(r"(?<!\w)(oesia|tecnobit)(?!\w)"), "Navantia": _rx(r"(?<!\w)navantia(?!\w)"),
    "Escribano": _rx(r"(?<!\w)escribano(?!\w)"), "GDELS / Santa Bárbara": _rx(r"(?<!\w)(gdels|santa barbara)(?!\w)"),
}

def extraer_entidades_clave(t_norm):
    et = []
    for n, p in NER_ACTORES.items():
        if p.search(t_norm):
            et.append((n, "#2563eb"))
    for n, p in NER_VECTORES.items():
        if p.search(t_norm):
            et.append((n, "#dc2626"))
    for n, p in NER_EMPRESAS.items():
        if p.search(t_norm):
            et.append((n, "#059669"))
    return et[:5]

def clasificar(a):
    if a["plataforma"] == "REDES":
        return "REDES"
    if a["plataforma"] == "TELEGRAM":
        return "TELEGRAM"
    t = normalizar_texto(RX_DESPARASITAR.sub(
        " ", f"{a['titular_raw']} {a.get('titular_es', '')} {a['cuerpo_raw']} {a.get('cuerpo_es', '')}"))
    if (RX_EMPRESAS.search(t) or RX_EMPRESA_GENERICA.search(t)) and RX_VINCULO_EMPRESA.search(t):
        return "EMPRESA"
    if RX_ESPANA.search(t) and not (RX_ESP_FUERA.search(t) and not RX_ESP_FUERTE.search(t)):
        return "ESPANA"
    if RX_DETENCION.search(t):
        return "DETENCION"
    if RX_CRIPTO.search(t):
        return "CRIPTO"
    if RX_SANCIONES.search(t):
        return "SANCIONES"
    if RX_CHINA.search(t) or any(s in t for s in SUB_CHINO):
        return "CHINA"
    if RX_CRIMENES.search(t):
        return "CRIMENES"
    return "GLOBAL"

def generar_analisis_contexto(categoria, canal="", canal_id=""):
    c = f"{canal} {canal_id}".lower()
    if "rybar" in c:
        return "Canal de análisis militar prorruso (@rybar), fundado por Mijaíl Zvinchuk. Fuente de parte: contrastar antes de citar."
    if canal_id.lower() in CANALES_DESINFORMACION_ESP:
        return "Canal incluido en la lista de vigilancia del analista como posible difusor de propaganda/desinformación (FIMI) prorrusa. Contrastar antes de citar."
    if "sharij" in c or "shariy" in c:
        return "Canal de Anatolii Sharij: contenido de opinión y seguimiento del conflicto ruso-ucraniano."
    if any(k in c for k in ["bellingcat", "insider", "occrp"]):
        return "Investigación OSINT / periodismo de investigación sobre inteligencia, operaciones encubiertas y crimen transnacional."
    if any(k in c for k in ["stratcom", "euvsdisinfo", "dfrlab"]):
        return "Monitorización institucional de manipulación informativa e injerencia de actores extranjeros (FIMI)."
    textos = {
        "SANCIONES": "Vigilancia de elusión de sanciones: flota fantasma, redes de contrabando de doble uso o triangulación logística.",
        "CHINA": "Actividad estratégica de la República Popular China: ciberespionaje, injerencia en Occidente y alineación con Moscú.",
        "EMPRESA": "Seguimiento corporativo: empresas estratégicas españolas frente a amenazas híbridas, ciberataques o contratos de defensa.",
        "CRIMENES": "Documentación de crímenes de guerra, terrorismo no convencional y ataques a civiles.",
        "DETENCION": "Operaciones policiales y de contrainteligencia: arresto de espías, saboteadores o redes de inteligencia extranjera.",
        "REDES": "Contenido capturado en redes sociales y canales de análisis del conflicto.",
        "ESPANA": "Posible impacto en la seguridad nacional e infraestructuras críticas de España.",
        "CRIPTO": "Inteligencia financiera (FININT): evasión de sanciones y uso de criptoactivos.",
        "TELEGRAM": "Reporte capturado de canales de Telegram: información de primera línea sin verificar.",
    }
    return textos.get(categoria, "Actividad vinculada a operaciones híbridas globales, crimen organizado y servicios de inteligencia.")

def enriquecer(a):
    a["corpus_norm"] = normalizar_texto(
        f"{a['titular_raw']} {a.get('titular_es', '')} {a['cuerpo_raw']} {a.get('cuerpo_es', '')}")
    a["categoria"] = clasificar(a)
    a["etiquetas_ner"] = extraer_entidades_clave(a["corpus_norm"])
    a["evaluacion_contexto"] = generar_analisis_contexto(a["categoria"], a["canal"], a.get("canal_id", ""))

# ==========================================
# BÚSQUEDA BOOLEANA REAL (AND / OR / NOT / - / "frases" / paréntesis)
# ==========================================
RE_SIN_ESPACIOS = re.compile(r"[\u4e00-\u9fff\u0600-\u06ff]")

def _tokenizar_consulta(q):
    tokens, i, n = [], 0, len(q)
    while i < n:
        c = q[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            tokens.append((c, c))
            i += 1
            continue
        if c == '"':
            j = q.find('"', i + 1)
            if j == -1:
                j = n
            frase = q[i + 1:j].strip()
            if frase:
                tokens.append(("FRASE", frase))
            i = j + 1
            continue
        j = i
        while j < n and not q[j].isspace() and q[j] not in '()"':
            j += 1
        palabra = q[i:j]
        i = j
        up = palabra.upper()
        if up in ("AND", "OR", "NOT"):
            tokens.append((up, up))
        elif palabra == "-":
            tokens.append(("NOT", "-"))
        elif palabra.startswith("-") and len(palabra) > 1:
            tokens.append(("NOT", "-"))
            tokens.append(("TERM", palabra[1:]))
        else:
            tokens.append(("TERM", palabra))
    depth, limpios = 0, []
    for tk in tokens:
        if tk[0] == "(":
            depth += 1
        elif tk[0] == ")":
            if depth == 0:
                continue
            depth -= 1
        limpios.append(tk)
    return limpios

class _Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i][0] if self.i < len(self.t) else None

    def eat(self):
        tk = self.t[self.i]
        self.i += 1
        return tk

    def parse_or(self):
        nodos = [self.parse_and()]
        while self.peek() == "OR":
            self.eat()
            nodos.append(self.parse_and())
        return nodos[0] if len(nodos) == 1 else ("OR", nodos)

    def parse_and(self):
        nodos = []
        while self.peek() is not None and self.peek() not in ("OR", ")"):
            if self.peek() == "AND":
                self.eat()
                continue
            nodos.append(self.parse_not())
        if not nodos:
            return ("TRUE",)
        return nodos[0] if len(nodos) == 1 else ("AND", nodos)

    def parse_not(self):
        if self.peek() == "NOT":
            self.eat()
            return ("NOT", self.parse_not())
        return self.parse_atom()

    def parse_atom(self):
        tipo = self.peek()
        if tipo == "(":
            self.eat()
            nodo = self.parse_or()
            if self.peek() == ")":
                self.eat()
            return nodo
        if tipo in ("TERM", "FRASE"):
            return (tipo, self.eat()[1])
        return ("TRUE",)

@lru_cache(maxsize=64)
def compilar_consulta(q):
    return _Parser(_tokenizar_consulta(q)).parse_or()

def _evaluar(nodo, t):
    k = nodo[0]
    if k == "TRUE":
        return True
    if k == "TERM":
        term = normalizar_texto(nodo[1]).rstrip("*")
        if not term:
            return True
        if RE_SIN_ESPACIOS.search(term):
            return term in t
        return re.search(r"(?<!\w)" + re.escape(term), t) is not None
    if k == "FRASE":
        return normalizar_texto(nodo[1]) in t
    if k == "NOT":
        return True if nodo[1] == ("TRUE",) else not _evaluar(nodo[1], t)
    if k == "AND":
        return all(_evaluar(n, t) for n in nodo[1])
    if k == "OR":
        return any(_evaluar(n, t) for n in nodo[1])
    return True

def cumple_busqueda_booleana(item, query_str):
    if not query_str or not query_str.strip():
        return True
    return _evaluar(compilar_consulta(query_str.strip()), item.get("corpus_norm", ""))

# ==========================================
# FECHAS E ITEMS
# ==========================================
def parsear_fecha(texto):
    if not texto:
        return None
    texto = texto.strip()
    try:
        dt = parsedate_to_datetime(texto)
    except Exception:
        try:
            dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def resolver_fecha(dt, id_):
    """Si la fecha falta o es futura, usa la 'primera vez vista' (estable entre refrescos)."""
    ahora = datetime.now(timezone.utc)
    if dt is not None and dt <= ahora + timedelta(hours=2):
        return min(dt, ahora), False
    with RECURSOS["lock"]:
        return RECURSOS["primera_vista"].setdefault(id_, ahora), True

def crear_item(id_, plataforma, canal, titulo, cuerpo, enlace, dt, lang="", medio="", canal_id="",
               multimedia=None, tiene_video=False):
    fecha, estimada = resolver_fecha(dt, id_)
    return {
        "id": id_, "plataforma": plataforma, "canal": canal, "canal_id": canal_id, "medio": medio, "lang": lang,
        "fecha_dt": fecha, "fecha_estimada": estimada, "titular_raw": titulo, "cuerpo_raw": cuerpo,
        "multimedia": multimedia, "tiene_video": tiene_video, "enlace": enlace,
    }

# ==========================================
# RASTREADORES (no tocan st.session_state: se ejecutan en hilos)
# ==========================================
def _get(url, timeout=(3, 6)):
    return SESION_HTTP.get(url, timeout=timeout)

def raspar_youtube_canal(canal_id, etiqueta):
    items, err = [], None
    try:
        r = _get(f"https://www.youtube.com/feeds/videos.xml?channel_id={canal_id}")
        if r.status_code != 200:
            return [], f"{etiqueta} (HTTP {r.status_code})"
        root = ET.fromstring(r.content)
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(f".//{ns}entry")[:20]:
            tit = limpiar_html_texto(entry.findtext(f"{ns}title", ""))
            link_el = entry.find(f"{ns}link")
            link = link_el.get("href", "") if link_el is not None else ""
            desc = limpiar_html_texto(entry.findtext(".//{http://search.yahoo.com/mrss/}description", ""))
            texto = f"{tit} {desc}"
            if not (tiene_vinculo_geopolitico(texto) and tiene_vector_amenaza_global(texto)):
                continue
            id_ = f"YT-{id_determinista(link)}"
            items.append(crear_item(id_, "REDES", etiqueta, tit, desc[:1500], link,
                                    parsear_fecha(entry.findtext(f"{ns}published", "")), tiene_video=True))
    except Exception as e:
        err = f"{etiqueta} ({type(e).__name__})"
    return items, err

NOMBRES_TG = {"rybar": "Rybar (@rybar)", "ASupersharij": "Telegram: Anatolii Sharij"}
CANALES_TG_REDES = {"rybar", "ASupersharij"}

def raspar_un_canal_tg(canal):
    mensajes, err = [], None
    try:
        r = _get(f"https://t.me/s/{canal}")
        if r.status_code != 200:
            return [], f"Telegram @{canal} (HTTP {r.status_code})"
        soup = BeautifulSoup(r.text, "html.parser")
        for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-40:]:
            time_tag = p.find("time")
            dt = parsear_fecha(time_tag.get("datetime")) if time_tag and time_tag.get("datetime") else None
            if dt is not None and not cumple_ventana_temporal(dt, VENTANA_TG_HORAS):
                continue
            t_div = p.find("div", class_="tgme_widget_message_text")
            texto_raw = t_div.get_text(separator=" ", strip=True) if t_div else ""
            if len(texto_raw) < 20 or es_publicidad_o_spam(texto_raw):
                continue
            texto = limpiar_html_texto(re.sub(r"http\S+", "", texto_raw))
            # Telegram: canales ya curados, basta el vínculo geopolítico
            if len(texto) < 15 or not tiene_vinculo_geopolitico(texto):
                continue
            foto, video = None, bool(p.find(class_=re.compile(r"video")))
            for tag in p.find_all("a", class_="tgme_widget_message_photo_wrap"):
                m = re.search(r"url\(['\"]?(https?://[^'\")]+)['\"]?\)", tag.get("style", ""))
                if m:
                    foto = m.group(1)
                    break
            a_link = p.find("a", class_="tgme_widget_message_date")
            link = a_link["href"] if a_link and a_link.get("href") else f"https://t.me/s/{canal}"
            primera = re.split(r"(?<=[.!?])\s+", texto)[0]
            tit = primera if 20 <= len(primera) <= 160 else texto[:140]
            plat = "REDES" if canal in CANALES_TG_REDES else "TELEGRAM"
            nombre = NOMBRES_TG.get(canal, f"@{canal}")
            mensajes.append(crear_item(f"TG-{id_determinista(link)}", plat, nombre, tit, texto[:2200], link, dt,
                                       canal_id=canal, multimedia=foto, tiene_video=video))
    except Exception as e:
        err = f"Telegram @{canal} ({type(e).__name__})"
    return mensajes, err

def procesar_feed(datos):
    url, etiqueta, lang, *resto = datos
    plataforma = resto[0] if resto else "PRENSA"
    items, err = [], None
    try:
        r = _get(url)
        if r.status_code != 200:
            return [], f"{etiqueta} (HTTP {r.status_code})"
        root = ET.fromstring(r.content)
        for it in root.findall(".//item")[:60]:
            medio = limpiar_html_texto(it.findtext("source", "") or "")
            tit = quitar_firma(limpiar_html_texto(it.findtext("title", "")), medio)
            cuerpo = limpiar_html_texto(it.findtext("description", ""))[:1500]
            if cuerpo and normalizar_texto(cuerpo).startswith(normalizar_texto(tit)[:30]) and len(tit) >= 15:
                cuerpo = cuerpo[len(tit):].strip(" -–—|:") if cuerpo.lower().startswith(tit.lower()) else cuerpo
            if len(tit) < 12:
                continue
            texto = f"{tit} {cuerpo}"
            if not (tiene_vinculo_geopolitico(texto) and tiene_vector_amenaza_global(texto)):
                continue
            link = it.findtext("link", "") or ""
            id_ = f"{plataforma[:3]}-{id_determinista(link or tit)}"
            items.append(crear_item(id_, plataforma, etiqueta, tit, cuerpo, link,
                                    parsear_fecha(it.findtext("pubDate", "")), lang=lang, medio=medio))
    except Exception as e:
        err = f"{etiqueta} ({type(e).__name__})"
    return items, err

CANALES_YT = [
    ("UCVPYbobPRzz0SjinWekjUBw", "YouTube: Anatolii Sharij"),
    ("UCP2QApi83fGDJGyZ31yX-pQ", "YouTube: ISW War Studies"),
    ("UC6n-ikz_s3zP9v9H6a_3tKg", "YouTube: CSIS Strategic Studies"),
    ("UCvps_Xgqj0i2v4Puh2p-E2w", "YouTube: Kyiv Independent"),
    ("UC5q_h_4qjF04bJkU-Fq0e5A", "YouTube: BBC News Russian"),
    ("UCN3h_V5k0Z4P9WvE9j_Yg3A", "YouTube: DW News"),
    ("UC7_2eFfP_n5v3H_Z_L-v_4w", "YouTube: Bellingcat OSINT"),
]
CANALES_TG = [
    "rybar", "ASupersharij", "DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua", "GeneralStaffZSU",
    "kpszsu", "operativnoZSU", "CenterCounteringDisinformation", "meduzalive", "astrapress", "istories_media",
    "vchkogpu", "mediazzzona", "bbcrussian", "novayagazeta_europe", "theinsider", "kyivindependent_official",
    "pravda_com_ua", "ukrainenowenglish", "musicarusaesp", "Irinamar_Z", "eurekapress", "elOJOen", "ophiucu",
]

_GN = {"es": ("es", "ES", "ES:es"), "en": ("en-US", "US", "US:en"), "pl": ("pl", "PL", "PL:pl"),
       "ru": ("ru", "RU", "RU:ru"), "uk": ("uk", "UA", "UA:uk"), "ar": ("ar", "EG", "EG:ar")}

def _gn(q, idioma, etiqueta):
    hl, gl, ceid = _GN[idioma]
    return (f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}", etiqueta, idioma)

FUENTES_RSS = [
    _gn('(DSN+OR+"Seguridad+Nacional"+OR+"FIMI"+OR+"injerencia+extranjera")+España', "es", "DSN / Seguridad Nacional (ES)"),
    _gn('("Consilium"+OR+"Consejo+de+la+UE")+("amenazas+híbridas"+OR+"sanciones"+OR+"sabotaje")', "es", "UE Consilium (Híbridas)"),
    _gn('("NATO+StratCom"+OR+"StratCom+COE")+disinformation', "en", "NATO StratCom COE"),
    _gn('Europol+("hybrid+threats"+OR+"organized+crime"+OR+"sabotage"+OR+"arrested")', "en", "Europol Press & SOCTA"),
    _gn('Eurojust+("cross-border"+OR+"investigation"+OR+"sanctions")', "en", "Eurojust Judicial Ops"),
    ("https://euvsdisinfo.eu/feed/", "EUvsDisinfo", "en"),
    ("https://www.disinfo.eu/feed/", "EU DisinfoLab", "en"),
    ("https://citizenlab.ca/feed/", "Citizen Lab Tech", "en"),
    ("https://chinadigitaltimes.net/feed/", "China Digital Times", "en"),
    ("https://www.bellingcat.com/feed/", "Bellingcat OSINT", "en"),
    ("https://theins.ru/feed", "The Insider (RU/EN)", ""),
    ("https://www.occrp.org/en/feed", "OCCRP Investigations", "en"),
    ("https://theobjective.com/feed/", "The Objective (General)", "es"),
    ("https://theobjective.com/espana/feed/", "The Objective (España)", "es"),
    ("https://theobjective.com/internacional/feed/", "The Objective (Internacional)", "es"),
    _gn('(Rusia+OR+ruso+OR+Kremlin+OR+NoName057+OR+APT28)+(España+OR+Madrid+OR+CNI+OR+INCIBE)', "es", "España Seguridad (ES)"),
    _gn('(Russia+OR+Russian+OR+Kremlin)+(Spain+OR+Madrid)', "en", "Spain Watch (EN)"),
    _gn('(China+OR+Pekín+OR+Xi+Jinping+OR+APT41+OR+"Volt+Typhoon")+(España+OR+Occidente)', "es", "China Injerencia (ES)"),
    _gn('(China+OR+Beijing)+(espionage+OR+sabotage+OR+cyberattack+OR+"hybrid+warfare")', "en", "China Intel (EN)"),
    _gn('China+Taiwan+(military+OR+sabotage+OR+cyber+OR+espionage)', "en", "Asia Pacific Watch (EN)"),
    _gn('("empresas+amenazadas+por+rusia"+OR+(Tecnobit+OR+Oesia+OR+Destinus+OR+Indra+OR+Navantia+OR+Escribano)+AND+(Rusia+OR+Kremlin+OR+Putin))', "es", "Empresas Amenazadas (ES)"),
    _gn('(Destinus+OR+Kokorich)+(Rusia+OR+Ucrania+OR+drones)', "es", "Destinus / Drones (ES)"),
    _gn('(Rusia+OR+China+OR+Ucrania)+("elusion+de+sanciones"+OR+"flota+fantasma"+OR+"petrolero"+OR+"doble+uso")', "es", "Sanciones / Elusión (ES)"),
    _gn('(OFAC+OR+"OpenSanctions"+OR+"KSE+Institute"+OR+"CREA")+(Russia+OR+sanctions)', "en", "OFAC / Sanciones Tracker"),
    _gn('(detenido+OR+arrestado+OR+detencion)+(espia+ruso+OR+espia+chino+OR+sabotaje)', "es", "Detenciones (ES)"),
    _gn('(arrested+OR+detained)+(Russian+spy+OR+Chinese+spy+OR+sabotage)', "en", "Detenciones (EN)"),
    _gn('(Polska+OR+Poland)+(sabotaż+OR+szpiegostwo+OR+dywersja+OR+ABW+OR+Rosja)', "pl", "Polonia / ABW (PL)"),
    _gn('Poland+sabotage+Russian+agents+OR+arson', "en", "Poland Sabotage (EN)"),
    _gn('(Rusia+OR+Ucrania+OR+China)+("crimen+organizado"+OR+"asesinato"+OR+"sicario"+OR+"atentado"+OR+"terrorismo")', "es", "Crimen y Terrorismo (ES)"),
    _gn('("Institute+for+the+Study+of+War"+OR+"ISW")+Russian+offensive', "en", "ISW War Studies"),
    ("https://kyivindependent.com/feed/", "The Kyiv Independent", "en"),
    ("https://www.pravda.com.ua/rss/", "Ukrainska Pravda", "uk"),
    ("https://meduza.io/rss/all", "Meduza (RU/EN)", ""),
    _gn('Россия+диверсия+OR+теракт+OR+шпионаж+OR+СБУ', "ru", "Rusia / Ecosistema RU"),
    _gn('Україна+диверсія+OR+агент+OR+обстріл+OR+ГУР', "uk", "Ucrania / Frente UA"),
    _gn('(crypto+OR+bitcoin+OR+USDT+OR+Tether)+(Russia+OR+China+OR+sanctions+OR+Garantex)', "en", "FININT Cripto (EN)"),
    _gn('(روسيا+OR+أوكرانيا+OR+الصين)+(تجسس+OR+طائرات+مسيرة)', "ar", "Mundo Árabe / Intel (AR)"),
    ("https://www.rumafia.net/en/rss/", "RuMafia.net", "en", "INVESTIGACIÓN"),
    ("https://antikor.com.ua/articles.rss", "Antikor Portal", "", "INVESTIGACIÓN"),
]

def obtener_clave_deepl():
    try:
        return str(st.secrets.get("DEEPL_API_KEY", "")).strip()
    except Exception:
        return ""

def _correr_fuente(nombre, fn, args):
    t0 = time.time()
    try:
        items, err = fn(*args)
    except Exception as e:
        items, err = [], f"{nombre} ({type(e).__name__})"
    return nombre, items, err, time.time() - t0

def rastrear_todo():
    """Rastrea todas las fuentes en paralelo. Las que fallan 3 veces seguidas se pausan 1 hora."""
    tareas = [(tag, raspar_youtube_canal, (cid, tag)) for cid, tag in CANALES_YT]
    tareas += [(f"Telegram @{c}", raspar_un_canal_tg, (c,)) for c in CANALES_TG]
    tareas += [(f[1], procesar_feed, (f,)) for f in FUENTES_RSS]
    ahora = time.time()
    activas, pausadas = [], []
    for nombre, fn, args in tareas:
        hasta = RECURSOS["pausadas"].get(nombre, 0)
        if ahora < hasta:
            pausadas.append(f"{nombre} (pausada {int((hasta - ahora) / 60) + 1} min)")
        else:
            activas.append((nombre, fn, args))
    items, errores, lentas = [], [], []
    if activas:
        with ThreadPoolExecutor(max_workers=40) as ex:
            futs = [ex.submit(_correr_fuente, *t) for t in activas]
            for f in as_completed(futs):
                nombre, its, err, dur = f.result()
                items.extend(its)
                if err:
                    errores.append(err)
                    n = RECURSOS["fallos"].get(nombre, 0) + 1
                    if n >= 3:
                        RECURSOS["pausadas"][nombre] = time.time() + 3600
                        n = 0
                    RECURSOS["fallos"][nombre] = n
                else:
                    RECURSOS["fallos"][nombre] = 0
                if dur > 6:
                    lentas.append(f"{nombre} ({dur:.0f}s)")
    items = [i for i in items if cumple_ventana_temporal(i["fecha_dt"], RETENCION_HORAS)]
    return items, sorted(set(errores)), sorted(lentas), sorted(pausadas)

def pretraducir_titulos(items):
    """Traduce en segundo plano los titulares de las últimas 72 h y los deja en la caché."""
    pend = [b for b in items
            if b.get("lang") != "es" and cumple_ventana_temporal(b["fecha_dt"], 72)
            and not parece_espanol(f"{b['titular_raw']} {b['cuerpo_raw'][:400]}")]
    pend.sort(key=lambda b: b["fecha_dt"], reverse=True)
    if not pend:
        return
    RECURSOS["traduciendo"] = len(pend)
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda b: traducir_texto(b["titular_raw"], b.get("lang", "")), pend))

def _ejecutar_rastreo():
    RECURSOS["ocupado"] = True
    items = []
    try:
        items, errores, lentas, pausadas = rastrear_todo()
        with RECURSOS["lock"]:
            RECURSOS["snapshot"] = {"items": items, "errores": errores, "lentas": lentas,
                                    "pausadas": pausadas, "ts": time.time()}
        RECURSOS["listo"].set()
    finally:
        RECURSOS["ocupado"] = False
    try:
        pretraducir_titulos(items)
    except Exception:
        pass
    finally:
        RECURSOS["traduciendo"] = 0

def _bucle_refresco():
    while True:
        try:
            _ejecutar_rastreo()
        except Exception:
            pass
        RECURSOS["despertar"].wait(timeout=REFRESCO_SEG)
        RECURSOS["despertar"].clear()

def asegurar_refresco():
    with RECURSOS["lock"]:
        h = RECURSOS.get("hilo")
        if h is not None and h.is_alive():
            return
        h = threading.Thread(target=_bucle_refresco, daemon=True, name="refresco-osint")
        RECURSOS["hilo"] = h
        h.start()

def obtener_snapshot(esperar=90):
    """Devuelve el último rastreo sin esperar (salvo en el primer arranque)."""
    if RECURSOS["snapshot"] is None:
        RECURSOS["listo"].wait(timeout=esperar)
    snap = RECURSOS["snapshot"]
    if snap is None:
        return {"items": [], "errores": ["El primer rastreo aún no ha terminado: pulsa BUSCAR NOVEDADES en unos segundos."],
                "lentas": [], "pausadas": [], "ts": None}
    return snap

# ==========================================
# DEDUPLICACIÓN
# ==========================================
def _titulo_cmp(a, traducido):
    t = a["titular_es"] if (traducido and a.get("titular_es")) else a["titular_raw"]
    t = normalizar_texto(re.sub(r"\[.*?\]", "", t))
    return re.sub(r"^(ultima hora|exclusiva|urgente|video)[:\-]?\s*", "", t)[:160]

def son_mismo_evento(a, b, traducido=False):
    if abs((a["fecha_dt"] - b["fecha_dt"]).total_seconds()) > 36 * 3600:
        return False
    t1, t2 = _titulo_cmp(a, traducido), _titulo_cmp(b, traducido)
    if len(t1) < 20 or len(t2) < 20:
        return False
    sm = SequenceMatcher(None, t1, t2, autojunk=False)
    if sm.real_quick_ratio() <= 0.84 or sm.quick_ratio() <= 0.84:
        return False
    return sm.ratio() > 0.84

def fusionar_en(destino, origen):
    corr = destino.setdefault("canales_correlacionados", [destino["canal"]])
    for c in origen.get("canales_correlacionados", [origen["canal"]]):
        if c not in corr:
            corr.append(c)
    sec = destino.setdefault("fuentes_coincidentes", [])
    candidatos = [{"canal": origen["canal"], "enlace": origen["enlace"],
                   "titular": origen.get("titular_es") or origen["titular_raw"]}]
    candidatos += origen.get("fuentes_coincidentes", [])
    for f in candidatos:
        if f["enlace"] != destino["enlace"] and not any(x["enlace"] == f["enlace"] for x in sec):
            sec.append(f)

def nueva_alerta(b):
    a = dict(b)
    extranjero = b.get("lang") != "es" and not parece_espanol(f"{b['titular_raw']} {b['cuerpo_raw'][:400]}")
    a.update({
        "extranjero": extranjero,
        "titular_es": "" if extranjero else b["titular_raw"],
        "cuerpo_es": "" if extranjero else b["cuerpo_raw"],
        "traducido_ok": not extranjero,
        "cuerpo_ok": (not extranjero) or not b["cuerpo_raw"],
        "error_traduccion": False,
        "canales_correlacionados": [b["canal"]], "fuentes_coincidentes": [],
        "categoria": "GLOBAL", "etiquetas_ner": [], "evaluacion_contexto": "", "corpus_norm": "",
    })
    return a

def consolidar_traducidos(alertas, ids_nuevos):
    """Segunda pasada: fusiona la misma noticia publicada en idiomas distintos."""
    resultado = []
    for a in sorted(alertas, key=lambda x: x["fecha_dt"], reverse=True):
        dup = None
        if a["traducido_ok"]:
            dup = next((x for x in resultado if x["traducido_ok"] and (a["id"] in ids_nuevos or x["id"] in ids_nuevos)
                        and son_mismo_evento(a, x, traducido=True)), None)
        if dup:
            fusionar_en(dup, a)
            continue
        resultado.append(a)
    return resultado

def ejecutar_actualizacion(snap):
    """Incorpora el último rastreo (instantáneo: no hay red ni traducción aquí)."""
    brutos = snap["items"]
    base = [a for a in st.session_state.get("alertas_monitor", [])
            if cumple_ventana_temporal(a["fecha_dt"], RETENCION_HORAS)]
    ids = {a["id"] for a in base}
    urls = {limpiar_url(a["enlace"]) for a in base}
    candidatos, nuevos = list(base), []
    for b in sorted(brutos, key=lambda x: x["fecha_dt"], reverse=True):
        if b["id"] in ids:
            continue
        u = limpiar_url(b["enlace"])
        if u in urls:
            continue
        al = nueva_alerta(b)
        dup = next((c for c in candidatos if son_mismo_evento(al, c)), None)
        if dup:
            fusionar_en(dup, al)
            continue
        ids.add(b["id"])
        urls.add(u)
        candidatos.append(al)
        nuevos.append(al)

    alertas = base + nuevos
    ids_nuevos = {a["id"] for a in nuevos}
    aplicar_cache_traducciones(alertas)
    alertas = consolidar_traducidos(alertas, ids_nuevos)
    for a in alertas:
        enriquecer(a)
    alertas.sort(key=lambda x: x["fecha_dt"], reverse=True)

    st.session_state.alertas_monitor = alertas
    st.session_state.ultima_actualizacion = obtener_fecha_madrid_str()
    st.session_state.snap_ingerido = snap["ts"]
    sel = st.session_state.setdefault("seleccionadas_pdf", set())
    for a in alertas:
        if a["id"] in ids_nuevos:
            sel.add(a["id"])
            st.session_state[f"sel_{a['id']}"] = True
    sel.intersection_update({a["id"] for a in alertas})

# ==========================================
# EXPORTACIONES
# ==========================================
SECCIONES = [
    ("ESPANA", "🇪🇸 ESPAÑA", "#dc2626", "ESPAÑA E INFRAESTRUCTURAS CRÍTICAS"),
    ("EMPRESA", "🏢 EMPRESAS", "#d97706", "EMPRESAS ESTRATÉGICAS Y DEFENSA"),
    ("GLOBAL", "📰 GLOBAL", "#0f172a", "RUSIA, UCRANIA Y AMENAZAS GLOBALES"),
    ("CRIMENES", "⚖️ CRÍMENES DE GUERRA Y TERRORISMO", "#991b1b", "CRÍMENES DE GUERRA Y TERRORISMO"),
    ("CRIPTO", "🟣 CRIPTO", "#7c3aed", "CRIPTOACTIVOS Y FINANCIACIÓN ILÍCITA"),
    ("SANCIONES", "📦 SANCIONES", "#b45309", "SANCIONES, ELUSIÓN Y FLOTA FANTASMA"),
    ("DETENCION", "🛑 DETENCIONES", "#be123c", "DETENCIONES Y CONTRAESPIONAJE"),
    ("CHINA", "🇨🇳 CHINA", "#1e3a8a", "CHINA Y AMENAZAS ESTRATÉGICAS A OCCIDENTE"),
    ("TELEGRAM", "✈️ TELEGRAM", "#0284c7", "TELEGRAM OSINT Y CANALES TÁCTICOS"),
    ("REDES", "🌐 REDES SOCIALES", "#059669", "REDES SOCIALES Y ANÁLISIS MULTIMEDIA"),
]

def exportar_alertas_csv(alertas):
    if not alertas:
        return b""
    filas = [{
        "Fecha (Madrid)": obtener_fecha_madrid_str(a["fecha_dt"]),
        "Fecha estimada": "sí" if a.get("fecha_estimada") else "no",
        "Categoría": a["categoria"], "Plataforma": a["plataforma"], "Canal/Fuente": a["canal"],
        "Medio": a.get("medio", ""), "Entidades": ", ".join(e[0] for e in a.get("etiquetas_ner", [])),
        "Difusión cruzada": ", ".join(a.get("canales_correlacionados", [])),
        "Titular (original)": a["titular_raw"], "Titular (ES)": a.get("titular_es", ""),
        "Texto (original)": a["cuerpo_raw"], "Texto (ES)": a.get("cuerpo_es", ""),
        "Traducido": "sí" if a["traducido_ok"] else "no", "Enlace": a["enlace"],
    } for a in alertas]
    return pd.DataFrame(filas).to_csv(index=False).encode("utf-8-sig")

def generar_informe_markdown(alertas, traducir=True):
    md = (f"# INFORME DE INTELIGENCIA // BUSCADOR DE AMENAZAS HÍBRIDAS\n"
          f"**Fecha de emisión:** {obtener_fecha_madrid_str()} (hora peninsular)\n"
          f"**Total de alertas:** {len(alertas)}\n\n---\n\n")
    for cod, _lab, _col, nombre in SECCIONES:
        items = [a for a in alertas if a["categoria"] == cod]
        if not items:
            continue
        md += f"## {nombre} ({len(items)})\n\n"
        for a in items:
            md += f"### {titular_visible(a, traducir)}\n"
            md += f"- **Fecha:** {obtener_fecha_madrid_str(a['fecha_dt'])}{' (estimada)' if a.get('fecha_estimada') else ''} | **Fuente:** {a['canal']}\n"
            if a.get("etiquetas_ner"):
                md += f"- **Entidades:** {', '.join(e[0] for e in a['etiquetas_ner'])}\n"
            cuerpo = cuerpo_visible(a, traducir)
            if cuerpo:
                md += f"- **Reporte:** {cuerpo[:500]}{'...' if len(cuerpo) > 500 else ''}\n"
            md += f"- **Contexto de la fuente:** {a['evaluacion_contexto']}\n"
            md += f"- **Enlace:** {a['enlace']}\n\n"
        md += "---\n\n"
    return md.encode("utf-8")

def generar_html_ejecutivo(alertas, ventana_str="GENERAL", traducir=True):
    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Informe Buscador de Amenazas Híbridas</title>
<style>
@page {{ size: A4 portrait; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; color:#0f172a; margin:0; line-height:1.35; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
@media screen {{ body {{ background:#f1f5f9; padding:20px; }} #rep {{ max-width:860px; margin:0 auto; background:#fff; padding:26px; border:1px solid #cbd5e1; border-radius:8px; }}
.toolbar {{ max-width:860px; margin:0 auto 16px; display:flex; justify-content:space-between; align-items:center; background:#fff; padding:12px 18px; border-radius:8px; border:1px solid #cbd5e1; }}
.btn-print {{ background:#dc2626; color:#fff; border:none; padding:10px 20px; font-size:13.5px; font-weight:800; border-radius:6px; cursor:pointer; }} }}
@media print {{ .toolbar {{ display:none !important; }} #rep {{ border:none !important; padding:0 !important; }} .card {{ page-break-inside:avoid; }} }}
.header {{ background:#0f172a; color:#fff; border-left:6px solid #dc2626; padding:12px 16px; margin-bottom:12px; border-radius:4px; }}
.header h1 {{ margin:0 0 2px; font-size:16px; text-transform:uppercase; }} .header p {{ margin:0; color:#cbd5e1; font-size:11px; }}
.card {{ border:1px solid #cbd5e1; border-left-width:4px; border-left-style:solid; padding:8px 12px; margin-bottom:8px; border-radius:3px; }}
.card h3 {{ margin:0 0 4px; font-size:13px; }} .meta {{ font-size:10px; color:#64748b; font-weight:700; margin-bottom:4px; }}
.cuerpo {{ background:#f8fafc; border:1px solid #e2e8f0; padding:6px 8px; font-size:11px; margin-bottom:4px; text-align:justify; }}
.ctx {{ background:#f0f9ff; border-left:3px solid #0284c7; padding:5px 8px; font-size:10.5px; color:#0c4a6e; margin-bottom:4px; }}
a {{ color:#0284c7; font-size:10.5px; font-weight:700; }}
</style></head><body>
<div class="toolbar"><div><strong>Informe de Inteligencia</strong></div><button class="btn-print" onclick="window.print()">🖨️ IMPRIMIR / GUARDAR PDF</button></div>
<div id="rep"><div class="header"><h1>BUSCADOR DE AMENAZAS HÍBRIDAS</h1>
<p>INFORME OPERATIVO ({esc(ventana_str)}) | Emisión: {obtener_fecha_madrid_str()} (hora peninsular) | {len(alertas)} alertas</p></div>"""
    for cod, _lab, color, nombre in SECCIONES:
        items = [a for a in alertas if a["categoria"] == cod]
        if not items:
            continue
        html += (f'<div style="background:{color};color:#fff;font-size:11.5px;font-weight:900;padding:5px 10px;'
                 f'margin:10px 0 6px;text-transform:uppercase;">{esc(nombre)} ({len(items)})</div>')
        for a in items:
            cuerpo = cuerpo_visible(a, traducir)
            cuerpo_html = f'<div class="cuerpo"><strong>Reporte:</strong> {esc(cuerpo[:900])}</div>' if cod in ("TELEGRAM", "REDES") and cuerpo else ""
            est = " (fecha estimada)" if a.get("fecha_estimada") else ""
            html += f"""<div class="card" style="border-left-color:{color};">
<div class="meta">📅 {obtener_fecha_madrid_str(a['fecha_dt'])}{est} | 📢 {esc(a['canal'])}</div>
<h3>{esc(titular_visible(a, traducir))}</h3>{cuerpo_html}
<div class="ctx"><strong>Contexto de la fuente:</strong> {esc(a['evaluacion_contexto'])}</div>
<div><a href="{url_sanitizada(a['enlace'])}" target="_blank" rel="noopener noreferrer">🔗 Fuente original en {esc(a['canal'])} &rarr;</a></div></div>"""
    return html + "</div></body></html>"

# ==========================================
# CALLBACKS
# ==========================================
def marcar_todas_callback(ids):
    st.session_state.seleccionadas_pdf |= set(ids)
    for i in ids:
        st.session_state[f"sel_{i}"] = True

def desmarcar_todas_callback(ids):
    st.session_state.seleccionadas_pdf -= set(ids)
    for i in ids:
        st.session_state[f"sel_{i}"] = False

def toggle_checkbox_callback(item_id):
    if st.session_state.get(f"sel_{item_id}", False):
        st.session_state.seleccionadas_pdf.add(item_id)
    else:
        st.session_state.seleccionadas_pdf.discard(item_id)

def mostrar_mas(clave):
    st.session_state[clave] = st.session_state.get(clave, LIMITE_TARJETAS) + LIMITE_TARJETAS

def traducir_alerta_callback(item_id):
    a = next((x for x in st.session_state.get("alertas_monitor", []) if x["id"] == item_id), None)
    if not a:
        return
    t_es, ok_t = traducir_texto(a["titular_raw"], a.get("lang", ""))
    c_es, ok_c = traducir_texto(a["cuerpo_raw"], a.get("lang", ""))
    if ok_t:
        a["titular_es"], a["traducido_ok"] = t_es, True
    if ok_c:
        a["cuerpo_es"], a["cuerpo_ok"] = c_es, True
    a["error_traduccion"] = not (ok_t and ok_c)
    enriquecer(a)

# ==========================================
# ACCESO
# ==========================================
def obtener_clave():
    try:
        return str(st.secrets.get("GAD_PASSWORD", "GAD2026"))
    except Exception:
        return "GAD2026"

def gestionar_acceso():
    if st.session_state.get("autenticado", False):
        return True
    st.markdown("""
        <div style="max-width:470px;margin:40px auto 20px auto;padding:26px;background:#0f172a;border-radius:12px;border-left:6px solid #dc2626;color:white;">
            <h2 style="margin-top:0;color:#fff;font-size:21px;">🔒 Acceso Restringido</h2>
            <p style="color:#94a3b8;font-size:13.5px;margin-bottom:0;">Sistema de Vigilancia de Fuentes Abiertas. Introduzca la credencial autorizada.</p>
        </div>""", unsafe_allow_html=True)
    _, col2, _ = st.columns([1, 1.2, 1])
    with col2:
        with st.form("form_login"):
            clave = st.text_input("Clave de acceso:", type="password", placeholder="Introduce la contraseña...")
            if st.form_submit_button("INGRESAR AL SISTEMA", type="primary", use_container_width=True):
                if time.time() < st.session_state.get("bloqueo_hasta", 0):
                    st.error("Demasiados intentos. Espera un minuto.")
                elif hmac.compare_digest(clave.encode("utf-8"), obtener_clave().encode("utf-8")):
                    st.session_state.autenticado = True
                    st.rerun()
                else:
                    fallos = st.session_state.get("fallos_login", 0) + 1
                    st.session_state.fallos_login = fallos
                    if fallos >= 5:
                        st.session_state.bloqueo_hasta = time.time() + 60
                        st.session_state.fallos_login = 0
                    st.error("Credencial no autorizada.")
    return False

# ==========================================
# ESTILOS
# ==========================================
ESTILOS = """
<style>
    #MainMenu, footer { visibility: hidden !important; display: none !important; }
    [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
    [data-testid="manage-app-button"], .viewerBadge_container__1QSob, div[class*="viewerBadge"],
    div[class*="profileContainer"] { visibility: hidden !important; display: none !important; }
    header[data-testid="stHeader"] { background: transparent !important; }
    a.header-anchor, a[data-testid="header-anchor"] { display: none !important; }
    .main { background-color: #0b0f19; }
    .block-container { padding-top: 1.2rem !important; }
    .tactical-header { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); border-left: 8px solid #dc2626; border-radius: 12px; padding: 18px 24px; margin-bottom: 16px; display: flex; align-items: center; gap: 20px; }
    .header-titles h1 { color: #fff; margin: 0; font-size: 24px !important; font-weight: 900; letter-spacing: 0.8px; text-transform: uppercase; }
    .header-titles p { color: #94a3b8; font-size: 13.5px !important; margin: 4px 0 0 0; text-transform: uppercase; }
    div[data-baseweb="tab-list"] { display: flex !important; flex-wrap: wrap !important; gap: 6px !important; background: #0f172a !important; padding: 8px 10px !important; border-radius: 10px !important; margin-bottom: 14px !important; }
    button[data-baseweb="tab"] { flex: 1 1 auto !important; min-width: fit-content !important; height: 38px !important; font-size: 11.5px !important; font-weight: 800 !important; padding: 6px 12px !important; margin: 0 !important; border-radius: 6px !important; color: #fff !important; text-transform: uppercase; border: 1px solid rgba(255,255,255,0.1) !important; justify-content: center !important; }
    button[data-baseweb="tab"]:hover { filter: brightness(1.2) !important; }
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
    button[data-baseweb="tab"][aria-selected="true"] { border: 2px solid #38bdf8 !important; transform: scale(1.02) !important; }
    div[data-baseweb="tab-highlight"] { display: none !important; }
    .osint-card { background: #fff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px 20px; margin-bottom: 14px; }
    .osint-flex { display: flex; gap: 20px; align-items: flex-start; flex-wrap: wrap; }
    .osint-media { flex: 0 0 280px; max-width: 280px; }
    .osint-media img { width: 100%; height: auto; max-height: 220px; object-fit: cover; border-radius: 6px; }
    .osint-body { flex: 1; min-width: 260px; }
    .badge-item { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; margin-right: 6px; }
    .ner-pill { font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px; color: #fff; margin-right: 5px; }
    .osint-title { color: #0f172a; font-size: 17.5px; font-weight: 850; margin: 4px 0 8px 0; line-height: 1.35; }
    .osint-resumen { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 14px; color: #1e293b; font-size: 14px; line-height: 1.55; text-align: justify; margin-bottom: 8px; }
    .osint-analisis { background: #f0f9ff; border-left: 4px solid #0284c7; padding: 8px 12px; color: #0c4a6e; font-size: 13.5px; line-height: 1.5; margin-bottom: 10px; border-radius: 0 4px 4px 0; }
    .btn-source { background: #0f172a; color: #fff !important; padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: 700; text-decoration: none !important; }
    .btn-video { background: #0284c7; color: #fff !important; padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: 700; text-decoration: none !important; }
</style>
"""

LOGO_SVG = """<svg width="70" height="70" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
<circle cx="50" cy="50" r="47" fill="#1e293b" stroke="#334155" stroke-width="2"/>
<circle cx="50" cy="50" r="40" stroke="#38bdf8" stroke-width="1.2" stroke-dasharray="3 3" opacity="0.6"/>
<path d="M50 14 L75 25 V52 C75 69 50 84 50 84 C50 84 25 69 25 52 V25 Z" fill="#0f172a" stroke="#38bdf8" stroke-width="2.5"/>
<polygon points="53,24 37,48 48,48 43,72 61,44 51,44" fill="#ef4444"/></svg>"""

# ==========================================
# TARJETAS
# ==========================================
def renderizar_tarjetas(lista, auto_traducir, clave_tab):
    if not lista:
        st.info("No se registran alertas que cumplan los filtros seleccionados para esta sección.")
        return
    clave_lim = f"lim_{clave_tab}"
    limite = st.session_state.get(clave_lim, LIMITE_TARJETAS)
    colores = {c: col for c, _l, col, _n in SECCIONES}

    for item in lista[:limite]:
        item_id, cat = item["id"], item["categoria"]
        b_color = colores.get(cat, "#0f172a")
        badges = ""
        if cumple_ventana_temporal(item["fecha_dt"], 24):
            badges += '<span class="badge-item" style="background:#fee2e2;color:#991b1b;border:1px solid #f87171;">⚡ 24H</span>'
        elif cumple_ventana_temporal(item["fecha_dt"], 48):
            badges += '<span class="badge-item" style="background:#fef08a;color:#854d0e;border:1px solid #eab308;">🔥 48H</span>'
        if item.get("canal_id", "").lower() in CANALES_DESINFORMACION_ESP:
            badges += '<span class="badge-item" style="background:#ffe4e6;color:#be123c;border:1px solid #fda4af;">📢 LISTA FIMI</span>'
        if len(item.get("canales_correlacionados", [])) > 1:
            badges += f'<span class="badge-item" style="background:#e0e7ff;color:#3730a3;border:1px solid #c7d2fe;">🔄 {len(item["canales_correlacionados"])} MEDIOS</span>'
        if auto_traducir and item.get("extranjero") and not item["traducido_ok"]:
            badges += '<span class="badge-item" style="background:#fef3c7;color:#92400e;border:1px solid #fbbf24;">⏳ SIN TRADUCIR</span>'

        pildoras = "".join(f'<span class="ner-pill" style="background-color:{c};">{esc(n)}</span>'
                           for n, c in item.get("etiquetas_ner", []))
        titulo = titular_visible(item, auto_traducir)
        cuerpo = cuerpo_visible(item, auto_traducir)
        fecha_str = obtener_fecha_madrid_str(item["fecha_dt"]) + (" (est.)" if item.get("fecha_estimada") else "")
        origen = esc(item["canal"]) + (f" · {esc(item['medio'])}" if item.get("medio") else "")
        enlace = url_sanitizada(item["enlace"])
        media = ""
        if item.get("multimedia") and es_url_segura(item["multimedia"]):
            media = f'<div class="osint-media"><img src="{url_sanitizada(item["multimedia"])}" alt="Evidencia" loading="lazy" referrerpolicy="no-referrer"/></div>'
        vid = f'<a href="{enlace}" target="_blank" rel="noopener noreferrer" class="btn-video">▶️ VER MULTIMEDIA</a>' if item.get("tiene_video") else ""
        reporte = f'<div class="osint-resumen"><strong>📄 Reporte/Post:</strong><br>{esc(cuerpo)}</div>' if cat in ("TELEGRAM", "REDES") and cuerpo else ""
        tarjeta = (
            f'<div class="osint-card" style="border-left: 8px solid {b_color};"><div class="osint-flex">{media}'
            f'<div class="osint-body"><div style="margin-bottom:8px;">{badges}'
            f'<span style="font-size:12px;color:#64748b;">📅 {fecha_str} | 📢 {origen}</span></div>'
            f'<div class="osint-title">{esc(titulo)}</div><div style="margin-bottom:8px;">{pildoras}</div>{reporte}'
            f'<div class="osint-analisis"><strong>🧠 Contexto de la fuente:</strong> {esc(item["evaluacion_contexto"])}</div>'
            f'<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">'
            f'<a href="{enlace}" target="_blank" rel="noopener noreferrer" class="btn-source">🔗 ABRIR FUENTE ORIGINAL</a>{vid}'
            f'</div></div></div></div>')

        c_chk, c_card = st.columns([0.05, 0.95])
        with c_chk:
            if f"sel_{item_id}" not in st.session_state:
                st.session_state[f"sel_{item_id}"] = item_id in st.session_state.seleccionadas_pdf
            st.checkbox("Incluir en informe", key=f"sel_{item_id}", on_change=toggle_checkbox_callback,
                        args=(item_id,), label_visibility="collapsed")
        with c_card:
            st.markdown(tarjeta, unsafe_allow_html=True)
            if item.get("extranjero") and (not item["traducido_ok"] or (cat in ("TELEGRAM", "REDES") and not item["cuerpo_ok"])):
                st.button("🌐 Traducir texto", key=f"tr_{item_id}", on_click=traducir_alerta_callback, args=(item_id,))
            if item.get("error_traduccion"):
                st.caption("⚠️ El servicio de traducción no respondió. Inténtalo de nuevo en unos segundos.")
            texto_copia = (f"🚨 ALERTA OSINT | {cat}\nTitular: {titulo}\n"
                           f"Fuente: {item['canal']} | Fecha: {fecha_str} (Madrid)\nEnlace: {item['enlace']}")
            with st.expander("📋 Formato para copiar alerta"):
                st.code(texto_copia, language="markdown")
            if auto_traducir and item.get("titular_es") and item["titular_es"] != item["titular_raw"]:
                with st.expander("🌐 Texto original"):
                    st.text(item["titular_raw"])
                    if item["cuerpo_raw"]:
                        st.text(item["cuerpo_raw"][:800])
            sec = item.get("fuentes_coincidentes", [])
            if sec:
                with st.expander(f"📑 {len(sec)} cobertura(s) adicional(es) del mismo evento"):
                    lis = "".join(
                        f'<li><strong>{esc(f["canal"])}:</strong> <a href="{url_sanitizada(f["enlace"])}" '
                        f'target="_blank" rel="noopener noreferrer">{esc(f["titular"])}</a></li>' for f in sec)
                    st.markdown(f"<ul>{lis}</ul>", unsafe_allow_html=True)

    if len(lista) > limite:
        st.button(f"Mostrar más ({len(lista) - limite} restantes)", key=f"btn_{clave_tab}",
                  on_click=mostrar_mas, args=(clave_lim,))

# ==========================================
# APLICACIÓN
# ==========================================
def main():
    st.set_page_config(page_title="Buscador de Amenazas Híbridas", page_icon="🛡️", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(ESTILOS, unsafe_allow_html=True)
    if not gestionar_acceso():
        st.stop()

    st.session_state.setdefault("check_traducir", True)
    st.session_state.setdefault("seleccionadas_pdf", set())
    RECURSOS["deepl_key"] = obtener_clave_deepl()
    asegurar_refresco()

    st.markdown(
        f'<div class="tactical-header"><div>{LOGO_SVG}</div><div class="header-titles">'
        f'<h1>BUSCADOR DE AMENAZAS HÍBRIDAS</h1><p>SISTEMA DE VIGILANCIA DE FUENTES ABIERTAS</p></div></div>',
        unsafe_allow_html=True)

    # Sesiones antiguas con otro formato de alertas: se reinician
    if any("cuerpo_ok" not in a for a in st.session_state.get("alertas_monitor", [])):
        st.session_state.alertas_monitor = []
        st.session_state.pop("snap_ingerido", None)

    if RECURSOS["snapshot"] is None:
        with st.spinner("Primer rastreo de fuentes abiertas (solo al arrancar)..."):
            snap = obtener_snapshot()
    else:
        snap = obtener_snapshot()
    if "alertas_monitor" not in st.session_state or snap["ts"] != st.session_state.get("snap_ingerido"):
        ejecutar_actualizacion(snap)

    auto_traducir = bool(st.session_state.get("check_traducir", True))
    alertas = st.session_state.get("alertas_monitor", [])

    # Titulares que el hilo de fondo ya tradujo (instantáneo)
    cambiadas = aplicar_cache_traducciones(alertas)
    if cambiadas:
        for a in cambiadas:
            enriquecer(a)
        alertas = consolidar_traducidos(alertas, {a["id"] for a in cambiadas})
        st.session_state.alertas_monitor = alertas

    aviso = st.session_state.pop("aviso", None)
    if aviso:
        st.info(aviso)

    # ---------- Barra lateral ----------
    with st.sidebar:
        st.subheader("🔍 Filtros de Búsqueda")
        busqueda = st.text_input("Búsqueda booleana:", placeholder='"flota fantasma" OR (sabotaje AND Polonia) -incendio',
                                 help='Admite "frases exactas", AND, OR, NOT o "-", y paréntesis. Busca en el texto original y en lo ya traducido.')
        rango = st.selectbox("📅 Ventana temporal:", ["Últimas 24 Horas", "Últimas 48 Horas", "Última Semana", "Todo el Histórico"], index=2)
        canales_sel = st.multiselect("Filtrar por periódicos / canales:", sorted({a["canal"] for a in alertas}))
        cat_sel = st.multiselect("Filtrar por categoría:", [c for c, *_ in SECCIONES])
        st.write("---")
        st.subheader("📊 Exportación")
        st.download_button("📊 BASE DE DATOS COMPLETA (CSV)", data=exportar_alertas_csv(alertas),
                           file_name=f"alertas_osint_{datetime.now(ZONA_MADRID).strftime('%Y%m%d_%H%M')}.csv",
                           mime="text/csv", use_container_width=True)

    horas = {"Últimas 24 Horas": 24, "Últimas 48 Horas": 48, "Última Semana": 168}.get(rango)
    datos = [x for x in alertas if cumple_ventana_temporal(x["fecha_dt"], horas)] if horas else list(alertas)
    if busqueda:
        datos = [x for x in datos if cumple_busqueda_booleana(x, busqueda)]
    if canales_sel:
        datos = [x for x in datos if x["canal"] in canales_sel]
    if cat_sel:
        datos = [x for x in datos if x["categoria"] in cat_sel]

    # ---------- Botonera ----------
    c1, c2, c3, c4, c5 = st.columns([1.1, 1.0, 1.2, 1.2, 1.7])
    with c1:
        if st.button("🔄 BUSCAR NOVEDADES", type="primary", use_container_width=True):
            edad = (time.time() - snap["ts"]) if snap["ts"] else None
            if edad is None or edad > 45:
                RECURSOS["despertar"].set()
                st.session_state.aviso = "Rastreo lanzado en segundo plano. Las novedades aparecerán en tu próxima interacción (suele tardar 15-40 s)."
            else:
                st.session_state.aviso = "Los datos ya están al día (rastreo de hace menos de 45 s)."
            st.rerun()
    with c2:
        st.checkbox("🌐 Auto-Traducir", key="check_traducir",
                    help="Muestra titulares y textos en español cuando están traducidos. Es instantáneo; los titulares se traducen solos en segundo plano.")
    para_informe = [x for x in datos if x["id"] in st.session_state.seleccionadas_pdf]
    marca = datetime.now(ZONA_MADRID).strftime("%Y%m%d_%H%M")
    with c3:
        st.download_button(f"📥 INFORME IMPRIMIBLE ({len(para_informe)})",
                           data=generar_html_ejecutivo(para_informe, rango.upper(), auto_traducir),
                           file_name=f"Informe_Amenazas_{marca}.html", mime="text/html", use_container_width=True)
    with c4:
        st.download_button(f"📝 INFORME MARKDOWN ({len(para_informe)})",
                           data=generar_informe_markdown(para_informe, auto_traducir),
                           file_name=f"Informe_Amenazas_{marca}.md", mime="text/markdown", use_container_width=True)
    with c5:
        estado = []
        if RECURSOS["ocupado"]:
            estado.append("⏳ rastreando fuentes")
        if RECURSOS["traduciendo"]:
            estado.append("🌐 traduciendo titulares en segundo plano")
        edad_txt = f"hace {int((time.time() - snap['ts']) / 60)} min" if snap["ts"] else "pendiente"
        st.markdown(
            f'<div style="font-size:12.5px;color:#94a3b8;margin-top:4px;">🕒 <strong>Último rastreo:</strong> {edad_txt}'
            f'{" · " + " · ".join(estado) if estado else ""}<br>Mostrando <strong>{len(datos)}</strong> alertas ({esc(rango)}).</div>',
            unsafe_allow_html=True)

    ids_visibles = [x["id"] for x in datos]
    s1, s2, _ = st.columns([1.2, 1.2, 4.0])
    with s1:
        st.button("✅ Seleccionar visibles", use_container_width=True, on_click=marcar_todas_callback, args=(ids_visibles,))
    with s2:
        st.button("⬜ Desmarcar visibles", use_container_width=True, on_click=desmarcar_todas_callback, args=(ids_visibles,))

    pend = [a for a in alertas if a.get("extranjero") and not a["traducido_ok"]]
    if auto_traducir and pend:
        p1, p2 = st.columns([3, 1])
        with p1:
            st.caption(f"⏳ {len(pend)} titulares sin traducir todavía: se traducen solos en segundo plano. "
                       f"También puedes traducirlos ahora o pulsar 🌐 en cada tarjeta.")
        with p2:
            if st.button(f"🌐 Traducir ahora ({min(len(pend), 60)})", use_container_width=True):
                with st.spinner("Traduciendo titulares..."):
                    hechas = traducir_titulares_pendientes(alertas, 60)
                    for a in hechas:
                        enriquecer(a)
                st.session_state.aviso = f"Traducidos {len(hechas)} titulares." + (
                    "" if hechas else " El servicio de traducción no respondió; prueba de nuevo en un momento.")
                st.rerun()

    n_diag = len(snap["errores"]) + len(snap["lentas"]) + len(snap["pausadas"])
    if n_diag:
        with st.expander(f"⚠️ Diagnóstico de fuentes: {len(snap['errores'])} con error, "
                         f"{len(snap['lentas'])} lentas, {len(snap['pausadas'])} en pausa"):
            for titulo, lista in (("Con error", snap["errores"]), ("Lentas (>6 s)", snap["lentas"]),
                                  ("En pausa (3 fallos seguidos; se reintentan en 1 h)", snap["pausadas"])):
                if lista:
                    st.markdown(f"**{titulo}**")
                    for e in lista:
                        st.caption(f"• {e}")

    st.write("")
    listas = {cod: [x for x in datos if x["categoria"] == cod] for cod, *_ in SECCIONES}
    tabs = st.tabs([f"{lab} ({len(listas[cod])})" for cod, lab, *_ in SECCIONES])
    for tab, (cod, *_r) in zip(tabs, SECCIONES):
        with tab:
            renderizar_tarjetas(listas[cod], auto_traducir, cod)

if __name__ == "__main__":
    main()
