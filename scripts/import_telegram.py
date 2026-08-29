#!/usr/bin/env python3
"""
import_telegram.py — Importar exports de Telegram a la base de datos Flujos.

Procesa result.json de un export de Telegram, registra el chat, los mensajes
y sus archivos multimedia. Opcionalmente ingiere los multimedia en la tabla media
para que pasen por el pipeline de enriquecimiento (colores, transcripción, etc.).

Los archivos quedan referenciados en la carpeta del export; para agrupar o
consolidar medios en otra ubicación usá scripts/mover_media.py o
scripts/consolidar_medios.py. Al re-importar con --mode skip, los mensajes
existentes se saltan pero se recuperan los medios que no estaban disponibles
anteriormente.

Uso:
    python scripts/import_telegram.py --export-path RUTA_AL_EXPORT
    python flujos.py import-telegram --export-path RUTA_AL_EXPORT
    python flujos.py tg -e RUTA

Args:
    --export-path / -e    Ruta al directorio del export (con result.json)
    --include-system      Incluir mensajes de sistema (default: True)
    --ingest-media        Ingerir multimedia en tabla media (default: True)
    --mode                skip | update | replace (default: skip)
    --dry-run             Solo previsualizar
    --db                  Ruta a la base de datos
    --verbose / -v        Verbose
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys

# ── Path fix para ejecución standalone ────────────────────────────────
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.util import abrir, resolver_db, conectar
from db.migrate import verificar_schema

log = logging.getLogger("import_telegram")

# ── Constantes ────────────────────────────────────────────────────────

JSON_FILENAME = "result.json"

# Mapeo de media_type de Telegram a type de media table.
# Los stickers NO entran: son decoración del chat y nunca se ingieren como media.
MEDIA_TYPE_MAP = {
    "photo": "image",
    "video_file": "video",
    "animation": "video",
    "voice_message": "audio",
    "document": "other",
}

# ── Helpers ───────────────────────────────────────────────────────────

def reparar_json(texto: str) -> str:
    """
    Intenta reparar un JSON de Telegram que pueda venir truncado.
    El export a veces no cierra el array messages ni el objeto raíz.
    Usa conteo de brackets para determinar qué falta.
    """
    t = texto.rstrip().rstrip(",")
    # Contar brackets abiertos/cerrados
    llaves = t.count("{") - t.count("}")
    corchetes = t.count("[") - t.count("]")
    if corchetes > 0:
        t += "\n" + "]" * corchetes
    if llaves > 0:
        t += "\n" + "}" * llaves
    return t


def aplanar_texto(text: str | list) -> str:
    """
    Convierte el campo 'text' de Telegram (string o array de entidades)
    a texto plano.
    """
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        partes = []
        for item in text:
            if isinstance(item, str):
                partes.append(item)
            elif isinstance(item, dict):
                partes.append(item.get("text", ""))
        return "".join(partes)
    return str(text) if text else ""


def extraer_hashtags(text: str | list) -> str:
    """Extrae hashtags de un texto del export (formato array)."""
    tags = set()
    if isinstance(text, list):
        for item in text:
            if isinstance(item, dict) and item.get("type") == "hashtag":
                tag = item.get("text", "").lstrip("#")
                if tag:
                    tags.add(tag)
    return " ".join(sorted(tags))


def sha256_archivo(ruta: str) -> str | None:
    """Calcula SHA-256 de un archivo, o None si no existe."""
    if not os.path.isfile(ruta):
        return None
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def fecha_iso_a_utc(fecha_iso: str) -> str:
    """
    Convierte fecha ISO de Telegram (sin timezone, asumida UTC)
    a formato ISO 8601 UTC.
    """
    if not fecha_iso:
        return ""
    # Telegram usa formato "2025-08-08T11:21:29" (sin Z ni offset)
    # Asumimos UTC
    fecha = fecha_iso.strip()
    if not fecha.endswith("Z"):
        fecha += "Z"
    return fecha


# ── Tipos de mensaje Telegram → message_type ──────────────────────────

def detectar_message_type(msg: dict) -> str:
    """Determina el tipo de mensaje de Telegram."""
    if msg.get("type") == "service":
        return "system"

    if "photo" in msg:
        return "photo"
    if "location" in msg:
        return "location"
    if "poll" in msg:
        return "poll"

    media_type = msg.get("media_type", "")
    if media_type:
        m = {
            "voice_message": "voice",
            "video_file": "video",
            "video_message": "video",
            "animation": "animation",
            "sticker": "sticker",
            "audio_file": "audio",
        }.get(media_type, "document")
        return m

    # Si hay file pero no media_type, detectar por mime_type
    if "file" in msg:
        mt = msg.get("mime_type", "")
        if mt.startswith("video/"):
            return "video"
        if mt.startswith("audio/"):
            return "audio"
        if mt.startswith("image/"):
            return "image"
        return "document"

    # Texto plano (último recurso)
    return "text"


def detectar_type_media(media_type: str, mime_type: str) -> str:
    """Mapea media_type/mime_type de Telegram a type de media table."""
    if media_type == "photo":
        return "image"
    if media_type in ("video_file", "video_message", "animation"):
        return "video"
    if media_type in ("voice_message", "audio_file", "audio"):
        return "audio"
    # Sticker: solo chat, nunca se ingiere como media. La ruta de ingesta lo
    # salta antes de llamar a esta función, pero el mapeo no debe declararlo
    # imagen (el mime image/webp lo inferiría como "image" incorrectamente).
    if media_type == "sticker":
        return "other"
    # Por mime_type
    if mime_type:
        if mime_type.startswith("video/"):
            return "video"
        if mime_type.startswith("audio/"):
            return "audio"
        if mime_type.startswith("image/"):
            return "image"
    return "other"


# ── Parser principal ──────────────────────────────────────────────────

def parsear_export(ruta_export: str) -> dict:
    """
    Lee y parsea result.json de un export de Telegram.
    Repara el JSON si está truncado.
    """
    ruta_json = os.path.join(ruta_export, JSON_FILENAME)
    if not os.path.isfile(ruta_json):
        raise FileNotFoundError(f"No se encuentra {ruta_json}")

    with open(ruta_json, "r", encoding="utf-8") as f:
        raw = f.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("JSON truncado, intentando reparar...")
        raw_reparado = reparar_json(raw)
        try:
            data = json.loads(raw_reparado)
            log.info("JSON reparado correctamente")
        except json.JSONDecodeError as e:
            raise ValueError(f"No se pudo reparar el JSON: {e}")

    # Validar estructura mínima
    if "messages" not in data:
        raise ValueError("El JSON no contiene la clave 'messages'")

    return data


# ── Ingesta en media table ────────────────────────────────────────────

def ingerir_media_telegram(
    conn: sqlite3.Connection,
    export_path: str,
    file_rel_path: str,
    mensaje: dict,
    media_type_tg: str,       # media_type del item (photo, video_file, voice_message, etc.)
    mime_type: str,            # mime_type del item
) -> int | None:
    """
    Ingiere un archivo multimedia de Telegram en la tabla media.

    Los archivos quedan referenciados en la carpeta del export. Para agrupar
    o consolidar usá mover_media.py / consolidar_medios.py.
    Retorna el id del medio insertado, o None si falla.
    """
    abs_path = os.path.abspath(os.path.join(export_path, file_rel_path))
    if not os.path.isfile(abs_path):
        log.warning("  Archivo no encontrado (aún descargándose?): %s", file_rel_path)
        return None

    # Determinar tipo según el media_type del item
    tipo = detectar_type_media(media_type_tg, mime_type)

    # Verificar si ya existe por file_hash
    file_hash = sha256_archivo(abs_path)
    if file_hash:
        existente = conn.execute(
            "SELECT id, type FROM media WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if existente:
            # Actualizar type si cambió (ej: de 'other' a 'image' por fix de mapeo)
            if existente[1] != tipo:
                conn.execute("UPDATE media SET type=?, updated_at=datetime('now') WHERE id=?",
                             (tipo, existente[0]))
            return existente[0]

    filename = os.path.basename(file_rel_path)
    carpeta_original = os.path.dirname(file_rel_path).replace("\\", "/")
    filepath_absoluto = abs_path
    filepath_relativo = file_rel_path.replace("\\", "/")
    carpeta = carpeta_original

    # Timestamp desde el mensaje
    date_iso = mensaje.get("date", "")
    timestamp_utc = fecha_iso_a_utc(date_iso)

    # Tamaño
    size = mensaje.get("file_size") or mensaje.get("photo_file_size") or 0

    # Insertar
    conn.execute(
        """
        INSERT INTO media (
            filename_original, filepath_absoluto, filepath_relativo,
            carpeta, type, size_bytes, file_hash,
            timestamp_utc, duration_secs, author,
            ingested_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            filename,
            filepath_absoluto,
            filepath_relativo,
            carpeta,
            tipo,
            size,
            file_hash,
            timestamp_utc,
            mensaje.get("duration_seconds"),
            mensaje.get("from", ""),
        ),
    )

    media_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Guardar metadata: de dónde vino
    conn.execute(
        "INSERT OR IGNORE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
        (media_id, "fuente", "telegram"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO media_metadata (media_id, key, value) VALUES (?, ?, ?)",
        (media_id, "telegram_chat", str(mensaje.get("from", ""))),
    )

    return media_id


# ── Procesamiento principal ───────────────────────────────────────────

def procesar_export(
    conn: sqlite3.Connection,
    export_path: str,
    data: dict,
    *,
    include_system: bool = True,
    ingest_media: bool = True,
    modo: str = "skip",
    dry_run: bool = False,
) -> dict:
    """
    Procesa el export de Telegram y escribe en DB.

    Retorna dict con estadísticas.
    """
    stats = {
        "total": 0,
        "mensajes": 0,
        "sistema": 0,
        "saltados": 0,
        "media_registrada": 0,
        "media_ingerida": 0,
        "errores": 0,
    }

    export_path = os.path.abspath(export_path)

    chat_info = {
        "telegram_id": data.get("id"),
        "name": data.get("name", "Desconocido"),
        "chat_type": data.get("type", "unknown"),
    }

    if not chat_info["telegram_id"]:
        raise ValueError("El JSON no contiene 'id' del chat")

    # ── Registrar / obtener chat ──
    if modo == "replace":
        conn.execute("DELETE FROM telegram_chats WHERE telegram_id = ?",
                      (chat_info["telegram_id"],))
        conn.commit()

    cur = conn.execute(
        "SELECT id FROM telegram_chats WHERE telegram_id = ?",
        (chat_info["telegram_id"],),
    )
    fila = cur.fetchone()
    if fila:
        chat_db_id = fila[0]
        # Actualizar datos
        if not dry_run:
            conn.execute(
                "UPDATE telegram_chats SET name=?, chat_type=?, export_path=?, "
                "imported_at=datetime('now') WHERE id=?",
                (chat_info["name"], chat_info["chat_type"], export_path, chat_db_id),
            )
            conn.commit()
    else:
        chat_db_id = None
        if not dry_run:
            conn.execute(
                "INSERT INTO telegram_chats (telegram_id, name, chat_type, export_path) "
                "VALUES (?, ?, ?, ?)",
                (chat_info["telegram_id"], chat_info["name"],
                 chat_info["chat_type"], export_path),
            )
            chat_db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

    if dry_run:
        stats["total"] = len(data["messages"])
        # Contar mensajes por tipo
        for msg in data["messages"]:
            if msg.get("type") == "service":
                stats["sistema"] += 1
            mt = detectar_message_type(msg)
            if mt != "text" and mt != "system":
                stats["media_registrada"] += 1
        stats["mensajes"] = len(data["messages"]) - stats["sistema"]
        return stats

    # ── Procesar mensajes ──
    mensajes_procesados = 0
    for msg in data["messages"]:
        stats["total"] += 1

        msg_id = msg.get("id")
        if not msg_id:
            stats["saltados"] += 1
            continue

        # Detectar tipo
        es_service = msg.get("type") == "service"
        message_type = detectar_message_type(msg)

        if es_service:
            stats["sistema"] += 1
            if not include_system:
                stats["saltados"] += 1
                continue

        # Verificar si ya existe
        if modo == "skip":
            existente = conn.execute(
                "SELECT id FROM telegram_messages WHERE chat_id=? AND message_id=?",
                (chat_db_id, msg_id),
            ).fetchone()
            if existente:
                stats["saltados"] += 1
                continue

        # Aplanar texto
        text = aplanar_texto(msg.get("text", ""))

        # Extraer hashtags
        hashtags = extraer_hashtags(msg.get("text", ""))

        # Fecha UTC
        date_iso = msg.get("date", "")
        date_utc = fecha_iso_a_utc(date_iso)

        # Reacciones
        reactions_raw = msg.get("reactions")
        reactions = json.dumps(reactions_raw, ensure_ascii=False) if reactions_raw else None

        # Members (service)
        members = msg.get("members")
        members_json = json.dumps(members, ensure_ascii=False) if members else None

        # Service action
        action = msg.get("action") if es_service else None
        actor = msg.get("actor") if es_service else None
        actor_id = msg.get("actor_id") if es_service else None

        # Insertar mensaje
        if modo == "replace":
            conn.execute(
                "DELETE FROM telegram_messages WHERE chat_id=? AND message_id=?",
                (chat_db_id, msg_id),
            )

        conn.execute(
            """
            INSERT OR REPLACE INTO telegram_messages
                (chat_id, message_id, type, message_type, es_sistema,
                 from_name, from_id, text,
                 date_unixtime, date_utc, edited_unixtime,
                 reply_to_message_id, media_group_id,
                 reactions, hashtags,
                 action, actor_name, actor_id, members)
            VALUES (?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?, ?)
            """,
            (
                chat_db_id, msg_id, msg.get("type", "message"), message_type,
                1 if es_service else 0,
                msg.get("from") or msg.get("actor", ""),
                msg.get("from_id") or msg.get("actor_id", ""),
                text,
                int(msg.get("date_unixtime", 0)),
                date_utc,
                int(msg["edited_unixtime"]) if msg.get("edited_unixtime") else None,
                msg.get("reply_to_message_id"),
                msg.get("grouped_id"),
                reactions,
                hashtags or None,
                action, actor, actor_id, members_json,
            ),
        )
        msg_db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        stats["mensajes"] += 1

        # ── Procesar multimedia ──
        media_items = extraer_media_del_mensaje(msg)
        for midx, mitem in enumerate(media_items):
            stats["media_registrada"] += 1

            # Los stickers quedan SOLO en telegram_media (registro del chat);
            # nunca se ingieren en media (no pasan por el pipeline).
            es_sticker = mitem["media_type"] == "sticker"

            # Insertar en telegram_media
            conn.execute(
                """
                INSERT INTO telegram_media
                    (message_id, media_order, media_type,
                     file_relative_path, file_name, mime_type,
                     file_size, width, height, duration_seconds, thumbnail_path)
                VALUES (?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?)
                """,
                (
                    msg_db_id, midx, mitem["media_type"],
                    mitem["file_relative_path"], mitem.get("file_name"),
                    mitem.get("mime_type"),
                    mitem.get("file_size"), mitem.get("width"),
                    mitem.get("height"), mitem.get("duration_seconds"),
                    mitem.get("thumbnail_path"),
                ),
            )
            tg_media_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Ingerir en media table (los stickers se omiten: solo chat)
            if es_sticker:
                log.debug("  Sticker (solo chat, no se ingiere en media): %s",
                          mitem.get("file_relative_path"))
            elif ingest_media:
                media_id = ingerir_media_telegram(
                    conn, export_path, mitem["file_relative_path"],
                    msg, mitem["media_type"], mitem.get("mime_type", ""),
                )
                if media_id:
                    # Vincular telegram_media → media
                    conn.execute(
                        "UPDATE telegram_media SET media_id=? WHERE id=?",
                        (media_id, tg_media_id),
                    )
                    # Vincular media → telegram_messages
                    conn.execute(
                        "UPDATE media SET telegram_message_id=? WHERE id=?",
                        (msg_db_id, media_id),
                    )
                    stats["media_ingerida"] += 1

        # Commit cada mensaje (para no perder progreso en exports grandes)
        conn.commit()

    # ── Recuperar media pendiente (de corridas previas donde el archivo no existía) ──
    recuperados = 0
    if ingest_media and not dry_run:
        pendientes = conn.execute("""
            SELECT tgm.id, tgm.file_relative_path, tgm.media_type, tgm.mime_type,
                   tm.id AS msg_db_id
            FROM telegram_media tgm
            JOIN telegram_messages tm ON tgm.message_id = tm.id
            WHERE tgm.media_id IS NULL AND tgm.file_relative_path != ''
              AND tgm.media_type != 'sticker'   -- los stickers nunca se recuperan como media
        """).fetchall()
        if pendientes:
            log.info("Recuperando %d medios pendientes (archivos que no estaban disponibles antes)...",
                     len(pendientes))
            for row in pendientes:
                tg_media_id, file_rel, media_type_tg, mime_t, msg_db_id = row
                # Defensa extra: si por alguna razón llegara un sticker, se salta
                # (solo chat, nunca ingerido como media).
                if media_type_tg == "sticker":
                    continue
                if not os.path.isfile(os.path.abspath(os.path.join(export_path, file_rel))):
                    continue
                # Obtener datos del mensaje original para el ingest
                msg_data = conn.execute(
                    "SELECT date_utc, from_name, from_id, date_unixtime "
                    "FROM telegram_messages WHERE id=?",
                    (msg_db_id,),
                ).fetchone()
                if not msg_data:
                    continue

                # Reconstruir datos de file_size/duration desde telegram_media
                tgm_data = conn.execute(
                    "SELECT file_size, duration_seconds "
                    "FROM telegram_media WHERE id=?",
                    (tg_media_id,),
                ).fetchone()

                msg_min = {
                    "date": msg_data[0] or "",
                    "from": msg_data[1] or "",
                    "from_id": msg_data[2] or "",
                    "file_size": tgm_data[0] if tgm_data else 0,
                    "duration_seconds": tgm_data[1] if tgm_data else None,
                }
                media_id = ingerir_media_telegram(
                    conn, export_path, file_rel, msg_min,
                    media_type_tg, mime_t or "",
                )
                if media_id:
                    conn.execute(
                        "UPDATE telegram_media SET media_id=? WHERE id=?",
                        (media_id, tg_media_id),
                    )
                    conn.execute(
                        "UPDATE media SET telegram_message_id=? WHERE id=?",
                        (msg_db_id, media_id),
                    )
                    recuperados += 1
                    stats["media_ingerida"] += 1
                    log.info("  Recuperado: %s", os.path.basename(file_rel))
            conn.commit()
    stats["media_recuperada"] = recuperados

    return stats


def extraer_media_del_mensaje(msg: dict) -> list[dict]:
    """
    Extrae todos los archivos multimedia de un mensaje de Telegram.
    Retorna lista de dicts con metadata.
    """
    items = []

    # Fotos
    if "photo" in msg:
        items.append({
            "media_type": "photo",
            "file_relative_path": msg["photo"],
            "file_name": os.path.basename(msg["photo"]),
            "mime_type": "image/jpeg",
            "file_size": msg.get("photo_file_size"),
            "width": msg.get("width"),
            "height": msg.get("height"),
        })

    # Archivos (voice, video, animation, sticker, document)
    if "file" in msg:
        media_type = msg.get("media_type", "document")
        mime_type = msg.get("mime_type", "")
        items.append({
            "media_type": media_type,
            "file_relative_path": msg["file"],
            "file_name": msg.get("file_name") or os.path.basename(msg["file"]),
            "mime_type": mime_type,
            "file_size": msg.get("file_size"),
            "width": msg.get("width"),
            "height": msg.get("height"),
            "duration_seconds": msg.get("duration_seconds"),
            "thumbnail_path": msg.get("thumbnail"),
        })

    # Stickers (a veces vienen con "file" + media_type=sticker, ya incluido arriba)
    # Pero también podrían venir con "sticker" como sub-objeto en webhook.
    # En el export vienen con "file" + media_type=sticker.

    # Location
    if "location" in msg:
        loc = msg["location"]
        items.append({
            "media_type": "location",
            "file_relative_path": "",
            "mime_type": "application/geo+json",
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
        })

    return items


# ── Interfaz CLI ──────────────────────────────────────────────────────

def crear_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Importar export de Telegram a la base de datos Flujos",
    )
    p.add_argument(
        "--export-path", "-e",
        required=True,
        help="Ruta al directorio del export de Telegram (con result.json)",
    )
    p.add_argument(
        "--include-system",
        action="store_true",
        default=True,
        help="Incluir mensajes de sistema (default: True)",
    )
    p.add_argument(
        "--no-system",
        action="store_false",
        dest="include_system",
        help="Excluir mensajes de sistema",
    )
    p.add_argument(
        "--ingest-media",
        action="store_true",
        default=True,
        help="Ingerir multimedia en tabla media (default: True)",
    )
    p.add_argument(
        "--no-ingest",
        action="store_false",
        dest="ingest_media",
        help="No ingerir multimedia en tabla media",
    )
    p.add_argument(
        "--mode",
        choices=["skip", "update", "replace"],
        default="skip",
        help="Modo de inserción (default: skip)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo previsualizar, no escribir en DB",
    )
    p.add_argument("--db", help="Ruta a la base de datos")
    p.add_argument("--verbose", "-v", action="store_true", help="Modo verbose")
    return p


def main(argv: list[str] | None = None):
    parser = crear_parser()
    args = parser.parse_args(argv)

    nivel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # Validar export path
    export_path = os.path.abspath(args.export_path)
    json_path = os.path.join(export_path, JSON_FILENAME)
    if not os.path.isdir(export_path):
        log.error("El directorio no existe: %s", export_path)
        sys.exit(1)
    if not os.path.isfile(json_path):
        log.error("No se encuentra %s en %s", JSON_FILENAME, export_path)
        sys.exit(1)

    # Abrir DB
    db_path = resolver_db(args.db)
    if not os.path.isfile(db_path):
        log.error("Base de datos no encontrada: %s", db_path)
        sys.exit(1)

    conn = abrir(db_path)
    verificar_schema(conn)

    if not args.dry_run and args.mode == "replace":
        # Auto-backup antes de reemplazar
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"flujos_auto_{ts}.db")
        shutil.copy2(db_path, backup_path)
        log.info("Backup automático creado: %s", os.path.basename(backup_path))

    # Parsear JSON (una sola vez)
    data = parsear_export(export_path)

    # Resumen previo
    log.info("Export: %s", export_path)
    log.info("Chat: %s (id=%s)", data.get("name", "?"), data.get("id", "?"))
    log.info("Tipo: %s", data.get("type", "?"))
    log.info("Total mensajes en JSON: %d", len(data["messages"]))
    log.info("Modo: %s", args.mode)

    if args.dry_run:
        log.info("── DRY RUN ──")

    stats = procesar_export(
        conn,
        export_path,
        data,
        include_system=args.include_system,
        ingest_media=args.ingest_media,
        modo=args.mode,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        log.info("── DRY RUN (sin cambios) ──")

    # Mostrar stats
    log.info("")
    log.info("Resumen de importación:")
    log.info("  Total mensajes en JSON: %d", stats["total"])
    log.info("  Mensajes importados:   %d", stats["mensajes"])
    log.info("  Sistema (marcados):    %d", stats["sistema"])
    log.info("  Saltados (ya existen): %d", stats["saltados"])
    log.info("  Media registrada:      %d", stats["media_registrada"])
    log.info("  Media ingerida (media):%d", stats["media_ingerida"])
    if stats.get("media_recuperada"):
        log.info("  Media recuperada:      %d", stats["media_recuperada"])
    log.info("  Errores:               %d", stats["errores"])

    conn.close()


if __name__ == "__main__":
    main()
