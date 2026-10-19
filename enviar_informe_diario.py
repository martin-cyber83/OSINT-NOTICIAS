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
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
from deep_translator import GoogleTranslator

# ==========================================
# CONFIGURACIÓN
# ==========================================
SESION_HTTP = requests.Session()
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,de;q=0.6"
})

EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR")
PASSWORD_EMISOR = os.environ.get("PASSWORD_EMISOR")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO")

CACHE_TITULARES = {}
CACHE_CUERPOS = {}

# ==========================================
# LIMPIEZA Y NORMALIZACIÓN
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
    nfkd = unicodedata.normalize('NFKD', str(texto))
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower()

def limpiar_firma_medio(texto):
    return re.sub(r'\s*[-–|·]\s*[^-–|·]{2,45}$', '', str(texto)).strip()

def limpiar_url(url):
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))
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

# ==========================================
# DEDUPLICACIÓN REFORZADA MULTIMEDIO
# ==========================================
def extraer_tokens_clave(texto):
    t_limpio = normalizar_texto(limpiar_firma_medio(texto))
    palabras = re.findall(r'\b[a-záéíóúñ0-9]{4,}\b', t_limpio)
    stop_words = {
        "para", "como", "sobre", "entre", "este", "esta", "estos", "estas",
        "desde", "hacia", "contra", "rusia", "ruso", "rusa", "rusos", "ucrania",
        "informe", "noticias", "noticia", "ultimo", "minuto", "segun", "afirma",
        "dice", "tras", "durante", "habria", "pueden", "seria", "nuevo", "nueva"
    }
    return set(p for p in palabras if p not in stop_words)

def son_noticias_duplicadas(item1, item2):
    # 1. Comparación estricta de titulares
    t1 = normalizar_texto(limpiar_firma_medio(item1["titular"]))
    t2 = normalizar_texto(limpiar_firma_medio(item2["titular"]))
    
    if SequenceMatcher(None, t1[:90], t2[:90]).ratio() > 0.60:
        return True

    # 2. Coincidencia de tokens clave en el titular
    k1 = extraer_tokens_clave(t1)
    k2 = extraer_tokens_clave(t2)
    if k1 and k2:
        interseccion = k1.intersection(k2)
        if len(interseccion) >= 3 and (len(interseccion) / min(len(k1), len(k2))) >= 0.5:
            return True

    # 3. Mismo origen o enlace limpio
    if item1["enlace"] and item2["enlace"] and item1["enlace"] == item2["enlace"]:
        return True

    return False

# ==========================================
# TRADUCTOR BLINDADO (TITULARES Y TELEGRAM)
# ==========================================
def traducir_con_reintentos(texto_corto):
    if not texto_corto or len(texto_corto.strip()) < 3:
        return texto_corto
    
    # Intento 1: deep-translator
    for intento in range(3):
        try:
            trad = GoogleTranslator(source='auto', target='es').translate(texto_corto)
            if trad and len(trad.strip()) > 0:
                return trad
        except Exception:
            time.sleep(1.0)

    # Intento 2 (Fallback directo por API pública)
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto_corto}
        r = SESION_HTTP.get(url, params=params, timeout=4.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                partes = [seg[0] for seg in data[0] if isinstance(seg, list) and seg[0]]
                return "".join(partes)
    except Exception:
        pass

    return texto_corto

def traducir_titular(titular_orig):
    if not titular_orig:
        return ""
    t_clean = limpiar_html_texto(titular_orig)
    if len(t_clean) < 4:
        return t_clean
    if t_clean in CACHE_TITULARES:
        return CACHE_TITULARES[t_clean]

    medio_suffix = ""
    texto_a_traducir = t_clean
    m_medio = re.search(r'(\s*[-–|]\s*[^-–|]{2,40})$', t_clean)
    if m_medio:
        medio_suffix = m_medio.group(1)
        texto_a_traducir = t_clean[:m_medio.start()].strip()

    trad = traducir_con_reintentos(texto_a_traducir[:350])
    resultado = f"{trad}{medio_suffix}" if medio_suffix else trad
    CACHE_TITULARES[t_clean] = resultado
    time.sleep(0.3)
    return resultado

def traducir_cuerpo_telegram(texto_largo):
    if not texto_largo:
        return ""
    t_clean = limpiar_html_texto(texto_largo)
    if len(t_clean) < 10:
        return t_clean
    if t_clean in CACHE_CUERPOS:
        return CACHE_CUERPOS[t_clean]

    # Cortar en frases para no saturar los límites de la API
    oraciones = [o.strip() for o in re.split(r'(?<=[.!?\n])\s+', t_clean) if o.strip()]
    bloques = []
    bloque = ""
    for o in oraciones:
        if len(bloque) + len(o) < 300:
            bloque += (" " + o if bloque else o)
        else:
            if bloque:
                bloques.append(bloque)
            bloque = o
    if bloque:
        bloques.append(bloque)

    traducidos = []
    for b in bloques:
        trad_b = traducir_con_reintentos(b)
        traducidos.append(trad_b)
        time.sleep(0.4)

    resultado = " ".join(traducidos)
    CACHE_CUERPOS[t_clean] = resultado
    return resultado

def decodificar_url_google_news_offline(url_rss):
    if not url_rss or "news.google.com" not in url_rss:
        return url_rss
    try:
        m = re.search(r'articles/([A-Za-z0-9_-]+)', url_rss)
        if m:
            token = m.group(1)
            pad = token + "=" * ((4 - len(token) % 4) % 4)
            b = base64.urlsafe_b64decode(pad)
            for u in re.findall(rb'https?://[^\s"\'<>\\\x00-\x1f\x7f-\xff]+', b):
                u_str = u.decode('utf-8', errors='ignore')
                if "google.com" not in u_str and len(u_str) > 16:
                    return u_str
    except Exception:
        pass
    return url_rss

def extraer_resumen_rss(tit_orig, desc_rss):
    if not desc_rss:
        return limpiar_firma_medio(tit_orig)
    try:
        soup = BeautifulSoup(desc_rss, "html.parser")
        textos = [limpiar_html_texto(tag.get_text(" ", strip=True)) for tag in soup.find_all(["li", "p", "a", "font", "div"]) if len(tag.get_text()) > 20]
        if textos:
            return " — ".join(textos)[:1800]
    except Exception:
        pass
    limpio = limpiar_html_texto(desc_rss)
    return limpio[:1800] if len(limpio) > 25 else limpiar_firma_medio(tit_orig)

# ==========================================
# PALABRAS CLAVE Y CLASIFICACIÓN
# ==========================================
EMPRESAS_ESTRATEGICAS_ESP = [
    "indra", "grupo oesia", "oesia", "uav navigation", "tecnobit", "navantia",
    "escribano", "em&e", "sener", "arquimea", "destinus", "santa barbara", "gdels",
    "expal", "rheinmetall expal", "instalaza", "sapla", "urovesa", "itp aero",
    "airbus espana", "airbus getafe", "hisdesat", "hispasat", "sateliot", "pld space",
    "amadeus", "aena", "enaire", "renfe", "adif", "telefonica", "iberdrola",
    "repsol", "naturgy", "redeia", "red electrica", "enagas", "talgo"
]

ACTORES_RUSIA = [
    "rusia", "ruso", "rusa", "rusos", "rusas", "moscu", "kremlin", "putin",
    "russia", "russian", "moscow", "russie", "russe", "moscou", "russland", "russisch", "moskau",
    "gru", "fsb", "svr", "apt28", "apt29", "fancy bear", "cozy bear", "sandworm",
    "noname057", "killnet", "turla", "gamaredon", "cyberarmyofrussia", "garantex", "wagner",
    "bielorrusia", "belarus", "kaliningrad", "lukashenko",
    "ucrania", "ukraine", "ukrainian", "kiev", "kyiv", "donbas", "crimea", "bucha", "mariupol",
    "kursk", "belgorod", "zaporiyia", "zaporizhzhia", "kherson", "donetsk", "jarkov", "kharkiv", "odesa"
]

def tiene_vinculo_ruso(t):
    t_raw = str(t).lower()
    t_norm = normalizar_texto(t)
    raices = ["росси", "рф", "москв", "кремл", "путин", "гру", "фсб", "свр", "украин", "киев", "київ", "зсу", "всу", "сбу", "гур", "донбасс", "крым", "беларус", "вагнер", "шахед", "геран", "искандер"]
    return any(c in t_raw for c in raices) or any(r in t_norm for r in ACTORES_RUSIA)

def tiene_amenaza(t):
    t_raw = str(t).lower()
    t_norm = normalizar_texto(t)
    cir = ["диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид", "крипт", "биткоин", "usdt", "бпла", "дрон", "рэб", "арест", "задержан", "санкц", "кабел", "флот", "казн", "пленн", "военн", "преступлен", "поджог", "теракт"]
    lat = ["hibrid", "hybrid", "sabotaj", "sabotage", "espia", "espionaje", "spy", "espionage", "ciber", "cyber", "hack", "ddos", "malware", "ransomware", "apt28", "apt29", "sandworm", "noname057", "killnet", "desinformacion", "disinformation", "propaganda", "cripto", "crypto", "bitcoin", "btc", "usdt", "tether", "garantex", "sancion", "sanction", "flota fantasma", "shadow fleet", "cable submarino", "crimen de guerra", "war crime", "icc", "detenido", "arrestado", "arrested", "detained", "dron", "uav", "jamming"]
    return any(c in t_raw for c in cir) or any(v in t_norm for v in lat)

def clasificar(tit, cuerpo):
    t_all = normalizar_texto(f"{tit} {cuerpo}")
    if re.search(r'\b(crimen de guerra|war crime|corte penal internacional|genocidio|tortura)\b', t_all):
        return "CRIMENES"
    if re.search(r'\b(detenido|arrestado|arrested|detained)\b', normalizar_texto(tit)) and re.search(r'\b(espia|spy|espionaje|sabotaje|gru|fsb)\b', t_all):
        return "DETENCION"
    if re.search(r'\b(cripto|crypto|usdt|bitcoin|btc|garantex|sancion)\b', t_all):
        return "CRIPTO"
    if any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP):
        return "EMPRESA"
    if re.search(r'\b(espana|spain|madrid|cni|incibe|guardia civil|policia nacional)\b', t_all):
        return "ESPANA"
    return "GLOBAL"

def evaluacion_contexto(cat):
    m = {
        "CRIMENES": "Documentación de crímenes de guerra, ataques a población o infraestructuras civiles e investigaciones judiciales internacionales.",
        "EMPRESA": "Seguimiento sobre empresas estratégicas de España (Defensa, Aeroespacial, Energía y Telecomunicaciones) ante ciberataques o sabotajes.",
        "ESPANA": "Impacto directo en la seguridad nacional, ciberespacio o infraestructuras críticas de España frente a operaciones híbridas del Kremlin.",
        "CRIPTO": "Inteligencia financiera (FININT): evasión de sanciones y empleo de criptoactivos para financiación encubierta.",
        "DETENCION": "Operaciones de contrainteligencia y policiales frente a redes de espionaje del GRU/FSB/SVR o sabotajes.",
        "TELEGRAM": "Reporte táctico de canales primarios de Telegram: inteligencia operativa de la zona de conflicto."
    }
    return m.get(cat, "Operaciones híbridas globales del Kremlin: ciberataques, sabotajes, flota fantasma o desinformación.")

# ==========================================
# RECOLECCIÓN
# ==========================================
def obtener_prensa():
    fuentes = [
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin)+(España+OR+Madrid+OR+CNI)+(sabotaje+OR+ciberataque+OR+espionaje)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(Indra+OR+Navantia+OR+Telefónica)+(Rusia+OR+ciberataque+OR+sabotaje)&hl=es&gl=ES&ceid=ES:es", "Empresas España (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(sabotage+OR+cyberattack+OR+espionage+OR+\"hybrid+warfare\")&hl=en-US&gl=US&ceid=US:en", "Intel Global (EN)"),
        ("https://news.google.com/rss/search?q=(Russland+OR+Ukraine)+(Sabotage+OR+Spionage+OR+Cyberangriff)&hl=de&gl=DE&ceid=DE:de", "Intel Europa Central (DE)"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)"),
        ("https://news.google.com/rss/search?q=(arrested+OR+detained)+(Russian+spy+OR+Russian+sabotage+OR+GRU)&hl=en-US&gl=US&ceid=US:en", "Detenciones (EN)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(\"war+crimes\"+OR+ICC)&hl=en-US&gl=US&ceid=US:en", "War Crimes (EN)")
    ]
    items = []
    for url, canal in fuentes:
        try:
            r = SESION_HTTP.get(url, timeout=5.0)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                for it in root.findall(".//item")[:20]:
                    tit = limpiar_html_texto(it.findtext("title", ""))
                    desc = it.findtext("description", "")
                    pub = it.findtext("pubDate", "")
                    link = it.findtext("link", "")
                    try:
                        dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                    except Exception:
                        dt = datetime.now(timezone.utc)

                    if not es_reciente_48h(dt):
                        continue

                    cuerpo = extraer_resumen_rss(tit, desc)
                    txt = f"{tit} {cuerpo}"
                    if tiene_vinculo_ruso(txt) and tiene_amenaza(txt):
                        items.append({
                            "canal": canal,
                            "fecha_dt": dt,
                            "titular_raw": tit,
                            "cuerpo": cuerpo,
                            "enlace": decodificar_url_google_news_offline(link),
                            "es_telegram": False
                        })
        except Exception:
            pass
    return items

def obtener_telegram():
    canales = ["DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua", "meduzalive", "astrapress"]
    items = []
    for c in canales:
        try:
            r = SESION_HTTP.get(f"https://t.me/s/{c}", timeout=5.0)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-15:]:
                    t_div = p.find("div", class_="tgme_widget_message_text")
                    txt = t_div.get_text(separator=" ", strip=True) if t_div else ""
                    if len(txt) < 30 or not (tiene_vinculo_ruso(txt) and tiene_amenaza(txt)):
                        continue
                    time_tag = p.find("time")
                    dt = datetime.now(timezone.utc)
                    if time_tag and time_tag.get("datetime"):
                        try:
                            dt = datetime.fromisoformat(time_tag.get("datetime").replace("Z", "+00:00")).astimezone(timezone.utc)
                        except Exception:
                            pass
                    if not es_reciente_48h(dt):
                        continue
                    a_link = p.find("a", class_="tgme_widget_message_date")
                    link = a_link["href"] if a_link and a_link.get("href") else f"https://t.me/s/{c}"
                    
                    # Titular sintético de las primeras palabras
                    primeras_palabras = txt.strip()[:110]
                    items.append({
                        "canal": f"@{c}",
                        "fecha_dt": dt,
                        "titular_raw": f"[{c.upper()}] {primeras_palabras}...",
                        "cuerpo": txt[:1400],
                        "enlace": link,
                        "cat_fija": "TELEGRAM",
                        "es_telegram": True
                    })
        except Exception:
            pass
    return items

# ==========================================
# FORMATO Y ENVÍO DEL CORREO
# ==========================================
def generar_html_email(alertas):
    fecha_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    secciones = [
        ("ESPANA", "SECCIÓN 1: ESPAÑA E INFRAESTRUCTURAS CRÍTICAS", "#dc2626"),
        ("EMPRESA", "SECCIÓN 2: EMPRESAS ESTRATÉGICAS Y DEFENSA", "#d97706"),
        ("GLOBAL", "SECCIÓN 3: RUSIA, UCRANIA Y AMENAZAS GLOBALES", "#0f172a"),
        ("CRIMENES", "SECCIÓN 4: CRÍMENES DE GUERRA", "#991b1b"),
        ("CRIPTO", "SECCIÓN 5: CRIPTOACTIVOS Y FINANCIACIÓN", "#7c3aed"),
        ("DETENCION", "SECCIÓN 6: DETENCIONES Y CONTRAINTELIGENCIA", "#be123c"),
        ("TELEGRAM", "SECCIÓN 7: TELEGRAM OSINT", "#0284c7")
    ]
    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f8fafc; padding:16px; color:#0f172a;">
        <div style="max-width:700px; margin:0 auto; background:#ffffff; padding:20px; border-radius:6px; border:1px solid #cbd5e1;">
            <div style="background:#0f172a; color:#ffffff; padding:14px; border-left:6px solid #dc2626; border-radius:4px; margin-bottom:16px;">
                <h2 style="margin:0 0 4px 0; font-size:17px;">INFORME OSINT - ÚLTIMAS 48 HORAS</h2>
                <p style="margin:0; font-size:12px; color:#cbd5e1;">Emisión: {fecha_str} | Alertas consolidadas: {len(alertas)}</p>
            </div>
    """
    for cod, nombre, color in secciones:
        sub = [a for a in alertas if a["categoria"] == cod]
        if not sub:
            continue
        html += f"""
        <div style="background:{color}; color:#ffffff; padding:6px 10px; font-size:12px; font-weight:800; border-radius:3px; margin:14px 0 8px 0;">
            {nombre} ({len(sub)})
        </div>
        """
        for a in sub:
            cuerpo_telegram = f'<div style="font-size:11.5px; color:#334155; background:#f8fafc; padding:8px 10px; border-radius:3px; margin-bottom:6px; line-height:1.5; border:1px solid #e2e8f0;"><strong>📄 Despacho Original Telegram (Traducido):</strong><br>{html_module.escape(a["cuerpo"])}</div>' if a["categoria"] == "TELEGRAM" else ""
            html += f"""
            <div style="border:1px solid #e2e8f0; border-left:4px solid {color}; padding:8px 12px; margin-bottom:8px; border-radius:3px; background:#ffffff;">
                <div style="font-size:10.5px; color:#64748b; font-weight:700; margin-bottom:4px;">
                    📅 {a['fecha_dt'].strftime('%d/%m %H:%M')} UTC | 📢 {a['canal']}
                </div>
                <div style="font-size:13.5px; font-weight:700; color:#0f172a; margin-bottom:6px;">
                    {html_module.escape(a['titular'])}
                </div>
                {cuerpo_telegram}
                <div style="font-size:11px; color:#0369a1; background:#f0f9ff; padding:4px 8px; border-radius:2px; margin-bottom:6px;">
                    🧠 {a['evaluacion']}
                </div>
                <div>
                    <a href="{a['enlace']}" target="_blank" style="color:#0284c7; font-size:11px; font-weight:700;">🔗 Noticia original completa &rarr;</a>
                </div>
            </div>
            """
    html += "</div></body></html>"
    return html

def enviar_correo(html_content, total_alertas):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🛡️ Informe OSINT (48h): {total_alertas} Alertas - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    msg["From"] = EMAIL_EMISOR
    msg["To"] = EMAIL_DESTINO
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_EMISOR, PASSWORD_EMISOR)
        server.sendmail(EMAIL_EMISOR, EMAIL_DESTINO, msg.as_string())

def main():
    brutos = obtener_prensa() + obtener_telegram()
    brutos.sort(key=lambda x: x["fecha_dt"], reverse=True)

    # 1. Filtro estricto por enlace
    unicos_por_url = []
    vistos_url = set()
    for b in brutos:
        u = limpiar_url(b["enlace"])
        if u not in vistos_url:
            vistos_url.add(u)
            unicos_por_url.append(b)

    # 2. Traducción robusta (con esperas y fallback)
    alertas_procesadas = []
    for it in unicos_por_url:
        tit_es = traducir_titular(it["titular_raw"])
        
        if it.get("es_telegram"):
            cuerpo_es = traducir_cuerpo_telegram(it["cuerpo"])
        else:
            cuerpo_es = it["cuerpo"]

        cat = it.get("cat_fija") or clasificar(tit_es, cuerpo_es)
        alertas_procesadas.append({
            "canal": it["canal"],
            "fecha_dt": it["fecha_dt"],
            "titular": tit_es,
            "cuerpo": cuerpo_es,
            "categoria": cat,
            "evaluacion": evaluacion_contexto(cat),
            "enlace": it["enlace"]
        })

    # 3. Deduplicación cruzada entre distintos medios
    alertas_finales = []
    for alerta in alertas_procesadas:
        if not any(son_noticias_duplicadas(alerta, previa) for previa in alertas_finales):
            alertas_finales.append(alerta)

    if alertas_finales and EMAIL_EMISOR and PASSWORD_EMISOR and EMAIL_DESTINO:
        html = generar_html_email(alertas_finales)
        enviar_correo(html, len(alertas_finales))
        print(f"Correo enviado correctamente con {len(alertas_finales)} alertas consolidadas.")
    else:
        print("No hay alertas recientes o faltan credenciales.")

if __name__ == "__main__":
    main()
