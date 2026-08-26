"""
Puente BD → TouchDesigner vía OSC.

Modos:
  elecciones   → envía las nubes de metadatos seleccionables (horas,
                 municipios, colores, tags, días, clima) al motor de TD
  fluir        → escucha la ráfaga de selección del botón "Fluir" (9001),
                 acumula por grupo, detecta el fin, genera el spec del loop
                 con loop_db.generar_loop y lo envía por 9002 (medios por
                 tipo, chiches y, si hay municipios elegidos, los mensajes
                 de Telegram del chat como bloque /mensaje → fluir_telegram
                 y las rutas de mapas por municipio como bloque /mapa →
                 fluir_mapas). Además genera dos HTMLs autocontenidos que
                 TouchDesigner carga con Web Render TOPs (file://, cero red):
                 - td/chat_fluir.html (chat de Telegram, web_render_chat)
                 - td/textos_fluir.html (crónicas type='text',
                   web_render_textos)

Uso básico:
  python scripts/td/puente_td.py elecciones           # nubes de elecciones
  python scripts/td/puente_td.py elecciones --grupo horas,tags
  python scripts/td/puente_td.py fluir                # escucha continua (Enter para detener)
"""

import argparse
import json
import logging
import os
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pythonosc import udp_client
from pythonosc import osc_server
from pythonosc import dispatcher

from db.util import abrir, resolver_db
# util_enter.py vive en scripts/td/ (misma carpeta que este script)
if __package__ is None:
    import sys, os as _os
    _scripts_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
from elecciones import enviar_grupos as enviar_elecciones
from util_enter import detener_con_enter

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración OSC
# ---------------------------------------------------------------------------
OSC_HOST = "127.0.0.1"
OSC_PUERTO_TD = 9000            # Python → TD (nubes de elecciones)
OSC_PUERTO_PY = 9001            # TD → Python (ráfaga del "Fluir")
OSC_PUERTO_TD_RESULTADO = 9002  # Python → TD (resultado del loop, canal separado)

# Direcciones OSC del flujo "Fluir".
OSC_ADDR_SELECCION = "/flujos/seleccion"   # prefijo TD → Python (por grupo)
OSC_ADDR_FLUIR = "/flujos/fluir"            # prefijo Python → TD por 9002


def enviar(cliente: udp_client.SimpleUDPClient, address: str, *args):
    """Envía un mensaje OSC."""
    log.debug(f"OSC → {address} {args}")
    cliente.send_message(address, list(args))


# ---------------------------------------------------------------------------
# Modo: elecciones (nubes de metadatos seleccionables)
# ---------------------------------------------------------------------------

def modo_elecciones(db_path: str, grupos: Optional[str] = None):
    """Envía las nubes de elecciones (horas, municipios, colores, tags...) a TD."""
    ids = [s.strip() for s in grupos.split(",") if s.strip()] if grupos else []
    log.info("Enviando nubes de elecciones a TD...")
    enviar_elecciones(db_path, ids)
    log.info("✅ Nubes de elecciones enviadas")


# ---------------------------------------------------------------------------
# Modo: fluir (botón "Fluir" de TouchDesigner)
# ---------------------------------------------------------------------------

# Grupo OSC (sufijo tras /flujos/seleccion/) → clave de filtro en loop_db.
GRUPOS_OSC_A_FILTRO = {
    "tags": "tags",
    "colores": "colores",
    "municipios": "municipios",
    "dias": "dias",
    "clima": "clima",
}

# Separador que utiliza el CLI de loop_db por grupo (tags por ';', el resto por
# coma). Se respeta para no romper el formato que espera _filtrar_media.
SEPARADOR_GRUPO = {
    "tags": ";",
    "colores": ",",
    "municipios": ",",
    "dias": ",",
    "clima": ",",
}


def _importar_loop_db() -> Any:
    """
    Importa loop_db desde scripts/ai_media agregando su carpeta al sys.path.

    loop_db.py convive con loop_engine en scripts/ai_media/ y hace
    `import loop_engine`; por eso necesita su propio directorio en sys.path
    (mismo patrón que ya usa el script para scripts/ y la raíz del proyecto).
    """
    if __package__ is None:
        import sys as _sys
        import os as _os
        _raiz_proyecto = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        _ai_media_dir = _os.path.join(_raiz_proyecto, "scripts", "ai_media")
        if _ai_media_dir not in _sys.path:
            _sys.path.insert(0, _ai_media_dir)
    import loop_db  # noqa: E402
    return loop_db


def _parsear_hora_osc(valor: Any) -> Optional[int]:
    """
    Convierte un valor hora de la ráfaga ('13:00', '06:00') a hora entera (13, 6).

    El ':minutos' es opcional; si el texto no es un entero 0..23 se
    devuelve None para que el caller lo descarte con advertencia.
    """
    texto = str(valor).strip()
    parte = texto.split(":")[0]
    try:
        hora = int(parte)
    except (TypeError, ValueError):
        return None
    if not 0 <= hora <= 23:
        return None
    return hora


def _filtros_desde_seleccion(
    selecciones: dict[str, list[str]],
) -> tuple[list[int], dict[str, list[str]]]:
    """
    Traduce la ráfaga acumulada {grupo: [valores...]} a (horas, filtros).

    La estructura de filtros es exactamente la misma que arma el CLI de
    loop_db (municipios/colores/días/clima como listas; tags como lista que
    _filtrar_media usa con LIKE '%tag%'). Cada mensaje de la ráfaga puede
    traer un valor simple, pero también se soporta que venga más de uno
    (separados por el separador del grupo).
    """
    horas: list[int] = []
    for valor in selecciones.get("horas", []):
        hora = _parsear_hora_osc(valor)
        if hora is None:
            log.warning("  Hora no parseable de la ráfaga, descartada: %r", valor)
        elif hora not in horas:
            horas.append(hora)

    filtros: dict[str, list[str]] = {}
    for grupo_osc, nombre_filtro in GRUPOS_OSC_A_FILTRO.items():
        valores = selecciones.get(grupo_osc) or []
        sep = SEPARADOR_GRUPO.get(grupo_osc, ",")
        items: list[str] = []
        for valor in valores:
            for parte in str(valor).split(sep):
                parte = parte.strip()
                if parte and parte not in items:
                    items.append(parte)
        if items:
            filtros[nombre_filtro] = items

    return horas, filtros


def _enviar_filtro(cli, clave: str, valor) -> None:
    """Envía un filtro del usuario como mensaje clave-valor por 9002.

    El callbacks escribe `/flujos/fluir/filtro <clave> <valor>` como una fila
    [clave, valor] en `fluir_estado` (la misma tabla donde van los totales).
    Si `valor` es una lista, se une con ", " para que quede un único texto.
    """
    if isinstance(valor, (list, tuple)):
        valor = ", ".join(str(v) for v in valor if str(v).strip())
    enviar(cli, f"{OSC_ADDR_FLUIR}/filtro", clave, "" if valor is None else str(valor))


def _separar_videos_360(por_tipo: dict) -> tuple[list, list]:
    """Divide por_tipo['video'] en (normales, 360) según el marcador es_360.

    El marcador `es_360` lo agrega loop_db al spec (True solo para videos con
    media.subtype = '360'). Los items sin la clave se tratan como normales
    (los specs viejos no la traen y el campo es aditivo).

    Args:
        por_tipo: dict del spec, {tipo: [medios...]}.

    Returns:
        (normales, es360): dos listas de dicts de medios.
    """
    videos = por_tipo.get("video", []) or []
    normales = [m for m in videos if not m.get("es_360")]
    es360 = [m for m in videos if m.get("es_360")]
    return normales, es360


# ── Telegram (chat que acompaña al loop) ─────────────────────────────────────

# Zona horaria del viaje (Argentina, UTC-3). Los mensajes de Telegram se
# almacenan con date_utc (ISO 8601 UTC); la instalación los agrupa por hora
# LOCAL, igual que los medios del loop (mismo criterio que loop_db._extraer_hora
# y que app.js de la web: hora_utc - 3).
_ZONA_ARGENTINA = timezone(timedelta(hours=-3))

# Límite defensivo de longitud del texto de cada mensaje antes de enviarlo a TD
# (decisión de diseño: un chat renderizable en TD; la web recorta a 150 chars).
MAX_TEXTO_TELEGRAM = 250

# Ruta RELATIVA (a la raíz del proyecto) del HTML del chat de Telegram que
# genera el puente para que TouchDesigner lo levante con un Web Render TOP.
# Vive en td/ porque es un artefacto del parche TD (no del deploy web).
CHAT_HTML_RELATIVO = "td/chat_fluir.html"

# Ruta RELATIVA (a la raíz del proyecto) del HTML de los textos (type='text')
# que genera el puente para el Web Render TOP `web_render_textos` de TD.
TEXTOS_HTML_RELATIVO = "td/textos_fluir.html"


def _parsear_fecha_utc(valor: Optional[str]) -> Optional[datetime]:
    """Parsea un date_utc de Telegram (ISO 8601) a datetime aware UTC."""
    if not valor:
        return None
    texto = str(valor).strip()
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hora_local_mensaje(date_utc: Optional[str]) -> float:
    """Hora local (float 0..23.99) de un date_utc de Telegram, UTC→UTC-3.

    Replica el criterio de app.js (hora - 3 con wrap) para que la hora del
    mensaje coincida con la hora del loop (Argentina).
    """
    dt = _parsear_fecha_utc(date_utc)
    if dt is None:
        return 0.0
    local = dt.astimezone(_ZONA_ARGENTINA)
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def _consultar_mensajes_telegram(
    conn: sqlite3.Connection,
    municipios: list[str],
) -> list[dict]:
    """Mensajes de Telegram de los municipios elegidos (rango de fechas).

    Replica el criterio de la web (deploy/api/mensajes_telegram.php): por cada
    municipio toma el rango de fechas de sus medios (MIN/MAX timestamp_utc) y
    trae los mensajes de Telegram dentro de esa ventana. Excluye los mensajes
    del sistema (es_sistema=1).

    Args:
        conn: conexión SQLite a la DB de flujos.
        municipios: municipios elegidos por el visitante (filtros del loop).

    Returns:
        Lista de dicts con id, from_name, texto, hora (local), fecha, tipo,
        fotos (JSON de media_ids) y municipio, ordenados por fecha UTC.
    """
    if not municipios:
        return []
    limpiados = [str(m).strip() for m in municipios if m and str(m).strip()]
    if not limpiados:
        return []

    marcadores = ",".join("?" * len(limpiados))
    filas = conn.execute(f"""
        SELECT municipio, MIN(timestamp_utc) AS desde, MAX(timestamp_utc) AS hasta
        FROM media
        WHERE municipio IN ({marcadores}) AND timestamp_utc IS NOT NULL
        GROUP BY municipio
    """, limpiados).fetchall()

    mensajes: list[dict] = []
    for municipio, desde, hasta in filas:
        if not desde or not hasta:
            continue
        ventana_desde = str(desde)[:10] + "T00:00:00Z"
        ventana_hasta = str(hasta)[:10] + "T23:59:59Z"
        filas_msg = conn.execute("""
            SELECT id, from_name, text, date_utc, message_type
            FROM telegram_messages
            WHERE es_sistema = 0 AND date_utc >= ? AND date_utc <= ?
            ORDER BY date_utc
        """, (ventana_desde, ventana_hasta)).fetchall()
        for fila in filas_msg:
            texto = str(fila[2] or "")
            if len(texto) > MAX_TEXTO_TELEGRAM:
                texto = texto[:MAX_TEXTO_TELEGRAM]
            mensajes.append({
                "id": int(fila[0]),
                "from_name": str(fila[1] or ""),
                "texto": texto,
                "hora": _hora_local_mensaje(fila[3]),
                "fecha": str(fila[3])[:10] if fila[3] else "",
                "tipo": str(fila[4] or ""),
                "fotos": [],  # se completa abajo
                "municipio": str(municipio),
                "_date_utc": str(fila[3] or ""),
            })

    if not mensajes:
        return []

    # Fotos: media_ids de telegram_media (media_type='photo') por mensaje.
    ids = [m["id"] for m in mensajes]
    marcadores_ids = ",".join("?" * len(ids))
    fotos_por_msg: dict[int, list[int]] = {}
    for mid, media_id in conn.execute(f"""
        SELECT message_id, media_id FROM telegram_media
        WHERE media_type = 'photo' AND media_id IS NOT NULL
          AND message_id IN ({marcadores_ids})
        ORDER BY message_id, media_order
    """, ids).fetchall():
        fotos_por_msg.setdefault(int(mid), []).append(int(media_id))

    for m in mensajes:
        m["fotos"] = json.dumps(fotos_por_msg.get(m["id"], []))

    # Orden estable por fecha UTC (igual que la web: el chat sigue el día).
    mensajes.sort(key=lambda m: (m["_date_utc"], m["id"]))
    for m in mensajes:
        m.pop("_date_utc", None)
    return mensajes


def _generar_html_chat(
    conn: sqlite3.Connection,
    mensajes: list[dict],
    ruta_salida: str,
    con_fotos: bool = True,
) -> Optional[str]:
    """Genera el HTML autocontenido del chat de Telegram para TouchDesigner.

    Web Render TOP (file://, cero red). Embebe los mensajes como JSON inline
    y el reloj del loop (?t0=&loop_secs=). Cuando se abre sin parámetros
    (?t0, ?loop_secs) todos los mensajes se muestran de inmediato (preview
    estática).

    Args:
        conn: conexión SQLite (para resolver media_ids de fotos).
        mensajes: lista de dicts devuelta por _consultar_mensajes_telegram.
        ruta_salida: ruta del archivo HTML a escribir.
        con_fotos: si se embeben fotos inline como data URIs (default True).

    Returns:
        Ruta absoluta del archivo escrito, o None si no se pudo generar.
    """
    import base64
    import io

    # Pillow es opcional: si no está instalado, se omiten las fotos.
    try:
        from PIL import Image
    except ImportError:
        if con_fotos:
            log.warning("  Pillow no disponible; se omiten fotos del HTML del chat.")
        con_fotos = False

    n_fotos = 0
    datos: list[dict] = []

    for m in mensajes:
        fotos_data: list[str] = []
        if con_fotos:
            # m["fotos"] es un JSON string de media_ids, p. ej. "[42, 57]"
            try:
                media_ids = json.loads(m["fotos"]) if m["fotos"] else []
            except (json.JSONDecodeError, TypeError):
                media_ids = []
            for mid in media_ids:
                try:
                    fila = conn.execute(
                        "SELECT filepath_absoluto FROM media WHERE id = ?",
                        (int(mid),),
                    ).fetchone()
                    if not fila:
                        continue
                    ruta_archivo = str(fila[0])
                    if not os.path.isfile(ruta_archivo):
                        continue
                    img = Image.open(ruta_archivo)
                    # Si tiene canal alfa, composar sobre negro para JPEG
                    if img.mode in ("RGBA", "LA", "PA"):
                        fondo = Image.new("RGB", img.size, (0, 0, 0))
                        fondo.paste(img, mask=img.split()[-1])
                        img = fondo
                    elif img.mode != "RGB":
                        img = img.convert("RGB")
                    # Redimensionar: lado más largo ≤ 240 px
                    ancho, alto = img.size
                    largo = max(ancho, alto)
                    if largo > 240:
                        ratio = 240.0 / largo
                        img = img.resize(
                            (int(ancho * ratio), int(alto * ratio)),
                            Image.LANCZOS,
                        )
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=75)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    fotos_data.append(f"data:image/jpeg;base64,{b64}")
                    n_fotos += 1
                except Exception:
                    # Nunca crashear por una foto; simplemente se salta.
                    continue

        datos.append({
            "id": m["id"],
            "from_name": m["from_name"],
            "texto": m["texto"],
            "hora": m["hora"],
            "fecha": m["fecha"],
            "tipo": m["tipo"],
            "municipio": m["municipio"],
            "fotos": fotos_data,
        })

    # Serializar y proteger contra </script>
    json_cadena = json.dumps(datos, ensure_ascii=False)
    json_cadena = json_cadena.replace("</", "<\\/")

    html = _PLANTILLA_CHAT_HTML.replace("<JSON_EMBEDDED>", json_cadena)

    try:
        Path(ruta_salida).parent.mkdir(parents=True, exist_ok=True)
        with open(ruta_salida, "w", encoding="utf-8") as fh:
            fh.write(html)
        log.info("  Chat HTML escrito: %s (%d mensajes, %d fotos)",
                 ruta_salida, len(datos), n_fotos)
        return os.path.abspath(ruta_salida)
    except OSError as exc:
        log.error("  Error escribiendo HTML del chat: %s", exc)
        return None


# Plantilla del HTML autocontenido del chat. El placeholder <JSON_EMBEDDED> se
# reemplaza por el JSON serializado de los mensajes (con escapes de </script>).
_PLANTILLA_CHAT_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Flujos · Chat Telegram (TD)</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; overflow:hidden; font-family:system-ui,'Segoe UI',sans-serif; background:transparent; }
  :root { --tr:200; --tg:200; --tb:200; --ar:255; --ag:200; --ab:100; }
  #chat { display:flex; flex-direction:column; gap:.1rem; width:100%; height:100%; min-height:0; overflow-y:auto; padding:.2rem .3rem; color:rgb(var(--tr),var(--tg),var(--tb)); scrollbar-width:none; }
  #chat::-webkit-scrollbar { display:none; }
  .lugar { margin:.25rem 0 .1rem; font-size:.48rem; letter-spacing:.08em; text-transform:uppercase; opacity:.5; color:rgb(var(--ar),var(--ag),var(--ab)); border-top:1px solid rgba(var(--tr),var(--tg),var(--tb),.12); padding-top:.15rem; font-weight:500; flex-shrink:0; }
  .msg { font-size:.5rem; line-height:1.3; border-bottom:1px solid rgba(var(--tr),var(--tg),var(--tb),.08); padding:.1rem 0; opacity:0; transition:opacity .6s ease; flex-shrink:0; }
  .msg.visible { opacity:1; }
  .msg .hora { opacity:.5; font-size:.45rem; }
  .msg .nombre { opacity:.85; font-weight:600; }
  .msg .texto { opacity:.65; white-space:pre-wrap; }
  .fotos { display:flex; gap:.15rem; margin-top:.1rem; flex-wrap:wrap; }
  .fotos img { width:auto; height:2.8rem; max-width:5rem; object-fit:cover; border-radius:2px; border:1px solid rgba(var(--tr),var(--tg),var(--tb),.12); }
</style>
</head>
<body>
<div id="chat"></div>
<script>
var MENSAJES = <JSON_EMBEDDED>;
(function(){
  function iconoTipo(t){
    if (t === 'photo') return '\uD83D\uDCF7 ';
    if (t === 'video') return '\uD83C\uDFAC ';
    if (t === 'voice') return '\uD83C\uDFA4 ';
    return '\uD83D\uDCCE ';
  }
  function hhmm(h){
    if (h == null || isNaN(h)) return '';
    var hi = Math.floor(h);
    var mi = Math.round((h - hi) * 60);
    if (mi === 60) { mi = 0; hi += 1; }
    if (hi === 24) hi = 0;
    return ('0' + hi).slice(-2) + ':' + ('0' + mi).slice(-2);
  }
  var params = new URLSearchParams(location.search);
  var t0 = parseFloat(params.get('t0'));
  var loopSecs = parseFloat(params.get('loop_secs'));
  var conReloj = isFinite(t0) && isFinite(loopSecs) && loopSecs > 0;
  function horaActual(){
    if (!conReloj) return 24;
    var t = ((Date.now() - t0) / 1000) % loopSecs;
    return (t / loopSecs) * 24;
  }
  var cont = document.getElementById('chat');
  var NODOS = [];
  var orden = [];
  MENSAJES.forEach(function(m){
    if (orden.indexOf(m.municipio) === -1) orden.push(m.municipio);
  });
  orden.forEach(function(lugar){
    var sep = document.createElement('div');
    sep.className = 'lugar';
    sep.textContent = lugar || '—';
    cont.appendChild(sep);
    MENSAJES.forEach(function(m){
      if (m.municipio !== lugar) return;
      var div = document.createElement('div');
      div.className = 'msg';
      var linea = document.createElement('div');
      linea.appendChild(document.createTextNode(hhmm(m.hora) + ' '));
      var horaEl = document.createElement('span'); horaEl.className = 'hora';
      horaEl.textContent = (m.fecha || '') + ' ';
      linea.appendChild(horaEl);
      var nom = document.createElement('span'); nom.className = 'nombre';
      nom.textContent = (m.from_name || 'Desconocido') + ' ';
      linea.appendChild(nom);
      var txt = document.createElement('span'); txt.className = 'texto';
      txt.textContent = iconoTipo(m.tipo) + (m.texto || '');
      linea.appendChild(txt);
      div.appendChild(linea);
      if (m.fotos && m.fotos.length) {
        var fot = document.createElement('div'); fot.className = 'fotos';
        m.fotos.forEach(function(src){
          var im = document.createElement('img'); im.src = src; im.loading = 'lazy';
          fot.appendChild(im);
        });
        div.appendChild(fot);
      }
      cont.appendChild(div);
      NODOS.push(div);
    });
  });
  setInterval(function(){
    var ha = horaActual();
    for (var i = 0; i < MENSAJES.length && i < NODOS.length; i++) {
      if (ha >= MENSAJES[i].hora) NODOS[i].classList.add('visible');
    }
    cont.scrollTop = cont.scrollHeight;
  }, 250);
})();
</script>
</body>
</html>
"""


def _generar_html_textos(
    textos: list[dict],
    ruta_salida: str,
) -> Optional[str]:
    """Genera el HTML autocontenido de los textos (crónicas, type='text') para TD.

    Web Render TOP `web_render_textos` (file://, cero red). Embebe los textos
    como JSON inline + el reloj del loop (?t0=&loop_secs=). Muestra UN texto a
    la vez, rotando con el reloj del loop en orden de ruta (keypoint = t_loop).
    Sin parámetros (preview en browser) rota cada 6 s para mostrar el
    comportamiento.

    Args:
        textos: lista de dicts del spec (por_tipo["text"]). Cada item tiene
            media_id, titulo (=titulo_seccion), desc (=texto_completo),
            hora (float 0..24), keypoint/t_loop (float, posición en el loop).
        ruta_salida: ruta del archivo HTML a escribir.

    Returns:
        Ruta absoluta del archivo escrito, o None si no se pudo generar.
    """
    datos: list[dict] = []
    for medio in textos:
        media_id = int(medio.get("media_id") or 0)
        titulo = str(medio.get("titulo") or "")
        texto = str(medio.get("desc") or "")
        hora = float(medio.get("hora") or 0.0)
        keypoint = float(medio.get("keypoint") or medio.get("t_loop") or 0.0)
        municipio = str(medio.get("municipio") or "")
        datos.append({
            "media_id": media_id,
            "titulo": titulo,
            "texto": texto,
            "hora": hora,
            "keypoint": keypoint,
            "municipio": municipio,
        })
    # Orden estable de ruta: los textos rotan en el orden en que aparecen en el
    # loop (keypoint = t_loop). Los que comparten keypoint se ordenan por media_id.
    datos.sort(key=lambda d: (d["keypoint"], d["media_id"]))

    json_cadena = json.dumps(datos, ensure_ascii=False)
    json_cadena = json_cadena.replace("</", "<\\/")

    html = _PLANTILLA_TEXTOS_HTML.replace("<JSON_EMBEDDED>", json_cadena)

    try:
        Path(ruta_salida).parent.mkdir(parents=True, exist_ok=True)
        with open(ruta_salida, "w", encoding="utf-8") as fh:
            fh.write(html)
        log.info("  Textos HTML escrito: %s (%d textos)", ruta_salida, len(datos))
        return os.path.abspath(ruta_salida)
    except OSError as exc:
        log.error("  Error escribiendo HTML de textos: %s", exc)
        return None


# Plantilla del HTML autocontenido de los textos (crónicas, type='text').
# El placeholder <JSON_EMBEDDED> se reemplaza por el JSON serializado de los
# textos (con escapes de </script>). Muestra UN texto a la vez, rotando con el
# reloj del loop (?t0=&loop_secs=) en orden de ruta (keypoint); sin reloj
# (preview) rota cada 6 s.
_PLANTILLA_TEXTOS_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Flujos · Textos (TD)</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; overflow:hidden; font-family:system-ui,'Segoe UI',sans-serif; background:transparent; }
  :root { --tr:200; --tg:200; --tb:200; --ar:255; --ag:200; --ab:100; }
  #textos { display:flex; flex-direction:column; justify-content:center; gap:.35rem; width:100%; height:100%; min-height:0; overflow-y:auto; padding:.4rem .5rem; color:rgb(var(--tr),var(--tg),var(--tb)); scrollbar-width:none; }
  #textos::-webkit-scrollbar { display:none; }
  .texto-card { font-size:.6rem; line-height:1.5; padding:.4rem 0; opacity:0; transition:opacity .7s ease; }
  .texto-card.visible { opacity:1; }
  .texto-card .titulo { font-weight:600; letter-spacing:.06em; color:rgb(var(--ar),var(--ag),var(--ab)); margin-bottom:.2rem; }
  .texto-card .hora { opacity:.5; font-size:.48rem; margin-right:.3rem; }
  .texto-card .lugar { opacity:.5; font-size:.48rem; }
  .texto-card .cuerpo { opacity:.8; white-space:pre-wrap; }
</style>
</head>
<body>
<div id="textos"></div>
<script>
var TEXTOS = <JSON_EMBEDDED>;
(function(){
  function hhmm(h){
    if (h == null || isNaN(h)) return '';
    var hi = Math.floor(h);
    var mi = Math.round((h - hi) * 60);
    if (mi === 60) { mi = 0; hi += 1; }
    if (hi === 24) hi = 0;
    return ('0' + hi).slice(-2) + ':' + ('0' + mi).slice(-2);
  }
  var params = new URLSearchParams(location.search);
  var t0 = parseFloat(params.get('t0'));
  var loopSecs = parseFloat(params.get('loop_secs'));
  var conReloj = isFinite(t0) && isFinite(loopSecs) && loopSecs > 0;
  var cont = document.getElementById('textos');
  var NODOS = [];
  TEXTOS.forEach(function(tx){
    var card = document.createElement('div');
    card.className = 'texto-card';
    var tit = document.createElement('div'); tit.className = 'titulo';
    var horaEl = document.createElement('span'); horaEl.className = 'hora';
    horaEl.textContent = hhmm(tx.hora) + ' ';
    tit.appendChild(horaEl);
    tit.appendChild(document.createTextNode(tx.titulo || ''));
    if (tx.municipio) {
      var lugar = document.createElement('span'); lugar.className = 'lugar';
      lugar.textContent = ' \u00b7 ' + tx.municipio;
      tit.appendChild(lugar);
    }
    var cuerpo = document.createElement('div'); cuerpo.className = 'cuerpo';
    cuerpo.textContent = tx.texto || '';
    card.appendChild(tit);
    card.appendChild(cuerpo);
    cont.appendChild(card);
    NODOS.push(card);
  });
  var actual = -1;
  function indiceActual(){
    if (TEXTOS.length === 0) return -1;
    if (conReloj) {
      // Un texto a la vez: slots de igual duración en orden de ruta; el loop
      // avanza y el texto activo rota a un ritmo estable.
      var t = ((Date.now() - t0) / 1000) % loopSecs;
      var slot = loopSecs / TEXTOS.length;
      return Math.floor(t / slot) % TEXTOS.length;
    }
    // Preview (sin ?t0): rota cada 6 s para mostrar el comportamiento.
    return Math.floor((Date.now() / 1000) / 6) % TEXTOS.length;
  }
  function pintar(){
    var idx = indiceActual();
    if (idx < 0) return;
    if (idx !== actual) {
      if (actual >= 0 && NODOS[actual]) NODOS[actual].classList.remove('visible');
      NODOS[idx].classList.add('visible');
      actual = idx;
    }
  }
  setInterval(pintar, 250);
  pintar();
})();
</script>
</body>
</html>
"""


# ── Mapas por municipio (ruta al HTML generado por mapas_municipio.py) ────────

# Variante del mapa que se envía a TD. Las posibles viven en
# `scripts/mapas_municipio.py` (VARIANTES): ruta, puntos, contexto, gradiente.
# 'ruta' = puntos del municipio + línea que los conecta.
VARIANTE_MAPA_MUNICIPIO = "ruta"

# Plantilla de la ruta (RELATIVA a la raíz del proyecto) del mapa de cada
# municipio, consistente con el nombre de archivo que genera
# `scripts/mapas_municipio.py` (`_nombre_archivo`):
#   mapas/mapa_municipio_<slug>_<variante>.html
# donde <slug> es el municipio normalizado a ASCII (sin acentos ni símbolos,
# espacios→_; ej: 'Río Hondo' -> 'Rio_Hondo'). Esta convención ASCII evita
# problemas de visualización en TouchDesigner.
# El puente NO genera los mapas; solo construye la ruta del archivo.
PLANTILLA_MAPA_MUNICIPIO = "mapas/mapa_municipio_{slug}_{variante}.html"


def _slug_municipio(nombre: str) -> str:
    """Convierte el nombre de un municipio al formato usado en el archivo.

    Igual que `mapas_municipio.py::_slug_municipio`: normaliza a NFD (elimina
    tildes, diéresis y la virgulilla de la ñ), reemplaza los espacios por guion
    bajo y descarta símbolos no alfanuméricos. Conserva las mayúsculas.
    Ejemplos: 'Bell Ville' -> 'Bell_Ville', 'Río Hondo' -> 'Rio_Hondo'.
    """
    nfkd = unicodedata.normalize("NFD", str(nombre or ""))
    sin_diacriticos = "".join(c for c in nfkd if unicodedata.combining(c) == 0)
    return "".join(
        c if c.isalnum() else "_" if c == " " else ""
        for c in sin_diacriticos
    ).strip("_")


def _ruta_mapa_municipio(nombre: str) -> str:
    """Ruta ABSOLUTA al mapa HTML de un municipio.

    La ruta que se envía por OSC es la completa (raíz del proyecto + archivo),
    porque TD/Web Render necesita la ruta absoluta para cargar el archivo.
    """
    relativa = PLANTILLA_MAPA_MUNICIPIO.format(
        slug=_slug_municipio(nombre),
        variante=VARIANTE_MAPA_MUNICIPIO,
    )
    return str(Path(__file__).resolve().parents[2] / relativa)


def _procesar_rafaga(
    db_path: str,
    selecciones: dict[str, list[str]],
    loop_secs: float,
    spec_salida: str,
    host: str = OSC_HOST,
    enviar_medios: bool = True,
    enviar_telegram: bool = True,
    enviar_mapas: bool = True,
    generar_chat_html: bool = True,
    generar_textos_html: bool = True,
) -> Optional[str]:
    """
    Genera el spec del loop con loop_db.generar_loop, lo escribe a archivo y
    lo envía por OSC al puerto 9002 (canal separado para el resultado).

Contrato de salida por 9002 (rediseño: el spec trae `por_tipo` y `resumen`):
      1. `/flujos/fluir/resumen <total> <loop_secs> <image> <video> <audio> <text> <video360> <telegram>`
         — resumen con conteos por tipo (de `spec['resumen']`, salvo video/
         video360 que salen de la separación por es_360; `telegram` es el
         conteo de mensajes del chat, 0 si no hay municipios elegidos).
      2. `/flujos/fluir/filtro <clave> <valor>` — uno por filtro puesto por el
         usuario (hora_inicio, hora_fin, horas_elegidas, municipios, colores,
         tags, dias, clima). El callbacks los escribe como filas [clave, valor]
         en `fluir_estado`.
      3. Por tipo, en orden estable (image, video, video360, audio, text):
         `/flujos/fluir/tabla <tipo> <cantidad>` — anuncio del comienzo de una
         tabla para un tipo (TD arma fluir_fotos/fluir_videos/fluir_videos_360/
         fluir_audios/fluir_textos).
         `/flujos/fluir/medio <media_id> <ruta> <keypoint> <hora> <tipo>` — uno
         por medio (keypoint + hora posicionan temporalmente sin leer el archivo).
         Los videos 360 van con `tipo=video360` a su propia tabla.
         `/flujos/fluir/texto <media_id> <titulo> <texto>` — uno por medio
         tipo=text, justo después de su `/medio`, con el contenido completo
         (titulo_seccion + texto_completo) para que TD lo visualice (la ruta
         .md que viaja en `/medio` no sirve para mostrar el texto).
      4. `/flujos/fluir/chiche <hora> <texto>` — uno por chiche ambiental.
      5. `/flujos/fluir/mensaje <id> <from_name> <texto> <hora> <fecha> <tipo> <fotos> <municipio>`
         — uno por mensaje de Telegram del chat, SOLO si el visitante eligió
         municipio(s). Cada mensaje lleva su hora local (UTC-3) para que TD lo
         sincronice con la hora que corre en el loop (mismo criterio que la web).
      6. `/flujos/fluir/mapa <municipio> <ruta>` — uno por municipio elegido,
         SOLO si el visitante eligió municipio(s). `ruta` es la ruta ABSOLUTA al
         mapa HTML del municipio (generado por `scripts/mapas_municipio.py`,
         variante 'ruta'); TD la guarda en `fluir_mapas` para renderizarla.
      7. `/flujos/fluir/fin <total>` — marca de finalización.
      8. La spec completa se escribe a `spec_salida` (TD puede leerla).

    Con `enviar_medios` False solo se envían resumen + fin (sin tabla/medio/chiche).
    Con `enviar_telegram` False se omite el bloque de mensajes de Telegram.
    Con `enviar_mapas` False se omite el bloque de rutas de mapas.

    Args:
        db_path: ruta a la base de datos.
        selecciones: ráfaga acumulada {grupo: [valores...]}.
        loop_secs: duración del loop en segundos.
        spec_salida: ruta del archivo JSON donde se vuelca el spec.
        host: host de TD para el cliente OSC de salida.
        enviar_medios: si se envían los mensajes tabla/medio/chiche (default True).
        enviar_telegram: si se envían los mensajes de Telegram (default True).
        enviar_mapas: si se envían las rutas de mapas por municipio (default True).
        generar_chat_html: si se escribe el HTML autocontenido del chat
            (td/chat_fluir.html) para el Web Render de TD (default True).
        generar_textos_html: si se escribe el HTML autocontenido de los textos
            (td/textos_fluir.html) para el Web Render de TD (default True).

    Returns:
        Ruta del spec escrita, o None si no se pudo generar.
    """
    loop_db = _importar_loop_db()

    horas, filtros = _filtros_desde_seleccion(selecciones)
    log.info("  Horas: %s | filtros: %s",
             ", ".join(str(h) for h in horas) or "sin horas (0..23)",
             filtros or "ninguno")

    ruta_spec = Path(spec_salida)
    if not ruta_spec.is_absolute():
        ruta_spec = Path(__file__).resolve().parents[2] / ruta_spec
    ruta_spec.parent.mkdir(parents=True, exist_ok=True)
    ruta_spec = str(ruta_spec)

    ruta_chat_html = Path(__file__).resolve().parents[2] / CHAT_HTML_RELATIVO
    ruta_textos_html = Path(__file__).resolve().parents[2] / TEXTOS_HTML_RELATIVO

    spec = loop_db.generar_loop(
        db_path=db_path,
        horas=horas,
        loop_secs=loop_secs,
        modalidad_ubicaciones="geo",
        filtros=filtros,
        salida=ruta_spec,
    )

    resumen = spec.get("resumen") or {}
    n_total = resumen.get("total", len(spec.get("medios", [])))
    n_chiches = len(spec.get("chiches", []))
    log.info("   Loop generado: %d medios, %d chiches.", n_total, n_chiches)
    if n_total == 0:
        log.warning("  El loop no dejó medios dentro del arco "
                    "(filtros + horas demasiado estrictos).")

    por_tipo = spec.get("por_tipo") or {}
    # Separación de videos 360° (marcador es_360 del spec): los normales van a
    # fluir_videos y los 360 a fluir_videos_360 en la emisión por 9002.
    videos_normales, videos_360 = _separar_videos_360(por_tipo)

    # HTML autocontenido de los textos (type='text') para el Web Render de TD.
    # Se escribe SIEMPRE que generar_textos_html esté activo (aunque haya 0
    # textos), para que td/textos_fluir.html exista y el Web Render tenga a qué
    # apuntar. Canal OSC intacto.
    if generar_textos_html:
        ruta_textos = _generar_html_textos(
            por_tipo.get("text", []) or [], str(ruta_textos_html))
        if not ruta_textos:
            log.warning("  No se pudo escribir el HTML de textos.")

    # Mensajes de Telegram del chat: solo si el visitante eligió municipio(s).
    # Acompañan al loop (cada uno con su hora local), no son medios del arco.
    mensajes_telegram: list[dict] = []
    if enviar_telegram and (filtros.get("municipios") or []):
        conn = abrir(db_path)
        try:
            mensajes_telegram = _consultar_mensajes_telegram(
                conn, filtros["municipios"])
            # HTML autocontenido del chat para el Web Render de TD (canal OSC intacto).
            # Se escribe SIEMPRE que haya municipios (aunque haya 0 mensajes), para que
            # td/chat_fluir.html exista y el Web Render tenga a qué apuntar.
            if generar_chat_html:
                ruta_chat = _generar_html_chat(
                    conn, mensajes_telegram, str(ruta_chat_html))
                if not ruta_chat:
                    log.warning("  No se pudo escribir el HTML del chat.")
        finally:
            conn.close()
        log.info("   Telegram: %d mensajes del chat (municipios: %s)",
                 len(mensajes_telegram),
                 ", ".join(filtros["municipios"]) or "-")

    cli = udp_client.SimpleUDPClient(host, OSC_PUERTO_TD_RESULTADO)
    enviar(cli, f"{OSC_ADDR_FLUIR}/resumen",
           n_total,
           spec.get("loop_secs", loop_secs),
           resumen.get("image", 0),
           len(videos_normales),
           resumen.get("audio", 0),
           resumen.get("text", 0),
           len(videos_360),
           len(mensajes_telegram))

    # Filtros puestos por el usuario: se reflejan en fluir_estado para que el
    # estado del loop muestre qué eligió el visitante (hora inicio/fin y
    # municipios/colores/tags/dias/clima si vienen). Mensaje genérico por
    # clave-valor; el callbacks los escribe como filas [clave, valor].
    rango = resumen.get("rango_horas") or [0, 23]
    _enviar_filtro(cli, "hora_inicio", rango[0])
    _enviar_filtro(cli, "hora_fin", rango[1])
    _enviar_filtro(cli, "horas_elegidas", horas if horas else [])
    for clave_filtro, etiqueta in (
        ("municipios", "municipios"),
        ("colores", "colores"),
        ("tags", "tags"),
        ("dias", "dias"),
        ("clima", "clima"),
    ):
        valores = filtros.get(clave_filtro) or []
        if valores:
            _enviar_filtro(cli, etiqueta, valores)
    log.info("  Filtros enviados por 9002: hora_inicio=%s hora_fin=%s "
             "municipios=%s colores=%s tags=%s",
             rango[0], rango[1],
             filtros.get("municipios") or [],
             filtros.get("colores") or [],
             filtros.get("tags") or [])

    if enviar_medios:
        # Bloques en orden estable: image, video (normales), video360,
        # audio, text. Si un bloque no tiene medios no se emite nada: el
        # resumen ya lo reporta en 0.
        bloques_envio: list[tuple[str, list]] = [
            ("image", por_tipo.get("image", []) or []),
            ("video", videos_normales),
            ("video360", videos_360),
            ("audio", por_tipo.get("audio", []) or []),
            ("text", por_tipo.get("text", []) or []),
        ]
        for tipo, items in bloques_envio:
            if not items:
                continue
            enviar(cli, f"{OSC_ADDR_FLUIR}/tabla", tipo, len(items))
            for medio in items:
                media_id = medio.get("media_id")
                ruta = str(medio.get("ruta") or "").replace("\\", "/")
                keypoint = medio.get("keypoint")
                if keypoint is None:
                    keypoint = medio.get("t_loop", 0.0)
                hora = medio.get("hora", 0.0)
                if not ruta:
                    log.warning("  ruta vacía para media_id %s; se envía igual",
                                media_id)
                enviar(cli, f"{OSC_ADDR_FLUIR}/medio",
                       media_id, ruta, keypoint, hora, tipo)
                # Textos: el contenido completo (texto_completo + titulo_seccion)
                # no viaja en /medio (solo la ruta .md, inútil para TD). Se envía
                # DESPUÉS del /medio del mismo item: TD ubica la fila por
                # media_id, la fila debe existir antes de llegar el /texto.
                if tipo == "text":
                    titulo = str(medio.get("titulo") or "")
                    texto = str(medio.get("desc") or "")
                    if len(texto) > 8000:
                        log.warning("  texto truncado a 8000 chars para media_id "
                                    "%s (len=%d)", media_id, len(texto))
                        texto = texto[:8000]
                    enviar(cli, f"{OSC_ADDR_FLUIR}/texto",
                           media_id, titulo, texto)

        for chich in spec.get("chiches", []):
            hora_chiche = chich.get("hora")
            if hora_chiche is None:
                hora_chiche = chich.get("t", 0.0)
            enviar(cli, f"{OSC_ADDR_FLUIR}/chiche",
                   hora_chiche, chich.get("texto", ""))

    if enviar_telegram and mensajes_telegram:
        # Bloque del chat: /tabla telegram + un /mensaje por mensaje. El
        # callbacks de TD lo escribe en fluir_telegram (no cuenta en los
        # recibidos/esperados del /fin, que validan medios del loop).
        enviar(cli, f"{OSC_ADDR_FLUIR}/tabla", "telegram", len(mensajes_telegram))
        for m in mensajes_telegram:
            enviar(cli, f"{OSC_ADDR_FLUIR}/mensaje",
                   m["id"], m["from_name"], m["texto"], m["hora"],
                   m["fecha"], m["tipo"], m["fotos"], m["municipio"])

    if enviar_mapas and (filtros.get("municipios") or []):
        # Bloque de mapas: un /mapa por municipio elegido, con la ruta al HTML
        # (generado por scripts/mapas_municipio.py, variante 'ruta'). El
        # callbacks de TD lo escribe en fluir_mapas [municipio, ruta].
        municipios_unicos = list(dict.fromkeys(str(m) for m in filtros["municipios"]))
        for nombre in municipios_unicos:
            ruta_mapa = _ruta_mapa_municipio(nombre)
            enviar(cli, f"{OSC_ADDR_FLUIR}/mapa", nombre, ruta_mapa)
        log.info("   Mapas enviados por 9002: %d municipios", len(municipios_unicos))

    enviar(cli, f"{OSC_ADDR_FLUIR}/fin", n_total)
    detalle = f"{n_total} medios"
    if enviar_medios:
        detalle += f" + {n_chiches} chiches"
    else:
        detalle += " (modo resumen+fin, sin tabla/medio/chiche)"
    if mensajes_telegram:
        detalle += f" + {len(mensajes_telegram)} mensajes de Telegram"
    if enviar_mapas and (filtros.get("municipios") or []):
        detalle += f" + {len(municipios_unicos)} mapas"
    log.info("  Enviado por 9002: %s.", detalle)
    return ruta_spec


def modo_fluir(
    db_path: str,
    debounce: float = 0.7,
    loop_secs: float = 300.0,
    spec_salida: str = "td/spec_fluir.json",
    una_vez: bool = False,
    host: str = OSC_HOST,
    enviar_medios: bool = True,
    enviar_telegram: bool = True,
    enviar_mapas: bool = True,
    generar_chat_html: bool = True,
    generar_textos_html: bool = True,
) -> None:
    """
    Modo "Fluir": escucha la ráfaga de selección de TD, la acumula por grupo,
    detecta el fin con debounce y genera/envia el loop por 9002.

    TD envía un mensaje OSC por cada elección acumulada en el único click del
    "Fluir", con el formato `/flujos/seleccion/<grupo> <valor>`. No existe una
    marca de "fin": se considera la ráfaga completa cuando pasan `debounce`
    segundos sin recibir otro mensaje. Tras procesarla el proceso queda
    escuchando la próxima ráfaga (a menos que `una_vez` sea True).

    Args:
        db_path: ruta a la base de datos.
        debounce: segundos sin mensajes para considerar la ráfaga terminada.
        loop_secs: duración del loop en segundos.
        spec_salida: ruta del archivo JSON del spec.
        una_vez: procesar una única ráfaga y salir.
        host: host de TD.
        enviar_medios: si se envían los mensajes tabla/medio/chiche por 9002
            (False → solo resumen + fin).
        enviar_telegram: si se envían los mensajes de Telegram del chat por 9002
            (solo cuando hay municipios elegidos; False → sin bloque /mensaje).
        enviar_mapas: si se envían las rutas de mapas por municipio por 9002
            (solo cuando hay municipios elegidos; False → sin bloque /mapa).
        generar_chat_html: si se escribe el HTML autocontenido del chat
            (td/chat_fluir.html) para el Web Render de TD (default True).
        generar_textos_html: si se escribe el HTML autocontenido de los textos
            (td/textos_fluir.html) para el Web Render de TD (default True).
    """
    selecciones: dict[str, list[str]] = {}
    ultimo_mensaje = time.monotonic()

    def al_recibir_seleccion(addr: str, *args: Any) -> None:
        """Acumula cada mensaje de la ráfaga en su grupo."""
        nonlocal ultimo_mensaje
        if not addr.startswith(OSC_ADDR_SELECCION + "/"):
            return
        grupo = addr.rsplit("/", 1)[-1]
        if grupo == "seleccion":
            return
        valores = [str(a) for a in args if a is not None]
        if not valores:
            return
        if grupo != "horas" and grupo not in GRUPOS_OSC_A_FILTRO:
            log.warning("  Grupo OSC desconocido, se ignora: %r", grupo)
            return
        ultimo_mensaje = time.monotonic()
        selecciones.setdefault(grupo, []).extend(valores)
        log.info("  Ráfaga %s → %s", grupo, ", ".join(valores))

    disp = dispatcher.Dispatcher()
    disp.set_default_handler(al_recibir_seleccion)
    server = osc_server.ThreadingOSCUDPServer((host, OSC_PUERTO_PY), disp)
    hilo = threading.Thread(target=server.serve_forever, daemon=True)
    hilo.start()
    log.info("👂 Escuchando 'Fluir' en %s:%d (debounce %.1fs)... (Enter para detener)",
             host, OSC_PUERTO_PY, debounce)

    detener = detener_con_enter()
    try:
        while not detener.is_set():
            if selecciones and time.monotonic() - ultimo_mensaje >= debounce:
                log.info("  Ráfaga completa (%d selecciones). Generando loop...",
                         sum(len(v) for v in selecciones.values()))
                _procesar_rafaga(
                    db_path,
                    selecciones,
                    loop_secs,
                    spec_salida,
                    host,
                    enviar_medios=enviar_medios,
                    enviar_telegram=enviar_telegram,
                    enviar_mapas=enviar_mapas,
                    generar_chat_html=generar_chat_html,
                    generar_textos_html=generar_textos_html,
                )
                selecciones.clear()
                if una_vez:
                    log.info("  --una-vez: saliendo tras la primera ráfaga.")
                    break
            time.sleep(0.1)
    except KeyboardInterrupt:
        log.warning("⏹  Ctrl+C: escucha del 'Fluir' detenida.")
    finally:
        server.shutdown()
        server.server_close()
    if detener.is_set():
        log.info("  Detenido por el usuario (Enter).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Puente BD → TouchDesigner vía OSC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/td/puente_td.py elecciones           # nubes de elecciones
  python scripts/td/puente_td.py elecciones --grupo horas,tags
  python scripts/td/puente_td.py fluir                # escucha continua (Enter para detener)
  python scripts/td/puente_td.py fluir --una-vez --debounce 1.0  # 1 ráfaga y sale
  python scripts/td/puente_td.py fluir --no-enviar-medios        # solo resumen + fin
  python scripts/td/puente_td.py fluir --no-enviar-telegram      # sin mensajes de Telegram
  python scripts/td/puente_td.py fluir --no-enviar-mapas         # sin rutas de mapas por municipio
  python scripts/td/puente_td.py fluir --no-generar-chat-html   # sin HTML del chat (solo OSC)
  python scripts/td/puente_td.py fluir --no-generar-textos-html  # sin HTML de textos (solo OSC)

Probar "fluir" sin TouchDesigner (3 terminales):

  T1 (escucha 1 ráfaga y sale):
    python scripts/td/puente_td.py fluir --una-vez --debounce 1.0
  T2 (ráfaga falsa — 2 horas + municipio = rango + chat):
    python -c "from pythonosc import udp_client as c; cl=c.SimpleUDPClient('127.0.0.1',9001); msgs=[('/flujos/seleccion/horas','06:00'),('/flujos/seleccion/horas','13:00'),('/flujos/seleccion/municipios','Bell Ville')]; [cl.send_message(a,v) for a,v in msgs]"
  T3 (ver retorno por 9002):
    python scripts/td/osc_probe.py 9002 15
        """,
    )

    parser.add_argument("modo",
                        nargs="?",
                        choices=["elecciones", "fluir"],
                        default="elecciones",
                        help="Modo de operación")
    parser.add_argument("--grupo", default=None,
                        help="Grupos de elecciones separados por coma (ej: horas,tags)")
    parser.add_argument("--db", default=None,
                        help="Ruta a la DB (default: db/flujos.db)")
    parser.add_argument("--debounce", type=float, default=0.7,
                        help="Segundos sin nuevos mensajes OSC para considerar "
                             "terminada la ráfaga del 'Fluir' (default: 0.7)")
    parser.add_argument("--loop-secs", type=float, default=300.0,
                        help="Duración del loop en segundos (default: 300)")
    parser.add_argument("--spec-salida", default="td/spec_fluir.json",
                        help="Ruta del archivo JSON donde se escribe el spec "
                             "(default: td/spec_fluir.json)")
    parser.add_argument("--una-vez", action="store_true",
                        help="Procesar una única ráfaga y salir "
                             "(default: escucha continua)")
    parser.add_argument("--enviar-medios", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Enviar por 9002 los mensajes tabla/medio/chiche "
                             "(default: True). Con --no-enviar-medios solo van "
                             "resumen + fin.")
    parser.add_argument("--enviar-telegram", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Enviar por 9002 los mensajes de Telegram del chat "
                             "(default: True, solo si hay municipios elegidos). "
                             "Con --no-enviar-telegram se omite el bloque /mensaje.")
    parser.add_argument("--enviar-mapas", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Enviar por 9002 las rutas de mapas por municipio "
                             "(default: True, solo si hay municipios elegidos). "
                             "Con --no-enviar-mapas se omite el bloque /mapa.")
    parser.add_argument("--generar-chat-html", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Escribir el HTML autocontenido del chat "
                             "(td/chat_fluir.html) para el Web Render de TD "
                             "(default: True). Con --no-generar-chat-html no "
                             "se genera.")
    parser.add_argument("--generar-textos-html", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Escribir el HTML autocontenido de los textos "
                             "(td/textos_fluir.html) para el Web Render de TD "
                             "(default: True). Con --no-generar-textos-html no "
                             "se genera.")
    parser.add_argument("--host", default=OSC_HOST,
                        help=f"Host TD (default: {OSC_HOST})")
    parser.add_argument("--port", type=int, default=OSC_PUERTO_TD,
                        help=f"Puerto OSC TD (default: {OSC_PUERTO_TD})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Modo detallado")

    args = parser.parse_args(argv)

    nivel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=nivel, format="%(message)s")

    db_path = resolver_db(args.db)

    if not Path(db_path).exists():
        log.error(f"Base de datos no encontrada: {db_path}")
        return 1

    if args.modo == "elecciones":
        modo_elecciones(db_path, args.grupo)
    elif args.modo == "fluir":
        modo_fluir(
            db_path,
            debounce=args.debounce,
            loop_secs=args.loop_secs,
            spec_salida=args.spec_salida,
            una_vez=args.una_vez,
            host=args.host,
            enviar_medios=args.enviar_medios,
            enviar_telegram=args.enviar_telegram,
            enviar_mapas=args.enviar_mapas,
            generar_chat_html=args.generar_chat_html,
            generar_textos_html=args.generar_textos_html,
        )

    return 0


if __name__ == "__main__":
    exit(main())
