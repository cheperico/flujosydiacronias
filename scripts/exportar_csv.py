#!/usr/bin/env python3
"""
exportar_csv.py — Exporta tablas de la base de datos a archivos CSV.

Exporta cada tabla como un archivo CSV separado dentro de un directorio
con timestamp (ej: db/exports/flujos_20260722_181500/).

Tablas exportadas:
  - media              (registros principales)
  - media_metadata     (pares clave-valor por medio)
  - media_keypoints    (transcripciones segmentadas)
  - media_embeddings   (vectores — solo cabecera por tamaño)
  - config             (configuración global)
  - tracks             (tracks GPS)
  - waypoints          (puntos de interés)

Además, genera un archivo `_resumen.txt` con el conteo por tabla.

Uso:
    python scripts/exportar_csv.py                          # exporta todo
    python scripts/exportar_csv.py --table media            # solo una tabla
    python scripts/exportar_csv.py --output ./mis_exports   # directorio custom
    python scripts/exportar_csv.py --db ruta/alternativa.db
"""

import argparse
import csv
import logging
import os
import sqlite3
import sys
from datetime import datetime

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.util import abrir, resolver_db

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("exportar_csv")

# ── Constantes ───────────────────────────────────────────────────────────────

TABLAS_VISIBLES = [
    "media",
    "media_metadata",
    "media_keypoints",
    "media_embeddings",
    "config",
    "tracks",
    "waypoints",
]

TABLAS_QUE_NO_EXPORTAN_EMBEDDINGS = ["media_embeddings"]
"""media_embeddings contiene BLOB binarios; exportamos metadatos sin la columna embedding."""

# Claves de media_metadata cuyo valor es demasiado grande o ruidoso para CSV útil
# (legacy "transcript" nunca ocurre; las reales grandes son whisper_segments/video_analysis)
MEDIA_METADATA_EXCLUIR_VALORES_GRANDES = {
    "whisper_segments", "whisper_info", "video_analysis",
    "ia_sonido_raw", "contenedor_streams", "whisper_estado",
    "transcript", "transcript_segments",  # legacy
}
# Umbral adicional por tamaño (chars) — aunque la key no esté listada, si supera 64k se filtra
MEDIA_METADATA_MAX_CHARS = 64 * 1024


def obtener_resumen(conn: sqlite3.Connection) -> dict[str, int]:
    """Cuenta registros de cada tabla visible (DRY: db.util.obtener_resumen con filtro)."""
    try:
        from db.util import obtener_resumen as _obtener
        full = _obtener(conn)
        # Filtrar solo visibles, mantener orden
        return {t: full.get(t, -1) for t in TABLAS_VISIBLES}
    except Exception:
        resumen: dict[str, int] = {}
        for tabla in TABLAS_VISIBLES:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
                resumen[tabla] = count
            except sqlite3.OperationalError:
                resumen[tabla] = -1
        return resumen


def _tiene_columna_id(conn: sqlite3.Connection, tabla: str) -> bool:
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
        return "id" in cols
    except Exception:
        return False


def exportar_tabla(
    conn: sqlite3.Connection,
    tabla: str,
    directorio: str,
    incluir_encabezado: bool = True,
) -> str | None:
    """
    Exporta una tabla completa a CSV (keyset pagination si hay id, sino OFFSET; quoting Excel-safe).
    Para media_metadata filtra claves grandes y por tamaño LENGTH(value) > 64k.
    """
    try:
        cursor = conn.execute(f"SELECT * FROM {tabla} LIMIT 0")
        columnas = [desc[0] for desc in cursor.description]
    except sqlite3.OperationalError:
        log.warning("  Tabla '%s' no existe. Se omite.", tabla)
        return None

    has_id = _tiene_columna_id(conn, tabla) and tabla not in ("config",)

    if tabla in TABLAS_QUE_NO_EXPORTAN_EMBEDDINGS:
        columnas_sin_blob = [c for c in columnas if c != "embedding"]
        cols_sql = ", ".join(columnas_sin_blob)
        path = os.path.join(directorio, f"{tabla}.csv")
        total = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            if incluir_encabezado:
                writer.writerow(columnas_sin_blob)
            batch_size = 500
            if has_id:
                last_id = 0
                while True:
                    filas = conn.execute(
                        f"SELECT {cols_sql} FROM {tabla} WHERE id > ? ORDER BY id LIMIT ?",
                        (last_id, batch_size),
                    ).fetchall()
                    if not filas:
                        break
                    for fila in filas:
                        writer.writerow([str(v) if v is not None else "" for v in fila])
                    total += len(filas)
                    try:
                        last_id = filas[-1][0]
                    except Exception:
                        break
            else:
                offset = 0
                while True:
                    filas = conn.execute(
                        f"SELECT {cols_sql} FROM {tabla} LIMIT ? OFFSET ?",
                        (batch_size, offset),
                    ).fetchall()
                    if not filas:
                        break
                    for fila in filas:
                        writer.writerow([str(v) if v is not None else "" for v in fila])
                    total += len(filas)
                    offset += batch_size
        log.info("  %s.csv → %d columnas (sin embedding), %d filas", tabla, len(columnas_sin_blob), total)
        return path

    path = os.path.join(directorio, f"{tabla}.csv")
    filtrados = 0

    if tabla == "media_metadata":
        total = 0
        idx_key = columnas.index("key") if "key" in columnas else 2
        idx_val = columnas.index("value") if "value" in columnas else 3
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            if incluir_encabezado:
                writer.writerow(columnas)
            batch_size = 500
            if has_id:
                last_id = 0
                while True:
                    filas = conn.execute(
                        f"SELECT * FROM {tabla} WHERE id > ? ORDER BY id LIMIT ?",
                        (last_id, batch_size),
                    ).fetchall()
                    if not filas:
                        break
                    for fila in filas:
                        key = fila[idx_key]
                        val = fila[idx_val]
                        if key in MEDIA_METADATA_EXCLUIR_VALORES_GRANDES:
                            filtrados += 1
                            continue
                        if val is not None and len(str(val)) > MEDIA_METADATA_MAX_CHARS:
                            filtrados += 1
                            continue
                        writer.writerow([str(v) if v is not None else "" for v in fila])
                        total += 1
                    last_id = filas[-1][0]
            else:
                offset = 0
                while True:
                    filas = conn.execute(
                        f"SELECT * FROM {tabla} LIMIT ? OFFSET ?", (batch_size, offset)
                    ).fetchall()
                    if not filas:
                        break
                    for fila in filas:
                        key = fila[idx_key]
                        val = fila[idx_val]
                        if key in MEDIA_METADATA_EXCLUIR_VALORES_GRANDES:
                            filtrados += 1
                            continue
                        if val is not None and len(str(val)) > MEDIA_METADATA_MAX_CHARS:
                            filtrados += 1
                            continue
                        writer.writerow([str(v) if v is not None else "" for v in fila])
                        total += 1
                    offset += batch_size
        log.info("  %s.csv → %d registros (filtrados %d: claves grandes / >64k)", tabla, total, filtrados)
        return path

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        if incluir_encabezado:
            writer.writerow(columnas)
        batch_size = 500
        total = 0
        if has_id:
            cols_sql = ", ".join(columnas)
            last_id = 0
            while True:
                filas = conn.execute(
                    f"SELECT {cols_sql} FROM {tabla} WHERE id > ? ORDER BY id LIMIT ?",
                    (last_id, batch_size),
                ).fetchall()
                if not filas:
                    break
                for fila in filas:
                    writer.writerow([str(v) if v is not None else "" for v in fila])
                total += len(filas)
                last_id = filas[-1][0]
        else:
            offset = 0
            while True:
                filas = conn.execute(f"SELECT * FROM {tabla} LIMIT ? OFFSET ?", (batch_size, offset)).fetchall()
                if not filas:
                    break
                for fila in filas:
                    writer.writerow([str(v) if v is not None else "" for v in fila])
                total += len(filas)
                offset += batch_size

    # Alinear _resumen.txt con filas realmente escritas (no con total DB si hubo filtro)
    resumen = obtener_resumen(conn).get(tabla, "?")
    if filtrados:
        log.info("  %s.csv → %d registros (DB %s, filtrados %d), %d columnas", tabla, total, resumen, filtrados, len(columnas))
    else:
        log.info("  %s.csv → %d registros, %d columnas", tabla, total, len(columnas))
    return path


def exportar_todo(
    db_path: str,
    output_dir: str | None = None,
    tablas: list[str] | None = None,
) -> str:
    """
    Exporta las tablas seleccionadas (o todas) a CSV.

    Returns:
        Ruta al directorio de exportación.
    """
    if not os.path.isfile(db_path):
        print(f"  ERROR: No se encuentra la DB: {db_path}")
        sys.exit(1)

    # Conectar
    conn = abrir(db_path)

    # Directorio de salida
    if output_dir is None:
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "exports")
    else:
        base_dir = output_dir

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_name = f"flujos_csv_{ts}"
    directorio = os.path.join(base_dir, dir_name)
    os.makedirs(directorio, exist_ok=True)

    # Determinar tablas a exportar
    if tablas:
        a_exportar = [t for t in TABLAS_VISIBLES if t in tablas]
    else:
        a_exportar = TABLAS_VISIBLES

    # Exportar cada tabla
    exportadas = 0
    omitidas = 0
    for tabla in a_exportar:
        ruta = exportar_tabla(conn, tabla, directorio)
        if ruta:
            exportadas += 1
        else:
            omitidas += 1

    # Resumen
    resumen = obtener_resumen(conn)
    resumen_path = os.path.join(directorio, "_resumen.txt")
    with open(resumen_path, "w", encoding="utf-8") as f:
        f.write(f"Exportacion: {ts}\n")
        f.write(f"Base de datos: {db_path}\n")
        f.write(f"Directorio: {directorio}\n")
        f.write("-" * 50 + "\n")
        for tabla in TABLAS_VISIBLES:
            count = resumen.get(tabla, -1)
            if count >= 0:
                f.write(f"  {tabla:20s}  {count:>8d} registros\n")
            else:
                f.write(f"  {tabla:20s}  (no existe)\n")
        f.write("-" * 50 + "\n")
        total = sum(c for c in resumen.values() if c > 0)
        f.write(f"  {'TOTAL':20s}  {total:>8d} registros\n")

    conn.close()

    print(f"\n  -> Exportación completa: {directorio}")
    print(f"     Tablas exportadas: {exportadas}")
    if omitidas:
        print(f"     Tablas omitidas: {omitidas} (no existen en la DB)")
    return directorio


# ==============================================================================
# Main
# ==============================================================================

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Exporta tablas de la DB a archivos CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/exportar_csv.py                              # exporta todo
  python scripts/exportar_csv.py --table media                # solo media
  python scripts/exportar_csv.py --table media,config         # varias tablas
  python scripts/exportar_csv.py --output ./mis_exports       # directorio custom
        """,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos (default: db/flujos.db)")
    parser.add_argument("--output", "-o", default=None, help="Directorio de salida (default: db/exports/)")
    parser.add_argument("--table", "-t", default=None, help="Tabla(s) a exportar separadas por coma (default: todas)")
    parser.add_argument("--list-tables", action="store_true", help="Listar tablas disponibles y salir")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar qué se exportaría, sin escribir")

    args = parser.parse_args(argv)

    # Resolver DB
    db_path = resolver_db(args.db)
    if db_path is None or not os.path.isfile(db_path):
        print("  No se encontró db/flujos.db. Especificá --db.")
        sys.exit(1)

    # Listar tablas
    if args.list_tables:
        conn = abrir(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        print("Tablas disponibles:")
        for row in cursor:
            print(f"  - {row[0]}")
        conn.close()
        return

    # Resolver tablas
    tablas: list[str] | None = None
    if args.table:
        tablas = [t.strip() for t in args.table.split(",")]

    # Dry-run: solo mostrar resumen
    if args.dry_run:
        conn = abrir(db_path)
        resumen = obtener_resumen(conn)
        conn.close()
        print(f"  DB: {db_path}")
        if tablas:
            print(f"  Tablas seleccionadas: {', '.join(tablas)}")
        else:
            print(f"  Tablas: todas ({len(TABLAS_VISIBLES)} disponibles)")
        print()
        for tabla in TABLAS_VISIBLES:
            count = resumen.get(tabla, -1)
            if count >= 0:
                print(f"  {tabla:20s}  {count:>8d} registros")
            else:
                print(f"  {tabla:20s}  (no existe)")
        print(f"\n  Salida: {args.output or 'db/exports/<timestamp>/'}")
        return

    # Exportar
    exportar_todo(db_path, args.output, tablas)


if __name__ == "__main__":
    main()
