#!/usr/bin/env python3
"""
ubicar_videos_gpx.py — Ubica videos interpolando su intervalo temporal contra
el track GPX del viaje.

Los videos 360° (Insta360) fueron remuxados y perdieron su GPS embebido, pero
conservan `timestamp_utc` y `duration_secs` correctos. Este script infiere su
posición real muestreando el intervalo del video cada `--intervalo` segundos,
interpolando (lat, lon, ele) contra los puntos del track GPX, y aplicando una
regla de emisión que colapsa momentos estáticos y emite nuevas filas solo cuando
hay movimiento suficiente.

GAPS del track: las muestras que caen dentro de un hueco del track mayor que
`--umbral-gap` (default 1800 s = 30 min) NO se emiten — la interpolación a través
del hueco produciría una posición falsa (recta entre dos puntos separados por el
gap). Esos huecos quedan registrados en `ubicacion_video_gaps` para diagnóstico.
Si un video queda sin ninguna posición válida (todo en gaps), en update/replace se
limpia su `media.latitude/longitude` previo de track (evita puntos ficticios).

Resultado:
  - `media.latitude/longitude/altitude` = posición al inicio del video
    (primera muestra emitida con track real; no se emite dentro de gaps > umbral)
  - `media_keypoints` key=`ubicacion_video` = posiciones a lo largo del video
    (múltiples filas para videos largos o en movimiento; una sola si estático)
  - `media_metadata` key=`ubicacion_video_estado` = sentinel de procesado
  - `media_metadata` key=`ubicacion_video_gaps` = JSON con gaps GPX grandes

Modos:
  skip    (default) solo videos sin sentinel ni keypoints ubicacion_video
  update  reprocesa todos los videos candidatos
  replace limpia TODOS los ubicacion_video + sentinels y regenera

Uso:
    python scripts/ubicar_videos_gpx.py --solo-360 --dry-run
    python scripts/ubicar_videos_gpx.py --solo-360
    python scripts/ubicar_videos_gpx.py --mode update --verbose
"""

import argparse
import bisect
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# La consola de Windows por defecto usa cp1252, que no puede codificar los
# caracteres acentuados. Reconfiguramos stdout a UTF-8 con fallback 'replace'.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Permitir importar db.util y scripts hermanos desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.util import abrir, resolver_db  # noqa: E402
from scripts.gradiente import haversine  # noqa: E402
from scripts.ingest_gpx import parsear_gpx  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

KEY_UBICACION = "ubicacion_video"
KEY_ESTADO = "ubicacion_video_estado"
KEY_GAPS = "ubicacion_video_gaps"

ESTADO_OK = "ok"
ESTADO_SIN_DATOS = "sin_datos"
ESTADO_FUERA_RANGO = "fuera_rango"
ESTADO_SIN_TRACK = "sin_track"

SOURCE_TRACK = "track_interpolado"


# ---------------------------------------------------------------------------
# Helpers de tiempo (copiados de keypoints_contexto — NO importar ese módulo
# porque trae astronomia/fetch_weather/geocode al import)
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


def _muestrear_intervalo(inicio: datetime, fin: datetime, intervalo_s: int) -> list[datetime]:
    """Puntos de muestreo cada intervalo_s en [inicio, fin] (inclusive)."""
    muestras: list[datetime] = []
    t = inicio
    while t <= fin:
        muestras.append(t)
        t += timedelta(seconds=intervalo_s)
    return muestras


# ---------------------------------------------------------------------------
# Interpolación sobre el track (copiados de keypoints_contexto)
# ---------------------------------------------------------------------------

def _puntos_track_con_tiempo(
    track_points: list[dict],
) -> list[tuple[datetime, float, float, float | None]]:
    """Puntos del track con time ordenados: [(dt, lat, lon, ele)]."""
    pts: list[tuple[datetime, float, float, float | None]] = []
    for tp in track_points:
        dt = _normalizar_dt(tp.get("time"))
        if dt is None or tp.get("latitude") is None or tp.get("longitude") is None:
            continue
        pts.append((dt, tp["latitude"], tp["longitude"], tp.get("elevation")))
    pts.sort(key=lambda p: p[0])
    return pts


def _interpolar_lineal(
    p1: tuple[datetime, float, float, float | None],
    p2: tuple[datetime, float, float, float | None],
    t: datetime,
) -> tuple[float, float, float | None]:
    """Interpola (lat, lon, ele) entre dos puntos track en el instante t."""
    t1, lat1, lon1, ele1 = p1
    t2, lat2, lon2, ele2 = p2
    span = (t2 - t1).total_seconds()
    if span <= 0:
        return lat1, lon1, ele1
    frac = (t - t1).total_seconds() / span
    lat = lat1 + (lat2 - lat1) * frac
    lon = lon1 + (lon2 - lon1) * frac
    ele = None
    if ele1 is not None and ele2 is not None:
        ele = ele1 + (ele2 - ele1) * frac
    return lat, lon, ele


def _interpolar_en_track(
    puntos: list[tuple[datetime, float, float, float | None]],
    t: datetime,
) -> tuple[float, float, float | None] | None:
    """Posición (lat, lon, ele) interpolada en t, o None si t está fuera de rango."""
    if len(puntos) < 2:
        return None
    tiempos = [p[0] for p in puntos]
    idx = bisect.bisect_left(tiempos, t)
    if idx == 0:
        return None  # antes del primer punto del track
    if idx >= len(tiempos):
        return None  # después del último punto del track
    return _interpolar_lineal(puntos[idx - 1], puntos[idx], t)


def _gap_entre_puntos_bracket(
    puntos: list[tuple[datetime, float, float, float | None]],
    t: datetime,
) -> float | None:
    """
    Gap temporal (segundos) entre los dos puntos del track que encuadran a t.
    Devuelve None si t está fuera de rango o hay < 2 puntos.
    """
    if len(puntos) < 2:
        return None
    tiempos = [p[0] for p in puntos]
    idx = bisect.bisect_left(tiempos, t)
    if idx == 0 or idx >= len(tiempos):
        return None
    return abs((tiempos[idx] - tiempos[idx - 1]).total_seconds())


# ---------------------------------------------------------------------------
# Selección de track (simplificada de keypoints_contexto._elegir_track)
# ---------------------------------------------------------------------------

def _obtener_tracks_gpx(conn: sqlite3.Connection) -> list[dict]:
    """
    Relee los .gpx de TODOS los tracks registrados en `tracks`.

    Returns:
        Lista de dicts con "name", "puntos_tiempo", "start_dt", "end_dt".
        Tracks sin archivo o sin puntos útiles se descartan con log warning.
    """
    conn.row_factory = sqlite3.Row
    filas = conn.execute(
        "SELECT id, name, start_time, end_time, filepath_absoluto "
        "FROM tracks ORDER BY id"
    ).fetchall()
    tracks: list[dict] = []
    for fila in filas:
        ruta = fila["filepath_absoluto"]
        if not ruta or not Path(ruta).exists():
            log.warning("Archivo GPX no existe o sin ruta (track %s): %s",
                        fila["name"], ruta)
            continue
        try:
            gpx = parsear_gpx(ruta)
        except Exception as e:
            log.warning("No se pudo parsear GPX %s: %s", ruta, e)
            continue
        puntos = _puntos_track_con_tiempo(gpx.get("track_points") or [])
        if len(puntos) < 2:
            log.warning("Track sin suficientes puntos con tiempo: %s", ruta)
            continue
        start_dt = _normalizar_dt(fila["start_time"])
        end_dt = _normalizar_dt(fila["end_time"])
        if start_dt is None:
            start_dt = puntos[0][0]
        if end_dt is None:
            end_dt = puntos[-1][0]
        tracks.append({
            "name": fila["name"],
            "puntos_tiempo": puntos,
            "start_dt": start_dt,
            "end_dt": end_dt,
        })
        log.info("Track: %s — %d puntos con tiempo, rango %s → %s",
                 fila["name"], len(puntos),
                 start_dt.isoformat(), end_dt.isoformat())
    return tracks


def _elegir_track(
    tracks: list[dict],
    inicio: datetime,
    fin: datetime,
) -> dict | None:
    """
    Elige el mejor track GPX para el intervalo [inicio, fin] de un video.

    Prioridad:
      1. contiene: el track CONTIENE el intervalo (de varios, el de rango más chico).
      2. solapa: solapamiento parcial (el de mayor solapamiento).
      3. cercano: el de menor gap (sin solapamiento).

    Returns:
        dict del track con "modo" y "gap_s", o None si no hay tracks.
    """
    if not tracks:
        return None

    candidatos: list[dict] = []
    for t in tracks:
        solape_inicio = max(inicio, t["start_dt"])
        solape_fin = min(fin, t["end_dt"])
        if solape_fin >= solape_inicio:
            t["_solape"] = (solape_fin - solape_inicio).total_seconds()
            t["_gap"] = 0.0
        else:
            t["_solape"] = 0.0
            gap_antes = (t["start_dt"] - fin).total_seconds()
            gap_despues = (inicio - t["end_dt"]).total_seconds()
            t["_gap"] = max(gap_antes, gap_despues)
        candidatos.append(t)

    if not candidatos:
        return None

    # 1) Contiene el intervalo completo del video
    contienen = [t for t in candidatos
                 if t["start_dt"] <= inicio and fin <= t["end_dt"]]
    if contienen:
        elegido = min(contienen, key=lambda t: (t["end_dt"] - t["start_dt"]).total_seconds())
        elegido["modo"] = "contiene"
        elegido["gap_s"] = 0.0
        return elegido

    # 2) Solapa parcialmente
    solapan = [t for t in candidatos if t["_solape"] > 0]
    if solapan:
        elegido = max(solapan, key=lambda t: t["_solape"])
        elegido["modo"] = "solapa"
        elegido["gap_s"] = 0.0
        return elegido

    # 3) El más cercano
    elegido = min(candidatos, key=lambda t: t["_gap"])
    elegido["modo"] = "cercano"
    elegido["gap_s"] = elegido["_gap"]
    return elegido


# ---------------------------------------------------------------------------
# Muestreo y emisión de ubicaciones
# ---------------------------------------------------------------------------

def _formatear_ubicacion(lat: float, lon: float, ele: float | None) -> str:
    """Formatea 'lat,lon[,ele]' con 6 decimales lat/lon, 1 decimal ele."""
    if ele is not None:
        return f"{lat:.6f},{lon:.6f},{ele:.1f}"
    return f"{lat:.6f},{lon:.6f}"


def _generar_ubicaciones(
    puntos_tiempo: list[tuple[datetime, float, float, float | None]],
    inicio: datetime,
    fin: datetime,
    duration_secs: float,
    intervalo_s: int,
    umbral_movimiento_kmh: float,
    distancia_minima_m: float,
    umbral_gap_s: float,
) -> tuple[list[dict], list[dict]]:
    """
    Genera las ubicaciones emitidas para un video.

    Returns:
        (filas, gaps) — filas son dicts con offset_s, dt, lat, lon, ele;
        gaps son dicts con offset_s, gap_s para gaps GPX grandes.
    """
    # Generar muestras finas
    tiempos_track = [p[0] for p in puntos_tiempo]
    muestras_raw: list[dict] = []
    for dt in _muestrear_intervalo(inicio, fin, intervalo_s):
        offset_s = round((dt - inicio).total_seconds(), 1)
        pos = _interpolar_en_track(puntos_tiempo, dt)
        if pos is None:
            continue  # fuera de rango track — no emitir esta muestra
        lat, lon, ele = pos
        # Gap GPX entre puntos bracket
        gap = _gap_entre_puntos_bracket(puntos_tiempo, dt)
        muestras_raw.append({
            "offset_s": offset_s,
            "dt": dt,
            "lat": lat,
            "lon": lon,
            "ele": ele,
            "gap_s": gap,
        })

    if not muestras_raw:
        return [], []

    # Calcular velocidad entre muestras consecutivas
    for i, m in enumerate(muestras_raw):
        if i == 0:
            m["speed_kmh"] = 0.0
        else:
            prev = muestras_raw[i - 1]
            dt_s = (m["dt"] - prev["dt"]).total_seconds()
            if dt_s > 0:
                dist = haversine(prev["lat"], prev["lon"], m["lat"], m["lon"])
                m["speed_kmh"] = dist / dt_s * 3.6
            else:
                m["speed_kmh"] = 0.0

    # Regla de emisión:
    # - Muestras dentro de un gap del track > umbral_gap_s NO se emiten
    #   (la interpolación a través del hueco sería una posición falsa).
    # - Primera muestra emitida = la primera con track real (post-gap).
    # - Después: emitir solo si speed >= umbral AND distancia desde última emitida >= distancia_minima.
    # - Run de speed < umbral colapsa en UNA fila (la primera del run).
    filas: list[dict] = []
    gaps: list[dict] = []
    ultimo_emitido: dict | None = None

    # Trackear run de velocidad baja
    en_pausa = False  # True cuando estamos en una secuencia de speed < umbral
    primera_pausa: dict | None = None  # primera muestra de la pausa actual
    en_gap = False  # True si la muestra anterior estaba dentro de un hueco

    for m in muestras_raw:
        # Filtro de gap: no emitir posiciones dentro de huecos del track.
        # Se registra el gap solo en la PRIMERA muestra de cada run de hueco
        # (evita decenas de entradas idénticas para el mismo gap).
        if m["gap_s"] is not None and m["gap_s"] > umbral_gap_s:
            if not en_gap:
                gaps.append({"offset_s": m["offset_s"], "gap_s": round(m["gap_s"], 1)})
            en_gap = True
            continue
        en_gap = False

        es_primera = ultimo_emitido is None
        en_movimiento = m["speed_kmh"] >= umbral_movimiento_kmh

        if es_primera:
            # Emitir la primera (ya tiene track real, el gap se filtró arriba)
            filas.append({
                "offset_s": m["offset_s"],
                "dt": m["dt"],
                "lat": m["lat"],
                "lon": m["lon"],
                "ele": m["ele"],
            })
            ultimo_emitido = m
            en_pausa = False
            primera_pausa = None
            continue

        if en_movimiento:
            en_pausa = False
            primera_pausa = None
            # Emitir solo si cumple distancia mínima
            dist_desde_ultimo = haversine(
                ultimo_emitido["lat"], ultimo_emitido["lon"],
                m["lat"], m["lon"],
            )
            if dist_desde_ultimo >= distancia_minima_m:
                filas.append({
                    "offset_s": m["offset_s"],
                    "dt": m["dt"],
                    "lat": m["lat"],
                    "lon": m["lon"],
                    "ele": m["ele"],
                })
                ultimo_emitido = m
        else:
            # Velocidad baja: colapsar en UNA fila
            if not en_pausa:
                # Inicio de pausa: emitir la primera muestra de velocidad baja
                en_pausa = True
                primera_pausa = m
                filas.append({
                    "offset_s": m["offset_s"],
                    "dt": m["dt"],
                    "lat": m["lat"],
                    "lon": m["lon"],
                    "ele": m["ele"],
                })
                ultimo_emitido = m
            # Si ya estamos en pausa, no emitir nada más hasta que cambie el estado

    # Emitir fila final si la última emitida no cubre el fin del video
    # (y el fin no cae dentro de un gap del track > umbral)
    duracion_total_s = round(duration_secs, 1)
    gap_final = _gap_entre_puntos_bracket(puntos_tiempo, fin)
    if gap_final is not None and gap_final > umbral_gap_s:
        gaps.append({"offset_s": duracion_total_s, "gap_s": round(gap_final, 1)})
    elif ultimo_emitido is not None and ultimo_emitido["offset_s"] < duracion_total_s:
        # Interpolar posición exacta al final
        pos_final = _interpolar_en_track(puntos_tiempo, fin)
        if pos_final is not None:
            lat_f, lon_f, ele_f = pos_final
            filas.append({
                "offset_s": duracion_total_s,
                "dt": fin,
                "lat": lat_f,
                "lon": lon_f,
                "ele": ele_f,
            })
            ultimo_emitido_fict = {"lat": lat_f, "lon": lon_f, "offset_s": duracion_total_s}

    # Defensivo: ordenar por offset y deduplicar filas consecutivas más
    # cercanas que distancia_minima
    filas.sort(key=lambda f: f["offset_s"])
    deduped: list[dict] = []
    for f in filas:
        if deduped:
            dist = haversine(deduped[-1]["lat"], deduped[-1]["lon"], f["lat"], f["lon"])
            if dist < distancia_minima_m:
                continue  # skip fila demasiado cercana a la anterior
        deduped.append(f)

    return deduped, gaps


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def _query_candidatos(
    conn: sqlite3.Connection, mode: str, solo_360: bool,
) -> list[sqlite3.Row]:
    """Videos candidatos según el modo y filtro."""
    conn.row_factory = sqlite3.Row
    tipo_filter = "subtype='360'" if solo_360 else "type='video'"
    base = f"""
        SELECT id, filename_original, timestamp_utc, end_time, duration_secs,
               latitude, longitude, altitude, geolocation_source
        FROM media
        WHERE {tipo_filter} AND timestamp_utc IS NOT NULL
    """
    if mode == "skip":
        base += (
            " AND (latitude IS NULL OR geolocation_source != 'track_interpolado')"
            " AND id NOT IN ("
            "   SELECT media_id FROM media_metadata WHERE key = ?"
            ")"
        )
        return conn.execute(base, (KEY_ESTADO,)).fetchall()
    return conn.execute(base).fetchall()


def _marcar_estado(conn: sqlite3.Connection, media_id: int, estado: str) -> None:
    """Registra el estado en media_metadata (INSERT OR REPLACE)."""
    conn.execute(
        "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
        (media_id, KEY_ESTADO, estado),
    )


def _escribir_keypoints(
    conn: sqlite3.Connection,
    media_id: int,
    inicio: datetime,
    filas: list[dict],
    gaps: list[dict],
    dry_run: bool,
) -> int:
    """
    Escribe keypoints + gaps para un video. Retorna cantidad de keypoints escritos.
    """
    if dry_run:
        return len(filas)

    for f in filas:
        ts_abs = (inicio + timedelta(seconds=f["offset_s"])).isoformat()
        value = _formatear_ubicacion(f["lat"], f["lon"], f["ele"])
        conn.execute(
            """INSERT INTO media_keypoints
               (media_id, timestamp_offset_secs, timestamp_absolute, key, value, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (media_id, f["offset_s"], ts_abs, KEY_UBICACION, value, SOURCE_TRACK),
        )

    if gaps:
        conn.execute(
            "INSERT OR REPLACE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
            (media_id, KEY_GAPS, json.dumps(gaps, ensure_ascii=False)),
        )

    return len(filas)


def procesar_conexion(
    conn: sqlite3.Connection,
    mode: str = "skip",
    intervalo_s: int = 30,
    umbral_movimiento: float = 5.0,
    distancia_minima: float = 100.0,
    umbral_gap: float = 600.0,
    solo_360: bool = False,
    sobrescribir_gps: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Pipeline sobre una conexión abierta (testeable con DB temporal).

    Returns:
        dict con estadísticas del procesamiento.
    """
    stats = {
        "videos": 0,
        "con_ubicacion": 0,
        "fuera_rango": 0,
        "sin_track": 0,
        "sin_datos": 0,
        "keypoints": 0,
        "gaps": 0,
        "gps_sobrescritos": 0,
        "dry_run": dry_run,
    }

    # Modo replace: limpiar TODO primero
    if mode == "replace" and not dry_run:
        conn.execute("DELETE FROM media_keypoints WHERE key = ?", (KEY_UBICACION,))
        conn.execute(
            "DELETE FROM media_metadata WHERE key IN (?, ?)",
            (KEY_ESTADO, KEY_GAPS),
        )
        conn.commit()

    # Cargar tracks
    tracks = _obtener_tracks_gpx(conn)
    if not tracks:
        log.warning("Sin track GPX: no se puede interpolar posición.")
        # Marcar todos los candidatos como sin_track
        candidatos = _query_candidatos(conn, mode, solo_360)
        for row in candidatos:
            if not dry_run:
                _marcar_estado(conn, row["id"], ESTADO_SIN_TRACK)
        if not dry_run and candidatos:
            conn.commit()
        stats["videos"] = len(candidatos)
        stats["sin_track"] = len(candidatos)
        return stats

    log.info("Tracks GPX disponibles: %d", len(tracks))

    # Consultar candidatos
    candidatos = _query_candidatos(conn, mode, solo_360)
    stats["videos"] = len(candidatos)
    log.info("Videos candidatos (%s, %s): %d", "360" if solo_360 else "todos", mode, len(candidatos))

    for row in candidatos:
        mid = row["id"]
        titulo = row["filename_original"] or f"#{mid}"

        # Parsear intervalo del video
        inicio = _normalizar_dt(row["timestamp_utc"])
        if inicio is None:
            stats["sin_datos"] += 1
            log.info("  [%s] %-55s — timestamp_utc inválido", mid, titulo)
            if not dry_run:
                _marcar_estado(conn, mid, ESTADO_SIN_DATOS)
            continue

        fin = _normalizar_dt(row["end_time"]) if row["end_time"] else None
        if fin is None and row["duration_secs"]:
            fin = inicio + timedelta(seconds=row["duration_secs"])
        if fin is None or fin < inicio:
            fin = inicio

        duracion_secs = (fin - inicio).total_seconds()

        # Verificar si tiene GPS propio
        tiene_gps = row["latitude"] is not None and row["longitude"] is not None
        gps_es_track = (row["geolocation_source"] or "") == "track_interpolado"

        # En skip: si tiene GPS propio (no de este script), saltar
        if mode == "skip" and tiene_gps and not gps_es_track and not sobrescribir_gps:
            stats["gps_sobrescritos"] += 1  # reusar contador para "saltados"
            log.info("  [%s] %-55s — GPS propio, skip (no sobrescribir)", mid, titulo)
            continue

        # Elegir track
        track = _elegir_track(tracks, inicio, fin)
        if track is None:
            stats["sin_track"] += 1
            log.info("  [%s] %-55s — sin track que cubra %s → %s",
                     mid, titulo,
                     inicio.isoformat(), fin.isoformat())
            if not dry_run:
                _marcar_estado(conn, mid, ESTADO_SIN_TRACK)
            continue

        if track["modo"] != "contiene":
            log.warning(
                "  [%s] track '%s' no contiene el video (modo=%s, gap=%.0fs)",
                mid, track["name"], track["modo"], track["gap_s"],
            )

        # Verificar rango: ¿el video tiene ALGUNA parte dentro del track?
        video_antes_track = fin < track["start_dt"]
        video_despues_track = inicio > track["end_dt"]

        if video_antes_track or video_despues_track:
            stats["fuera_rango"] += 1
            log.info("  [%s] %-55s — fuera de rango GPX (track %s: %s → %s)",
                     mid, titulo, track["name"],
                     track["start_dt"].isoformat(), track["end_dt"].isoformat())
            if not dry_run:
                _marcar_estado(conn, mid, ESTADO_FUERA_RANGO)
            continue

        # Calcular porción dentro del rango (para videos parcialmente solapados)
        inicio_efectivo = max(inicio, track["start_dt"])
        fin_efectivo = min(fin, track["end_dt"])

        if inicio < track["start_dt"] or fin > track["end_dt"]:
            log.warning(
                "  [%s] video parcialmente fuera de rango: interpolando solo "
                "%s → %s (video: %s → %s)",
                mid, inicio_efectivo.isoformat(), fin_efectivo.isoformat(),
                inicio.isoformat(), fin.isoformat(),
            )

        # Generar ubicaciones
        filas, gaps = _generar_ubicaciones(
            track["puntos_tiempo"],
            inicio_efectivo,
            fin_efectivo,
            duracion_secs,
            intervalo_s,
            umbral_movimiento,
            distancia_minima,
            umbral_gap,
        )

        if not filas:
            stats["sin_datos"] += 1
            log.info("  [%s] %-55s — sin interpolaciones válidas", mid, titulo)
            if not dry_run:
                _marcar_estado(conn, mid, ESTADO_SIN_DATOS)
                # Si el GPS previo vino del track (posible posición falsa por gap),
                # limpiarlo en update/replace para que el mapa no muestre un punto ficticio.
                if mode in ("update", "replace") and gps_es_track:
                    conn.execute(
                        "UPDATE media SET latitude=NULL, longitude=NULL, altitude=NULL, "
                        "geolocation_source=NULL, updated_at=datetime('now') WHERE id=?",
                        (mid,),
                    )
                    log.info("  [%s] GPS previo de track limpiado (sin cobertura real)", mid)
            continue

        # Empezar la primera fila desde offset 0 del video (no desde inicio_efectivo)
        # El offset debe ser relativo al INICIO del video
        if filas[0]["offset_s"] > 0 and inicio_efectivo > inicio:
            # Recalcular offset de todas las filas relativo al inicio del video
            delta_s = (inicio_efectivo - inicio).total_seconds()
            for f in filas:
                f["offset_s"] = round(f["offset_s"] + delta_s, 1)
                f["dt"] = inicio + timedelta(seconds=f["offset_s"])

        stats["con_ubicacion"] += 1
        stats["keypoints"] += len(filas)
        stats["gaps"] += len(gaps)

        # Log resumen
        primera = filas[0]
        ultima = filas[-1]
        log.info(
            "  [%s] %-55s — %d ubicaciones, %.0fs → %.0fs | "
            "track '%s' (%s) | gaps: %d",
            mid, titulo, len(filas),
            primera["offset_s"], ultima["offset_s"],
            track["name"], track["modo"], len(gaps),
        )

        if dry_run:
            continue

        # Escritura en DB
        # update/replace: borrar keypoints previos de este medio
        if mode in ("update", "replace"):
            conn.execute(
                "DELETE FROM media_keypoints WHERE media_id = ? AND key = ?",
                (mid, KEY_UBICACION),
            )

        # Escribir keypoints
        _escribir_keypoints(conn, mid, inicio, filas, gaps, dry_run)

        # Escribir media columns (posición al inicio del video)
        lat_inicio = filas[0]["lat"]
        lon_inicio = filas[0]["lon"]
        ele_inicio = filas[0]["ele"]

        # Solo escribir si el video empieza dentro del rango y no tiene GPS propio
        # (o si sobrescribir_gps está activo)
        escribir_inicio = inicio >= track["start_dt"]
        if escribir_inicio and (not tiene_gps or gps_es_track or sobrescribir_gps):
            conn.execute(
                "UPDATE media SET latitude=?, longitude=?, altitude=?, "
                "geolocation_source=?, updated_at=datetime('now') WHERE id=?",
                (lat_inicio, lon_inicio, ele_inicio, SOURCE_TRACK, mid),
            )
            log.debug("  media %d: lat=%.6f lon=%.6f ele=%s",
                      mid, lat_inicio, lon_inicio,
                      f"{ele_inicio:.1f}" if ele_inicio is not None else "NULL")
        elif tiene_gps and not sobrescribir_gps:
            stats["gps_sobrescritos"] += 1
            log.info("  [%s] GPS propio preservado (no sobrescribir)", mid)

        # Marcar sentinels
        _marcar_estado(conn, mid, ESTADO_OK)

    if not dry_run:
        conn.commit()

    return stats


def procesar(db_path: str, **kwargs) -> dict:
    """Abre la DB real y ejecuta el pipeline."""
    conn = abrir(resolver_db(db_path))
    try:
        return procesar_conexion(conn, **kwargs)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def crear_parser() -> argparse.ArgumentParser:
    """Parser de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Ubica videos interpolando su intervalo temporal contra el track "
            "GPX del viaje. Genera ubicacion_video keypoints en media_keypoints "
            "y posición inicial en media.latitude/longitude."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/ubicar_videos_gpx.py --solo-360 --dry-run
  python scripts/ubicar_videos_gpx.py --solo-360
  python scripts/ubicar_videos_gpx.py --mode update --verbose
        """,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos")
    parser.add_argument(
        "--mode", choices=["skip", "update", "replace"], default="skip",
        help="skip: solo pendientes (default) | update: reprocesa todos | replace: limpia y regenera",
    )
    parser.add_argument(
        "--intervalo", type=int, default=30,
        help="Muestreo fino en segundos (default 30)",
    )
    parser.add_argument(
        "--umbral-movimiento", type=float, default=5.0,
        help="Velocidad mínima para emitir nueva ubicación, en km/h (default 5)",
    )
    parser.add_argument(
        "--distancia-minima", type=float, default=100.0,
        help="Distancia mínima entre ubicaciones emitidas, en metros (default 100)",
    )
    parser.add_argument(
        "--umbral-gap", type=float, default=1800.0,
        help="Gap GPX máximo antes de NO emitir posición, en segundos (default 1800 = 30 min)",
    )
    parser.add_argument(
        "--solo-360", action="store_true",
        help="Restringir a videos subtype='360' (default: todos los type='video')",
    )
    parser.add_argument(
        "--sobrescribir-gps", action="store_true",
        help="Sobrescribir lat/lon de videos que ya tienen GPS propio",
    )
    parser.add_argument("--dry-run", action="store_true", help="Previsualizar sin escribir en la DB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Logging detallado")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point del script (ejecutable standalone o desde flujos.py)."""
    args = crear_parser().parse_args(argv)

    # Limpiar handlers previos: importar ingest_gpx puede haber llamado
    # logging.basicConfig a nivel de módulo.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    log.info("=== UBICACIÓN DE VIDEOS POR TRACK GPX ===")
    log.info(
        "Modo: %s | intervalo: %ds | umbral-mov: %.1f km/h | "
        "dist-min: %.0f m | gap: %.0f s",
        args.mode, args.intervalo, args.umbral_movimiento,
        args.distancia_minima, args.umbral_gap,
    )
    log.info(
        "Filtro: %s | sobrescribir-gps: %s",
        "360" if args.solo_360 else "todos", args.sobrescribir_gps,
    )
    if args.dry_run:
        log.info("=== DRY RUN — no se escribirá en la DB ===")

    stats = procesar(
        args.db,
        mode=args.mode,
        intervalo_s=args.intervalo,
        umbral_movimiento=args.umbral_movimiento,
        distancia_minima=args.distancia_minima,
        umbral_gap=args.umbral_gap,
        solo_360=args.solo_360,
        sobrescribir_gps=args.sobrescribir_gps,
        dry_run=args.dry_run,
    )

    log.info("")
    log.info("Resumen: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
