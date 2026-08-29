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


# ── Resumen DRY (contadores centralizados) ───────────────────────────────────


_TABLAS_RESUMEN = [
    "media", "media_metadata", "media_keypoints", "media_embeddings",
    "config", "tracks", "waypoints",
    "telegram_chats", "telegram_messages", "telegram_media",
]

def obtener_resumen(conn: sqlite3.Connection) -> dict[str, int]:
    """Cuenta registros de cada tabla conocida. Retorna dict tabla->count (-1 si no existe)."""
    resumen: dict[str, int] = {}
    for tabla in _TABLAS_RESUMEN:
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            resumen[tabla] = int(cnt)
        except sqlite3.OperationalError:
            resumen[tabla] = -1
    return resumen


def resumen_por_tipo(conn: sqlite3.Connection) -> dict[str, int]:
    """Cuenta medios por type (image/video/audio/text/other)."""
    try:
        cur = conn.execute("SELECT type, COUNT(*) FROM media GROUP BY type")
        d = {row[0] or "NULL": row[1] for row in cur.fetchall()}
        # total
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        d["__total__"] = int(total)
        return d
    except sqlite3.OperationalError:
        return {}


def resumen_texto(conn: sqlite3.Connection) -> str:
    """Texto resumido para TUI: totales por tipo."""
    d = resumen_por_tipo(conn)
    if not d:
        return "  (Base de datos vacía o sin schema)"
    total = d.pop("__total__", 0)
    imagenes = d.get("image", 0)
    videos = d.get("video", 0)
    audios = d.get("audio", 0)
    textos = d.get("text", 0)
    otros = total - imagenes - videos - audios - textos
    return (
        f"  Total:      {total:>6d}\n"
        f"  Imagenes:   {imagenes:>6d}\n"
        f"  Videos:     {videos:>6d}\n"
        f"  Audios:     {audios:>6d}\n"
        f"  Textos:     {textos:>6d}\n"
        f"  Otros:      {otros:>6d}"
    )


# ── Path utils compartidos (relocate / mover / consolidar) ───────────────────


def normalizar_ruta(p: str) -> str:
    """Normaliza separadores, elimina barra final y normaliza case en Windows."""
    p = os.path.normpath(p)
    if len(p) > 2 and p.endswith(os.sep):
        p = p[:-1]
    # En Windows, normalizar case para comparaciones
    if os.name == "nt":
        p = os.path.normcase(p)
    return p


def _ruta_es_prefijo(abs_path: str, old_root: str) -> bool:
    """True si abs_path está dentro de old_root (con separador), case-insensitive en Windows."""
    old_norm = normalizar_ruta(old_root)
    # Necesitamos comparar con normcase pero preservar longitud original para slicing
    # Usamos normpath sin normcase para medir len, pero comparamos normcased
    abs_norm = os.path.normpath(abs_path)
    if os.name == "nt":
        if abs_norm.lower().startswith(old_norm.lower()):
            # Verificar separador: exacto o con /
            resto = abs_norm[len(os.path.normpath(old_root)):]
            if resto == "" or resto.startswith(os.sep):
                return True
            # También el caso donde old_root ya incluía separador
            return resto == "" or abs_norm[len(old_norm):].startswith(os.sep) or len(abs_norm) == len(old_norm)
        return False
    else:
        # POSIX: case-sensitive, requiere separador
        if abs_norm == old_norm:
            return True
        return abs_norm.startswith(old_norm + os.sep)


def sidecar_abs(sidecar_rel: str, root: str) -> str:
    """Convierte sidecar_xml (relativo a ingest_root) a ruta absoluta."""
    if os.path.isabs(sidecar_rel):
        return sidecar_rel
    return os.path.normpath(os.path.join(root, sidecar_rel))


def calcular_nueva_ruta(abs_path: str, old_root: str, new_root: str) -> str:
    """Calcula nueva ruta absoluta reemplazando SOLO prefijo old_root por new_root (con separador)."""
    if not _ruta_es_prefijo(abs_path, old_root):
        return abs_path
    old_norm_fs = os.path.normpath(old_root)
    new_norm_fs = os.path.normpath(new_root)
    # Preservar case original de new_root, pero usar longitud de old_norm_fs para slicing
    # En Windows, slicing es seguro porque normpath no cambia longitud salvo separadores
    # Usamos len(old_norm_fs) que coincide con el prefijo real en abs_path (case puede diferir)
    # Buscamos el prefijo real en abs_path con lower
    if os.name == "nt":
        # Encontrar prefijo ignorando case
        abs_norm = os.path.normpath(abs_path)
        # Si abs empieza con old (case-insensitive), reemplazar conservando resto con separador correcto
        if abs_norm.lower().startswith(old_norm_fs.lower()):
            resto = abs_norm[len(old_norm_fs):]
            # Si old termina sin sep y resto no empieza con sep, es porque es exact match o subpath
            # Reconstruir con new_norm_fs + resto (resto ya incluye sep si es subcarpeta)
            return os.path.normpath(new_norm_fs + resto)
    else:
        if abs_path == old_norm_fs:
            return new_norm_fs
        if abs_path.startswith(old_norm_fs + os.sep):
            return new_norm_fs + abs_path[len(old_norm_fs):]
    # Fallback al método simple
    old_norm = normalizar_ruta(old_root)
    new_norm = normalizar_ruta(new_root)
    if abs_path.startswith(old_norm):
        return new_norm + abs_path[len(old_norm):]
    return abs_path
