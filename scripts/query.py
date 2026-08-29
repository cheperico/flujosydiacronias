#!/usr/bin/env python3
"""
query.py - Consulta y explora la base de datos de medios.

Uso:
    # Listar columnas y keys disponibles
    python scripts/query.py --columns

    # Valores únicos de una columna de media
    python scripts/query.py --distinct type
    python scripts/query.py --distinct author --count

    # Valores únicos en tabla alternativa (keypoints, telegram, etc)
    python scripts/query.py --distinct key --table media_keypoints --count

    # Valores únicos de una key en media_metadata
    python scripts/query.py --key ia_keywords --count

    # Buscar texto en todas las columnas
    python scripts/query.py --search "tucuman"

    # Filtrar por tipo y paginar
    python scripts/query.py --distinct author --count --where "type='image'" --limit 20
"""

import argparse
import re
import sqlite3
import sys
import os

# Permitir ejecución standalone: agregar raíz del proyecto al path
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.util import abrir, resolver_db


# ── Validación de seguridad ──────────────────────────────────────────────────

# Palabras prohibidas en --where (previene inyección SQL básica)
_PROHIBIDO_EN_WHERE = re.compile(
    r'\b(DROP|ALTER|DELETE|INSERT|UPDATE|CREATE|ATTACH|DETACH|REINDEX|VACUUM|UNION|EXEC)\b',
    re.IGNORECASE,
)

# Patrones adicionales peligrosos (comentarios SQL, múltiples statements)
_PATRON_PELIGROSO = re.compile(r'(--|/\*|\*/|;)')

# Tablas permitidas para --table / --distinct
_TABLAS_PERMITIDAS = {
    "media", "media_metadata", "media_keypoints", "media_embeddings",
    "config", "tracks", "waypoints",
    "telegram_chats", "telegram_messages", "telegram_media",
}


def _es_columna_valida(conn: sqlite3.Connection, col: str, tabla: str = "media") -> bool:
    """Verifica que col sea una columna real de la tabla indicada."""
    if tabla not in _TABLAS_PERMITIDAS:
        return False
    try:
        cursor = conn.execute(f"PRAGMA table_info({tabla})")
        columnas = {c["name"] for c in cursor.fetchall()}
        return col in columnas
    except sqlite3.OperationalError:
        return False


def _es_tabla_valida(tabla: str) -> bool:
    return tabla in _TABLAS_PERMITIDAS


def _quitar_strings(where: str) -> str:
    """Quita contenido entre comillas simples/dobles para no confundir valores con SQL."""
    # Remueve '...' y "..." (incluyendo escapes '')
    s = re.sub(r"'[^']*'", "''", where)
    s = re.sub(r'"[^"]*"', '""', s)
    return s

def _where_seguro(where: str) -> bool:
    """Valida WHERE ignorando valores entre comillas."""
    limpio = _quitar_strings(where)
    if _PROHIBIDO_EN_WHERE.search(limpio):
        return False
    if _PATRON_PELIGROSO.search(limpio):
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

    print()
    print("=== Keys en media_keypoints (top 30) ===")
    try:
        cursor = conn.execute("""
            SELECT key, COUNT(*) as total
            FROM media_keypoints
            GROUP BY key
            ORDER BY total DESC
            LIMIT 30
        """)
        rows = cursor.fetchall()
        if rows:
            for r in rows:
                print(f"  {r['key']:<45s} ({r['total']} registros)")
        else:
            print("  (vacío)")
    except sqlite3.OperationalError:
        print("  (tabla no existe)")

    print()
    print("=== Tablas disponibles (--table) ===")
    for t in sorted(_TABLAS_PERMITIDAS):
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<22s} {cnt:>6d} filas")
        except sqlite3.OperationalError:
            print(f"  {t:<22s} (no existe)")

    print()
    print("Tip: usa --distinct COL --table TABLA para consultar otras tablas")
    print("     usa --key KEY para valores de una clave de media_metadata")


def distinct_column(conn, col: str, count: bool, where: str | None, tabla: str = "media", limit: int | None = None):
    """Valores únicos de una columna (soporta cualquier tabla permitida y paginación)."""
    if not _es_tabla_valida(tabla):
        print(f"Error: tabla '{tabla}' no permitida. Usa --columns para ver tablas.")
        return
    if not _es_columna_valida(conn, col, tabla):
        print(f"Error: '{col}' no es una columna válida de la tabla '{tabla}'.")
        print("Usá --columns para ver las columnas disponibles.")
        return
    if where:
        if not _where_seguro(where):
            print("Error: la condición --where contiene comandos SQL no permitidos (UNION, comentarios o DDL).")
            return
        where_clause = f"WHERE {where}"
    else:
        where_clause = ""

    # Clamp limit
    limit_clause = ""
    if limit is not None and limit > 0:
        limit = max(1, min(int(limit), 500))
        limit_clause = f"LIMIT {limit}"

    if count:
        query = f"""
            SELECT {col} AS val, COUNT(*) as total
            FROM {tabla}
            {where_clause}
            GROUP BY {col}
            ORDER BY total DESC
            {limit_clause}
        """
        try:
            cursor = conn.execute(query)
        except sqlite3.OperationalError as e:
            print(f"Error SQL: {e}")
            print("Tip: verificá el --where con --columns")
            return
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
        if limit and len(rows) == limit:
            print(f"  (mostrando top {limit}, puede haber más)")
    else:
        query = f"""
            SELECT DISTINCT {col} AS val
            FROM {tabla}
            {where_clause}
            ORDER BY val
            {limit_clause}
        """
        try:
            cursor = conn.execute(query)
        except sqlite3.OperationalError as e:
            print(f"Error SQL: {e}")
            return
        rows = cursor.fetchall()
        for r in rows:
            val = r["val"]
            if val is not None:
                print(f"  {val}")
        print(f"  ({len(rows)} valores únicos" + (f", límite {limit}" if limit else "") + ")")


def distinct_key(conn, key: str, count: bool, where: str | None, limit: int | None = None):
    """Valores únicos de una key en media_metadata (con paginación y WHERE seguro)."""
    if where and not _where_seguro(where):
        print("Error: la condición --where contiene comandos SQL no permitidos.")
        return
    where_clause = f"AND ({where})" if where else ""
    limit_clause = ""
    if limit is not None and limit > 0:
        limit = max(1, min(int(limit), 500))
        limit_clause = f"LIMIT {limit}"

    if count:
        query = f"""
            SELECT mm.value AS val, COUNT(*) as total
            FROM media_metadata mm
            JOIN media m ON m.id = mm.media_id
            WHERE mm.key = ?
            {where_clause}
            GROUP BY mm.value
            ORDER BY total DESC
            {limit_clause}
        """
        try:
            cursor = conn.execute(query, (key,))
        except sqlite3.OperationalError as e:
            print(f"Error SQL: {e}")
            return
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
        if limit and len(rows) == limit:
            print(f"  (mostrando top {limit}, puede haber más)")
    else:
        query = f"""
            SELECT DISTINCT mm.value AS val
            FROM media_metadata mm
            JOIN media m ON m.id = mm.media_id
            WHERE mm.key = ?
            {where_clause}
            ORDER BY val
            {limit_clause}
        """
        try:
            cursor = conn.execute(query, (key,))
        except sqlite3.OperationalError as e:
            print(f"Error SQL: {e}")
            return
        rows = cursor.fetchall()
        for r in rows:
            val = r["val"]
            if val is not None:
                print(f"  {val[:100]}")
        print(f"  ({len(rows)} valores únicos" + (f", límite {limit}" if limit else "") + ")")


def _escape_like(text: str) -> str:
    """Escapa % y _ para LIKE, manteniendo búsqueda literal."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_text(conn, text: str, limit: int = 30):
    """Busca texto repartiendo presupuesto entre media / metadata / keypoints para evitar starvation."""
    limit = max(1, min(int(limit), 200))
    esc = _escape_like(text)
    pattern = f"%{esc}%"
    results: list[sqlite3.Row] = []

    # Reparto: 50% media, 30% metadata, 20% keypoints (mín 1 cada uno)
    media_budget = max(1, int(limit * 0.5))
    meta_budget = max(1, int(limit * 0.3))
    kp_budget = max(1, limit - media_budget - meta_budget)
    # Ajuste si limit pequeño
    if limit < 3:
        media_budget = min(limit, 1)
        meta_budget = min(max(0, limit - media_budget), 1)
        kp_budget = max(0, limit - media_budget - meta_budget)

    cursor = conn.execute("PRAGMA table_info(media)")
    text_cols = [c["name"] for c in cursor.fetchall() if c["type"] in ("TEXT", "VARCHAR")]
    media_hits = 0
    if text_cols:
        union_parts = " UNION ALL ".join(
            f"SELECT id, '{col}' AS columna, substr({col},1,120) AS valor FROM media WHERE {col} LIKE ? ESCAPE '\\'"
            for col in text_cols
        )
        params = [pattern] * len(text_cols)
        try:
            q = f"SELECT * FROM ({union_parts}) LIMIT ?"
            cur = conn.execute(q, (*params, media_budget))
            rows = cur.fetchall()
            results.extend(rows)
            media_hits = len(rows)
        except sqlite3.OperationalError:
            for col in text_cols:
                try:
                    cur = conn.execute(
                        f"SELECT id, '{col}' AS columna, substr({col},1,120) AS valor FROM media WHERE {col} LIKE ? ESCAPE '\\' LIMIT ?",
                        (pattern, media_budget),
                    )
                    rows = cur.fetchall()
                    results.extend(rows)
                    media_hits += len(rows)
                    if media_hits >= media_budget:
                        break
                except sqlite3.OperationalError:
                    pass
    # metadata con su presupuesto
    cur = conn.execute(
        "SELECT mm.media_id AS id, 'media_metadata:' || mm.key AS columna, substr(mm.value,1,120) AS valor "
        "FROM media_metadata mm WHERE mm.value LIKE ? ESCAPE '\\' LIMIT ?",
        (pattern, meta_budget),
    )
    meta_rows = cur.fetchall()
    results.extend(meta_rows)
    # keypoints con su presupuesto
    kp_rows = []
    try:
        cur = conn.execute(
            "SELECT kp.media_id AS id, 'keypoints:' || kp.key AS columna, substr(kp.value,1,120) AS valor "
            "FROM media_keypoints kp WHERE kp.value LIKE ? ESCAPE '\\' LIMIT ?",
            (pattern, kp_budget),
        )
        kp_rows = cur.fetchall()
        results.extend(kp_rows)
    except sqlite3.OperationalError:
        pass
    # Si aún queda cupo global (porque alguna fuente dio menos), redistribuir sobrante a metadata
    total_obtenido = len(results)
    if total_obtenido < limit:
        sobrante = limit - total_obtenido
        # Intentar completar desde metadata
        try:
            cur = conn.execute(
                "SELECT mm.media_id AS id, 'media_metadata:' || mm.key AS columna, substr(mm.value,1,120) AS valor "
                "FROM media_metadata mm WHERE mm.value LIKE ? ESCAPE '\\' LIMIT ? OFFSET ?",
                (pattern, sobrante, len(meta_rows)),
            )
            extra = cur.fetchall()
            results.extend(extra)
        except Exception:
            pass

    if not results:
        print(f"  No se encontraron resultados para '{text}'")
        return

    print(f"  {len(results)} resultados para '{text}' (límite global {limit}):")
    print()
    # Mostrar con filename, deduplicando ids si hay muchos resultados
    shown = 0
    for r in results:
        if shown >= limit:
            break
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
        shown += 1
    if len(results) == limit:
        print(f"  (mostrando {limit}, puede haber más — usa --limit mayor)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consulta y explora la base de datos de medios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=r"""
Ejemplos:
  python scripts\query.py --columns
  python scripts\query.py --distinct type
  python scripts\query.py --distinct author --count --where "type='image'" --limit 20
  python scripts\query.py --distinct key --table media_keypoints --count
  python scripts\query.py --key ia_keywords --count --limit 30
  python scripts\query.py --search "tucuman" --limit 50
  python scripts\query.py --distinct color_1_name_basic --count
        """,
    )
    parser.add_argument("--db", default=None, help="Ruta a la base de datos")
    parser.add_argument("--columns", action="store_true", help="Listar columnas y keys disponibles")
    parser.add_argument("--distinct", metavar="COLUMNA", help="Valores únicos de una columna")
    parser.add_argument("--table", default="media", help="Tabla para --distinct (default: media)")
    parser.add_argument("--key", metavar="KEY", help="Valores únicos de una key en media_metadata")
    parser.add_argument("--count", action="store_true", help="Incluir conteo de ocurrencias")
    parser.add_argument("--where", metavar="CONDICION", help="Filtro SQL (ej: \"type='image'\")")
    parser.add_argument("--search", metavar="TEXTO", help="Buscar texto en toda la DB (media, metadata, keypoints)")
    parser.add_argument("--limit", type=int, default=None, help="Límite de resultados (default: sin límite para distinct, 30 para search)")

    args = parser.parse_args(argv)

    db_path = resolver_db(args.db)
    conn = abrir(db_path)
    conn.row_factory = sqlite3.Row

    if args.columns:
        list_columns(conn)
    elif args.distinct:
        lim = args.limit
        if args.where and not _where_seguro(args.where):
            print("Error: la condición --where contiene comandos SQL no permitidos.")
            conn.close()
            return
        distinct_column(conn, args.distinct, args.count, args.where, tabla=args.table, limit=lim)
    elif args.key:
        if args.where and not _where_seguro(args.where):
            print("Error: la condición --where contiene comandos SQL no permitidos.")
            conn.close()
            return
        lim = args.limit
        distinct_key(conn, args.key, args.count, args.where, limit=lim)
    elif args.search:
        lim = args.limit if args.limit is not None else 30
        search_text(conn, args.search, lim)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()
