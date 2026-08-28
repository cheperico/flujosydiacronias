#!/usr/bin/env python3
"""generar_galeria_keypoints.py — Galería HTML de keypoints al azar con fotos cercanas y mapa."""

import argparse
import heapq
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Permitir importar db.util y scripts hermanos desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.util import abrir, resolver_db  # noqa: E402
from scripts.track_gpx import cargar_tracks, distancia_haversine, interpolar_posicion  # noqa: E402

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de tiempo
# ---------------------------------------------------------------------------

def _normalizar_dt(ts: str | None) -> datetime | None:
    """Convierte un timestamp ISO a datetime aware UTC (maneja Z y naive)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _path_a_uri(ruta: str) -> str:
    """Convierte una ruta absoluta (Windows o POSIX) a URI file:///."""
    try:
        return Path(ruta).as_uri()
    except (ValueError, OSError):
        # Fallback manual para rutas con caracteres extraños
        p = ruta.replace("\\", "/")
        if not p.startswith("/"):
            p = "/" + p
        # Evitar doble prefijo si ya empieza con /
        return "file://" + p


# ---------------------------------------------------------------------------
# Carga de pool de imágenes
# ---------------------------------------------------------------------------

def _cargar_pool_imagenes(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """
    Carga pools de imágenes type='image' para búsqueda de vecinas.

    Returns:
        (pool_geo, pool_tiempo) donde pool_geo tiene lat/lon y pool_tiempo
        tiene timestamp_utc parseado para fallback temporal.
    """
    conn.row_factory = sqlite3.Row

    # Pool geo: solo imágenes con coordenadas
    filas_geo = conn.execute(
        """
        SELECT id, filepath_absoluto, latitude, longitude, timestamp_utc
        FROM media
        WHERE type = 'image' AND filepath_absoluto IS NOT NULL
          AND latitude IS NOT NULL AND longitude IS NOT NULL
        """
    ).fetchall()

    pool_geo: list[dict] = []
    for r in filas_geo:
        pool_geo.append({
            "id": r["id"],
            "path": r["filepath_absoluto"],
            "filename": Path(r["filepath_absoluto"]).name,
            "lat": r["latitude"],
            "lon": r["longitude"],
            "ts": r["timestamp_utc"],
            "dt": _normalizar_dt(r["timestamp_utc"]),
        })

    # Pool tiempo: todas las imágenes con timestamp_utc (para fallback)
    filas_tiempo = conn.execute(
        """
        SELECT id, filepath_absoluto, latitude, longitude, timestamp_utc
        FROM media
        WHERE type = 'image' AND filepath_absoluto IS NOT NULL
          AND timestamp_utc IS NOT NULL
        """
    ).fetchall()

    pool_tiempo: list[dict] = []
    for r in filas_tiempo:
        pool_tiempo.append({
            "id": r["id"],
            "path": r["filepath_absoluto"],
            "filename": Path(r["filepath_absoluto"]).name,
            "lat": r["latitude"],
            "lon": r["longitude"],
            "ts": r["timestamp_utc"],
            "dt": _normalizar_dt(r["timestamp_utc"]),
        })
    # Filtrar solo las que tienen dt parseable
    pool_tiempo = [p for p in pool_tiempo if p["dt"] is not None]

    log.info("Pool imágenes: %d con geo, %d con timestamp", len(pool_geo), len(pool_tiempo))
    return pool_geo, pool_tiempo


# ---------------------------------------------------------------------------
# Resolución de posición del keypoint
# ---------------------------------------------------------------------------

def _resolver_posicion_keypoint(
    kp_media_lat: float | None,
    kp_media_lon: float | None,
    kp_timestamp_abs: str | None,
    tracks: list[dict],
) -> tuple[float | None, float | None, str | None]:
    """
    Resuelve la posición del keypoint por prioridad.

    1. Si media.latitude/longitude NOT NULL -> usa eso (source='media').
    2. Si no, interpola contra tracks GPX en timestamp_absolute.
    3. Si no hay posición -> (None, None, None).
    """
    if kp_media_lat is not None and kp_media_lon is not None:
        return kp_media_lat, kp_media_lon, "media"

    dt = _normalizar_dt(kp_timestamp_abs)
    if dt is None or not tracks:
        return None, None, None

    # Probar cada track que contenga el instante (interpolar_posicion retorna None si fuera de rango)
    for track in tracks:
        pos = interpolar_posicion(track["puntos_tiempo"], dt)
        if pos is not None:
            lat, lon, _ele = pos
            return lat, lon, "track"

    # Fallback: elegir el track más cercano por gap temporal (no interpolable, pero deja rastro para diagnóstico)
    # Como interpolar_posicion ya dio None para todos, no hay posición interpolable.
    return None, None, None


# ---------------------------------------------------------------------------
# Fotos más cercanas
# ---------------------------------------------------------------------------

def _fotos_cercanas(
    kp_lat: float | None,
    kp_lon: float | None,
    kp_dt: datetime | None,
    pool_geo: list[dict],
    pool_tiempo: list[dict],
) -> list[dict]:
    """
    Devuelve 10 fotos más cercanas al keypoint.

    - Si el keypoint tiene (lat,lon) -> geográficas por Haversine, relleno temporal si faltan.
    - Si no -> solo temporales por |Δ timestamp|.
    """
    if kp_lat is not None and kp_lon is not None:
        # Distancia geográfica
        candidatas = heapq.nsmallest(
            10,
            pool_geo,
            key=lambda f: distancia_haversine(kp_lat, kp_lon, f["lat"], f["lon"]),
        )
        resultado: list[dict] = []
        vistos: set[int] = set()
        for f in candidatas:
            dist = distancia_haversine(kp_lat, kp_lon, f["lat"], f["lon"])
            resultado.append({
                "id": f["id"],
                "path": f["path"],
                "filename": f["filename"],
                "lat": f["lat"],
                "lon": f["lon"],
                "ts": f["ts"],
                "dist_m": round(dist, 1),
            })
            vistos.add(f["id"])

        # Relleno temporal si faltan para 10 y el keypoint tiene dt
        if len(resultado) < 10 and kp_dt is not None:
            faltan = 10 - len(resultado)
            # Pool temporal excluyendo las ya elegidas
            pool_restante = [p for p in pool_tiempo if p["id"] not in vistos]
            extras = heapq.nsmallest(
                faltan,
                pool_restante,
                key=lambda f: abs((f["dt"] - kp_dt).total_seconds()),
            )
            for f in extras:
                delta = abs((f["dt"] - kp_dt).total_seconds())
                resultado.append({
                    "id": f["id"],
                    "path": f["path"],
                    "filename": f["filename"],
                    "lat": f["lat"],
                    "lon": f["lon"],
                    "ts": f["ts"],
                    "dist_m": None,
                    "delta_secs": round(delta, 1),
                })
        return resultado[:10]

    # Sin posición -> solo temporal
    if kp_dt is not None and pool_tiempo:
        cercanas = heapq.nsmallest(
            10,
            pool_tiempo,
            key=lambda f: abs((f["dt"] - kp_dt).total_seconds()),
        )
        resultado = []
        for f in cercanas:
            delta = abs((f["dt"] - kp_dt).total_seconds())
            resultado.append({
                "id": f["id"],
                "path": f["path"],
                "filename": f["filename"],
                "lat": f["lat"],
                "lon": f["lon"],
                "ts": f["ts"],
                "dist_m": None,
                "delta_secs": round(delta, 1),
            })
        return resultado

    return []


# ---------------------------------------------------------------------------
# Generación de HTML
# ---------------------------------------------------------------------------

def _generar_html(
    datos: list[dict],
    tipo: str,
    n_solicitado: int,
    titulo_tipo: str,
) -> str:
    """Genera el HTML autocontenido (online CDN) de la galería."""
    ahora = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %z")
    titulo = f"Keypoints de {titulo_tipo} — Flujos y Diacronías"
    subtitulo = f"{len(datos)} keypoints al azar (solicitados {n_solicitado}) — generado {ahora}"

    tiene_datos = len(datos) > 0
    datos_json = json.dumps(datos, ensure_ascii=False, indent=2)

    # Mensaje vacío
    aviso_vacio = ""
    if not tiene_datos:
        aviso_vacio = (
            "<div style='padding:24px;background:#fff3cd;border:1px solid #ffc107;"
            "border-radius:8px;margin:16px 0;'>"
            "No se encontraron keypoints del tipo solicitado en la base de datos."
            "</div>"
        )

    nota_portabilidad = f"<!-- Snapshot de rutas absolutas al {ahora} desde DB {tipo}; tras relocate/mover_medios o cambio de computadora regenerar este HTML. -->"
    html = f"""<!DOCTYPE html>
{nota_portabilidad}
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #f5f5f0; color: #222; }}
  header {{ padding: 16px 20px; background: #111; color: #fff; }}
  header h1 {{ margin: 0; font-size: 20px; }}
  header p {{ margin: 4px 0 0; opacity: 0.85; font-size: 13px; }}
  .contenedor {{ display: flex; height: calc(100vh - 72px); }}
  #lista {{ width: 380px; overflow-y: auto; background: #fff; border-right: 1px solid #ddd; padding: 8px; }}
  #detalle {{ flex: 1; overflow-y: auto; padding: 16px; }}
  .card {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; cursor: pointer; background: #fafafa; }}
  .card:hover {{ border-color: #888; background: #fff; }}
  .card.activa {{ border-color: #111; background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.12); }}
  .badge {{ display: inline-block; font-size: 11px; padding: 2px 6px; border-radius: 999px; background: #111; color: #fff; margin-right: 6px; }}
  .badge.media {{ background: #2a7ae2; }}
  .badge.track {{ background: #1a9e3e; }}
  .badge.sin {{ background: #999; }}
  .card .valor {{ font-size: 13px; margin: 6px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .card .meta {{ font-size: 11px; color: #666; }}
  #mapa {{ height: 320px; border-radius: 8px; border: 1px solid #ddd; background: #e8e8e8; position: relative; }}
  #mapa .sin-posicion {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,0.85); z-index: 400; font-size: 14px; color: #555; border-radius: 8px; }}
  #player {{ width: 100%; border-radius: 8px; background: #000; margin-bottom: 12px; }}
  #carrusel {{ position: relative; height: 360px; border-radius: 8px; border: 1px solid #ddd; background: #000; overflow: hidden; }}
  #carrusel img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; background: #000; opacity: 0; transition: opacity 1s ease; }}
  #carrusel img.activa {{ opacity: 1; }}
  #carrusel .vacio {{ font-size: 13px; color: #777; padding: 12px; background: #fff; }}
  #carrusel .contador {{ position: absolute; bottom: 8px; right: 8px; background: rgba(0,0,0,0.6); color: #fff; font-size: 11px; padding: 3px 7px; border-radius: 999px; z-index: 2; }}
  .detalle-meta {{ font-size: 13px; color: #444; margin: 8px 0; }}
  .detalle-meta code {{ background: #eee; padding: 1px 4px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <h1>{titulo}</h1>
  <p>{subtitulo}</p>
</header>
<div class="contenedor">
  <div id="lista"></div>
  <div id="detalle">
    {aviso_vacio}
    <div id="detalle-contenido" style="display: {'block' if tiene_datos else 'none'}">
      <div id="player-wrap"></div>
      <div id="mapa"></div>
      <div class="detalle-meta" id="detalle-meta"></div>
      <h3 style="margin:12px 0 4px;">10 fotos más cercanas</h3>
      <div id="carrusel"></div>
      <div class="detalle-meta" id="detalle-texto" style="white-space: pre-wrap; background:#fff; padding:12px; border-radius:8px; border:1px solid #e0e0e0;"></div>
    </div>
  </div>
</div>
<script>const DATOS = {datos_json};</script>
<script>
let mapa = null;
let marcador = null;
let carruselInterval = null;

function initMapa() {{
  mapa = L.map('mapa').setView([-26.8, -65.2], 6);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
    attribution: 'Esri World Light Gray',
    maxZoom: 16
  }}).addTo(mapa);
}}

function truncar(texto, n) {{
  if (!texto) return '';
  if (texto.length <= n) return texto;
  return texto.slice(0, n) + '…';
}}

function renderLista() {{
  const lista = document.getElementById('lista');
  if (!DATOS.length) {{
    lista.innerHTML = '<div style="padding:12px;color:#777;">Sin keypoints para mostrar.</div>';
    return;
  }}
  lista.innerHTML = '';
  DATOS.forEach((d, idx) => {{
    const card = document.createElement('div');
    card.className = 'card' + (idx === 0 ? ' activa' : '');
    card.dataset.idx = idx;
    const badgeSrc = d.source ? `<span class="badge ${{d.source}}">${{d.source}}</span>` : '<span class="badge sin">sin posición</span>';
    const hora = d.timestamp_absolute ? d.timestamp_absolute.slice(11, 19) : '';
    const fecha = d.timestamp_absolute ? d.timestamp_absolute.slice(0, 10) : '';
    card.innerHTML = `
      <div><span class="badge">${{d.key}}</span>${{badgeSrc}}</div>
      <div class="valor" title="${{escHtml(d.value || '')}}">${{escHtml(truncar(d.value || '', 90))}}</div>
      <div class="meta">${{fecha}} ${{hora}} · offset ${{d.offset != null ? d.offset.toFixed(1) + 's' : '—'}} · media #${{d.media_id}} [${{d.type || '?'}}]</div>
    `;
    card.addEventListener('click', () => mostrarDetalle(idx));
    lista.appendChild(card);
  }});
}}

function escHtml(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function mostrarDetalle(idx) {{
  document.querySelectorAll('.card').forEach(c => c.classList.remove('activa'));
  const card = document.querySelector(`.card[data-idx="${{idx}}"]`);
  if (card) card.classList.add('activa');

  const d = DATOS[idx];
  // Player
  const wrap = document.getElementById('player-wrap');
  wrap.innerHTML = '';
  if (d.file_uri) {{
    let el = null;
    if (d.type === 'video') {{
      el = document.createElement('video');
      el.controls = true;
      el.id = 'player';
      el.src = d.file_uri;
      if (d.offset != null) {{
        el.addEventListener('loadedmetadata', () => {{
          try {{ el.currentTime = d.offset; }} catch(e) {{}}
        }}, {{ once: true }});
      }}
    }} else if (d.type === 'audio') {{
      el = document.createElement('audio');
      el.controls = true;
      el.id = 'player';
      el.style.background = '#fff';
      el.style.border = '1px solid #ddd';
      el.src = d.file_uri;
      if (d.offset != null) {{
        el.addEventListener('loadedmetadata', () => {{
          try {{ el.currentTime = d.offset; }} catch(e) {{}}
        }}, {{ once: true }});
      }}
    }}
    if (el) {{
      wrap.appendChild(el);
      // Aviso si el archivo no existe (error de carga)
      el.addEventListener('error', () => {{
        const aviso = document.createElement('div');
        aviso.style.cssText = 'padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:13px;margin-top:6px;';
        aviso.textContent = 'No se pudo cargar el archivo (¿ruta inexistente?): ' + d.filepath;
        wrap.appendChild(aviso);
      }});
    }} else {{
      wrap.innerHTML = '<div style="padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:13px;">Tipo no reproducible directamente: ' + escHtml(d.type || '?') + ' — ' + escHtml(d.filepath || '') + '</div>';
    }}
  }} else {{
    wrap.innerHTML = '<div style="padding:8px;color:#777;">Sin archivo asociado.</div>';
  }}

  // Meta
  const meta = document.getElementById('detalle-meta');
  const posTxt = (d.lat != null && d.lon != null) ? `${{d.lat.toFixed(6)}}, ${{d.lon.toFixed(6)}} (${{d.source}})` : 'sin posición GPS';
  meta.innerHTML = `<strong>key:</strong> <code>${{escHtml(d.key)}}</code> · <strong>pos:</strong> ${{escHtml(posTxt)}} · <strong>media:</strong> #${{d.media_id}} · <strong>offset:</strong> ${{d.offset != null ? d.offset.toFixed(1)+'s' : '—'}} · <strong>ts:</strong> ${{escHtml(d.timestamp_absolute || '')}}<br><strong>archivo:</strong> <code>${{escHtml(d.filepath || '')}}</code>`;

  // Texto completo
  document.getElementById('detalle-texto').textContent = d.value || '';

  // Mapa
  if (mapa) {{
    // Limpiar aviso previo
    const avisoPrevio = document.querySelector('#mapa .sin-posicion');
    if (avisoPrevio) avisoPrevio.remove();
    if (d.lat != null && d.lon != null) {{
      mapa.setView([d.lat, d.lon], 12);
      if (marcador) mapa.removeLayer(marcador);
      marcador = L.marker([d.lat, d.lon]).addTo(mapa);
      setTimeout(() => mapa.invalidateSize(), 100);
    }} else {{
      mapa.setView([-26.8, -65.2], 6);
      if (marcador) {{ mapa.removeLayer(marcador); marcador = null; }}
      const aviso = document.createElement('div');
      aviso.className = 'sin-posicion';
      aviso.textContent = 'Sin posición GPS para este keypoint';
      document.getElementById('mapa').appendChild(aviso);
      setTimeout(() => mapa.invalidateSize(), 100);
    }}
  }}

  // Carrusel — slideshow con fundido, 3s por foto, loop infinito
  const carrusel = document.getElementById('carrusel');
  if (carruselInterval) {{ clearInterval(carruselInterval); carruselInterval = null; }}
  carrusel.innerHTML = '';
  if (!d.fotos || !d.fotos.length) {{
    carrusel.innerHTML = '<div class="vacio">Sin imágenes en el pool para mostrar.</div>';
    carrusel.style.height = 'auto';
  }} else {{
    carrusel.style.height = '360px';
    let idxFoto = 0;
    const imgs = d.fotos.map((f, i) => {{
      const img = document.createElement('img');
      img.src = f.file_uri || '';
      img.title = f.filename + (f.dist_m != null ? ` — ${{f.dist_m}} m` : (f.delta_secs != null ? ` — Δ ${{f.delta_secs}}s` : '')) + (f.ts ? ` — ${{f.ts}}` : '');
      img.alt = f.filename;
      img.loading = 'lazy';
      if (i === 0) img.classList.add('activa');
      img.addEventListener('error', () => {{ img.style.opacity = '0.2'; }});
      carrusel.appendChild(img);
      return img;
    }});
    const contador = document.createElement('div');
    contador.className = 'contador';
    carrusel.appendChild(contador);
    function actualizarContador() {{
      contador.textContent = `${{idxFoto + 1}} / ${{imgs.length}} — ${{imgs[idxFoto].title}}`;
    }}
    actualizarContador();
    if (imgs.length > 1) {{
      carruselInterval = setInterval(() => {{
        imgs[idxFoto].classList.remove('activa');
        idxFoto = (idxFoto + 1) % imgs.length;
        imgs[idxFoto].classList.add('activa');
        actualizarContador();
      }}, 3000);
    }}
  }}
}}

document.addEventListener('DOMContentLoaded', () => {{
  initMapa();
  renderLista();
  if (DATOS.length) mostrarDetalle(0);
}});
</script>
</body>
</html>
"""
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def crear_parser() -> argparse.ArgumentParser:
    """Crea el parser de argumentos."""
    parser = argparse.ArgumentParser(
        description="Genera una galería HTML con keypoints al azar, fotos cercanas y mapa.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/generar_galeria_keypoints.py --tipo transcripcion --n 50
  python scripts/generar_galeria_keypoints.py --tipo contexto --n 50
  python scripts/generar_galeria_keypoints.py --tipo transcripcion --n 3 --output C:\\Temp\\kp_test.html --verbose
        """,
    )
    parser.add_argument(
        "--tipo",
        choices=["transcripcion", "contexto"],
        default="transcripcion",
        help="Tipo de keypoints a mostrar (default: transcripcion)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=50,
        help="Cantidad de keypoints al azar (default: 50)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a la base de datos SQLite (default: db/flujos.db)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta del HTML de salida (default: pruebas/keypoints_<tipo>.html)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Logging detallado",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point del script (ejecutable standalone o desde flujos.py)."""
    parser = crear_parser()
    args = parser.parse_args(argv)

    # Logging
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    db_path = resolver_db(args.db)
    if not Path(db_path).is_file():
        log.error("Base de datos no encontrada: %s", db_path)
        return 1

    # Output por defecto según tipo
    if args.output:
        output_path = Path(args.output)
    else:
        pruebas_dir = Path(__file__).resolve().parent.parent / "pruebas"
        nombre = f"keypoints_{args.tipo}.html"
        output_path = pruebas_dir / nombre

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.n <= 0:
        log.error("--n debe ser mayor a 0 (recibido %s)", args.n)
        return 1

    # Filtro según tipo
    if args.tipo == "transcripcion":
        filtro = "k.key = 'transcription'"
        titulo_tipo = "transcripción"
    else:
        filtro = "k.key LIKE 'contexto_%'"
        titulo_tipo = "contexto"

    log.info("=== GALERÍA DE KEYPOINTS (%s) ===", args.tipo)
    log.info("DB: %s", db_path)
    log.info("Solicitados: %d keypoints al azar", args.n)

    conn = abrir(db_path)
    try:
        conn.row_factory = sqlite3.Row

        # 1) Keypoints al azar
        query = f"""
            SELECT k.id, k.media_id, k.timestamp_offset_secs, k.timestamp_absolute,
                   k.key, k.value,
                   m.filepath_absoluto, m.type, m.subtype, m.latitude, m.longitude, m.timestamp_utc
            FROM media_keypoints k
            JOIN media m ON m.id = k.media_id
            WHERE {filtro}
            ORDER BY RANDOM()
            LIMIT ?
        """
        filas = conn.execute(query, (args.n,)).fetchall()
        log.info("Keypoints obtenidos: %d", len(filas))
        if not filas:
            log.warning("No hay keypoints del tipo '%s' en la base de datos.", args.tipo)

        # 2) Pool de imágenes
        pool_geo, pool_tiempo = _cargar_pool_imagenes(conn)

        # 3) Tracks GPX (para interpolar posición si media no tiene GPS)
        tracks: list[dict] = []
        try:
            tracks = cargar_tracks(conn)
            log.info("Tracks GPX cargados: %d", len(tracks))
        except Exception as e:
            log.warning("No se pudieron cargar los tracks GPX: %s", e)
            tracks = []

        # 4) Construir datos para el HTML
        datos: list[dict] = []
        sin_posicion = 0
        for fila in filas:
            kp_id = fila["id"]
            media_id = fila["media_id"]
            offset = fila["timestamp_offset_secs"]
            ts_abs = fila["timestamp_absolute"]
            key = fila["key"]
            value = fila["value"]
            filepath = fila["filepath_absoluto"]
            mtype = fila["type"]
            msubtype = fila["subtype"]
            media_lat = fila["latitude"]
            media_lon = fila["longitude"]

            kp_lat, kp_lon, source = _resolver_posicion_keypoint(
                media_lat, media_lon, ts_abs, tracks
            )
            if kp_lat is None:
                sin_posicion += 1

            kp_dt = _normalizar_dt(ts_abs)

            # Fotos cercanas
            fotos_raw = _fotos_cercanas(kp_lat, kp_lon, kp_dt, pool_geo, pool_tiempo)
            # Enriquecer con file_uri
            fotos = []
            for f in fotos_raw:
                fotos.append({
                    **f,
                    "file_uri": _path_a_uri(f["path"]) if f.get("path") else None,
                })

            file_uri = _path_a_uri(filepath) if filepath else None

            datos.append({
                "id": kp_id,
                "media_id": media_id,
                "key": key,
                "value": value,
                "offset": offset,
                "timestamp_absolute": ts_abs,
                "filepath": filepath,
                "file_uri": file_uri,
                "type": mtype,
                "subtype": msubtype,
                "lat": kp_lat,
                "lon": kp_lon,
                "source": source,
                "fotos": fotos,
            })

        if sin_posicion:
            log.warning("%d keypoints sin posición GPS (sin mapa).", sin_posicion)

        # 5) Generar HTML
        html = _generar_html(datos, args.tipo, args.n, titulo_tipo)
        output_path.write_text(html, encoding="utf-8")
        log.info("HTML generado: %s (%d bytes)", output_path, len(html.encode("utf-8")))
        log.info("Abrir en el navegador: file:///%s", output_path.resolve().as_posix())
        log.info("Nota: el HTML embebe rutas absolutas (file://) vigentes al generar.")
        log.info("Tras relocate/consolidar o cambio de computadora, regenerar las galerías (TUI 6→4).")

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
