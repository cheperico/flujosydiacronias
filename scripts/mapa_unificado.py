#!/usr/bin/env python3
"""
mapa_unificado.py — Mapa unificado de "todo lo que tiene posición".

Un punto por cada "algo" con GPS:
  - media (image/video/audio/text) con latitude IS NOT NULL  → 1 marcador base
  - media_keypoints: contexto_* / ubicacion_video  → puntos de interés (capa contexto)
  - waypoints (GPX)                               → capa opcional

Transcripción (media_keypoints key='transcription') NO es marcador base:
se despliega como hijos del marcador padre al hacer click (misma capa, repliegue).

Variantes:
  --modo offline  (default): lee db/flujos.db (schema media/media_keypoints/waypoints)
                 → HTML 100% autocontenido (tiles + assets inline) para TD file://
  --modo online:  lee deploy/db/visualizacion.db (schema medios/keypoints de exportar_visualizacion.py)
                 → tiles/markercluster por CDN

Clusters: Leaflet.markercluster con iconCreateFunction multicolor.
  - 1 tipo → círculo sólido color del tipo
  - mixto  → conic-gradient segmentado por tipo

Uso:
    python scripts/mapa_unificado.py
    python scripts/mapa_unificado.py --modo online --db deploy/db/visualizacion.db
    python scripts/mapa_unificado.py --no-contexto --con-waypoints
    python scripts/mapa_unificado.py --output mapas/mapa_unificado.html --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import folium
    from folium import Element
except ImportError:
    print("ERROR: folium no instalado. pip install folium")
    sys.exit(1)

from scripts.track_gpx import cargar_tracks, interpolar_posicion  # noqa: E402
from scripts.tiles_offline import CACHE_DIR_ASSETS_DEFAULT, guardar_autocontenido  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("mapa_unificado")

# ── Paleta por tipo ──────────────────────────────────────────────────────────
TIPO_COLOR = {
    "image": "#3388ff",
    "video": "#cc3333",
    "audio": "#1a9e3e",
    "text": "#8a3acc",
    "waypoint": "#ff8c00",
    "contexto": "#e67e22",
    "transcription_seg": "#1a9e3e",
}
# Colores para contexto por sub-key
CONTEXTO_COLOR = {
    "contexto_elevacion": "#e67e22",
    "contexto_astronomia": "#8e44ad",
    "contexto_ubicacion": "#2980b9",
    "contexto_clima": "#16a085",
    "contexto_movimiento": "#d35400",
    "ubicacion_video": "#7f8c8d",
}
TILE_ESRI_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
ATTR_ESRI = '&copy; Esri &mdash; Esri, DeLorme, NAVTEQ, TomTom, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

CONTEXTO_KEYS = ["contexto_elevacion", "contexto_astronomia", "contexto_ubicacion", "contexto_clima", "contexto_movimiento"]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _norm_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _es_deploy_db(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT latitud FROM medios LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


# ── Lectura local (media) ───────────────────────────────────────────────────

def _ruta_a_file_uri(ruta: str | None) -> str | None:
    if not ruta:
        return None
    from urllib.parse import quote
    p = ruta.replace("\\", "/")
    if p.startswith("file://"):
        return p
    # Windows absoluto C:/... -> file:///C:/... con encoding de espacios/acentos
    if len(p) >= 2 and p[1] == ":":
        # p = "C:/resto" -> file:///C:/resto%20con%20espacios
        return "file:///" + p[0] + ":" + quote(p[2:], safe="/")
    if p.startswith("/"):
        return "file://" + quote(p, safe="/")
    # relativo (deploy/media/...) -> dejar relativo pero con encoding
    return quote(p, safe="/")


def leer_puntos_base_local(conn: sqlite3.Connection) -> list[dict]:
    """Lee media con GPS + flag has_transcription + preview (path/texto)."""
    rows = conn.execute("""
        SELECT m.id, m.filename_original, m.type, m.subtype, m.carpeta,
               m.latitude, m.longitude, m.timestamp_utc, m.municipio, m.provincia,
               m.altitude, m.filepath_absoluto,
               (SELECT COUNT(*) FROM media_keypoints k WHERE k.media_id=m.id AND k.key='transcription') as n_seg,
               (SELECT value FROM media_metadata mm WHERE mm.media_id=m.id AND mm.key='texto_completo' LIMIT 1) as texto_completo
        FROM media m
        WHERE m.latitude IS NOT NULL AND m.longitude IS NOT NULL
        ORDER BY m.timestamp_utc ASC
    """).fetchall()
    puntos = []
    for r in rows:
        puntos.append({
            "id": r[0], "filename": r[1], "type": r[2] or "other", "subtype": r[3] or "",
            "carpeta": r[4] or "", "lat": r[5], "lon": r[6],
            "timestamp": r[7] or "", "municipio": r[8] or "", "provincia": r[9] or "",
            "altitude": r[10], "filepath": r[11] or "", "n_seg": r[12] or 0,
            "texto": (r[13] or "")[:2000] if r[13] else "",
        })
    return puntos


def leer_transcripciones_local(conn: sqlite3.Connection, tracks) -> dict:
    """Dict media_id -> list de segmentos con lat/lon resueltas."""
    rows = conn.execute("""
        SELECT k.media_id, k.value, k.timestamp_offset_secs, k.timestamp_absolute,
               m.latitude as mlat, m.longitude as mlon
        FROM media_keypoints k
        JOIN media m ON m.id=k.media_id
        WHERE k.key='transcription'
        ORDER BY k.media_id, k.timestamp_offset_secs
    """).fetchall()
    out: dict[int, list] = {}
    for media_id, value, off, ts_abs, mlat, mlon in rows:
        lat, lon, fuente = _resolver_posicion_kp(mlat, mlon, ts_abs, tracks)
        if lat is None:
            # Sin posición resoluble -> se ignora del mapa (consigna)
            continue
        out.setdefault(media_id, []).append({
            "off": off or 0, "ts_abs": ts_abs or "", "lat": lat, "lon": lon,
            "text": (value or "")[:300], "fuente": fuente,
        })
    return out


def leer_contexto_local(conn: sqlite3.Connection, tracks) -> list[dict]:
    """Lee keypoints contexto_* + ubicacion_video con posición resoluble."""
    keys_ph = ",".join(f"'{k}'" for k in CONTEXTO_KEYS + ["ubicacion_video"])
    rows = conn.execute(f"""
        SELECT k.id, k.media_id, k.key, k.value, k.timestamp_absolute,
               m.latitude as mlat, m.longitude as mlon, m.filename_original, m.type
        FROM media_keypoints k
        JOIN media m ON m.id=k.media_id
        WHERE k.key IN ({keys_ph})
        ORDER BY k.timestamp_absolute
    """).fetchall()
    puntos = []
    for kid, mid, key, val, ts_abs, mlat, mlon, fname, mtype in rows:
        lat, lon, fuente = _resolver_posicion_kp(mlat, mlon, ts_abs, tracks)
        if lat is None:
            continue
        puntos.append({
            "id": kid, "media_id": mid, "key": key, "value": (val or "")[:200],
            "lat": lat, "lon": lon, "ts_abs": ts_abs or "", "filename": fname or "",
            "mtype": mtype or "", "fuente": fuente,
        })
    return puntos


def leer_waypoints_local(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute("SELECT id, name, category, type, latitude, longitude FROM waypoints").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r[0], "name": r[1] or "", "category": r[2] or "", "type": r[3] or "", "lat": r[4], "lon": r[5]} for r in rows]


def _resolver_posicion_kp(mlat: float | None, mlon: float | None, ts_abs: str | None, tracks: list[dict]) -> tuple[float | None, float | None, str | None]:
    if mlat is not None and mlon is not None:
        return mlat, mlon, "media"
    dt = _norm_dt(ts_abs)
    if dt is None or not tracks:
        return None, None, None
    for tr in tracks:
        pos = interpolar_posicion(tr["puntos_tiempo"], dt)
        if pos is not None:
            lat, lon, _ele = pos
            return lat, lon, "track"
    return None, None, None


# ── Lectura deploy (medios/keypoints) ───────────────────────────────────────

def leer_puntos_base_deploy(conn: sqlite3.Connection) -> list[dict]:
    # Deploy schema: medios(ruta_absoluta, no filepath_absoluto) + sin media_metadata
    try:
        rows = conn.execute("""
            SELECT id, archivo, tipo, subtipo, carpeta, latitud, longitud, fecha, hora, municipio, provincia,
                   ruta_absoluta, titulo, transcripcion,
                   (SELECT COUNT(*) FROM keypoints k WHERE k.media_id=medios.id AND k.kp_key='transcription') as n_seg
            FROM medios WHERE latitud IS NOT NULL AND longitud IS NOT NULL ORDER BY fecha, hora
        """).fetchall()
        has_ruta = True
    except sqlite3.OperationalError:
        rows = conn.execute("""
            SELECT id, archivo, tipo, subtipo, carpeta, latitud, longitud, fecha, hora, municipio, provincia,
                   (SELECT COUNT(*) FROM keypoints k WHERE k.media_id=medios.id AND k.kp_key='transcription') as n_seg
            FROM medios WHERE latitud IS NOT NULL AND longitud IS NOT NULL ORDER BY fecha, hora
        """).fetchall()
        has_ruta = False
    puntos = []
    for r in rows:
        if has_ruta:
            # 0:id 1:archivo 2:tipo 3:subtipo 4:carpeta 5:lat 6:lon 7:fecha 8:hora 9:mun 10:prov 11:ruta_abs 12:titulo 13:transc 14:n_seg
            ts = f"{r[7]}T{r[8]}" if r[7] and r[8] else (r[7] or "")
            filepath = r[11] or (f"deploy/media/{r[4]}/{r[1]}" if r[4] and r[1] else "")
            texto = (r[13] or r[12] or "")[:2000]
            n_seg = r[14] or 0
        else:
            # 0:id 1:archivo 2:tipo 3:subtipo 4:carpeta 5:lat 6:lon 7:fecha 8:hora 9:mun 10:prov 11:n_seg
            ts = f"{r[7]}T{r[8]}" if r[7] and r[8] else (r[7] or "")
            filepath = f"deploy/media/{r[4]}/{r[1]}" if r[4] and r[1] else ""
            texto = ""
            n_seg = r[11] or 0
        puntos.append({
            "id": r[0], "filename": r[1], "type": r[2] or "other", "subtype": r[3] or "",
            "carpeta": r[4] or "", "lat": r[5], "lon": r[6],
            "timestamp": ts, "municipio": r[9] or "", "provincia": r[10] or "",
            "altitude": None, "filepath": filepath, "n_seg": n_seg,
            "texto": texto,
        })
    return puntos


def leer_transcripciones_deploy(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("""
        SELECT media_id, value, offset_secs, timestamp_absolute, latitud, longitud
        FROM keypoints WHERE kp_key='transcription' ORDER BY media_id, offset_secs
    """).fetchall()
    out: dict[int, list] = {}
    for mid, val, off, ts_abs, lat, lon in rows:
        if lat is None or lon is None:
            continue
        out.setdefault(mid, []).append({"off": off or 0, "ts_abs": ts_abs or "", "lat": lat, "lon": lon, "text": (val or "")[:300], "fuente": "deploy"})
    return out


def leer_contexto_deploy(conn: sqlite3.Connection) -> list[dict]:
    keys_ph = ",".join(f"'{k}'" for k in CONTEXTO_KEYS + ["ubicacion_video"])
    rows = conn.execute(f"SELECT id, media_id, kp_key, value, latitud, longitud, timestamp_absolute, archivo, media_tipo FROM keypoints WHERE kp_key IN ({keys_ph}) ORDER BY timestamp_absolute").fetchall()
    puntos = []
    for kid, mid, key, val, lat, lon, ts_abs, fname, mtype in rows:
        if lat is None or lon is None:
            continue
        puntos.append({"id": kid, "media_id": mid, "key": key, "value": (val or "")[:200], "lat": lat, "lon": lon, "ts_abs": ts_abs or "", "filename": fname or "", "mtype": mtype or "", "fuente": "deploy"})
    return puntos


# ── Generación de mapa ───────────────────────────────────────────────────────

def generar_mapa_unificado(
    db_path: str,
    output: str = "mapas/mapa_unificado.html",
    modo: str = "offline",
    con_contexto: bool = True,
    con_waypoints: bool = False,
    con_segmentos: bool = True,
    cluster: bool = True,
    dry_run: bool = False,
    assets_cache: str = CACHE_DIR_ASSETS_DEFAULT,
    tiles_cache: str = "tiles_cache/esri",
) -> str | None:
    if not os.path.isfile(db_path):
        log.error("DB no encontrada: %s", db_path)
        return None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        is_deploy = _es_deploy_db(conn)
    except Exception:
        is_deploy = False

    # Cargar tracks solo en modo local (deploy ya tiene lat/lon materializadas)
    tracks = []
    if not is_deploy:
        try:
            tracks = cargar_tracks(conn)
            if tracks:
                log.info("Tracks GPX cargados: %d (%d puntos)", len(tracks), len(tracks[0]["puntos_tiempo"]))
        except Exception as e:
            log.warning("No se pudieron cargar tracks: %s", e)

    # Leer datos según origen
    if is_deploy:
        log.info("Origen detectado: deploy (medios/keypoints)")
        puntos_base = leer_puntos_base_deploy(conn)
        transcripciones = leer_transcripciones_deploy(conn) if con_segmentos else {}
        contexto = leer_contexto_deploy(conn) if con_contexto else []
        waypoints = []  # deploy no tiene waypoints
        if con_waypoints:
            log.warning("--con-waypoints ignorado en modo deploy (no hay waypoints)")
    else:
        log.info("Origen detectado: local (media/media_keypoints)")
        puntos_base = leer_puntos_base_local(conn)
        transcripciones = leer_transcripciones_local(conn, tracks) if con_segmentos else {}
        contexto = leer_contexto_local(conn, tracks) if con_contexto else []
        waypoints = leer_waypoints_local(conn) if con_waypoints else []

    conn.close()

    # Estadísticas
    tipos_base = {}
    for p in puntos_base:
        tipos_base[p["type"]] = tipos_base.get(p["type"], 0) + 1
    log.info("Puntos base con GPS: %d %s", len(puntos_base), dict(tipos_base))
    log.info("Contexto con posición: %d", len(contexto))
    if contexto:
        from collections import Counter
        log.info("  por key: %s", dict(Counter(c["key"] for c in contexto)))
    log.info("Waypoints: %d", len(waypoints))
    n_medios_con_seg = len(transcripciones)
    n_seg_total = sum(len(v) for v in transcripciones.values())
    log.info("Transcripciones: %d medios con segmentos, %d segmentos mapeables", n_medios_con_seg, n_seg_total)

    if dry_run:
        log.info("DRY-RUN: no se genera HTML")
        return None

    if not puntos_base and not contexto and not waypoints:
        log.warning("Sin puntos para mapear.")
        return None

    # Centro/bounds: prioriza puntos_base + contexto + waypoints
    all_lats = [p["lat"] for p in puntos_base] + [c["lat"] for c in contexto] + [w["lat"] for w in waypoints]
    all_lons = [p["lon"] for p in puntos_base] + [c["lon"] for c in contexto] + [w["lon"] for w in waypoints]
    # También track para encuadre si existe
    if tracks and not is_deploy:
        t_lats = [p[1] for p in tracks[0]["puntos_tiempo"]]
        t_lons = [p[2] for p in tracks[0]["puntos_tiempo"]]
        all_lats += t_lats
        all_lons += t_lons
    centro_lat = sum(all_lats) / len(all_lats)
    centro_lon = sum(all_lons) / len(all_lons)
    bounds = [[min(all_lats), min(all_lons)], [max(all_lats), max(all_lons)]]

    # Crear mapa base SIN tiles (se inyecta capa única offline/online)
    m = folium.Map(location=[centro_lat, centro_lon], zoom_start=10, tiles=None, control_scale=True)
    m.fit_bounds(bounds)

    # Payload JSON embebido
    datos_js = []
    for p in puntos_base:
        file_uri = _ruta_a_file_uri(p.get("filepath", ""))
        # Para file:// necesitamos escapar espacios y chars, pero el browser lo resuelve
        datos_js.append({
            "id": p["id"], "f": p["filename"], "t": p["type"], "lat": p["lat"], "lon": p["lon"],
            "ts": p["timestamp"], "mun": p["municipio"], "prov": p["provincia"], "nseg": p["n_seg"],
            "src": file_uri or "", "texto": (p.get("texto", "") or "")[:1500],
        })
    contexto_js = [{"id": c["id"], "mid": c["media_id"], "k": c["key"], "v": c["value"], "lat": c["lat"], "lon": c["lon"]} for c in contexto]
    waypoints_js = [{"id": w["id"], "n": w["name"], "c": w["category"], "lat": w["lat"], "lon": w["lon"]} for w in waypoints]
    # transcripciones ya es dict media_id -> list
    # Limitar texto a 120 chars para no inflar HTML demasiado
    trans_js = {str(k): [{"lat": s["lat"], "lon": s["lon"], "tx": s["text"][:120], "off": s["off"]} for s in v] for k, v in transcripciones.items()}

    # ── Inyectar capa de tiles ─────────────────────────────────────────────
    # Offline: tiles embebidos data-uri; Online: capa Esri directa sin data-uris
    from scripts.tiles_offline import incrustar_tiles_vista_inicial, TILE_URL_ESRI, zoom_fit_bounds
    if modo == "offline":
        # Para mapa nacional el bounds es enorme (BA→Tuc ~600km): no tiene sentido
        # embebeer zooms 11-13 (74k tiles). Calculamos el zoom que Folium usará y
        # embebemos solo ese ±1.
        try:
            z_est = zoom_fit_bounds(min(all_lats), min(all_lons), max(all_lats), max(all_lons))
            zooms_offline = sorted({z for z in (z_est-1, z_est, z_est+1) if 4 <= z <= 18})
        except Exception:
            zooms_offline = None
        n_tiles = incrustar_tiles_vista_inicial(m, all_lats, all_lons, zooms=zooms_offline, cache_dir=tiles_cache, atribucion=ATTR_ESRI)
        log.info("Tiles embebidos: %d (zooms %s)", n_tiles, zooms_offline)
    else:
        # Online: capa única Esri sin embebido (no data-uri)
        from folium.raster_layers import TileLayer
        TileLayer(tiles=TILE_URL_ESRI, attr=ATTR_ESRI, name="Esri Light Gray").add_to(m)

    # ── Track polyline (si hay) ────────────────────────────────────────────
    if tracks and not is_deploy:
        coords = [(p[1], p[2]) for p in tracks[0]["puntos_tiempo"]]
        folium.PolyLine(locations=coords, color="#3388ff", weight=3, opacity=0.6, tooltip=f"Track {len(coords)} pts").add_to(m)

    # ── CSS/JS de markercluster + UI ───────────────────────────────────────
    # Paleta para JS
    tipo_color_js = json.dumps(TIPO_COLOR)
    contexto_color_js = json.dumps(CONTEXTO_COLOR)

    # JS principal: crea clusters, marcadores, filtros, expansión
    js_code = f"""
<script>
var DATOS = {json.dumps(datos_js, ensure_ascii=False)};
var CONTEXTO = {json.dumps(contexto_js, ensure_ascii=False)};
var WAYPOINTS = {json.dumps(waypoints_js, ensure_ascii=False)};
var SEGMENTOS = {json.dumps(trans_js, ensure_ascii=False)};
var TIPO_COLOR = {tipo_color_js};
var CONTEXTO_COLOR = {contexto_color_js};
var CLUSTER_ENABLED = {str(cluster).lower()};
</script>
"""

    # MarkerCluster: siempre inyectado si clusters activos (offline se inlinea, online queda CDN)
    if cluster:
        js_code += """
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.css" />
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.4.1/dist/MarkerCluster.Default.css" />
<script src="https://unpkg.com/leaflet.markercluster@1.4.1/dist/leaflet.markercluster.js"></script>
"""

    # Panel de filtros + leyenda
    panel_html = """
<div id="panel-unificado" style="position:fixed;top:10px;right:10px;z-index:9999;background:white;border-radius:8px;padding:10px 14px;box-shadow:0 2px 12px rgba(0,0,0,0.25);font-family:'Segoe UI',Arial,sans-serif;font-size:12px;min-width:180px;max-width:260px;">
  <div style="font-weight:bold;margin-bottom:6px;border-bottom:1px solid #ddd;padding-bottom:4px;">Filtros</div>
  <label style="display:flex;align-items:center;gap:6px;margin:3px 0;"><input type="checkbox" class="filtro-tipo" value="image" checked> <span style="display:inline-block;width:12px;height:12px;background:#3388ff;border-radius:2px;"></span> Imágenes</label>
  <label style="display:flex;align-items:center;gap:6px;margin:3px 0;"><input type="checkbox" class="filtro-tipo" value="video" checked> <span style="display:inline-block;width:12px;height:12px;background:#cc3333;border-radius:2px;"></span> Videos</label>
  <label style="display:flex;align-items:center;gap:6px;margin:3px 0;"><input type="checkbox" class="filtro-tipo" value="audio" checked> <span style="display:inline-block;width:12px;height:12px;background:#1a9e3e;border-radius:2px;"></span> Audios</label>
  <label style="display:flex;align-items:center;gap:6px;margin:3px 0;"><input type="checkbox" class="filtro-tipo" value="text" checked> <span style="display:inline-block;width:12px;height:12px;background:#8a3acc;border-radius:2px;"></span> Textos</label>
  <label style="display:flex;align-items:center;gap:6px;margin:3px 0;"><input type="checkbox" id="filtro-contexto" checked> <span style="display:inline-block;width:12px;height:12px;background:#e67e22;border-radius:2px;"></span> Contexto</label>
  <label style="display:flex;align-items:center;gap:6px;margin:3px 0;"><input type="checkbox" id="filtro-waypoints"> <span style="display:inline-block;width:12px;height:12px;background:#ff8c00;border-radius:2px;"></span> Waypoints</label>
  <label style="display:flex;align-items:center;gap:6px;margin:6px 0 0 0;"><input type="checkbox" id="toggle-cluster" checked> Clusters</label>
  <div style="margin-top:8px;padding-top:6px;border-top:1px solid #eee;font-size:11px;color:#666;">
    <span id="contador-puntos"></span><br>
    <span id="estado-expansion" style="color:#3388ff;"></span>
  </div>
  <div id="leyenda-cluster" style="margin-top:8px;font-size:11px;color:#555;">
    Cluster monocolor = 1 tipo · multicolor = mixto
  </div>
</div>
"""

    m.get_root().html.add_child(Element(js_code))
    m.get_root().html.add_child(Element(panel_html))

    # JS de lógica (debe ir después de que Leaflet esté cargado; pollea el mapa)
    # Usa window[<map_name>] (mismo patrón que tiles_offline.js_capa_base_embebida)
    map_name = m.get_name()
    logic_js = f"""
<script>
(function() {{
  var MAP_NAME = {json.dumps(map_name)};
  var _intentos = 0;
  var _timer = setInterval(function() {{
    _intentos++;
    var _mapa = window[MAP_NAME];
    // markercluster puede no estar aún si offline (se inlinea después) — espera también L.markerClusterGroup
    if (_mapa && window.L) {{
      // offline: L.markerClusterGroup puede no existir aún si assets no inlineados; espera
      if (CLUSTER_ENABLED && !L.markerClusterGroup && _intentos < 100) return;
      clearInterval(_timer);
      try {{ initUnificado(_mapa); }} catch(e) {{ console.error("initUnificado", e); }}
    }} else if (_intentos > 300) {{
      clearInterval(_timer);
      console.warn("Mapa no encontrado para mapa_unificado");
    }}
  }}, 80);

function initUnificado(map) {{
  // ── Capas ──
  var capaBase = L.layerGroup().addTo(map);
  var capaContexto = L.layerGroup().addTo(map);
  var capaWaypoints = L.layerGroup();
  var capaSegmentos = L.layerGroup().addTo(map);
  var clusterGroup = null;
  var segmentosPolyline = null;
  var expandedMediaId = null;

  // Crear clusterGroup si está habilitado y disponible
  if (CLUSTER_ENABLED && L.markerClusterGroup) {{
    clusterGroup = L.markerClusterGroup({{
      maxClusterRadius: 50,
      spiderfyOnMaxZoom: true,
      chunkedLoading: true,
      iconCreateFunction: function(cluster) {{
        var markers = cluster.getAllChildMarkers();
        var counts = {{}};
        markers.forEach(function(m) {{
          var t = m.options._tipo || "other";
          counts[t] = (counts[t]||0)+1;
        }});
        var tipos = Object.keys(counts);
        var total = markers.length;
        var size = 40;
        if (total >= 100) size = 50;
        else if (total >= 20) size = 44;
        // monocolor vs multicolor
        if (tipos.length === 1) {{
          var c = TIPO_COLOR[tipos[0]] || "#3388ff";
          return L.divIcon({{
            html: '<div style="width:'+size+'px;height:'+size+'px;border-radius:50%;background:'+c+';border:3px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;font-size:13px;">'+total+'</div>',
            className: 'marker-cluster-unificado',
            iconSize: L.point(size, size)
          }});
        }} else {{
          // multicolor: conic-gradient
          var totalTipos = tipos.length;
          var gradientParts = [];
          var acc = 0;
          tipos.forEach(function(t) {{
            var cnt = counts[t];
            var perc = cnt / total * 100;
            var col = TIPO_COLOR[t] || "#888";
            gradientParts.push(col + " " + acc.toFixed(1) + "% " + (acc+perc).toFixed(1) + "%");
            acc += perc;
          }});
          var grad = "conic-gradient(" + gradientParts.join(", ") + ")";
          return L.divIcon({{
            html: '<div style="width:'+size+'px;height:'+size+'px;border-radius:50%;background:'+grad+';border:3px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;"><span style="background:white;border-radius:50%;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:11px;color:#333;">'+total+'</span></div>',
            className: 'marker-cluster-unificado',
            iconSize: L.point(size, size)
          }});
        }}
      }}
    }});
    map.addLayer(clusterGroup);
  }}

  function tipoIcon(tipo) {{
    var color = TIPO_COLOR[tipo] || "#888";
    var iconMap = {{image:"camera", video:"film", audio:"music", text:"file-text", other:"circle"}};
    var fa = iconMap[tipo] || "circle";
    // Usamos divIcon con color de fondo y letra
    return L.divIcon({{
      html: '<div style="width:28px;height:28px;border-radius:50%;background:'+color+';border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;color:white;font-size:13px;"><i class="fa fa-'+fa+'" style="font-size:12px;"></i></div>',
      className: 'marker-tipo-'+tipo,
      iconSize: L.point(28,28),
      iconAnchor: L.point(14,14),
      popupAnchor: L.point(0,-14)
    }});
  }}

  function contextoIcon(key) {{
    var col = CONTEXTO_COLOR[key] || "#e67e22";
    return L.divIcon({{
      html: '<div style="width:18px;height:18px;border-radius:50%;background:'+col+';border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.3);"></div>',
      className: 'marker-contexto',
      iconSize: L.point(18,18),
      iconAnchor: L.point(9,9)
    }});
  }}

  function waypointIcon() {{
    return L.divIcon({{
      html: '<div style="width:22px;height:22px;border-radius:50%;background:#ff8c00;border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;color:white;font-size:10px;"><i class="fa fa-map-pin"></i></div>',
      className: 'marker-waypoint',
      iconSize: L.point(22,22),
      iconAnchor: L.point(11,22)
    }});
  }}

  function escHtml(s) {{
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }}
  function previewHtml(d) {{
    var src = d.src || "";
    var t = d.t;
    if (!src) return "";
    if (t === "image") {{
      return '<div style="margin:8px 0;text-align:center;">'
        + '<img src="'+src+'" style="max-width:280px;max-height:220px;object-fit:contain;border-radius:4px;border:1px solid #ddd;display:block;margin:0 auto;" loading="lazy" onerror="this.style.display=&quot;none&quot;;this.nextElementSibling.style.display=&quot;block&quot;;">'
        + '<div style="display:none;font-size:11px;color:#999;padding:8px;border:1px dashed #ccc;border-radius:4px;">No se pudo cargar imagen<br><small style="word-break:break-all;">'+escHtml(src)+'</small></div>'
        + '<a href="'+src+'" target="_blank" style="font-size:11px;color:#3388ff;word-break:break-all;">Abrir original</a>'
        + '</div>';
    }}
    if (t === "video") {{
      return '<div style="margin:8px 0;">'
        + '<video controls preload="metadata" style="max-width:280px;max-height:220px;border-radius:4px;background:#000;display:block;width:100%;">'
        + '<source src="'+src+'">Tu navegador no soporta video.</video>'
        + '<a href="'+src+'" target="_blank" style="font-size:11px;color:#3388ff;word-break:break-all;">Abrir video</a>'
        + '</div>';
    }}
    if (t === "audio") {{
      return '<div style="margin:8px 0;">'
        + '<audio controls preload="metadata" src="'+src+'" style="width:260px;display:block;"></audio>'
        + '<a href="'+src+'" target="_blank" style="font-size:11px;color:#3388ff;word-break:break-all;">Abrir audio</a>'
        + '</div>';
    }}
    if (t === "text") {{
      var txt = d.texto ? escHtml(d.texto).replace(/\\n/g,'<br>') : "";
      if (!txt) return "";
      return '<div style="max-height:180px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.4;border:1px solid #ddd;padding:8px;border-radius:4px;background:#fafafa;margin:8px 0;">'+txt+'</div>';
    }}
    return "";
  }}

  // ── Crear marcadores base ──
  var marcadoresBase = [];
  DATOS.forEach(function(d) {{
    var hasSeg = SEGMENTOS[String(d.id)] && SEGMENTOS[String(d.id)].length > 0;
    var popup = '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;min-width:260px;max-width:320px;">'
      + '<div style="font-weight:bold;border-bottom:2px solid '+(TIPO_COLOR[d.t]||"#3388ff")+';padding-bottom:4px;margin-bottom:6px;word-break:break-all;">'+escHtml(d.f)+'</div>'
      + previewHtml(d)
      + '<div style="font-size:12px;line-height:1.4;">'
      + '<div><b>Tipo:</b> '+d.t+' '+(d.nseg? '('+d.nseg+' segs)':'')+'</div>'
      + '<div><b>Fecha:</b> '+(d.ts||"—")+'</div>'
      + '<div><b>Ubicación:</b> '+escHtml(d.prov||"—")+(d.mun?" , "+escHtml(d.mun):"")+'</div>'
      + '<div><b>Coords:</b> '+d.lat.toFixed(5)+', '+d.lon.toFixed(5)+'</div>'
      + '</div>'
      + (hasSeg ? '<div style="margin-top:8px;"><button onclick="window._expandir('+d.id+')" style="background:'+(TIPO_COLOR[d.t]||"#3388ff")+';color:white;border:none;border-radius:4px;padding:6px 10px;cursor:pointer;width:100%;">▶ Desplegar '+SEGMENTOS[String(d.id)].length+' segmentos</button></div>' : '')
      + '<div id="btn-replegar-'+d.id+'" style="display:none;margin-top:6px;"><button onclick="window._replegar()" style="background:#666;color:white;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;width:100%;">✕ Replegar</button></div>'
      + '</div>';
    var m = L.marker([d.lat, d.lon], {{icon: tipoIcon(d.t), _tipo: d.t, _id: d.id, _hasSeg: hasSeg}});
    m.bindPopup(popup, {{maxWidth:340}});
    m.on('click', function() {{}});
    marcadoresBase.push(m);
  }});

  // ── Contexto ──
  var marcadoresContexto = [];
  CONTEXTO.forEach(function(c) {{
    var popup = '<div style="font-size:12px;"><b>'+c.k+'</b><br>'+c.v+'<br><small>media #'+c.mid+'</small></div>';
    var mm = L.marker([c.lat, c.lon], {{icon: contextoIcon(c.k), _tipo:"contexto"}});
    mm.bindPopup(popup, {{maxWidth:250}});
    marcadoresContexto.push(mm);
  }});

  // ── Waypoints ──
  var marcadoresWaypoints = [];
  WAYPOINTS.forEach(function(w) {{
    var popup = '<div style="font-size:12px;"><b>'+(w.n||"Waypoint")+'</b><br>'+(w.c||"")+'</div>';
    var wm = L.marker([w.lat, w.lon], {{icon: waypointIcon(), _tipo:"waypoint"}});
    wm.bindPopup(popup);
    marcadoresWaypoints.push(wm);
  }});

  function rebuildLayers() {{
    // Filtros por tipo
    var activos = new Set();
    document.querySelectorAll('.filtro-tipo:checked').forEach(function(cb){{ activos.add(cb.value); }});
    var conCtx = document.getElementById('filtro-contexto').checked;
    var conWpt = document.getElementById('filtro-waypoints').checked;

    // Limpiar
    if (clusterGroup) clusterGroup.clearLayers();
    else capaBase.clearLayers();
    capaContexto.clearLayers();
    capaWaypoints.clearLayers();

    var visibles = 0;
    marcadoresBase.forEach(function(m) {{
      if (activos.has(m.options._tipo)) {{
        if (clusterGroup) clusterGroup.addLayer(m);
        else m.addTo(capaBase);
        visibles++;
      }}
    }});
    if (conCtx) {{
      marcadoresContexto.forEach(function(m){{ m.addTo(capaContexto); }});
      visibles += marcadoresContexto.length;
    }}
    if (conWpt) {{
      // waypoints no clusterizan (pocos)
      marcadoresWaypoints.forEach(function(m){{ m.addTo(capaWaypoints); if(conWpt) capaWaypoints.addTo(map); }});
      if (conWpt) visibles += marcadoresWaypoints.length;
      else map.removeLayer(capaWaypoints);
    }} else {{
      map.removeLayer(capaWaypoints);
    }}
    document.getElementById('contador-puntos').textContent = visibles + " puntos visibles";
  }}

  // Eventos filtros
  document.querySelectorAll('.filtro-tipo').forEach(function(cb){{ cb.addEventListener('change', rebuildLayers); }});
  document.getElementById('filtro-contexto').addEventListener('change', rebuildLayers);
  document.getElementById('filtro-waypoints').addEventListener('change', rebuildLayers);
  document.getElementById('toggle-cluster').addEventListener('change', function(e) {{
    if (e.target.checked) {{
      if (!map.hasLayer(clusterGroup) && clusterGroup) {{
        // mover marcadores base al cluster
        capaBase.clearLayers();
        rebuildLayers();
        // ocultar capaBase, mostrar cluster
        if (clusterGroup) map.addLayer(clusterGroup);
      }}
    }} else {{
      if (clusterGroup && map.hasLayer(clusterGroup)) {{
        clusterGroup.clearLayers();
        map.removeLayer(clusterGroup);
        // pasar a capaBase
        marcadoresBase.forEach(function(m){{
          var cb = document.querySelector('.filtro-tipo[value="'+m.options._tipo+'"]');
          if (cb && cb.checked) m.addTo(capaBase);
        }});
      }}
    }}
  }});

  // Inicial
  rebuildLayers();
  // Waypoints capa no visible por defecto
  map.removeLayer(capaWaypoints);

  // ── Expansión / repliegue de segmentos ──
  function expandir(mediaId) {{
    replegar(); // solo 1 a la vez
    var segs = SEGMENTOS[String(mediaId)];
    if (!segs || !segs.length) return;
    expandedMediaId = mediaId;
    var latlngs = [];
    segs.forEach(function(s, idx) {{
      var icon = L.divIcon({{
        html: '<div style="width:16px;height:16px;border-radius:50%;background:#1a9e3e;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;color:white;font-size:8px;">'+(idx+1)+'</div>',
        className: 'marker-seg',
        iconSize: L.point(16,16),
        iconAnchor: L.point(8,8)
      }});
      var sm = L.marker([s.lat, s.lon], {{icon: icon}});
      sm.bindPopup('<div style="font-size:12px;"><b>Segmento '+(idx+1)+'</b><br>'+s.tx+'<br><small>+'+s.off.toFixed(1)+'s</small></div>', {{maxWidth:250}});
      sm.addTo(capaSegmentos);
      latlngs.push([s.lat, s.lon]);
    }});
    if (latlngs.length > 1) {{
      segmentosPolyline = L.polyline(latlngs, {{color:"#1a9e3e", weight:2, opacity:0.7, dashArray:"6,6"}}).addTo(capaSegmentos);
    }}
    // mostrar botón replegar del popup (si está abierto)
    var btn = document.getElementById('btn-replegar-'+mediaId);
    if (btn) btn.style.display = 'block';
    document.getElementById('estado-expansion').textContent = "Desplegado: "+segs.length+" segmentos (media #"+mediaId+") — Esc o ✕ para replegar";
    // Hint: centrar si los segmentos están fuera de vista
    try {{ map.fitBounds(L.latLngBounds(latlngs), {{padding:[30,30], maxZoom: 15}}); }} catch(e){{}}
  }}

  function replegar() {{
    capaSegmentos.clearLayers();
    segmentosPolyline = null;
    if (expandedMediaId !== null) {{
      var prevBtn = document.getElementById('btn-replegar-'+expandedMediaId);
      if (prevBtn) prevBtn.style.display = 'none';
    }}
    expandedMediaId = null;
    document.getElementById('estado-expansion').textContent = "";
  }}

  window._expandir = expandir;
  window._replegar = replegar;

  // Click en mapa vacío → replegar
  map.on('click', function(e) {{
    // si el click no fue en un marcador (target == map), replegar
    if (e.originalEvent && e.originalEvent.target && e.originalEvent.target.closest && e.originalEvent.target.closest('.leaflet-marker-icon')) return;
    // no replegar si clickeó panel
    if (e.originalEvent.target.closest && e.originalEvent.target.closest('#panel-unificado')) return;
  }});
  // Tecla Esc → replegar
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') replegar();
  }});

  // Exponer para debug
  window._mapaUnificado = map;
}}
}})();
</script>
"""
    m.get_root().html.add_child(Element(logic_js))

    # Guardar
    output_abs = os.path.abspath(output)
    os.makedirs(os.path.dirname(output_abs), exist_ok=True)
    if modo == "offline":
        ok = guardar_autocontenido(m, output_abs, assets_cache)
        log.info("Mapa guardado: %s (%s)", output_abs, "autocontenido" if ok else "CDN fallback")
    else:
        m.save(output_abs)
        log.info("Mapa guardado (online CDN): %s", output_abs)

    # Resumen
    log.info("  Base: %d  | Contexto: %d  | Waypoints: %d  | Segmentos: %d", len(puntos_base), len(contexto), len(waypoints), n_seg_total)
    return output_abs


def main(argv=None):
    p = argparse.ArgumentParser(description="Mapa unificado offline/online con clusters y expansión de transcripción", formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="Ruta a la DB (default: db/flujos.db / deploy/db/visualizacion.db según --modo)")
    p.add_argument("--modo", choices=["offline", "online"], default="offline", help="offline=local+autocontenido, online=deploy+CDN (default: offline)")
    p.add_argument("--output", "-o", default="mapas/mapa_unificado.html", help="HTML de salida")
    p.add_argument("--no-contexto", action="store_true", help="No incluir puntos de contexto")
    p.add_argument("--con-waypoints", action="store_true", help="Incluir waypoints (capa opcional)")
    p.add_argument("--sin-cluster", action="store_true", help="Desactivar clusters")
    p.add_argument("--sin-segmentos", action="store_true", help="No incluir segmentos de transcripción")
    p.add_argument("--dry-run", action="store_true", help="Solo mostrar conteos, no generar HTML")
    p.add_argument("--assets-cache", default=CACHE_DIR_ASSETS_DEFAULT)
    p.add_argument("--tiles-cache", default="tiles_cache/esri")
    args = p.parse_args(argv)

    if args.db:
        db_path = os.path.abspath(args.db)
    else:
        if args.modo == "online":
            db_path = os.path.join(_PROJECT_ROOT, "deploy", "db", "visualizacion.db")
        else:
            db_path = os.path.join(_PROJECT_ROOT, "db", "flujos.db")

    if not os.path.isfile(db_path):
        log.error("DB no encontrada: %s", db_path)
        sys.exit(1)
    log.info("DB: %s  modo=%s", db_path, args.modo)

    generar_mapa_unificado(
        db_path=db_path,
        output=args.output,
        modo=args.modo,
        con_contexto=not args.no_contexto,
        con_waypoints=args.con_waypoints,
        con_segmentos=not args.sin_segmentos,
        cluster=not args.sin_cluster,
        dry_run=args.dry_run,
        assets_cache=args.assets_cache,
        tiles_cache=args.tiles_cache,
    )

if __name__ == "__main__":
    main()
