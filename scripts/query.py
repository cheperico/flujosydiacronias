#!/usr/bin/env python3
"""
query.py - Consulta y explora la base de datos de medios.

Uso:
    # Listar columnas y keys disponibles
    python scripts/query.py --columns

    # Valores únicos de una columna de media
    python scripts/query.py --distinct type
    python scripts/query.py --distinct author --count

    # Valores únicos de una key en media_metadata
    python scripts/query.py --key iptc_keywords --count

    # Buscar texto en todas las columnas
    python scripts/query.py --search "tucuman"

    # Filtrar por tipo
    python scripts/query.py --distinct author --count --where "type='image'"
"""

import argparse
import re
import sqlite3
import sys
import os
from collections import Counter

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.util import abrir, resolver_db


# ── Validación de seguridad ──────────────────────────────────────────────────

# Palabras prohibidas en --where (previene inyección SQL básica)
_PROHIBIDO_EN_WHERE = re.compile(
    r'\b(DROP|ALTER|DELETE|INSERT|UPDATE|CREATE|ATTACH|DETACH|REINDEX|VACUUM)\b',
    re.IGNORECASE,
)


def _es_columna_valida(conn: sqlite3.Connection, col: str) -> bool:
    """Verifica que col sea una columna real de la tabla media."""
    cursor = conn.execute("PRAGMA table_info(media)")
    columnas = {c["name"] for c in cursor.fetchall()}
    return col in columnas


def _where_seguro(where: str) -> bool:
    """Valida que la condición WHERE no contenga comandos destructivos."""
    if _PROHIBIDO_EN_WHERE.search(where):
        return False
    return True


# ── Conexión ─────────────────────────────────────────────────────────────────


def list_columns(conn):
    """Lista columnas de media y keys de media_metadata."""
    print("=== Columnas en media ===")
    cursor = conn.execute("PRAGMA table_info(media)")
    cols = cursor.fetchall()
    for c in cols:
        pk = " PK" if c["pk"] else ""
        nn = " NOT NULL" if c["notnull"] else ""
        default = f" default={c['dflt_value']}" if c["dflt_value"] else ""
        print(f"  {c['name']:<30s} {c['type']:<8s}{pk}{nn}{default}")

    print()
    print("=== Keys en media_metadata (top 50) ===")
    cursor = conn.execute("""
        SELECT key, COUNT(*) as total
        FROM media_metadata
        GROUP BY key
        ORDER BY total DESC
        LIMIT 50
    """)
    rows = cursor.fetchall()
    if rows:
        for r in rows:
            print(f"  {r['key']:<45s} ({r['total']} registros)")
    else:
        print("  (vacío)")


def distinct_column(conn, col: str, count: bool, where: str | None):
    """Valores únicos de una columna de media."""
    if where:
        if not _where_seguro(where):
            print("Error: la condición --where contiene comandos SQL no permitidos.")
            return
        where_clause = f"WHERE {where}"
    else:
        where_clause = ""

    if count:
        query = f"""
            SELECT {col} AS val, COUNT(*) as total
            FROM media
            {where_clause}
            GROUP BY {col}
            ORDER BY total DESC
        """
        cursor = conn.execute(query)
        rows = cursor.fetchall()
        total = sum(r["total"] for r in rows)
        sep = "-" * 40
        sep2 = "-" * 8
        print(f"  {'Valor':<40s} {'Cantidad':>8s}  %")
        print(f"  {sep} {sep2}  ---")
        for r in rows:
            val = r["val"] or "(NULL)"
            pct = r["total"] / total * 100 if total > 0 else 0
            print(f"  {str(val):<40s} {r['total']:>8d}  {pct:5.1f}%")
        print(f"  {sep} {sep2}")
        print(f"  {'TOTAL':<40s} {total:>8d}")
    else:
        query = f"""
            SELECT DISTINCT {col} AS val
            FROM media
            {where_clause}
            ORDER BY val
        """
        cursor = conn.execute(query)
        rows = cursor.fetchall()
        for r in rows:
            val = r["val"]
            if val is not None:
                print(f"  {val}")
        print(f"  ({len(rows)} valores únicos)")


def distinct_key(conn, key: str, count: bool, where: str | None):
    """Valores únicos de una key en media_metadata."""
    if where:
        where_clause = f"AND ({where})"
    else:
        where_clause = ""

    if count:
        query = f"""
            SELECT mm.value AS val, COUNT(*) as total
            FROM media_metadata mm
            JOIN media m ON m.id = mm.media_id
            WHERE mm.key = ?
            {where_clause}
            GROUP BY mm.value
            ORDER BY total DESC
        """
        cursor = conn.execute(query, (key,))
        rows = cursor.fetchall()
        total = sum(r["total"] for r in rows)
        sep = "-" * 50
        sep2 = "-" * 8
        print(f"  {'Valor':<50s} {'Cantidad':>8s}  %")
        print(f"  {sep} {sep2}  ---")
        for r in rows:
            val = r["val"] or "(NULL)"
            val_str = val[:50] if len(str(val)) > 50 else str(val)
            pct = r["total"] / total * 100 if total > 0 else 0
            print(f"  {val_str:<50s} {r['total']:>8d}  {pct:5.1f}%")
        print(f"  {sep} {sep2}")
        print(f"  {'TOTAL':<50s} {total:>8d}")
    else:
        query = f"""
            SELECT DISTINCT mm.value AS val
            FROM media_metadata mm
            JOIN media m ON m.id = mm.media_id
            WHERE mm.key = ?
            {where_clause}
            ORDER BY val
        """
        cursor = conn.execute(query, (key,))
        rows = cursor.fetchall()
        for r in rows:
            val = r["val"]
            if val is not None:
                print(f"  {val[:100]}")
        print(f"  ({len(rows)} valores únicos)")


def search_text(conn, text: str, limit: int = 30):
    """Busca texto en columnas de media y valores de media_metadata."""
    pattern = f"%{text}%"
    results = []

    # Buscar en columnas TEXT de media
    cursor = conn.execute("PRAGMA table_info(media)")
    text_cols = [c["name"] for c in cursor.fetchall()
                 if c["type"] in ("TEXT", "VARCHAR")]

    for col in text_cols:
        try:
            cursor = conn.execute(
                f"SELECT id, '{col}' AS columna, {col} AS valor "
                f"FROM media WHERE {col} LIKE ? LIMIT ?",
                (pattern, limit)
            )
            for row in cursor.fetchall():
                results.append(row)
        except sqlite3.OperationalError:
            pass

    # Buscar en media_metadata
    cursor = conn.execute("""
        SELECT mm.media_id AS id, 'media_metadata' AS columna,
               mm.key || ' = ' || substr(mm.value, 1, 100) AS valor
        FROM media_metadata mm
        WHERE mm.value LIKE ?
        LIMIT ?
    """, (pattern, limit))
    for row in cursor.fetchall():
        results.append(row)

    if not results:
        print(f"  No se encontraron resultados para '{text}'")
        return

    print(f"  {len(results)} resultados para '{text}':")
    print()
    seen_ids = set()
    for r in results:
        if r["id"] not in seen_ids or len(results) < 20:
            seen_ids.add(r["id"])
            # Obtener filename
            cursor = conn.execute(
                "SELECT filename_original, type FROM media WHERE id = ?",
                (r["id"],)
            )
            m = cursor.fetchone()
            fname = m["filename_original"] if m else "?"
            ftype = m["type"] if m else "?"
            print(f"  id={r['id']:6d} [{ftype:6s}] {fname}")
            print(f"         {r['columna']}: {r['valor']}")
            print()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consulta y explora la base de datos de medios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
Ejemplos:
  python scripts\query.py --columns
  python scripts\query.py --distinct type
  python scripts\query.py --distinct author --count
  python scripts\query.py --distinct author --count --where "type='image'"
  python scripts\query.py --key iptc_keywords --count
  python scripts\query.py --search "tucuman"
  python scripts\query.py --distinct color_1_name_basic --count
        """,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos")
    parser.add_argument("--columns", action="store_true", help="Listar columnas disponibles")
    parser.add_argument("--distinct", metavar="COLUMNA", help="Valores únicos de una columna de media")
    parser.add_argument("--key", metavar="KEY", help="Valores únicos de una key en media_metadata")
    parser.add_argument("--count", action="store_true", help="Incluir conteo de ocurrencias")
    parser.add_argument("--where", metavar="CONDICION", help="Filtro SQL (ej: \"type='image'\")")
    parser.add_argument("--search", metavar="TEXTO", help="Buscar texto en toda la DB")
    parser.add_argument("--limit", type=int, default=30, help="Límite de resultados (default: 30)")

    args = parser.parse_args(argv)

    db_path = resolver_db(args.db)
    conn = abrir(db_path)
    conn.row_factory = sqlite3.Row

    if args.columns:
        list_columns(conn)
    elif args.distinct:
        if not _es_columna_valida(conn, args.distinct):
            print(f"Error: '{args.distinct}' no es una columna válida de la tabla media.")
            print("Usá --columns para ver las columnas disponibles.")
            conn.close()
            return
        if args.where and not _where_seguro(args.where):
            print("Error: la condición --where contiene comandos SQL no permitidos.")
            conn.close()
            return
        distinct_column(conn, args.distinct, args.count, args.where)
    elif args.key:
        if args.where and not _where_seguro(args.where):
            print("Error: la condición --where contiene comandos SQL no permitidos.")
            conn.close()
            return
        distinct_key(conn, args.key, args.count, args.where)
    elif args.search:
        search_text(conn, args.search, args.limit)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()
