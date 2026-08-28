#!/usr/bin/env python3
"""
gradiente.py — Cálculo de pendiente/esfuerzo físico entre puntos GPS consecutivos.

Calcula distancia, cambio de elevación y gradiente porcentual entre pares
consecutivos de medios geolocalizados, ordenados por timestamp.

Columnas que actualiza en la tabla media:
  - distance_from_prev_m    Distancia horizontal Haversine (metros)
  - elevation_gain_m        Cambio de elevación (+ subida, - bajada, metros)
  - gradient_pct            Pendiente porcentual = (elevation / distance) * 100
  - cumul_distance_m        Distancia acumulada desde el inicio del viaje
  - cumul_elevation_gain_m  Ganancia de elevación acumulada (solo subidas)

Uso:
    python scripts/gradiente.py                            # Procesa toda la BD
    python scripts/gradiente.py --db ruta.db                # BD alternativa
    python scripts/gradiente.py --dry-run                   # Previsualizar sin escribir
    python scripts/gradiente.py --verbose                   # Mostrar cada punto
"""

import argparse
import logging
import math
import os
import sqlite3
import sys

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.util import abrir, resolver_db

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gradiente")

# ---------------------------------------------------------------------------
# Columnas que este script gestiona (para auto-migración)
# ---------------------------------------------------------------------------

GRADIENT_COLUMNS = [
    ("distance_from_prev_m", "REAL"),
    ("elevation_gain_m", "REAL"),
    ("gradient_pct", "REAL"),
    ("cumul_distance_m", "REAL"),
    ("cumul_elevation_gain_m", "REAL"),
]

# ---------------------------------------------------------------------------
# Haversine (solo math, sin dependencias externas)
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Distancia en metros entre dos puntos en WGS84 usando la fórmula de Haversine.

    Args:
        lat1, lon1: Coordenadas del punto 1 en grados decimales.
        lat2, lon2: Coordenadas del punto 2 en grados decimales.

    Returns:
        Distancia en metros (float).
    """
    R = 6371000  # radio terrestre medio en metros
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    # Clamp por error de punto flotante (a puede ser > 1.0 por precisión)
    c = 2 * math.asin(math.sqrt(min(a, 1.0)))
    return R * c


# ---------------------------------------------------------------------------
# Migración de DB (self-contained, como en geocode.py)
# ---------------------------------------------------------------------------

def migrar_db(conn: sqlite3.Connection):
    """Agrega las columnas de gradiente si no existen."""
    for col_name, col_type in GRADIENT_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE media ADD COLUMN {col_name} {col_type}")
            log.info("Columna '%s' agregada a media.", col_name)
        except sqlite3.OperationalError:
            pass  # ya existe
    conn.commit()


# ---------------------------------------------------------------------------
# Cálculo de gradientes
# ---------------------------------------------------------------------------

def calcular_gradientes(conn: sqlite3.Connection, dry_run: bool = False,
                        verbose: bool = False, mode: str = "skip") -> dict:
    """
    Calcula distancias, elevaciones y gradientes entre pares GPS consecutivos.

    Procesa todos los medios con latitud/longitud no nula, ordenados por
    timestamp_utc ascendente. Actualiza las columnas de gradiente en la tabla
    media usando transacciones cada 500 registros.

    Args:
        conn: Conexión SQLite a la base de datos.
        dry_run: Si True, solo calcula y muestra, no escribe en DB.
        verbose: Si True, muestra cada punto procesado.
        mode: skip/update → procesa todos (idéntico, intencional: el gradiente
            es determinista y barato de recalcular); replace → limpia primero.

    Returns:
        Dict con estadísticas del proceso.
    """
    # Asegurar que las columnas existan
    if not dry_run:
        migrar_db(conn)

    # Modo replace: limpiar columnas de gradiente antes de recalcular
    if mode == "replace" and not dry_run:
        log.info("Modo replace: limpiando columnas de gradiente...")
        conn.execute("""
            UPDATE media SET
                distance_from_prev_m = NULL,
                elevation_gain_m = NULL,
                gradient_pct = NULL,
                cumul_distance_m = NULL,
                cumul_elevation_gain_m = NULL
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """)
        conn.commit()

    # Obtener medios con GPS y timestamp, ordenados por timestamp
    rows = conn.execute("""
        SELECT id, latitude, longitude, altitude, timestamp_utc
        FROM media
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND timestamp_utc IS NOT NULL
        ORDER BY timestamp_utc ASC
    """).fetchall()

    total_puntos = len(rows)

    stats_base = {
        "total_puntos": total_puntos,
        "pares_procesados": 0,
        "distancia_total_m": 0.0,
        "distancia_total_km": 0.0,
        "ganancia_total_m": 0.0,
        "perdida_total_m": 0.0,
        "neto_elevacion_m": 0.0,
        "sin_altitud": 0,
        "coinciden": 0,
        "actualizados": 0,
        "dry_run": dry_run,
    }

    if total_puntos == 0:
        log.info("No hay medios con coordenadas GPS en la base de datos.")
        return stats_base

    if total_puntos == 1:
        log.info("Solo hay 1 punto con GPS. Se necesita al menos 2 para calcular gradientes.")
        # Actualizar el único punto con valores iniciales
        if not dry_run:
            conn.execute("""
                UPDATE media SET
                    distance_from_prev_m = NULL,
                    elevation_gain_m = NULL,
                    gradient_pct = NULL,
                    cumul_distance_m = 0,
                    cumul_elevation_gain_m = 0
                WHERE id = ?
            """, (rows[0][0],))
            conn.commit()
        return stats_base

    log.info("Procesando %d puntos con GPS ordenados por timestamp...", total_puntos)
    if dry_run:
        log.info("=== MODO DRY-RUN — No se escribirá en la DB ===")

    # Variables de estado
    prev_lat = None
    prev_lon = None
    prev_alt = None
    cumul_dist = 0.0
    cumul_elev_gain = 0.0
    total_dist = 0.0
    total_ganancia = 0.0
    total_perdida = 0.0
    count_pares = 0
    count_sin_altitud = 0
    count_coinciden = 0
    count_actualizados = 0

    # Transacciones: commit cada 500 updates
    COMMIT_INTERVAL = 500

    for i, row in enumerate(rows):
        media_id, lat, lon, alt, ts = row
        label = f"#{media_id}" + (f" [{ts}]" if ts else "")

        if i == 0:
            # Primer punto: sin valor anterior
            if not dry_run:
                conn.execute("""
                    UPDATE media SET
                        distance_from_prev_m = NULL,
                        elevation_gain_m = NULL,
                        gradient_pct = NULL,
                        cumul_distance_m = 0,
                        cumul_elevation_gain_m = 0
                    WHERE id = ?
                """, (media_id,))
                count_actualizados += 1

            if verbose:
                log.info("  [%s] Primer punto — acumulados inicializados a 0", label)

            prev_lat, prev_lon, prev_alt = lat, lon, alt
            continue

        # Calcular entre punto anterior y actual
        distancia = haversine(prev_lat, prev_lon, lat, lon)

        if alt is not None and prev_alt is not None:
            elev_gain = alt - prev_alt
        else:
            elev_gain = None
            count_sin_altitud += 1

        # Gradiente porcentual
        gradient = None
        if distancia > 0 and elev_gain is not None:
            gradient = (elev_gain / distancia) * 100
        elif distancia == 0 and elev_gain is not None:
            gradient = 0.0  # mismo punto, pendiente cero
            count_coinciden += 1
        elif distancia == 0:
            gradient = None

        # Acumulados
        cumul_dist += distancia
        total_dist += distancia

        if elev_gain is not None:
            if elev_gain > 0:
                cumul_elev_gain += elev_gain
                total_ganancia += elev_gain
            else:
                total_perdida += abs(elev_gain)

        # Actualizar DB
        if not dry_run:
            conn.execute("""
                UPDATE media SET
                    distance_from_prev_m = ?,
                    elevation_gain_m = ?,
                    gradient_pct = ?,
                    cumul_distance_m = ?,
                    cumul_elevation_gain_m = ?
                WHERE id = ?
            """, (
                round(distancia, 2) if distancia is not None else None,
                round(elev_gain, 2) if elev_gain is not None else None,
                round(gradient, 4) if gradient is not None else None,
                round(cumul_dist, 2),
                round(cumul_elev_gain, 2),
                media_id,
            ))
            count_actualizados += 1

            # Commit periódico
            if count_actualizados % COMMIT_INTERVAL == 0:
                conn.commit()

        if verbose:
            dist_km = distancia / 1000
            grad_str = f"{gradient:.2f}%" if gradient is not None else "N/A"
            elev_str = f"{elev_gain:+.1f}m" if elev_gain is not None else "N/A"
            alt_str = f"{alt}m" if alt is not None else "N/A"
            log.info(
                "  [%s] → dist: %.1fm (%.3fkm) | elev: %s | grad: %s | "
                "cumul_dist: %.1fkm | cumul_ganancia: %.1fm",
                label, distancia, dist_km, elev_str, grad_str,
                cumul_dist / 1000, cumul_elev_gain,
            )

        prev_lat, prev_lon, prev_alt = lat, lon, alt
        count_pares += 1

    # Commit final
    if not dry_run and count_actualizados > 0:
        conn.commit()

    # Estadísticas
    stats = {
        "total_puntos": total_puntos,
        "pares_procesados": count_pares,
        "distancia_total_m": round(total_dist, 2),
        "distancia_total_km": round(total_dist / 1000, 3),
        "ganancia_total_m": round(total_ganancia, 2),
        "perdida_total_m": round(total_perdida, 2),
        "neto_elevacion_m": round(total_ganancia - total_perdida, 2),
        "sin_altitud": count_sin_altitud,
        "coinciden": count_coinciden,
        "actualizados": count_actualizados,
        "dry_run": dry_run,
    }

    return stats


# ---------------------------------------------------------------------------
# Mostrar resumen
# ---------------------------------------------------------------------------

def mostrar_resumen(stats: dict):
    """Muestra un resumen formateado de los resultados del cálculo."""
    dry_run = stats.get("dry_run", False)
    modo = " [DRY-RUN — solo previsualización]" if dry_run else ""

    log.info("")
    log.info("=" * 60)
    log.info("  RESUMEN DE GRADIENTES%s", modo)
    log.info("=" * 60)
    log.info("  Puntos con GPS:              %s", f"{stats['total_puntos']:,}")
    log.info("  Pares procesados:            %s", f"{stats['pares_procesados']:,}")
    log.info("  Distancia total:             %.3f km", stats["distancia_total_km"])
    log.info("  Ganancia elevación total:    %.1f m", stats["ganancia_total_m"])
    log.info("  Pérdida elevación total:     %.1f m", stats["perdida_total_m"])
    log.info("  Neto elevación:              %+.1f m", stats["neto_elevacion_m"])

    if stats.get("coinciden"):
        log.info("  Pares en mismo punto:        %s", stats["coinciden"])
    if stats.get("sin_altitud"):
        log.info("  Pares sin altitud en ambos:  %s", stats["sin_altitud"])
    if not dry_run:
        log.info("  Registros actualizados:      %s", f"{stats['actualizados']:,}")
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Calcula pendientes y esfuerzo físico entre puntos GPS consecutivos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/gradiente.py                          # Procesa toda la BD
  python scripts/gradiente.py --db db/flujos.db         # BD explícita
  python scripts/gradiente.py --dry-run                 # Previsualizar sin escribir
  python scripts/gradiente.py --verbose                 # Mostrar cada punto
  python scripts/gradiente.py --mode replace            # Limpiar y recalcular
  python scripts/gradiente.py --db ruta.db --dry-run --verbose
        """,
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a la base de datos SQLite (default: ./db/flujos.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo calcula y muestra resultados, no escribe en DB",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Muestra cada punto procesado en detalle",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suprime logs intermedios (solo muestra resumen final)",
    )
    parser.add_argument(
        "--mode", default="skip", choices=["skip", "update", "replace"],
        help="Modo: skip/update (procesa todos los puntos), replace (limpia y recalcula)",
    )

    args = parser.parse_args(argv)

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    # Resolver ruta de DB
    db_path = resolver_db(args.db)

    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        log.error("Usá --db para especificar una ruta alternativa.")
        sys.exit(1)

    log.info("Base de datos: %s", db_path)

    # Conectar a la DB
    try:
        conn = abrir(db_path)
    except FileNotFoundError:
        log.error("Base de datos no encontrada: %s", db_path)
        log.error("Usá --db para especificar una ruta alternativa.")
        sys.exit(1)

    try:
        # Verificar que la tabla media existe
        try:
            conn.execute("SELECT COUNT(*) FROM media").fetchone()
        except sqlite3.OperationalError as e:
            log.error("La tabla 'media' no existe en la DB: %s", e)
            sys.exit(1)

        # Calcular
        stats = calcular_gradientes(conn, dry_run=args.dry_run, verbose=args.verbose, mode=args.mode)

        # Mostrar resumen
        mostrar_resumen(stats)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
