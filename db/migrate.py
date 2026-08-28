#!/usr/bin/env python3
"""
migrate.py — Sistema centralizado de migraciones de schema para Flujos.

Cada versión del schema tiene un número y un conjunto de sentencias SQL.
`verificar_schema()` detecta en qué versión está la DB y aplica las
migraciones faltantes en orden.

Uso:
    from db.migrate import verificar_schema
    verificar_schema(conn)  # al abrir la conexión
"""

import logging
import sqlite3

log = logging.getLogger("migrate")

# Versión actual del schema (incrementar al agregar migraciones)
SCHEMA_VERSION = 4

# Migraciones: cada entrada es (versión, descripción, [sentencias SQL])
_MIGRACIONES = [
    (1, "Schema inicial: media, media_metadata, media_keypoints, config", [
        # Se crean con init_db() / schema.sql — no repetimos las sentencias
        # Esta migración solo marca la versión 1 como existente.
    ]),
    (2, "Tablas tracks y waypoints para GPX", [
        """
        CREATE TABLE IF NOT EXISTS tracks (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT NOT NULL,
            filepath_absoluto TEXT NOT NULL,
            filepath_relativo TEXT NOT NULL,
            source_url        TEXT,
            start_time        TEXT,
            end_time          TEXT,
            total_points      INTEGER,
            ingested_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS waypoints (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id          INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
            name              TEXT NOT NULL,
            description       TEXT,
            category          TEXT,
            type              TEXT,
            latitude          REAL NOT NULL,
            longitude         REAL NOT NULL,
            timestamp         TEXT,
            ingested_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_waypoints_loc ON waypoints(latitude, longitude)",
        "CREATE INDEX IF NOT EXISTS idx_waypoints_track ON waypoints(track_id)",
        "CREATE INDEX IF NOT EXISTS idx_waypoints_type ON waypoints(type)",
        "CREATE INDEX IF NOT EXISTS idx_tracks_start ON tracks(start_time)",
    ]),
    (3, "Schema canónico para media_embeddings: UNIQUE(media_id, modelo) en vez de media_id PK", [
        # Callable: maneja tabla existente y DB nueva
        lambda conn: _migrar_media_embeddings(conn),
    ]),
    (4, "Tablas telegram_chats, telegram_messages, telegram_media + columna media.telegram_message_id", [
        """
        CREATE TABLE IF NOT EXISTS telegram_chats (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id       INTEGER NOT NULL UNIQUE,
            name              TEXT NOT NULL,
            chat_type         TEXT NOT NULL,
            export_path       TEXT NOT NULL,
            exported_at       TEXT,
            imported_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id               INTEGER NOT NULL REFERENCES telegram_chats(id) ON DELETE CASCADE,
            message_id            INTEGER NOT NULL,
            type                  TEXT NOT NULL DEFAULT 'message',
            message_type          TEXT NOT NULL DEFAULT 'text',
            es_sistema            INTEGER NOT NULL DEFAULT 0,
            from_name             TEXT,
            from_id               TEXT,
            text                  TEXT,
            date_unixtime         INTEGER NOT NULL,
            date_utc              TEXT NOT NULL,
            edited_unixtime       INTEGER,
            reply_to_message_id   INTEGER,
            media_group_id        TEXT,
            reactions             TEXT,
            hashtags              TEXT,
            action                TEXT,
            actor_name            TEXT,
            actor_id              TEXT,
            members               TEXT,
            UNIQUE(chat_id, message_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS telegram_media (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id          INTEGER NOT NULL REFERENCES telegram_messages(id) ON DELETE CASCADE,
            media_order         INTEGER NOT NULL DEFAULT 0,
            media_type          TEXT NOT NULL,
            file_relative_path  TEXT NOT NULL,
            file_name           TEXT,
            mime_type           TEXT,
            file_size           INTEGER,
            width               INTEGER,
            height              INTEGER,
            duration_seconds    REAL,
            thumbnail_path      TEXT,
            media_id            INTEGER REFERENCES media(id) ON DELETE SET NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tg_chat_id ON telegram_messages(chat_id)",
        "CREATE INDEX IF NOT EXISTS idx_tg_message_id ON telegram_messages(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_tg_date ON telegram_messages(date_unixtime)",
        "CREATE INDEX IF NOT EXISTS idx_tg_media_msg ON telegram_media(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_tg_media_media ON telegram_media(media_id)",
        lambda conn: _migrar_media_tg_message_id(conn),
    ]),
]


def _migrar_media_embeddings(conn: sqlite3.Connection):
    """
    Migración v3: unifica el schema de media_embeddings.

    - Si la tabla ya existe (creada por generate_embeddings.py viejo):
      recrea con UNIQUE(media_id, modelo) y ON DELETE CASCADE.
    - Si no existe (DB nueva): crea directamente el schema canónico.
    """
    # Verificar si la tabla existe
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='media_embeddings'"
    )
    tabla_existe = cur.fetchone() is not None

    if not tabla_existe:
        # DB nueva: crear con schema canónico directamente
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media_embeddings (
                media_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                embedding   BLOB NOT NULL,
                modelo      TEXT NOT NULL DEFAULT 'nomic-embed-text',
                fecha       TEXT DEFAULT (datetime('now')),
                UNIQUE(media_id, modelo)
            )
        """)
        log.info("  → Creada tabla media_embeddings (schema canónico, DB nueva)")
        return

    # Tabla existe: migrar datos del schema viejo al nuevo
    # Schema viejo: media_id INTEGER PRIMARY KEY, media_id_ref, embedding, modelo, fecha
    # Schema nuevo: media_id INTEGER NOT NULL, embedding, modelo, fecha, UNIQUE(media_id, modelo)
    log.info("  → Migrando tabla media_embeddings existente al schema canónico...")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("""
        CREATE TABLE media_embeddings_nuevo (
            media_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            embedding   BLOB NOT NULL,
            modelo      TEXT NOT NULL DEFAULT 'nomic-embed-text',
            fecha       TEXT DEFAULT (datetime('now')),
            UNIQUE(media_id, modelo)
        )
    """)
    conn.execute("""
        INSERT INTO media_embeddings_nuevo (media_id, embedding, modelo, fecha)
        SELECT media_id, embedding, COALESCE(modelo, 'nomic-embed-text'), COALESCE(fecha, datetime('now'))
        FROM media_embeddings
    """)
    conn.execute("DROP TABLE media_embeddings")
    conn.execute("ALTER TABLE media_embeddings_nuevo RENAME TO media_embeddings")
    conn.execute("PRAGMA foreign_keys=ON")
    log.info("  → Migración de media_embeddings completada: %s registros migrados",
             conn.execute("SELECT COUNT(*) FROM media_embeddings").fetchone()[0])



def _migrar_media_tg_message_id(conn: sqlite3.Connection):
    """
    Migración v4: agrega columna telegram_message_id a media si no existe.
    """
    # Si la tabla media no existe aún (DB vacía en tests), no hacer nada;
    # será creada por schema.sql / init_db con la columna ya incluida.
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='media'"
    ).fetchone():
        return
    cur = conn.execute("PRAGMA table_info(media)")
    cols = {row[1] for row in cur.fetchall()}
    if "telegram_message_id" not in cols:
        conn.execute(
            "ALTER TABLE media ADD COLUMN telegram_message_id INTEGER "
            "REFERENCES telegram_messages(id) ON DELETE SET NULL"
        )
        log.info("  → Columna media.telegram_message_id agregada")
    else:
        log.info("  → Columna media.telegram_message_id ya existe")


def schema_version(conn: sqlite3.Connection) -> int:
    """Retorna la versión actual del schema (0 si no existe)."""
    try:
        cur = conn.execute("SELECT value FROM config WHERE key = 'schema_version'")
        row = cur.fetchone()
        if row:
            return int(row[0])
    except (sqlite3.OperationalError, ValueError, TypeError):
        pass
    return 0


def _set_version(conn: sqlite3.Connection, version: int):
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )
    conn.commit()


def verificar_schema(conn: sqlite3.Connection):
    """
    Verifica la versión del schema y aplica migraciones pendientes.
    Es seguro llamarlo múltiples veces (usa IF NOT EXISTS).
    """
    actual = schema_version(conn)
    if actual >= SCHEMA_VERSION:
        return

    if actual == 0:
        log.info("Schema sin versionar. Se asume versión 1 (schema.sql inicial).")
        _set_version(conn, 1)
        actual = 1

    for version, desc, acciones in _MIGRACIONES:
        if version <= actual:
            continue
        log.info("Migrando schema a versión %d: %s", version, desc)
        for accion in acciones:
            if callable(accion):
                accion(conn)
            elif isinstance(accion, str) and accion.strip():
                conn.execute(accion)
        _set_version(conn, version)
        log.info("  → Versión %d aplicada.", version)
