#!/usr/bin/env python3
"""
track_gpx.py — Helpers compartidos para trabajar con tracks GPX.

Envuelve la lógica de carga, interpolación y tramos temporales sobre los tracks
registrados en `tracks` (la DB no persiste los puntos: se relee el .gpx original
por ejecución, decisión del plan Fase 4 — ver keypoints_contexto.py).

Funciones:
    cargar_tracks(conn)          → lista de dicts con "name", "puntos_tiempo",
                                   "start_dt", "end_dt"
    puntos_track_con_tiempo(gpx) → [(dt, lat, lon, ele)] ordenados por tiempo
    interpolar_posicion(puntos, t) → (lat, lon, ele) o None si fuera de rango
    tramo_temporal(puntos, inicio, fin) → sublista de puntos dentro de [inicio, fin]
    distancia_haversine(lat1, lon1, lat2, lon2) → metros
    medir_discrepancias(conn, puntos, tolerancia_m) → [(id, filename, distancia_m, ...)]

Uso (desde un script que ya conectó la DB):
    from scripts.track_gpx import cargar_tracks, interpolar_posicion
    tracks = cargar_tracks(conn)
    pos = interpolar_posicion(tracks[0]["puntos_tiempo"], dt)
"""

import bisect
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.gradiente import haversine  # noqa: E402
from scripts.ingest_gpx import parsear_gpx  # noqa: E402

log = logging.getLogger(__name__)


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


def puntos_track_con_tiempo(
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


def cargar_tracks(conn: sqlite3.Connection) -> list[dict]:
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
        puntos = puntos_track_con_tiempo(gpx.get("track_points") or [])
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


def interpolar_lineal(
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


def interpolar_posicion(
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
    return interpolar_lineal(puntos[idx - 1], puntos[idx], t)


def tramo_temporal(
    puntos: list[tuple[datetime, float, float, float | None]],
    inicio: datetime,
    fin: datetime,
) -> list[tuple[datetime, float, float, float | None]]:
    """Sublista de puntos del track dentro de [inicio, fin] (inclusive)."""
    return [p for p in puntos if inicio <= p[0] <= fin]


def distancia_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos coordenadas (Haversine)."""
    return haversine(lat1, lon1, lat2, lon2)


def medir_discrepancias(
    conn: sqlite3.Connection,
    puntos: list[tuple[datetime, float, float, float | None]],
    tolerancia_m: int = 1000,
) -> list[dict]:
    """
    Compara el GPS embebido de los medios (geolocation_source IN metadata/manual)
    contra la posición interpolada del track en su timestamp.

    Returns:
        Lista de dicts {id, filename, distancia_m, lat_media, lon_media,
        lat_track, lon_track} para los medios cuya distancia al track supera
        la tolerancia (en metros). Solo reporta; no escribe en la DB.
    """
    if not puntos:
        return []

    conn.row_factory = sqlite3.Row
    filas = conn.execute("""
        SELECT id, filename_original, timestamp_utc, latitude, longitude,
               municipio, departamento, provincia
        FROM media
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND timestamp_utc IS NOT NULL
          AND geolocation_source IN ('metadata', 'manual')
        ORDER BY timestamp_utc
    """).fetchall()

    discrepancias: list[dict] = []
    for fila in filas:
        dt = _normalizar_dt(fila["timestamp_utc"])
        if dt is None:
            continue
        pos = interpolar_posicion(puntos, dt)
        if pos is None:
            continue
        lat_t, lon_t, _ = pos
        dist_m = haversine(fila["latitude"], fila["longitude"], lat_t, lon_t)
        if dist_m > tolerancia_m:
            discrepancias.append({
                "id": fila["id"],
                "filename": fila["filename_original"],
                "distancia_m": dist_m,
                "lat_media": fila["latitude"],
                "lon_media": fila["longitude"],
                "lat_track": lat_t,
                "lon_track": lon_t,
                "municipio": fila["municipio"],
                "departamento": fila["departamento"],
                "provincia": fila["provincia"],
                "timestamp": fila["timestamp_utc"],
            })
    return discrepancias


def reportar_discrepancias(
    discrepancias: list[dict],
    tolerancia_m: int = 1000,
) -> None:
    """Imprime el reporte de discrepancias media vs track en el log."""
    if not discrepancias:
        log.info("Discrepancias media vs track: 0 (tolerancia %d m).", tolerancia_m)
        return
    log.warning("Discrepancias media vs track (>%d m): %d",
                tolerancia_m, len(discrepancias))
    for d in discrepancias[:20]:
        log.warning("  id=%d %-40s dist=%.0f m | media=(%.6f,%.6f) track=(%.6f,%.6f) | %s %s %s",
                    d["id"], d["filename"], d["distancia_m"],
                    d["lat_media"], d["lon_media"], d["lat_track"], d["lon_track"],
                    d["provincia"] or "", d["departamento"] or "", d["municipio"] or "")
    if len(discrepancias) > 20:
        log.warning("  ... y %d más.", len(discrepancias) - 20)