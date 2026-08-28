#!/usr/bin/env python3
"""
check_gps.py - Verifica qué archivos tienen GPS en el sistema de archivos
usando ExifTool. Muestra muestras aleatorias de imágenes sin GPS.

Uso:
    python scripts/check_gps.py                        # DB default
    python scripts/check_gps.py --db db/flujos.db      # DB personalizada
    python scripts/check_gps.py --exiftool "ruta"      # ExifTool personalizado
    python scripts/check_gps.py --samples 10           # Más muestras
"""

import argparse
import sqlite3
import subprocess
import os
import sys


def find_exiftool() -> str | None:
    """Busca ExifTool en ubicaciones comunes."""
    candidates = [
        "C:\\Program Files\\digiKam\\exiftool.exe",
        "C:\\Program Files\\exiftool.exe",
        "exiftool",
    ]
    for c in candidates:
        if c == "exiftool":
            try:
                subprocess.run([c, "-ver"], capture_output=True, timeout=5)
                return c
            except Exception:
                continue
        elif os.path.isfile(c):
            return c
    return None


def check_gps_folder(folder: str, exiftool: str, n: int = 5):
    """Revisa GPS en las primeras n fotos de una carpeta."""
    import glob
    fotos = glob.glob(os.path.join(folder, "*.jpg"))[:n]
    fotos += glob.glob(os.path.join(folder, "*.jpeg"))[:n]
    fotos = fotos[:n]
    if not fotos:
        print(f"  No hay fotos en {folder}")
        return
    for f in fotos:
        cmd = [exiftool, "-j", "-GPSLatitude", "-GPSLongitude",
               "-GPSPosition", "-DateTimeOriginal", "-Model", f]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.stdout:
            has_gps = "GPSLatitude" in r.stdout
            print(f"  {os.path.basename(f):40s} | GPS={'SI' if has_gps else 'NO':3s} | {r.stdout[:120].strip()}")
        else:
            print(f"  {os.path.basename(f):40s} | ERROR")


def check_gps_db(db_path: str, exiftool: str, samples: int = 5):
    """Muestra N imágenes al azar sin GPS en DB y las verifica con ExifTool."""
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT filepath_absoluto FROM media WHERE type='image' AND latitude IS NULL "
        "ORDER BY RANDOM() LIMIT ?", (samples,)
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("  No hay imágenes sin GPS en la DB.")
        return

    print(f"\n  Muestras de imágenes sin GPS en DB ({samples} al azar):")
    print()
    for (fp,) in rows:
        if not os.path.isfile(fp):
            print(f"  [NO EXISTE] {fp}")
            continue
        cmd = [exiftool, "-j", "-GPSLatitude", "-GPSLongitude",
               "-GPSPosition", "-DateTimeOriginal", "-Model", fp]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            has_gps = "GPSLatitude" in (r.stdout or "")
            print(f"  {'SI' if has_gps else 'NO':>3s} GPS | {os.path.basename(fp):40s} | {fp}")
        except Exception as e:
            print(f"  ERR | {os.path.basename(fp):40s} | {e}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verifica qué archivos tienen GPS en el sistema de archivos",
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos")
    parser.add_argument("--exiftool", default=None, help="Ruta a ExifTool")
    parser.add_argument("--samples", type=int, default=5,
                        help="Cantidad de muestras aleatorias (default: 5)")
    parser.add_argument("--folder", default=None,
                        help="Carpeta a inspeccionar directamente (usa check_gps_folder)")
    args = parser.parse_args(argv)

    # ExifTool
    exiftool = args.exiftool or find_exiftool()
    if not exiftool:
        print("Advertencia: ExifTool no encontrado. Solo se mostrarán rutas.")
    else:
        print(f"ExifTool: {exiftool}")

    # Modo carpeta directo
    if args.folder:
        if not os.path.isdir(args.folder):
            print(f"Error: carpeta no existe: {args.folder}")
            sys.exit(1)
        print(f"Carpeta:  {args.folder}")
        check_gps_folder(args.folder, exiftool or "exiftool", args.samples)
        return

    # DB path
    if args.db:
        db_path = os.path.abspath(args.db)
    else:
        try:
            from db.util import resolver_db
            db_path = resolver_db(None)
        except Exception:
            db_path = os.path.join(os.path.dirname(__file__), "..", "db", "flujos.db")

    if not os.path.isfile(db_path):
        print(f"Error: no se encuentra la DB en {db_path}")
        sys.exit(1)

    print(f"DB:       {db_path}")

    check_gps_db(db_path, exiftool, args.samples)


if __name__ == "__main__":
    main()
