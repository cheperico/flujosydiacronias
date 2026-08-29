#!/usr/bin/env python3
"""
check_db.py - Inspecciona la base de datos de Flujos.
Muestra todos los registros, metadatos y totales.

Uso:
    python scripts/check_db.py                        # DB default
    python scripts/check_db.py --db db/flujos.db      # DB personalizada
    python scripts/check_db.py --verbose               # Más detalle
"""

import argparse
import sqlite3
import os
import sys


def print_media(conn, verbose: bool, limit: int = 40):
    """Muestra los registros de media (paginado, streaming)."""
    print(f"=== MEDIA (primeros {limit}) ===")
    if verbose:
        cols = ["id", "filename_original", "type", "subtype", "carpeta",
                "timestamp_utc", "duration_secs", "end_time",
                "latitude", "longitude", "author",
                "color_1_name_basic", "ingest_batch_id"]
        query = f"SELECT {', '.join(cols)} FROM media ORDER BY id LIMIT ?"
    else:
        query = """
            SELECT id, filename_original, type, sidecar_parsed,
                   timestamp_utc, timezone_note
            FROM media ORDER BY id LIMIT ?
        """
    cursor = conn.execute(query, (limit,))
    rows = cursor.fetchall()
    if not rows:
        print("  (vacío)")
        return
    for row in rows:
        if verbose:
            vals = [str(v)[:30] if v else "-" for v in row]
            print(f"  id={row[0]:6d} | {' | '.join(vals[1:])}")
        else:
            ts = row[4] or "-"
            print(f"  id={row[0]:6d} | {row[1]:35s} | {row[2]:6s} | ts_utc={ts}")
    # Avisar si hay más
    try:
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        if total > limit:
            print(f"  ... ({total - limit} más, use --limit {total} para ver todos)")
    except Exception:
        pass


def print_metadata(conn, limit: int = 40):
    """Muestra los primeros N registros de media_metadata."""
    print()
    print(f"=== MEDIA_METADATA (primeros {limit}) ===")
    cursor = conn.execute("""
        SELECT m.id, m.filename_original, mm.key, mm.value
        FROM media_metadata mm
        JOIN media m ON m.id = mm.media_id
        ORDER BY m.id, mm.key
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    if not rows:
        print("  (vacío)")
        return
    for row in rows:
        val = str(row[3])[:80]
        print(f"  id={row[0]:6d} | {row[1]:30s} | {row[2]:45s} = {val}")


def print_keypoints(conn, limit: int = 20):
    """Muestra los primeros N keypoints."""
    print()
    print(f"=== MEDIA_KEYPOINTS (primeros {limit}) ===")
    cursor = conn.execute("""
        SELECT kp.id, m.filename_original, kp.timestamp_offset_secs,
               kp.key, substr(kp.value, 1, 60) AS val_preview
        FROM media_keypoints kp
        JOIN media m ON m.id = kp.media_id
        ORDER BY kp.id
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    if not rows:
        print("  (vacío)")
        return
    for row in rows:
        print(f"  #{row[0]:6d} | {row[1]:30s} | +{row[2]:8.1f}s | {row[3]:15s} | {row[4]}")


def print_totals(conn):
    """Muestra totales por tabla (DRY: db.util.obtener_resumen)."""
    print()
    print("=== TOTALES ===")
    try:
        from db.util import obtener_resumen
        resumen = obtener_resumen(conn)
        for table, cnt in resumen.items():
            if cnt >= 0:
                print(f"  {table:20s} {cnt:>8d}")
            else:
                print(f"  {table:20s}   (no existe)")
        # Mostrar también por tipo
        from db.util import resumen_por_tipo
        por_tipo = resumen_por_tipo(conn)
        if por_tipo:
            print()
            print("  Por tipo:")
            for k, v in por_tipo.items():
                if k != "__total__":
                    print(f"    {k:<12s} {v:>6d}")
        return
    except Exception:
        pass
    for table in ["media", "media_metadata", "config", "media_keypoints",
                  "media_embeddings", "tracks", "waypoints",
                  "telegram_chats", "telegram_messages", "telegram_media"]:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:20s} {cnt:>8d}")
        except sqlite3.OperationalError:
            print(f"  {table:20s}   (no existe)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspecciona la base de datos de Flujos",
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Mostrar más columnas de media")
    parser.add_argument("--limit", type=int, default=40,
                        help="Límite de filas a mostrar (default: 40)")
    args = parser.parse_args(argv)

    if args.db:
        db_path = os.path.abspath(args.db)
    else:
        # Usa resolver_db si está disponible (misma resolución que el resto del pipeline)
        try:
            from db.util import resolver_db
            db_path = resolver_db(None)
        except Exception:
            db_path = os.path.join(os.path.dirname(__file__), "..", "db", "flujos.db")

    if not os.path.isfile(db_path):
        print(f"Error: no se encuentra la DB en {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Clamp limit 1..500
    lim = max(1, min(int(args.limit) if args.limit else 40, 500))
    print_media(conn, args.verbose, limit=lim)
    print_metadata(conn, lim)
    print_keypoints(conn, lim)
    print_totals(conn)

    conn.close()


if __name__ == "__main__":
    main()
