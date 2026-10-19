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
from urllib.parse import urlparse, urlunparse, quote
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time

# ==========================================
# CONFIGURACIÓN
# ==========================================
SESION_HTTP = requests.Session()
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
})

EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR")
PASSWORD_EMISOR = os.environ.get("PASSWORD_EMISOR")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO")

CACHE_TRADUCCIONES = {}

# ==========================================
# LIMPIEZA Y UTILIDADES
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

def contiene_cirilico(texto):
    return bool(re.search(r'[\u0400-\u04FF]', str(texto)))

# ==========================================
# DEDUPLICACIÓN RADICAL MULTIMEDIO
# ==========================================
def palabras_significativas(texto):
    t_norm = normalizar_texto(limpiar_firma_medio(texto))
    palabras = re.findall(r'\b[a-z0-9]{4,}\b', t_norm)
    stop = {
        "para", "como", "sobre", "entre", "este", "esta", "estos", "estas",
        "desde", "hacia", "contra", "rusia", "ruso", "rusa", "rusos", "ucrania",
        "informe", "noticias", "noticia", "ultimo", "minuto", "segun", "afirma",
        "dice", "tras", "durante", "habria", "pueden", "seria", "nuevo", "nueva"
    }
    return [p for p in palabras if p not in stop]

def es_evento_repetido(item_nuevo, lista_existentes):
    t_nuevo = normalizar_texto(limpiar_firma_medio(item_nuevo["titular_raw"]))
    p_nuevo = set(palabras_significativas(t_nuevo))

    for ex in lista_existentes:
        # 1. Enlace idéntico
        if item_nuevo["enlace"] and ex["enlace"] and item_nuevo["enlace"] == ex["enlace"]:
            return True

        t_ex = normalizar_texto(limpiar_firma_medio(ex["titular_raw"]))
        # 2. Ratio de texto directo > 55%
        if SequenceMatcher(None, t_nuevo[:80], t_ex[:80]).ratio() > 0.55:
            return True

        # 3. Solapamiento de palabras clave entre noticias
        p_ex = set(palabras_significativas(t_ex))
        if p_nuevo and p_ex:
            coincidencias = p_nuevo.intersection(p_ex)
            if len(coincidencias) >= 3:
                return True

    return False

# ==========================================
# MOTOR DE TRADUCCIÓN ROBUSTO (MULTISOURCE)
# ==========================================
def traducir_segmento(texto_corto):
    t = texto_corto.strip()
    if not t or len(t) < 3:
        return t
    if t in CACHE_TRADUCCIONES:
        return CACHE_TRADUCCIONES[t]

    # Motor 1: MyMemory API (Gratuito, sin bloqueo por IP de servidores)
    try:
        url_mymemory = f"https://api.mymemory.translated.net/get?q={quote(t[:400])}&langpair=autodetect|es"
        r = SESION_HTTP.get(url_mymemory, timeout=4.0)
        if r.status_code == 200:
            datos = r.json()
            if "responseData" in datos and datos["responseData"].get("translatedText"):
                res = datos["responseData"]["translatedText"].strip()
                if res and "MYMEMORY WARNING" not in res.upper():
                    CACHE_TRADUCCIONES[t] = res
                    return res
    except Exception:
        pass

    # Motor 2: Fallback Lingva Scraper (espejo libre de Google Translate)
    try:
        url_lingva = f"https://lingva.ml/api/v1/auto/es/{quote(t[:400])}"
        r = SESION_HTTP.get(url_lingva, timeout=4.0)
        if r.status_code == 200:
            datos = r.json()
            if "translation" in datos and datos["translation"]:
                res = datos["translation"].strip()
                CACHE_TRADUCCIONES[t] = res
                return res
    except Exception:
        pass

    # Motor 3: Google Single Endpoint directo
    try:
        url_gt = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": t[:400]}
        r = SESION_HTTP.get(url_gt, params=params, timeout=3.5)
        if r.status_code == 200:
            datos = r.json()
            if isinstance(datos, list) and datos and isinstance(datos[0], list):
                res = "".join([seg[0] for seg in datos[0] if isinstance(seg, list) and seg[0]]).strip()
                if res:
                    CACHE_TRADUCCIONES[t] = res
                    return res
    except Exception:
        pass

    return t

def traducir_texto_completo(texto):
    if not texto or len(texto.strip()) < 3:
        return texto
    
    # Si no tiene cirílico y ya parece español, no saturar peticiones
    t_clean = limpiar_html_texto(texto)
    
    # Dividir oraciones para traducir
    oraciones = [o.strip() for o in re.split(r'(?<=[.!?\n])\s+', t_clean) if o.strip()]
    partes_traducidas = []
    
    for oracion in oraciones:
        if len(oracion) > 350:
            sub = [oracion[i:i+350] for i in range(0, len(oracion), 350)]
            for s in sub:
                partes_traducidas.append(traducir_segmento(s))
                time.sleep(0.15)
        else:
            partes_traducidas.append(traducir_segmento(oracion))
            time.sleep(0.15)

    return " ".join(partes_traducidas)

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
                    pub = it.findtext("pubDate", "")
                    link = it.findtext("link", "")
                    try:
                        dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                    except Exception:
                        dt = datetime.now(timezone.utc)

                    if not es_reciente_48h(dt):
                        continue

                    if tiene_vinculo_ruso(tit) and tiene_amenaza(tit):
                        items.append({
                            "canal": canal,
                            "fecha_dt": dt,
                            "titular_raw": tit,
                            "cuerpo": "",
                            "enlace": limpiar_url(link),
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
                for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-12:]:
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
                    
                    items.append({
                        "canal": f"@{c}",
                        "fecha_dt": dt,
                        "titular_raw": f"[{c.upper()}] {txt.strip()[:100]}...",
                        "cuerpo": txt[:1200],
                        "enlace": link,
                        "cat_fija": "TELEGRAM",
                        "es_telegram": True
                    })
        except Exception:
            pass
    return items

# ==========================================
# FORMATO Y ENVÍO
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
            cuerpo_telegram = f'<div style="font-size:11.5px; color:#334155; background:#f8fafc; padding:8px 10px; border-radius:3px; margin-bottom:6px; line-height:1.5; border:1px solid #e2e8f0;"><strong>📄 Despacho Traducido:</strong><br>{html_module.escape(a["cuerpo"])}</div>' if a["categoria"] == "TELEGRAM" else ""
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

    # 1. Filtro estricto anti-duplicados ANTES de traducir
    alertas_filtradas = []
    for b in brutos:
        if not es_evento_repetido(b, alertas_filtradas):
            alertas_filtradas.append(b)

    # 2. Traducción robusta multisource
    alertas_finales = []
    for it in alertas_filtradas:
        tit_es = traducir_texto_completo(it["titular_raw"])
        
        if it.get("es_telegram"):
            cuerpo_es = traducir_texto_completo(it["cuerpo"])
        else:
            cuerpo_es = ""

        cat = it.get("cat_fija") or clasificar(tit_es, cuerpo_es)
        alertas_finales.append({
            "canal": it["canal"],
            "fecha_dt": it["fecha_dt"],
            "titular": tit_es,
            "cuerpo": cuerpo_es,
            "categoria": cat,
            "evaluacion": evaluacion_contexto(cat),
            "enlace": it["enlace"]
        })

    if alertas_finales and EMAIL_EMISOR and PASSWORD_EMISOR and EMAIL_DESTINO:
        html = generar_html_email(alertas_finales)
        enviar_correo(html, len(alertas_finales))
        print(f"Correo enviado con {len(alertas_finales)} alertas consolidadas.")
    else:
        print("No hay alertas recientes o faltan credenciales.")

if __name__ == "__main__":
    main()
