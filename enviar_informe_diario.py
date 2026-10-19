import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import re
import html as html_module
from email.utils import parsedate_to_datetime
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse, quote
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SESION_HTTP = requests.Session()
SESION_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,ru;q=0.7,uk;q=0.7,de;q=0.6"
})

EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR")
PASSWORD_EMISOR = os.environ.get("PASSWORD_EMISOR")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO")

CACHE_TITULARES = {}

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

def traducir_solo_titular(titular_orig):
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

    try:
        url_my = f"https://api.mymemory.translated.net/get?q={quote(texto_a_traducir[:350])}&langpair=autodetect|es"
        r = SESION_HTTP.get(url_my, timeout=3.5)
        if r.status_code == 200:
            datos = r.json()
            if "responseData" in datos and datos["responseData"].get("translatedText"):
                trad = datos["responseData"]["translatedText"].strip()
                if trad and "MYMEMORY WARNING" not in trad.upper():
                    res = f"{trad}{medio_suffix}" if medio_suffix else trad
                    CACHE_TITULARES[t_clean] = res
                    return res
    except Exception:
        pass

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
                    CACHE_TITULARES[t_clean] = resultado
                    return resultado
    except Exception:
        pass

    CACHE_TITULARES[t_clean] = t_clean
    return t_clean

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

def tiene_vinculo_ruso(texto):
    if not texto or es_publicidad_o_spam(texto):
        return False
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)
    raices = ["росси", "рф", "москв", "кремл", "путин", "гру", "фсб", "свр", "украин", "киев", "київ", "зсу", "всу", "сбу", "гур", "донбасс", "крым", "беларус", "вагнер", "шахед", "геран", "искандер"]
    return any(c in t_raw for c in raices) or any(r in t_norm for r in ACTORES_RUSIA_ESTRICTOS)

def tiene_amenaza(texto):
    t_raw = str(texto).lower()
    t_norm = normalizar_texto(texto)
    cir = ["диверс", "шпион", "агент", "хакер", "кибер", "ддос", "ddos", "саботаж", "гибрид", "крипт", "биткоин", "usdt", "бпла", "дрон", "рэб", "арест", "задержан", "санкц", "кабел", "флот", "казн", "пленн", "военн", "преступлен", "поджог", "теракт"]
    lat = ["hibrid", "hybrid", "sabotaj", "sabotage", "espia", "espionaje", "spy", "espionage", "ciber", "cyber", "hack", "ddos", "malware", "ransomware", "apt28", "apt29", "sandworm", "noname057", "killnet", "desinformacion", "disinformation", "propaganda", "cripto", "crypto", "bitcoin", "btc", "usdt", "tether", "garantex", "sancion", "sanction", "flota fantasma", "shadow fleet", "cable submarino", "crimen de guerra", "war crime", "icc", "detenido", "arrestado", "arrested", "detained", "dron", "uav", "jamming"]
    return any(c in t_raw for c in cir) or any(v in t_norm for v in lat)

def clasificar(tit):
    t_all = normalizar_texto(tit)
    if re.search(r'\b(crimen de guerra|war crime|corte penal internacional|genocidio|tortura)\b', t_all):
        return "CRIMENES"
    if re.search(r'\b(detenido|arrestado|arrested|detained)\b', t_all) and re.search(r'\b(espia|spy|espionaje|sabotaje|gru|fsb)\b', t_all):
        return "DETENCION"
    if re.search(r'\b(cripto|crypto|usdt|bitcoin|btc|tether|garantex|sancion|sanction)\b', t_all):
        return "CRIPTO"
    if any(re.search(r'\b' + re.escape(emp) + r'\b', t_all) for emp in EMPRESAS_ESTRATEGICAS_ESP):
        return "EMPRESA"
    if re.search(r'\b(espana|spain|madrid|cni|incibe|guardia civil|policia nacional)\b', t_all):
        return "ESPANA"
    return "GLOBAL"

def evaluacion_contexto(cat):
    m = {
        "CRIMENES": "Documentación de crímenes de guerra, ataques a infraestructuras civiles e investigaciones judiciales internacionales.",
        "EMPRESA": "Seguimiento sobre empresas estratégicas de España ante ciberataques, sabotajes o señalamiento hostil.",
        "ESPANA": "Impacto directo en la seguridad nacional e infraestructuras críticas de España frente a operaciones híbridas.",
        "CRIPTO": "Inteligencia financiera (FININT): evasión de sanciones internacionales y uso de criptoactivos no regulados.",
        "DETENCION": "Operaciones policiales y de contrainteligencia frente a redes de espionaje o sabotaje.",
        "TELEGRAM": "Reporte táctico de canales primarios de Telegram: inteligencia operativa de la zona de conflicto."
    }
    return m.get(cat, "Operaciones híbridas globales del Kremlin: ciberataques, sabotajes o desinformación.")

def son_mismo_evento(tit1, tit2):
    t1 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit1)))
    t2 = normalizar_texto(limpiar_firma_medio(re.sub(r'\[.*?\]', '', tit2)))
    if len(t1) < 15 or len(t2) < 15:
        return False
    return SequenceMatcher(None, t1[:95], t2[:95]).ratio() > 0.65

def raspar_un_canal_tg(canal):
    mensajes = []
    try:
        r = SESION_HTTP.get(f"https://t.me/s/{canal}", timeout=6.0)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for p in soup.find_all("div", class_="tgme_widget_message_wrap")[-30:]:
            t_div = p.find("div", class_="tgme_widget_message_text")
            txt = t_div.get_text(separator=" ", strip=True) if t_div else ""
            if len(txt) < 25 or es_publicidad_o_spam(txt):
                continue
            if not (tiene_vinculo_ruso(txt) and tiene_amenaza(txt)):
                continue

            txt_sin_url = limpiar_html_texto(re.sub(r'http\S+', '', txt))
            if len(txt_sin_url) < 20:
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
            link = a_link["href"] if a_link and a_link.get("href") else f"https://t.me/s/{canal}"

            primera_frase = re.split(r'(?<=[.!?])\s+', txt_sin_url)[0]
            tit_corto = primera_frase if 20 <= len(primera_frase) <= 160 else txt_sin_url[:140]

            mensajes.append({
                "canal": f"@{canal}",
                "fecha_dt": dt,
                "titular_raw": tit_corto,
                "enlace": link,
                "es_tg": True
            })
    except Exception:
        pass
    return mensajes

def raspar_telegram():
    canales = ["DIUkraine", "SBUkr", "informnapalm", "cyberresistanceua", "entre_guerras", "rybar", "dva_majors", "vchkogpu", "meduzalive", "astrapress", "istories_media", "ostorozhno_novosti", "nvua_official", "operativnoZSU"]
    todos = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for f in as_completed([ex.submit(raspar_un_canal_tg, c) for c in canales]):
            try:
                todos.extend(f.result())
            except Exception:
                pass
    return todos

def procesar_feed(datos):
    url, etiqueta = datos
    items = []
    try:
        r = SESION_HTTP.get(url, timeout=6.0)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for it in root.findall(".//item")[:25]:
                tit = limpiar_html_texto(it.findtext("title", ""))
                desc = it.findtext("description", "")
                link = it.findtext("link", "")
                pub = it.findtext("pubDate", "")
                if len(tit) < 15 or es_publicidad_o_spam(tit + " " + desc):
                    continue
                txt_eval = f"{tit} {desc}"
                if not (tiene_vinculo_ruso(txt_eval) and tiene_amenaza(txt_eval)):
                    continue
                try:
                    dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)
                if not es_reciente_48h(dt):
                    continue
                items.append({
                    "canal": etiqueta,
                    "fecha_dt": dt,
                    "titular_raw": tit,
                    "enlace": limpiar_url(link),
                    "es_tg": False
                })
    except Exception:
        pass
    return items

def raspar_prensa():
    fuentes = [
        ("https://news.google.com/rss/search?q=(Rusia+OR+ruso+OR+Kremlin)+(España+OR+Madrid+OR+CNI)+(sabotaje+OR+ciberataque+OR+espionaje)&hl=es&gl=ES&ceid=ES:es", "España Seguridad (ES)"),
        ("https://news.google.com/rss/search?q=(Indra+OR+Navantia+OR+Telefónica)+(Rusia+OR+ciberataque+OR+sabotaje)&hl=es&gl=ES&ceid=ES:es", "Empresas España (ES)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(sabotage+OR+cyberattack+OR+espionage+OR+\"hybrid+warfare\")&hl=en-US&gl=US&ceid=US:en", "Intel Global (EN)"),
        ("https://news.google.com/rss/search?q=(Russland+OR+Ukraine)+(Sabotage+OR+Spionage+OR+Cyberangriff)&hl=de&gl=DE&ceid=DE:de", "Intel Europa Central (DE)"),
        ("https://news.google.com/rss/search?q=(crypto+OR+bitcoin+OR+USDT)+(Russia+OR+sanctions+OR+Garantex)&hl=en-US&gl=US&ceid=US:en", "FININT Cripto (EN)"),
        ("https://news.google.com/rss/search?q=(arrested+OR+detained)+(Russian+spy+OR+Russian+sabotage+OR+GRU)&hl=en-US&gl=US&ceid=US:en", "Detenciones (EN)"),
        ("https://news.google.com/rss/search?q=(Russia+OR+Ukraine)+(\"war+crimes\"+OR+ICC)&hl=en-US&gl=US&ceid=US:en", "War Crimes (EN)")
    ]
    todas = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(procesar_feed, item) for item in fuentes]):
            try:
                todas.extend(f.result())
            except Exception:
                pass
    return todas

def generar_html_email(alertas):
    fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M")
    secciones = [
        ("ESPANA", "SECCIÓN 1: ESPAÑA E INFRAESTRUCTURAS CRÍTICAS", "#dc2626"),
        ("EMPRESA", "SECCIÓN 2: EMPRESAS ESTRATÉGICAS DE ESPAÑA Y DEFENSA", "#d97706"),
        ("GLOBAL", "SECCIÓN 3: RUSIA, UCRANIA Y AMENAZAS GLOBALES", "#0f172a"),
        ("CRIMENES", "SECCIÓN 4: CRÍMENES DE GUERRA Y DERECHOS HUMANOS", "#991b1b"),
        ("CRIPTO", "SECCIÓN 5: CRIPTOACTIVOS Y FINANCIACIÓN ILÍCITA", "#7c3aed"),
        ("DETENCION", "SECCIÓN 6: DETENCIONES POR ESPIONAJE Y SABOTAJE", "#be123c"),
        ("TELEGRAM", "SECCIÓN 7: TELEGRAM OSINT Y CANALES TÁCTICOS", "#0284c7")
    ]
    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background:#f8fafc; padding:16px; color:#0f172a;">
        <div style="max-width:760px; margin:0 auto; background:#ffffff; padding:20px; border-radius:6px; border:1px solid #cbd5e1;">
            <div style="background:#0f172a; color:#ffffff; padding:14px; border-left:6px solid #dc2626; border-radius:4px; margin-bottom:16px;">
                <h2 style="margin:0 0 4px 0; font-size:17px; text-transform:uppercase;">ANALIZADOR DE AMENAZAS HÍBRIDAS</h2>
                <p style="margin:0; font-size:12px; color:#cbd5e1;">INFORME OPERATIVO EXCLUSIVO (ÚLTIMAS 48 HORAS) | Emisión: {fecha_actual} | Alertas consolidadas: {len(alertas)}</p>
            </div>
    """
    for cat_cod, cat_nombre, cat_color in secciones:
        sub = [a for a in alertas if a.get("categoria") == cat_cod]
        if not sub:
            continue
        html += f"""
        <div style="background:{cat_color}; color:#ffffff; padding:6px 10px; font-size:12px; font-weight:800; border-radius:3px; margin:14px 0 8px 0; text-transform:uppercase;">
            {cat_nombre} ({len(sub)})
        </div>
        """
        for a in sub:
            tit = html_module.escape(str(a.get('titular', '')))
            ana = html_module.escape(str(a.get('evaluacion', '')))
            fecha_str = a.get('fecha_dt').strftime('%d/%m/%Y %H:%M')
            canal = html_module.escape(str(a.get('canal', '')))
            enlace = html_module.escape(str(a.get('enlace', '#')))

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
                    <a href="{enlace}" target="_blank" style="color:#0284c7; font-size:11px; font-weight:700;">🔗 Noticia / Despacho original en {canal} &rarr;</a>
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
    brutos = raspar_prensa() + raspar_telegram()
    brutos.sort(key=lambda x: x["fecha_dt"], reverse=True)

    # 1. Filtro estricto por enlace y titular crudo
    pre_unicos = []
    urls_vistas = set()
    for b in brutos:
        u = limpiar_url(b["enlace"])
        if u in urls_vistas:
            continue
        if any(son_mismo_evento(b["titular_raw"], ex["titular_raw"]) for ex in pre_unicos):
            continue
        urls_vistas.add(u)
        pre_unicos.append(b)

    if not pre_unicos:
        print("No hay noticias en las últimas 48 horas.")
        return

    # 2. Traducción de titulares
    with ThreadPoolExecutor(max_workers=8) as ex:
        titulares_traducidos = list(ex.map(lambda it: traducir_solo_titular(it["titular_raw"]), pre_unicos))

    # 3. Deduplicación secundaria tras traducir
    alertas_finales = []
    for item, tit_es in zip(pre_unicos, titulares_traducidos):
        if item["es_tg"]:
            canal_tag = item["canal"].replace("@", "").upper()
            titular_final = f"[{canal_tag}] {tit_es}"
            cat_final = "TELEGRAM"
        else:
            titular_final = tit_es
            cat_final = clasificar(titular_final)

        nueva_alerta = {
            "canal": item["canal"],
            "fecha_dt": item["fecha_dt"],
            "titular": titular_final,
            "categoria": cat_final,
            "evaluacion": evaluacion_contexto(cat_final),
            "enlace": item["enlace"]
        }

        if not any(son_mismo_evento(nueva_alerta["titular"], ex["titular"]) for ex in alertas_finales):
            alertas_finales.append(nueva_alerta)

    if alertas_finales and EMAIL_EMISOR and PASSWORD_EMISOR and EMAIL_DESTINO:
        html = generar_html_email(alertas_finales)
        enviar_correo(html, len(alertas_finales))
        print(f"Correo enviado correctamente con {len(alertas_finales)} alertas.")
    else:
        print("No hay alertas o faltan credenciales.")

if __name__ == "__main__":
    main()
