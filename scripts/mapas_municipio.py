#!/usr/bin/env python3
"""
mapas_municipio.py — Genera un mapa HTML por cada municipio recorrido, con variantes.

Para cada municipio con puntos GPS en la BD, genera uno o más mapas HTML
interactivos (Folium) según las variantes pedidas. La línea de ruta de las
variantes ruta/contexto/gradiente usa el track GPX registrado en `tracks`:
cada municipio toma el tramo del track cuyo tiempo cae dentro del rango
[min, max] de timestamps de sus medios (los medios quedan como marcadores).
El nombre del archivo sigue la lógica:

    mapa_municipio_<slug>_<variante>.html

donde <slug> es el municipio normalizado a ASCII: sin acentos ni símbolos
(tildes, diéresis y ñ→n), con los espacios reemplazados por guion bajo y las
mayúsculas originales conservadas (ej: 'Río Hondo' → 'Rio_Hondo',
'Jesús María' → 'Jesus_Maria', 'Melincué' → 'Melincue'). Los nombres sin
acentos no cambian ('Bell Ville' → 'Bell_Ville'). Esta convención ASCII
evita problemas de visualización en TouchDesigner. <variante> es uno de:

    ruta      Puntos del municipio + tramo del track que los conecta
    puntos    Solo los marcadores del municipio, sin línea
    contexto  Puntos del municipio destacados sobre el track completo (en gris)
    gradiente Segmentos del tramo coloreados por pendiente (road-colors) + leyenda

Uso:
    python scripts/mapas_municipio.py                          # Todos los municipios, todas las variantes
    python scripts/mapas_municipio.py --variantes ruta,puntos  # Solo algunas variantes
    python scripts/mapas_municipio.py --municipio "Bell Ville" # Filtrar a un municipio
    python scripts/mapas_municipio.py --output mapas --mode skip   # Solo generar los que faltan
    python scripts/mapas_municipio.py --output mapas           # Regenerar todos (default)
    python scripts/mapas_municipio.py --dry-run                # Listar sin generar
    python scripts/mapas_municipio.py --db ruta.db             # BD alternativa
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Importar folium con mensaje de error claro si no está instalado
# ---------------------------------------------------------------------------

# Agregar la raíz del proyecto al sys.path para poder importar scripts.mapa_ruta
# tanto al ejecutar este script standalone como desde flujos.py.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import folium
except ImportError:
    print("=" * 60)
    print("  ERROR: Folium no está instalado.")
    print("  Ejecutá:  pip install folium")
    print("=" * 60)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Reutilizar helpers del mapa de ruta existente (consistencia visual)
# ---------------------------------------------------------------------------

from scripts.mapa_ruta import (  # noqa: E402
    ATTR_CARTO,
    RUTA_COLOR,
    TILE_DEFAULT,
    _crear_popup,
    _agregar_leyenda_gradiente,
    color_segun_gradiente,
    formatear_distancia,
    formatear_pendiente,
)
from scripts.gradiente import haversine  # noqa: E402
from scripts.track_gpx import (  # noqa: E402
    cargar_tracks,
    medir_discrepancias,
    reportar_discrepancias,
    tramo_temporal,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mapas_municipio")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Variantes disponibles: clave → (sufijo, descripción)
VARIANTES = {
    "ruta": "Puntos del municipio + línea que los conecta",
    "puntos": "Solo los marcadores del municipio, sin línea",
    "contexto": "Puntos del municipio destacados sobre la ruta completa",
    "gradiente": "Segmentos coloreados por pendiente + leyenda",
}

# Colores para la variante contexto
CONTEXTO_COLOR = "#bbbbbb"      # ruta completa (fondo)
CONTEXTO_OPACITY = 0.6
CONTEXTO_WEIGHT = 3
DESTACADO_COLOR = "#cc3333"     # puntos del municipio destacados

# ---------------------------------------------------------------------------
# Sanitización del nombre
# ---------------------------------------------------------------------------

def _slug_municipio(nombre: str) -> str:
    """Convierte un nombre de municipio a slug ASCII (sin acentos ni símbolos).

    Normaliza a NFD (descompone las vocales acentuadas y la ñ), elimina las
    marcas combinantes (tildes, diéresis, virgulilla), reemplaza los espacios
    por guion bajo y descarta cualquier símbolo no alfanumérico. Conserva las
    mayúsculas originales.

    Ejemplos:
        'Río Hondo'           → 'Rio_Hondo'
        'Jesús María'         → 'Jesus_Maria'
        'Melincué'            → 'Melincue'
        'Chañar Ladeado'      → 'Chanar_Ladeado'
        'San Andrés de Giles' → 'San_Andres_de_Giles'
    """
    nfkd = unicodedata.normalize("NFD", nombre or "")
    sin_diacriticos = "".join(c for c in nfkd if unicodedata.combining(c) == 0)
    return "".join(
        c if c.isalnum() else "_" if c == " " else ""
        for c in sin_diacriticos
    ).strip("_")


def _nombre_archivo(municipio: str, variante: str) -> str:
    """Genera el nombre del archivo: mapa_municipio_<slug>_<variante>.html.

    El municipio se convierte a slug ASCII sin acentos ni símbolos, con los
    espacios reemplazados por guion bajo (ej: 'Ojo de Agua' → 'Ojo_de_Agua',
    'Río Hondo' → 'Rio_Hondo', 'Melincué' → 'Melincue'). Los nombres sin
    acentos no cambian ('Bell Ville' → 'Bell_Ville').
    """
    return f"mapa_municipio_{_slug_municipio(municipio)}_{variante}.html"


def _parse_timestamp(ts: str | None) -> datetime | None:
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


# ---------------------------------------------------------------------------
# Lectura de datos desde la BD
# ---------------------------------------------------------------------------

def leer_puntos_por_municipio(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Agrupa los puntos GPS por municipio, ordenados por timestamp_utc.

    Returns:
        Dict: municipio → lista de dicts con id, filename, lat, lon, timestamp,
        provincia, departamento, municipio, distancia_m, gradiente,
        cumul_dist_m, altitud, max_gap_s.
    """
    rows = conn.execute("""
        SELECT
            m.id,
            m.filename_original,
            m.latitude,
            m.longitude,
            m.timestamp_utc,
            m.provincia,
            m.departamento,
            m.municipio,
            m.distance_from_prev_m,
            m.gradient_pct,
            m.cumul_distance_m,
            m.altitude,
            mm.value AS gaps_json
        FROM media m
        LEFT JOIN media_metadata mm
            ON mm.media_id = m.id AND mm.key = 'ubicacion_video_gaps'
        WHERE m.latitude IS NOT NULL AND m.longitude IS NOT NULL
          AND m.municipio IS NOT NULL AND m.municipio != ''
        ORDER BY m.municipio, m.timestamp_utc ASC
    """).fetchall()

    puntos_por_municipio: dict[str, list[dict]] = {}
    for row in rows:
        muni = row[7]
        puntos_por_municipio.setdefault(muni, []).append({
            "id": row[0],
            "filename": row[1],
            "lat": row[2],
            "lon": row[3],
            "timestamp": row[4] or "",
            "provincia": row[5] or "",
            "departamento": row[6] or "",
            "municipio": muni,
            "distancia_m": row[8],
            "gradiente": row[9],
            "cumul_dist_m": row[10],
            "altitud": row[11],
            "max_gap_s": _max_gap_s(row[12]),
        })
    return puntos_por_municipio


def _max_gap_s(gaps_json: str | None) -> float | None:
    """Máximo gap (segundos) desde el JSON de ubicacion_video_gaps."""
    if not gaps_json:
        return None
    try:
        gaps = json.loads(gaps_json)
    except (ValueError, TypeError):
        return None
    if not gaps:
        return None
    return max(g["gap_s"] for g in gaps)


# ---------------------------------------------------------------------------
# Construcción de cada variante
# ---------------------------------------------------------------------------

def _crear_mapa_base(lats: list, lons: list) -> folium.Map:
    """Crea el mapa base con centro en los puntos y bounds ajustados."""
    centro_lat = sum(lats) / len(lats)
    centro_lon = sum(lons) / len(lons)
    bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
    m = folium.Map(
        location=[centro_lat, centro_lon],
        zoom_start=12,
        tiles=TILE_DEFAULT,
        attr=ATTR_CARTO,
        control_scale=True,
    )
    m.fit_bounds(bounds)
    return m


def _agregar_marcadores_basicos(mapa, puntos: list[dict], umbral_gap_aviso: float = 1800):
    """Agrega marcadores inicio (verde) / fin (rojo) / intermedios (azul).

    Los medios con gap del track >= umbral_gap_aviso se marcan en naranja
    (posición incierta) y su popup muestra el aviso.
    """
    if not puntos:
        return

    def _tiene_gap(p: dict) -> bool:
        return (p.get("max_gap_s") or 0) >= umbral_gap_aviso

    # Inicio
    p = puntos[0]
    color = "orange" if _tiene_gap(p) else "green"
    folium.Marker(
        location=[p["lat"], p["lon"]],
        popup=folium.Popup(_crear_popup(p, es_inicio=True, umbral_gap_aviso=umbral_gap_aviso),
                           max_width=350),
        tooltip=f"🏁 Inicio: {p.get('filename', '')}",
        icon=folium.Icon(color=color, icon="play", prefix="fa"),
    ).add_to(mapa)
    # Fin
    if len(puntos) > 1:
        p = puntos[-1]
        color = "orange" if _tiene_gap(p) else "red"
        folium.Marker(
            location=[p["lat"], p["lon"]],
            popup=folium.Popup(_crear_popup(p, es_fin=True, umbral_gap_aviso=umbral_gap_aviso),
                               max_width=350),
            tooltip=f"🏁 Fin: {p.get('filename', '')}",
            icon=folium.Icon(color=color, icon="stop", prefix="fa"),
        ).add_to(mapa)
    # Intermedios
    for i in range(1, len(puntos) - 1):
        p = puntos[i]
        color = "orange" if _tiene_gap(p) else "blue"
        folium.Marker(
            location=[p["lat"], p["lon"]],
            popup=folium.Popup(_crear_popup(p, umbral_gap_aviso=umbral_gap_aviso), max_width=350),
            tooltip=p.get("filename", f"Punto {i}"),
            icon=folium.Icon(color=color, icon="info-sign", prefix="glyphicon"),
        ).add_to(mapa)


def _variante_ruta(puntos: list[dict], tramo_track: list[tuple] | None = None,
                   umbral_gap_aviso: float = 1800) -> folium.Map:
    """Puntos del municipio + tramo del track GPX que los conecta (si hay)."""
    lats = [p["lat"] for p in puntos]
    lons = [p["lon"] for p in puntos]
    m = _crear_mapa_base(lats, lons)

    if tramo_track and len(tramo_track) >= 2:
        coords = [(p[1], p[2]) for p in tramo_track]
        folium.PolyLine(
            locations=coords,
            color=RUTA_COLOR,
            weight=4,
            opacity=0.8,
            tooltip=f"Track — {len(tramo_track)} puntos",
        ).add_to(m)
    elif len(puntos) >= 2:
        # Fallback sin track: línea entre los GPS de los medios
        coords = [(p["lat"], p["lon"]) for p in puntos]
        folium.PolyLine(
            locations=coords,
            color=RUTA_COLOR,
            weight=4,
            opacity=0.8,
            tooltip=f"Ruta — {len(puntos)} puntos",
        ).add_to(m)

    _agregar_marcadores_basicos(m, puntos, umbral_gap_aviso)
    return m


def _variante_puntos(puntos: list[dict], umbral_gap_aviso: float = 1800) -> folium.Map:
    """Solo los marcadores del municipio, sin línea."""
    lats = [p["lat"] for p in puntos]
    lons = [p["lon"] for p in puntos]
    m = _crear_mapa_base(lats, lons)
    _agregar_marcadores_basicos(m, puntos, umbral_gap_aviso)
    return m


def _variante_contexto(
    puntos_municipio: list[dict],
    track_completo: list[tuple] | None = None,
    umbral_gap_aviso: float = 1800,
) -> folium.Map:
    """Puntos del municipio destacados sobre la ruta completa del track (gris)."""
    lats = [p["lat"] for p in puntos_municipio]
    lons = [p["lon"] for p in puntos_municipio]
    m = _crear_mapa_base(lats, lons)

    # Ruta completa en gris (contexto): track GPX si hay, sino los medios
    if track_completo and len(track_completo) >= 2:
        coords = [(p[1], p[2]) for p in track_completo]
        tooltip = f"Track completo — {len(track_completo)} puntos"
    else:
        coords = [(p["lat"], p["lon"]) for p in puntos_municipio]
        tooltip = f"Ruta — {len(puntos_municipio)} puntos"
    folium.PolyLine(
        locations=coords,
        color=CONTEXTO_COLOR,
        weight=CONTEXTO_WEIGHT,
        opacity=CONTEXTO_OPACITY,
        tooltip=tooltip,
    ).add_to(m)

    # Puntos del municipio destacados
    for p in puntos_municipio:
        folium.CircleMarker(
            location=[p["lat"], p["lon"]],
            radius=6,
            color=DESTACADO_COLOR,
            fill=True,
            fill_color=DESTACADO_COLOR,
            fill_opacity=0.8,
            popup=folium.Popup(_crear_popup(p, umbral_gap_aviso=umbral_gap_aviso), max_width=350),
            tooltip=p.get("filename", ""),
        ).add_to(m)

    # Marcadores inicio/fin del municipio destacados
    _agregar_marcadores_basicos(m, puntos_municipio, umbral_gap_aviso)
    return m


def _variante_gradiente(puntos: list[dict], tramo_track: list[tuple] | None = None,
                        umbral_gap_aviso: float = 1800) -> folium.Map:
    """Segmentos coloreados por pendiente (del tramo del track si hay) + leyenda."""
    lats = [p["lat"] for p in puntos]
    lons = [p["lon"] for p in puntos]
    m = _crear_mapa_base(lats, lons)

    if tramo_track and len(tramo_track) >= 2:
        # Gradiente calculado de la altitud del track
        for i in range(1, len(tramo_track)):
            dt1, lat1, lon1, ele1 = tramo_track[i - 1]
            dt2, lat2, lon2, ele2 = tramo_track[i]
            grad = None
            dist_m = None
            if ele1 is not None and ele2 is not None:
                dist_m = haversine(lat1, lon1, lat2, lon2)
                if dist_m and dist_m > 0:
                    grad = ((ele2 - ele1) / dist_m) * 100.0
            color = color_segun_gradiente(grad)
            grad_str = formatear_pendiente(grad)
            folium.PolyLine(
                locations=[(lat1, lon1), (lat2, lon2)],
                color=color,
                weight=4,
                opacity=0.8,
                tooltip=f"{grad_str} | {dt2.isoformat()}",
                popup=folium.Popup(
                    f"<b>Pendiente:</b> {grad_str}<br>"
                    f"<b>Distancia:</b> {formatear_distancia(dist_m)}",
                    max_width=250,
                ),
            ).add_to(m)
        _agregar_leyenda_gradiente(m)
    elif len(puntos) >= 2:
        # Fallback sin track: gradiente de los medios
        for i in range(1, len(puntos)):
            p_prev = puntos[i - 1]
            p_curr = puntos[i]
            color = color_segun_gradiente(p_curr["gradiente"])
            grad_str = formatear_pendiente(p_curr["gradiente"])
            folium.PolyLine(
                locations=[(p_prev["lat"], p_prev["lon"]),
                           (p_curr["lat"], p_curr["lon"])],
                color=color,
                weight=4,
                opacity=0.8,
                tooltip=f"{grad_str} | {p_curr['filename']}",
                popup=folium.Popup(
                    f"<b>Pendiente:</b> {grad_str}<br>"
                    f"<b>Distancia:</b> {formatear_distancia(p_curr['distancia_m'])}",
                    max_width=250,
                ),
            ).add_to(m)
        _agregar_leyenda_gradiente(m)

    _agregar_marcadores_basicos(m, puntos, umbral_gap_aviso)
    return m


# Generadores por variante (reciben puntos, tramo del track, track completo y umbral gap)
_GENERADORES = {
    "ruta": lambda puntos, tramo, completo, umbral: _variante_ruta(puntos, tramo, umbral),
    "puntos": lambda puntos, tramo, completo, umbral: _variante_puntos(puntos, umbral),
    "contexto": lambda puntos, tramo, completo, umbral: _variante_contexto(puntos, completo, umbral),
    "gradiente": lambda puntos, tramo, completo, umbral: _variante_gradiente(puntos, tramo, umbral),
}


# ---------------------------------------------------------------------------
# Generación principal
# ---------------------------------------------------------------------------

def generar_mapas(
    db_path: str,
    output_dir: str = "mapas",
    variantes: list[str] | None = None,
    municipio_filtro: str | None = None,
    dry_run: bool = False,
    tolerancia_metros: int = 1000,
    mode: str = "update",
    umbral_gap_aviso: float = 1800,
) -> dict:
    """Genera los mapas por municipio y devuelve estadísticas.

    La línea de ruta de las variantes ruta/contexto/gradiente usa el track GPX
    registrado en `tracks`: cada municipio toma el tramo del track cuyo tiempo
    cae dentro del rango [min, max] de timestamps de sus medios.

    Args:
        mode: 'update' (default) regenera todos los mapas; 'skip' solo genera
            los archivos que no existen en output_dir (los existentes se saltan).
        umbral_gap_aviso: gap del track (s) a partir del cual un medio se marca
            como "posición incierta" en su marcador/popup (default 1800).

    Returns:
        Dict con: total_municipios, total_archivos, generados, saltados, errores.
    """
    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        return {"total_municipios": 0, "total_archivos": 0, "generados": 0, "saltados": 0, "errores": 0}

    variantes = variantes or list(VARIANTES.keys())

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        try:
            conn.execute("SELECT COUNT(*) FROM media").fetchone()
        except sqlite3.OperationalError as e:
            log.error("La tabla 'media' no existe en la DB: %s", e)
            return {"total_municipios": 0, "total_archivos": 0, "generados": 0, "saltados": 0, "errores": 0}

        puntos_por_municipio = leer_puntos_por_municipio(conn)
        tracks = cargar_tracks(conn)
        track_completo: list[tuple] = tracks[0]["puntos_tiempo"] if tracks else []
        if not track_completo:
            log.warning("Sin track GPX: la ruta se dibuja con los GPS de los medios.")
        else:
            log.info("Track usado para la ruta: %s (%d puntos)",
                     tracks[0]["name"], len(track_completo))
            discrepancias = medir_discrepancias(conn, track_completo, tolerancia_metros)
            if discrepancias:
                reportar_discrepancias(discrepancias, tolerancia_metros)
    finally:
        conn.close()

    if not puntos_por_municipio:
        log.warning("No hay municipios con puntos GPS en la base de datos.")
        return {"total_municipios": 0, "total_archivos": 0, "generados": 0, "saltados": 0, "errores": 0}

    # Aplicar filtro por municipio
    if municipio_filtro:
        filtro = municipio_filtro.strip()
        coincidencias = [m for m in puntos_por_municipio if filtro.lower() in m.lower()]
        if not coincidencias:
            log.warning("No se encontró ningún municipio que contenga '%s'.", municipio_filtro)
            disponibles = ", ".join(sorted(puntos_por_municipio.keys()))
            log.warning("Municipios disponibles: %s", disponibles)
            return {"total_municipios": 0, "total_archivos": 0, "generados": 0, "saltados": 0, "errores": 0}
        puntos_por_municipio = {m: puntos_por_municipio[m] for m in coincidencias}

    municipios_ordenados = sorted(puntos_por_municipio.keys())
    log.info("Municipios a procesar: %d", len(municipios_ordenados))
    log.info("Variantes: %s", ", ".join(variantes))

    # Tramo del track por municipio: puntos del track dentro de [min, max]
    # de los timestamps de sus medios (por orden temporal del municipio).
    tramo_por_municipio: dict[str, list[tuple]] = {}
    if track_completo:
        for muni in municipios_ordenados:
            puntos = puntos_por_municipio[muni]
            timestamps = [_parse_timestamp(p["timestamp"]) for p in puntos]
            timestamps = [t for t in timestamps if t is not None]
            if timestamps:
                inicio, fin = min(timestamps), max(timestamps)
                tramo_por_municipio[muni] = tramo_temporal(track_completo, inicio, fin)
            else:
                tramo_por_municipio[muni] = []

    total_archivos = len(municipios_ordenados) * len(variantes)
    # En modo skip, los archivos que ya existen se consideran "saltados".
    def _existe(nombre: str) -> bool:
        return os.path.isfile(os.path.join(output_dir, nombre))

    if dry_run:
        log.info("[DRY-RUN] Modo: %s", mode)
        if mode == "skip":
            log.info("[DRY-RUN] Se generarían los faltantes de %d archivos:", total_archivos)
        else:
            log.info("[DRY-RUN] Se generarían %d archivos:", total_archivos)
        for muni in municipios_ordenados:
            n_tramo = len(tramo_por_municipio.get(muni, []))
            for var in variantes:
                nombre = _nombre_archivo(muni, var)
                existe = _existe(nombre)
                if mode == "skip" and existe:
                    log.info("  [skip] %s  (ya existe)", nombre)
                    continue
                log.info("  %s  (%d puntos, tramo track: %d)",
                         nombre, len(puntos_por_municipio[muni]), n_tramo)
        return {"total_municipios": len(municipios_ordenados),
                "total_archivos": total_archivos, "generados": 0, "saltados": 0, "errores": 0}

    os.makedirs(output_dir, exist_ok=True)

    generados = 0
    saltados = 0
    errores = 0
    for muni in municipios_ordenados:
        puntos = puntos_por_municipio[muni]
        tramo = tramo_por_municipio.get(muni, [])
        for var in variantes:
            nombre = _nombre_archivo(muni, var)
            ruta = os.path.join(output_dir, nombre)
            if mode == "skip" and os.path.isfile(ruta):
                saltados += 1
                log.info("Ya existe (skip): %s", nombre)
                continue
            try:
                generador = _GENERADORES[var]
                mapa = generador(puntos, tramo, track_completo, umbral_gap_aviso)
                mapa.save(ruta)
                generados += 1
                log.info("Generado: %s (%d puntos, tramo track: %d)",
                         nombre, len(puntos), len(tramo))
            except Exception as e:
                errores += 1
                log.error("Error generando %s (%s): %s", muni, var, e)

    log.info("")
    log.info("Resumen: %d municipios, %d/%d archivos generados, %d saltados, %d errores.",
             len(municipios_ordenados), generados, total_archivos, saltados, errores)
    return {"total_municipios": len(municipios_ordenados),
            "total_archivos": total_archivos, "generados": generados,
            "saltados": saltados, "errores": errores}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Genera un mapa HTML por municipio recorrido, con variantes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Variantes:
  ruta      Puntos del municipio + tramo del track que los conecta
  puntos    Solo los marcadores del municipio, sin línea
  contexto  Puntos del municipio destacados sobre el track completo
  gradiente Segmentos del tramo coloreados por pendiente + leyenda

Ejemplos:
  python scripts/mapas_municipio.py
  python scripts/mapas_municipio.py --variantes ruta,puntos
  python scripts/mapas_municipio.py --municipio "Bell Ville"
  python scripts/mapas_municipio.py --output mapas --mode skip
  python scripts/mapas_municipio.py --output mapas --dry-run
        """,
    )

    parser.add_argument(
        "--output", "-o",
        default="mapas",
        help="Carpeta de salida (default: mapas)",
    )
    parser.add_argument(
        "--db", default=None,
        help="Ruta a la base de datos SQLite (default: db/flujos.db en la raíz)",
    )
    parser.add_argument(
        "--variantes",
        default=None,
        help="Variantes separadas por coma (default: todas: ruta,puntos,contexto,gradiente)",
    )
    parser.add_argument(
        "--municipio",
        default=None,
        help="Filtrar a un municipio (substring, case-insensitive)",
    )
    parser.add_argument(
        "--mode",
        choices=["skip", "update"],
        default="update",
        help="update (default) regenera todos; skip solo genera los que faltan",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Listar los archivos que se generarían sin escribirlos",
    )
    parser.add_argument(
        "--tolerancia-metros",
        type=int,
        default=1000,
        help="Tolerancia (m) para reportar discrepancias media vs track (default: 1000)",
    )
    parser.add_argument(
        "--umbral-gap-aviso",
        type=float,
        default=1800,
        help="Gap del track (s) a partir del cual un medio se marca como posición incierta (default: 1800)",
    )
    args = parser.parse_args(argv)

    # Resolver ruta de DB
    if args.db:
        db_path = os.path.abspath(args.db)
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        db_path = os.path.join(project_root, "db", "flujos.db")

    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        log.error("Usá --db para especificar una ruta alternativa.")
        sys.exit(1)

    log.info("Base de datos: %s", db_path)

    # Parsear variantes
    variantes = None
    if args.variantes:
        pedidas = [v.strip() for v in args.variantes.split(",") if v.strip()]
        invalidas = [v for v in pedidas if v not in VARIANTES]
        if invalidas:
            log.error("Variantes inválidas: %s. Válidas: %s",
                      ", ".join(invalidas), ", ".join(VARIANTES.keys()))
            sys.exit(1)
        variantes = pedidas

    resultado = generar_mapas(
        db_path=db_path,
        output_dir=args.output,
        variantes=variantes,
        municipio_filtro=args.municipio,
        dry_run=args.dry_run,
        tolerancia_metros=args.tolerancia_metros,
        mode=args.mode,
        umbral_gap_aviso=args.umbral_gap_aviso,
    )

    if resultado["generados"] > 0:
        log.info("")
        log.info("Mapas generados exitosamente en: %s", os.path.abspath(args.output))
        log.info("  Abrí los archivos en tu navegador para explorar cada municipio.")
    elif not args.dry_run and resultado["errores"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
