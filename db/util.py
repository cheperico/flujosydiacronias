"""
util.py — Utilidades centralizadas de base de datos para el proyecto Flujos.

Provee:
  - resolver_db(db_path)         → resuelve ruta a db/flujos.db
  - conectar(db_path)            → context manager para conexiones SQLite
  - ModoHelper                   → gestión centralizada de skip/update/replace

Uso:
    from db.util import conectar, resolver_db, ModoHelper

    db = resolver_db(args.db)
    with conectar(db) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM media")
        print(cursor.fetchone()[0])

    helper = ModoHelper(args.mode)
    query = helper.build_query(
        base="SELECT id, filepath FROM media WHERE type='image'",
        check_col="color_1_hex"
    )
"""

import contextlib
import logging
import os
import sqlite3
from typing import Any, Iterator, Optional

log = logging.getLogger("db.util")

# ── Resolver ruta de DB ──────────────────────────────────────────────────────

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB = os.path.join(_PROJECT_ROOT, "db", "flujos.db")


def resolver_db(db_path: Optional[str] = None) -> str:
    """
    Resuelve la ruta absoluta a la base de datos.

    Si no se especifica, devuelve la ruta por defecto (db/flujos.db relativa
    al directorio raíz del proyecto).
    """
    if db_path:
        return os.path.abspath(db_path)
    return _DEFAULT_DB


# ── Conexión básica ──────────────────────────────────────────────────────────


def abrir(db_path: str) -> sqlite3.Connection:
    """
    Abre una conexión SQLite con WAL mode y foreign_keys activados.

    El caller es responsable de cerrarla (conn.close()).

    Uso:
        conn = abrir("db/flujos.db")
        # ... operaciones ...
        conn.close()
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Base de datos no encontrada: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Conexión como context manager ────────────────────────────────────────────


@contextlib.contextmanager
def conectar(db_path: str) -> Iterator[sqlite3.Connection]:
    """
    Context manager para conexiones SQLite.

    Configura automáticamente WAL mode y foreign_keys.
    Hace commit al salir del bloque (si no hay excepción).
    Hace rollback si hay excepción y la propaga.

    Uso:
        with conectar("db/flujos.db") as conn:
            conn.execute("INSERT INTO ...")
    """
    conn = abrir(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Helper de modos (skip / update / replace) ────────────────────────────────


class ModoHelper:
    """
    Centraliza la lógica de los modos skip/update/replace.

    Los modos se comportan así:
      - skip:    solo procesa registros donde la columna de control es NULL
      - update:  procesa TODOS los registros (sobrescribe)
      - replace: ejecuta SQL de limpieza, luego procesa todos

    Uso típico en una función run_xxx(conn, mode, stats):
        helper = ModoHelper(mode)
        # 1. Limpiar si es replace
        helper.clean(conn, "DELETE FROM media_metadata WHERE key = 'ia_keywords'")
        # 2. Obtener query + rows
        rows = conn.execute(helper.build_query(
            base="SELECT id, filepath FROM media WHERE type='image'",
            check_col="color_1_hex",
        )).fetchall()
        # 3. Procesar rows...
    """

    def __init__(self, mode: str):
        if mode not in ("skip", "update", "replace"):
            raise ValueError(f"Modo inválido: {mode!r}. Esperado: skip, update, replace")
        self.mode = mode

    def clean(self, conn: sqlite3.Connection, *statements: str) -> None:
        """Ejecuta SQL de limpieza SOLO si mode == 'replace'."""
        if self.mode == "replace":
            for sql in statements:
                conn.execute(sql)
            conn.commit()

    def build_query(
        self,
        base: str,
        check_col: Optional[str] = None,
        table_alias: str = "m",
    ) -> str:
        """
        Construye la query según el modo.

        Args:
            base: SELECT ... FROM ... WHERE condiciones_fijas
            check_col: columna que indica si el registro ya fue procesado
                       (ej: 'color_1_hex' para colores, NULL para tablas sin columna de control)
            table_alias: alias de la tabla para calificar la columna

        Returns:
            Query SQL completa.

        Si check_col es None:
          - skip:   igual que update (procesa todos)
          - update: base tal cual
          - replace: base tal cual
        Si check_col no es None:
          - skip:   base + "AND {alias}.{check_col} IS NULL"
          - update: base tal cual
          - replace: base tal cual
        """
        if self.mode == "skip" and check_col:
            col_ref = f"{table_alias}.{check_col}" if table_alias else check_col
            return f"{base} AND {col_ref} IS NULL"
        return base

    def update_flag_cols(self, conn: sqlite3.Connection, table: str,
                           flag_cols: list[str], extra_where: str = "1=1") -> None:
        """
        En modo update/replace, marca columnas como NULL para reprocesar.
        En modo replace esto es adicional al clean().

        Sirve cuando update debe reprocesar pero no borrar filas enteras.
        Nota: actualmente sin uso directo (helper disponible para scripts futuros).
        """
        if self.mode in ("update", "replace"):
            sets = ", ".join(f"{c} = NULL" for c in flag_cols)
            conn.execute(f"UPDATE {table} SET {sets} WHERE {extra_where}")
            conn.commit()

    def __str__(self) -> str:
        return self.mode
