#!/usr/bin/env python3
"""
relocate.py - Actualiza las rutas absolutas de los medios en la DB
cuando los archivos se mudan de ubicación.

Uso:
    # Ver qué cambiaría (sin escribir)
    python scripts/relocate.py --new-root E:/Medios --dry-run

    # Ejecutar el cambio
    python scripts/relocate.py --new-root E:/Medios

    # Especificar old-root si no está en DB o se quiere otra base
    python scripts/relocate.py --old-root D:/Flujos --new-root E:/Medios

    # Usar otra DB
    python scripts/relocate.py --new-root E:/Medios --db otra.db
"""

import argparse
import os
import sqlite3
import sys

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.util import abrir, resolver_db, normalizar_ruta, sidecar_abs, calcular_nueva_ruta, _ruta_es_prefijo


def get_ingest_root(conn) -> str | None:
    """Lee el ingest_root guardado en la tabla config."""
    cursor = conn.execute("SELECT value FROM config WHERE key = 'ingest_root'")
    row = cursor.fetchone()
    return row[0] if row else None


def normalize_path(p: str) -> str:
    """Normaliza separadores y elimina slash final (delegado a db.util)."""
    return normalizar_ruta(p)


def _sidecar_abs(sc_rel: str, old_root: str) -> str:
    """Convierte sidecar_xml (relativo a ingest_root) a ruta absoluta (delegado a db.util)."""
    return sidecar_abs(sc_rel, old_root)


def preview_changes(conn, old_root: str, new_root: str):
    """Muestra los cambios que se harían, sin escribirlos."""
    old_norm = normalize_path(old_root)
    new_norm = normalize_path(new_root)

    cursor = conn.execute(
        "SELECT id, filename_original, filepath_absoluto, filepath_relativo FROM media ORDER BY id"
    )
    rows = cursor.fetchall()

    cambios = 0
    for row in rows:
        mid, fname, abs_path, rel_path = row
        if _ruta_es_prefijo(abs_path, old_norm):
            new_abs = calcular_nueva_ruta(abs_path, old_norm, new_norm)
            print(f"  #{mid} {fname}")
            print(f"    {abs_path}")
            print(f"    -> {new_abs}")
            cambios += 1
        else:
            print(f"  #{mid} {fname}  [NO CAMBIA - no arranca con old_root]")

    # sidecar_xml: se guarda como relativo a ingest_root -> convertir a abs
    cursor2 = conn.execute(
        "SELECT id, filename_original, sidecar_xml FROM media WHERE sidecar_xml IS NOT NULL"
    )
    rows2 = cursor2.fetchall()
    cambios_sc = 0
    for row in rows2:
        mid, fname, sc_rel = row
        sc_abs = _sidecar_abs(sc_rel, old_norm)
        if _ruta_es_prefijo(sc_abs, old_norm):
            new_sc_abs = calcular_nueva_ruta(sc_abs, old_norm, new_norm)
            # Guardar como relativo al nuevo root (mismo criterio que ingest.py)
            new_sc_rel = os.path.relpath(new_sc_abs, new_norm)
            print(f"  #{mid} {fname} [sidecar]")
            print(f"    abs: {sc_abs}")
            print(f"    -> rel: {new_sc_rel}")
            cambios_sc += 1

    print(f"\nResumen: {cambios} rutas absolutas, {cambios_sc} sidecars cambiarían.")
    return cambios > 0 or cambios_sc > 0


def apply_changes(conn, old_root: str, new_root: str):
    """Ejecuta los cambios en la DB (prefijo seguro con separador)."""
    old_norm = normalize_path(old_root)
    new_norm = normalize_path(new_root)

    # Actualizar filepath_absoluto con verificación Python segura (evita C:/Medios → C:/Medios2)
    rows = conn.execute("SELECT id, filepath_absoluto FROM media").fetchall()
    cambios = 0
    for mid, abs_path in rows:
        if _ruta_es_prefijo(abs_path, old_norm):
            new_abs = calcular_nueva_ruta(abs_path, old_norm, new_norm)
            conn.execute(
                "UPDATE media SET filepath_absoluto = ?, updated_at = datetime('now') WHERE id = ?",
                (new_abs, mid),
            )
            cambios += 1

    # Actualizar sidecar_xml (relativo a ingest_root)
    sidecar_rows = conn.execute(
        "SELECT id, sidecar_xml FROM media WHERE sidecar_xml IS NOT NULL"
    ).fetchall()
    cambios_sc = 0
    for mid, sc_rel in sidecar_rows:
        sc_abs = _sidecar_abs(sc_rel, old_norm)
        if _ruta_es_prefijo(sc_abs, old_norm):
            new_sc_abs = calcular_nueva_ruta(sc_abs, old_norm, new_norm)
            new_sc_rel = os.path.relpath(new_sc_abs, new_norm)
            conn.execute(
                "UPDATE media SET sidecar_xml = ?, updated_at = datetime('now') WHERE id = ?",
                (new_sc_rel, mid),
            )
            cambios_sc += 1

    # Actualizar ingest_root en config
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('ingest_root', ?)",
        (new_norm,),
    )

    conn.commit()
    return cambios, cambios_sc


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Actualiza rutas absolutas de medios en la DB cuando los archivos se mudan."
    )
    parser.add_argument(
        "--new-root",
        required=True,
        help="Nueva raíz donde están los archivos ahora",
    )
    parser.add_argument(
        "--old-root",
        default=None,
        help="Raíz anterior (si no está guardada en la DB o se quiere otra distinta)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a la base de datos (default: db/flujos.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo mostrar qué cambiaría, no escribir nada",
    )
    args = parser.parse_args(argv)

    # Resolver DB path
    db_path = resolver_db(args.db)

    if not os.path.isfile(db_path):
        print(f"Error: no se encuentra la DB en {db_path}")
        sys.exit(1)

    new_root = normalize_path(args.new_root)

    if not os.path.isdir(new_root):
        print(f"Advertencia: la nueva raíz '{new_root}' no existe o no es un directorio.")

    conn = abrir(db_path)

    # Asegurar que la tabla config exista (DBs creadas antes de Julio 2026)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Obtener old_root
    old_root = args.old_root
    if not old_root:
        old_root = get_ingest_root(conn)
    if not old_root:
        print("Error: no se encontró ingest_root en la DB. Usá --old-root para indicarlo.")
        conn.close()
        sys.exit(1)

    old_root = normalize_path(old_root)
    print(f"Raíz anterior: {old_root}")
    print(f"Raíz nueva:    {new_root}")
    print()

    if old_root == new_root:
        print("Las raíces son iguales. No hay nada que cambiar.")
        conn.close()
        return

    if args.dry_run:
        preview_changes(conn, old_root, new_root)
    else:
        # Backup automático antes de tocar DB
        conn.commit()
        conn.close()
        # Reabrir para backup WAL-safe via utilidad local
        try:
            import sqlite3
            from datetime import datetime
            backup_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"flujos_{ts}__autobackup.db")
            src = sqlite3.connect(db_path)
            try:
                src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            dst = sqlite3.connect(backup_path)
            try:
                src.backup(dst)
                print(f"  ✓ Backup automático: {os.path.basename(backup_path)}")
            finally:
                dst.close()
            src.close()
        except Exception as e:
            print(f"  ⚠ No se pudo crear backup automático: {e}")
        conn = abrir(db_path)
        cambios, cambios_sc = apply_changes(conn, old_root, new_root)
        print(f"Actualizadas {cambios} rutas absolutas y {cambios_sc} sidecars.")
        print(f"ingest_root actualizado a: {new_root}")

    conn.close()


if __name__ == "__main__":
    main()
