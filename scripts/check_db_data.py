#!/usr/bin/env python3
"""check_db_data.py — Métricas de clima, día de semana y geocodificación en la DB.

Uso:
    python scripts/check_db_data.py                        # DB default
    python scripts/check_db_data.py --db db/flujos.db
    python scripts/check_db_data.py --limit 20
    # Importable:
    from scripts.check_db_data import main; main(["--db","db/flujos.db"])
"""
import argparse
import os
import sqlite3
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _resolver_db(db_path: str | None) -> str:
    try:
        from db.util import resolver_db
        return resolver_db(db_path)
    except Exception:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "flujos.db")


def run_checks(conn: sqlite3.Connection, limit: int = 10) -> None:
    pass  # compat: no imprimir objeto conexión

    # ========== WEATHER ==========
    print("=" * 60)
    print("DATOS CLIMATICOS (media_metadata)")
    print("=" * 60)

    print("\nCantidad por tipo de dato:")
    for r in conn.execute(
        "SELECT key, COUNT(*) FROM media_metadata "
        "WHERE key LIKE 'weather_%' GROUP BY key ORDER BY key"
    ):
        print(f"  {r[0]:25s} {r[1]}")

    w = conn.execute(
        "SELECT COUNT(DISTINCT media_id) FROM media_metadata WHERE key LIKE 'weather_%'"
    ).fetchone()[0]
    t = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    print(f"\n  Medios con datos climaticos: {w} de {t}")

    print(f"\nMuestras (primeras {limit}):")
    for r in conn.execute(
        "SELECT media_id, key, value FROM media_metadata "
        "WHERE key LIKE 'weather_%' ORDER BY media_id, key LIMIT ?",
        (limit,),
    ):
        print(f"  #{r[0]}  {r[1]:25s} = {r[2]}")

    # ========== DAY OF WEEK ==========
    print()
    print("=" * 60)
    print("DIA DE LA SEMANA")
    print("=" * 60)
    for r in conn.execute(
        "SELECT value, COUNT(*) FROM media_metadata "
        "WHERE key = 'dia_semana' GROUP BY value ORDER BY value"
    ):
        print(f"  {r[0]:10s} {r[1]}")

    # ========== GEO ==========
    print()
    print("=" * 60)
    print("PROVINCIA / MUNICIPIO / LOCALIDAD")
    print("=" * 60)

    g = conn.execute(
        "SELECT COUNT(provincia), COUNT(municipio), COUNT(localidad), "
        "COUNT(DISTINCT provincia), COUNT(DISTINCT municipio), "
        "COUNT(DISTINCT localidad) FROM media"
    ).fetchone()
    print(f"\n  Con provincia:  {g[0]:5d}  ({g[3]} distinct)")
    print(f"  Con municipio:  {g[1]:5d}  ({g[4]} distinct)")
    print(f"  Con localidad:  {g[2]:5d}  ({g[5]} distinct)")

    print(f"\nMuestras (con provincia, límite {limit}):")
    for r in conn.execute(
        "SELECT id, provincia, municipio, localidad, geocode_source "
        "FROM media WHERE provincia IS NOT NULL LIMIT ?",
        (limit,),
    ):
        print(f"  #{r[0]:5d}  prov={r[1]:15s}  mun={r[2] or '-':15s}  loc={r[3] or '-':10s}  src={r[4]}")

    print(f"\nMuestras (SIN provincia, con GPS — límite {limit}):")
    for r in conn.execute(
        "SELECT id, latitude, longitude FROM media "
        "WHERE latitude IS NOT NULL AND provincia IS NULL LIMIT ?",
        (limit,),
    ):
        print(f"  #{r[0]:5d}  GPS=({r[1]:.4f}, {r[2]:.4f})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Métricas de clima, día y geocode en la DB.")
    parser.add_argument("--db", default=None, help="Ruta a la DB (default: db/flujos.db)")
    parser.add_argument("--limit", type=int, default=10, help="Muestras a mostrar (default: 10, máx 50)")
    args = parser.parse_args(argv)

    limit = max(1, min(int(args.limit), 50))
    db_path = _resolver_db(args.db)

    if not os.path.isfile(db_path):
        print(f"DB no encontrada: {db_path}")
        sys.exit(1)

    print(f"DB: {db_path}\n")
    conn = sqlite3.connect(db_path)
    try:
        run_checks(conn, limit=limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
